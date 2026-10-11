"""Regression coverage for durable cancellation during worker-run recovery."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

import mas_core.memory.storage as storage_module
from mas_core.memory.storage import AgentStorage
from mas_core.worker_contract import (
    NativeWorkerAdapter,
    WorkerResult,
    WorkerRunController,
    WorkerRunError,
    WorkerRunRequest,
)


class _Result:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict[str, object]]:
        return self._rows

    def first(self) -> dict[str, object] | None:
        return self._rows[0] if self._rows else None


@pytest.mark.asyncio
async def test_result_evidence_claim_is_checked_after_worker_row_lock_wait(monkeypatch) -> None:
    lock_acquired_at = datetime(2026, 1, 1, tzinfo=UTC)
    lease_expires_at = lock_acquired_at + timedelta(seconds=5)

    class _Clock:
        current = lock_acquired_at

        @classmethod
        def now(cls, *, tz=None):  # noqa: ANN001
            return cls.current

    monkeypatch.setattr(storage_module, "datetime", _Clock)
    run_id = uuid4()
    row = {
        "id": run_id,
        "state": "RUNNING",
        "claim_owner": "executor-a",
        "attempt_count": 2,
        "lease_expires_at": lease_expires_at,
    }
    connection = MagicMock()

    async def fetch_after_lock_wait(_query):  # noqa: ANN001
        # Simulate the SELECT FOR UPDATE returning only after the lease expired.
        _Clock.current = lease_expires_at + timedelta(seconds=1)
        return _Result([row])

    connection.execute = AsyncMock(side_effect=fetch_after_lock_wait)
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction
    storage = AgentStorage.__new__(AgentStorage)
    storage._engine = engine

    persisted = await storage.persist_worker_result_evidence_claim_fenced(
        run_id=run_id,
        claim_owner="executor-a",
        attempt_count=2,
        artifacts=[],
        usage={"prompt_tokens": 1},
    )

    assert persisted is None
    assert connection.execute.await_count == 1


@pytest.mark.asyncio
async def test_recovery_settles_durable_cancellation_instead_of_requeueing() -> None:
    run_id = uuid4()
    expired_at = datetime.now(tz=UTC) - timedelta(seconds=1)
    row = {
        "id": run_id,
        "state": "RUNNING",
        "attempt_count": 2,
        "cancel_requested_at": expired_at,
        "lease_expires_at": expired_at,
    }

    connection = MagicMock()
    connection.execute = AsyncMock(
        side_effect=[
            _Result([row]),
            MagicMock(rowcount=1),
            MagicMock(rowcount=1),
        ]
    )
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction

    storage = AgentStorage.__new__(AgentStorage)
    storage._engine = engine

    recovered = await storage.recover_expired_worker_runs()

    assert recovered[0]["id"] == run_id
    assert recovered[0]["state"] == "CANCELLED"
    assert recovered[0]["claim_owner"] is None
    assert recovered[0]["lease_expires_at"] is None
    assert "not requeued" in str(recovered[0]["recovery_reason"])

    cancellation_update = connection.execute.await_args_list[1].args[0]
    assert cancellation_update.compile().params["state"] == "CANCELLED"
    assert cancellation_update.compile().params["next_attempt_at"] is None

    transition_insert = connection.execute.await_args_list[2].args[0]
    assert transition_insert.compile().params["from_state"] == "RUNNING"
    assert transition_insert.compile().params["to_state"] == "CANCELLED"
    assert connection.execute.await_count == 3


@pytest.mark.asyncio
async def test_claim_excludes_queued_runs_with_durable_cancellation_marker() -> None:
    connection = MagicMock()
    connection.execute = AsyncMock(return_value=_Result([]))
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction

    storage = AgentStorage.__new__(AgentStorage)
    storage._engine = engine

    assert await storage.claim_worker_run(owner="host-executor") is None

    query = connection.execute.await_args.args[0]
    assert "cancel_requested_at IS NULL" in str(query)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["validate", "effect"])
async def test_cancel_requested_claim_cannot_validate_or_reserve_tool_effect(
    operation: str,
) -> None:
    run_id = uuid4()
    row = {
        "id": run_id,
        "state": "RUNNING",
        "claim_owner": "executor-a",
        "attempt_count": 3,
        "lease_expires_at": datetime.now(tz=UTC) + timedelta(minutes=1),
        "cancel_requested_at": datetime.now(tz=UTC),
    }
    connection = MagicMock()
    connection.execute = AsyncMock(return_value=_Result([row]))
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction
    storage = AgentStorage.__new__(AgentStorage)
    storage._engine = engine

    if operation == "validate":
        current = await storage.validate_worker_run_claim(
            run_id=run_id,
            claim_owner="executor-a",
            attempt_count=3,
        )
        assert current is False
    else:
        effect = await storage.begin_worker_tool_effect(
            run_id=run_id,
            request_id=uuid4(),
            idempotency_key="cancelled-run-effect",
            tool_name="repository.write",
            request_sha256="a" * 64,
            claim_owner="executor-a",
            attempt_count=3,
        )
        assert effect["disposition"] == "CLAIM_LOST"

    assert connection.execute.await_count == 1
    query = connection.execute.await_args.args[0]
    assert "FOR UPDATE" in str(query)


@pytest.mark.asyncio
async def test_recovery_settles_queued_run_with_durable_cancellation_marker() -> None:
    run_id = uuid4()
    row = {
        "id": run_id,
        "state": "QUEUED",
        "attempt_count": 0,
        "cancel_requested_at": datetime.now(tz=UTC),
        "lease_expires_at": None,
    }

    connection = MagicMock()
    connection.execute = AsyncMock(
        side_effect=[
            _Result([row]),
            MagicMock(rowcount=1),
            MagicMock(rowcount=1),
        ]
    )
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction

    storage = AgentStorage.__new__(AgentStorage)
    storage._engine = engine

    recovered = await storage.recover_expired_worker_runs()

    assert recovered[0]["state"] == "CANCELLED"
    cancellation_update = connection.execute.await_args_list[1].args[0]
    transition_insert = connection.execute.await_args_list[2].args[0]
    assert cancellation_update.compile().params["state"] == "CANCELLED"
    assert transition_insert.compile().params["from_state"] == "QUEUED"
    assert transition_insert.compile().params["to_state"] == "CANCELLED"


@pytest.mark.asyncio
async def test_heartbeat_requires_live_matching_owner_and_attempt() -> None:
    run_id = uuid4()
    owner = "executor-a"
    lease_expires_at = datetime.now(tz=UTC) + timedelta(minutes=1)
    current = {
        "id": run_id,
        "state": "RUNNING",
        "claim_owner": owner,
        "attempt_count": 4,
        "lease_expires_at": lease_expires_at,
    }

    connection = MagicMock()
    connection.execute = AsyncMock(side_effect=[_Result([current]), MagicMock(rowcount=1)])
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction

    storage = AgentStorage.__new__(AgentStorage)
    storage._engine = engine
    storage.get_worker_run = AsyncMock(return_value={**current, "heartbeat_at": datetime.now(tz=UTC)})

    heartbeat = await storage.heartbeat_worker_run(
        run_id,
        owner=owner,
        attempt_count=4,
        lease_seconds=60,
    )

    assert heartbeat is not None
    update = connection.execute.await_args_list[1].args[0]
    sql = str(update)
    assert "claim_owner" in sql
    assert "attempt_count" in sql
    assert "lease_expires_at" in sql
    assert update.compile().params["claim_owner_1"] == owner
    assert update.compile().params["attempt_count_1"] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("owner", "attempt_count", "lease_expires_at"),
    [
        ("executor-b", 4, datetime.now(tz=UTC) + timedelta(minutes=1)),
        ("executor-a", 3, datetime.now(tz=UTC) + timedelta(minutes=1)),
        ("executor-a", 4, datetime.now(tz=UTC) - timedelta(seconds=1)),
    ],
)
async def test_heartbeat_rejects_wrong_attempt_owner_or_expired_lease(
    owner: str,
    attempt_count: int,
    lease_expires_at: datetime,
) -> None:
    run_id = uuid4()
    current = {
        "id": run_id,
        "state": "RUNNING",
        "claim_owner": "executor-a",
        "attempt_count": 4,
        "lease_expires_at": lease_expires_at,
    }
    connection = MagicMock()
    connection.execute = AsyncMock(return_value=_Result([current]))
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction
    storage = AgentStorage.__new__(AgentStorage)
    storage._engine = engine

    assert await storage.heartbeat_worker_run(
        run_id,
        owner=owner,
        attempt_count=attempt_count,
    ) is None
    assert connection.execute.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("claim_owner", "attempt_count", "lease_expires_at"),
    [
        ("executor-b", 5, datetime.now(tz=UTC) + timedelta(minutes=1)),
        ("executor-a", 4, datetime.now(tz=UTC) - timedelta(seconds=1)),
    ],
)
async def test_worker_transition_rejects_stale_claim_fence(
    claim_owner: str,
    attempt_count: int,
    lease_expires_at: datetime,
) -> None:
    run_id = uuid4()
    current = {
        "id": run_id,
        "state": "RUNNING",
        "claim_owner": claim_owner,
        "attempt_count": attempt_count,
        "lease_expires_at": lease_expires_at,
    }
    connection = MagicMock()
    connection.execute = AsyncMock(return_value=_Result([current]))
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction
    storage = AgentStorage.__new__(AgentStorage)
    storage._engine = engine

    transitioned = await storage.transition_worker_run(
        run_id,
        new_state="SUCCEEDED",
        expected_state="RUNNING",
        claim_owner="executor-a",
        attempt_count=4,
        result={"output": "must not settle"},
    )

    assert transitioned is None
    assert connection.execute.await_count == 1


@pytest.mark.asyncio
async def test_stale_host_cancel_cannot_cancel_replacement_attempt() -> None:
    run_id = uuid4()
    replacement = {
        "id": run_id,
        "state": "RUNNING",
        "claim_owner": "executor-new",
        "attempt_count": 7,
        "lease_expires_at": datetime.now(tz=UTC) + timedelta(minutes=1),
    }
    connection = MagicMock()
    connection.execute = AsyncMock(return_value=_Result([replacement]))
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction
    storage = AgentStorage.__new__(AgentStorage)
    storage._engine = engine

    cancelled = await storage.transition_worker_run(
        run_id,
        new_state="CANCELLED",
        expected_state="RUNNING",
        actor="worker-host-executor",
        claim_owner="executor-old",
        attempt_count=6,
        error={"code": "CANCELLED", "message": "stale host"},
    )

    assert cancelled is None
    # The locked row is rejected before runtime-binding or canonical writes.
    assert connection.execute.await_count == 1


@pytest.mark.asyncio
async def test_controller_rejects_stale_host_cancel_before_adapter_cancellation() -> None:
    run_id = uuid4()
    storage = MagicMock()
    storage.get_worker_run = AsyncMock(
        return_value={
            "id": run_id,
            "state": "RUNNING",
            "claim_owner": "executor-new",
            "attempt_count": 7,
            "lease_expires_at": datetime.now(tz=UTC) + timedelta(minutes=1),
        }
    )
    adapter = MagicMock()
    adapter.cancel = AsyncMock()
    controller = WorkerRunController(storage=storage)

    with pytest.raises(WorkerRunError, match="claim is no longer current"):
        await controller.cancel(
            run_id,
            adapter,
            reason="old host lost its binding",
            requested_by="worker-host-executor",
            claim_owner="executor-old",
            attempt_count=6,
        )

    adapter.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_claim_requires_explicit_operator_cancel_flag() -> None:
    run_id = uuid4()
    expired_at = datetime.now(tz=UTC) - timedelta(seconds=1)
    current = {
        "id": run_id,
        "state": "RUNNING",
        "claim_owner": "executor-a",
        "attempt_count": 4,
        "lease_expires_at": expired_at,
    }

    denied_connection = MagicMock()
    denied_connection.execute = AsyncMock(return_value=_Result([current]))
    denied_transaction = MagicMock()
    denied_transaction.__aenter__ = AsyncMock(return_value=denied_connection)
    denied_transaction.__aexit__ = AsyncMock(return_value=False)
    denied_engine = MagicMock()
    denied_engine.begin.return_value = denied_transaction
    denied_storage = AgentStorage.__new__(AgentStorage)
    denied_storage._engine = denied_engine

    denied = await denied_storage.transition_worker_run(
        run_id,
        new_state="CANCELLED",
        expected_state="RUNNING",
        actor="operator",
        claim_owner="executor-a",
        attempt_count=4,
    )

    assert denied is None
    assert denied_connection.execute.await_count == 1

    allowed_connection = MagicMock()
    allowed_connection.execute = AsyncMock(
        side_effect=[
            _Result([current]),
            MagicMock(rowcount=1),  # runtime binding update
            MagicMock(rowcount=1),  # canonical run update
            MagicMock(rowcount=1),  # transition audit insert
        ]
    )
    allowed_transaction = MagicMock()
    allowed_transaction.__aenter__ = AsyncMock(return_value=allowed_connection)
    allowed_transaction.__aexit__ = AsyncMock(return_value=False)
    allowed_engine = MagicMock()
    allowed_engine.begin.return_value = allowed_transaction
    allowed_storage = AgentStorage.__new__(AgentStorage)
    allowed_storage._engine = allowed_engine
    allowed_storage.get_worker_run = AsyncMock(
        return_value={**current, "state": "CANCELLED", "claim_owner": None}
    )

    allowed = await allowed_storage.transition_worker_run(
        run_id,
        new_state="CANCELLED",
        expected_state="RUNNING",
        actor="operator",
        allow_unfenced_cancel=True,
        error={"code": "CANCELLED", "message": "operator cancellation"},
    )

    assert allowed is not None and allowed["state"] == "CANCELLED"
    canonical_update = allowed_connection.execute.await_args_list[2].args[0]
    assert "lease_expires_at >" not in str(canonical_update)


@pytest.mark.asyncio
async def test_current_claim_owner_and_attempt_can_settle_run() -> None:
    run_id = uuid4()
    owner = "executor-a"
    current = {
        "id": run_id,
        "state": "RUNNING",
        "claim_owner": owner,
        "attempt_count": 4,
        "lease_expires_at": datetime.now(tz=UTC) + timedelta(minutes=1),
    }
    connection = MagicMock()
    connection.execute = AsyncMock(
        side_effect=[
            _Result([current]),
            MagicMock(rowcount=1),  # runtime binding update
            MagicMock(rowcount=1),  # canonical run update
            MagicMock(rowcount=1),  # transition audit insert
        ]
    )
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction
    storage = AgentStorage.__new__(AgentStorage)
    storage._engine = engine
    storage.get_worker_run = AsyncMock(return_value={**current, "state": "SUCCEEDED"})

    transitioned = await storage.transition_worker_run(
        run_id,
        new_state="SUCCEEDED",
        expected_state="RUNNING",
        claim_owner=owner,
        attempt_count=4,
        result={"output": "completed"},
    )

    assert transitioned is not None
    assert transitioned["state"] == "SUCCEEDED"
    update = connection.execute.await_args_list[2].args[0]
    sql = str(update)
    assert "claim_owner" in sql
    assert "attempt_count" in sql
    assert "lease_expires_at" in sql
    assert update.compile().params["claim_owner_1"] == owner
    assert update.compile().params["attempt_count_1"] == 4


class _ClaimedExecutionStorage:
    def __init__(
        self,
        run_id: UUID,
        *,
        owner: str,
        attempt_count: int,
        reclaim_on_running: bool = False,
    ) -> None:
        self.reclaim_on_running = reclaim_on_running
        self.persisted_result_evidence: dict[str, object] | None = None
        self.run = {
            "id": run_id,
            "state": "CLAIMED",
            "claim_owner": owner,
            "attempt_count": attempt_count,
            "lease_expires_at": datetime.now(tz=UTC) + timedelta(minutes=1),
        }
        self.transitions: list[dict[str, object]] = []

    async def create_worker_run(self, **values: object) -> dict[str, object]:
        self.run.update(values)
        return dict(self.run)

    async def get_worker_run(self, run_id: UUID) -> dict[str, object] | None:
        return dict(self.run) if run_id == self.run["id"] else None

    async def transition_worker_run(
        self,
        run_id: UUID,
        *,
        new_state: str,
        expected_state: str | None = None,
        **values: object,
    ) -> dict[str, object] | None:
        self.transitions.append({"state": new_state, **values})
        if new_state == "RUNNING" and self.reclaim_on_running:
            self.run.update(
                {
                    "state": "DISPATCHING",
                    "claim_owner": "executor-new",
                    "attempt_count": int(self.run["attempt_count"]) + 1,
                    "lease_expires_at": datetime.now(tz=UTC) + timedelta(minutes=1),
                }
            )
            return None
        if run_id != self.run["id"] or (
            expected_state is not None and self.run["state"] != expected_state
        ):
            return None
        current_owner = self.run.get("claim_owner")
        current_attempt = int(self.run.get("attempt_count") or 0)
        is_control_cancel = new_state == "CANCELLED" and values.get("allow_unfenced_cancel") is True
        if (
            not is_control_cancel
            and (current_owner is not None or current_attempt > 0)
            and (
                values.get("claim_owner") != current_owner
                or values.get("attempt_count") != current_attempt
                or self.run.get("lease_expires_at") <= datetime.now(tz=UTC)
            )
        ):
            return None
        if new_state not in {"VALIDATING", "READY", "DISPATCHING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}:
            raise AssertionError(f"unexpected controller transition: {new_state}")
        self.run["state"] = new_state
        if values.get("result") is not None:
            self.run["result_json"] = values["result"]
        if values.get("error") is not None:
            self.run["error_json"] = values["error"]
        if new_state in {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}:
            self.run["claim_owner"] = None
            self.run["lease_expires_at"] = None
        return dict(self.run)

    async def append_worker_event(self, **_values: object) -> dict[str, object]:
        return {}

    async def create_worker_usage(self, **_values: object) -> dict[str, object]:
        return {}

    async def persist_worker_result_evidence_claim_fenced(
        self,
        *,
        run_id: UUID,
        claim_owner: str | None,
        attempt_count: int,
        artifacts: list[dict[str, object]],
        usage: dict[str, object],
    ) -> dict[str, object] | None:
        if (
            run_id != self.run["id"]
            or self.run.get("state") != "RUNNING"
            or self.run.get("claim_owner") != claim_owner
            or self.run.get("attempt_count") != attempt_count
            or self.run.get("lease_expires_at") <= datetime.now(tz=UTC)
        ):
            return None
        self.persisted_result_evidence = {"artifacts": artifacts, "usage": usage}
        return self.persisted_result_evidence


@pytest.mark.asyncio
async def test_controller_threads_claim_fence_through_run_completion() -> None:
    run_id = uuid4()
    worker_id = uuid4()
    owner = "executor-current"
    storage = _ClaimedExecutionStorage(run_id, owner=owner, attempt_count=5)

    async def worker(request, _adapter):
        return WorkerResult(
            run_id=request.run_id,
            worker_id=request.worker_id,
            success=True,
            output={"result": "done"},
        )

    adapter = NativeWorkerAdapter(worker, worker_id="fenced-worker")
    request = WorkerRunRequest(
        run_id=run_id,
        idempotency_key="current-claim-completes",
        worker_id="fenced-worker",
        task_type="test",
    )

    outcome = await WorkerRunController(storage=storage).execute(
        request,
        adapter,
        worker_registry_id=worker_id,
    )

    assert outcome.state == "SUCCEEDED"
    assert storage.run["state"] == "SUCCEEDED"
    assert storage.persisted_result_evidence is not None
    execution_transitions = [
        transition
        for transition in storage.transitions
        if transition["state"] in {"VALIDATING", "READY", "DISPATCHING", "RUNNING", "SUCCEEDED"}
    ]
    assert execution_transitions
    assert all(transition["claim_owner"] == owner for transition in execution_transitions)
    assert all(transition["attempt_count"] == 5 for transition in execution_transitions)
    await adapter.close()


@pytest.mark.asyncio
async def test_controller_timeout_cancels_the_claimed_attempt_before_settlement() -> None:
    run_id = uuid4()
    worker_id = uuid4()
    owner = "executor-timeout"
    storage = _ClaimedExecutionStorage(run_id, owner=owner, attempt_count=2)
    runtime_stopped = asyncio.Event()

    async def worker(request, _adapter):
        try:
            await asyncio.Event().wait()
        finally:
            runtime_stopped.set()

    class RecordingAdapter(NativeWorkerAdapter):
        def __init__(self) -> None:
            super().__init__(worker, worker_id="fenced-worker")
            self.cancellations = []

        async def cancel(self, request):
            self.cancellations.append(request)
            return await super().cancel(request)

    adapter = RecordingAdapter()
    request = WorkerRunRequest(
        run_id=run_id,
        idempotency_key="current-claim-times-out",
        worker_id="fenced-worker",
        task_type="test",
        timeout_seconds=1,
    )

    outcome = await WorkerRunController(storage=storage).execute(
        request,
        adapter,
        worker_registry_id=worker_id,
    )

    assert outcome.state == "TIMED_OUT"
    assert runtime_stopped.is_set()
    assert len(adapter.cancellations) == 1
    assert adapter.cancellations[0].execution_claim_fence == {
        "claim_owner": owner,
        "attempt_count": 2,
    }
    assert storage.run["state"] == "TIMED_OUT"
    await adapter.close()


@pytest.mark.asyncio
async def test_controller_stale_attempt_cannot_settle_after_reclaim() -> None:
    run_id = uuid4()
    worker_id = uuid4()
    owner = "executor-old"
    storage = _ClaimedExecutionStorage(run_id, owner=owner, attempt_count=5)

    async def worker(request, _adapter):
        storage.run.update(
            {
                "state": "RUNNING",
                "claim_owner": "executor-new",
                "attempt_count": 6,
                "lease_expires_at": datetime.now(tz=UTC) + timedelta(minutes=1),
            }
        )
        return WorkerResult(
            run_id=request.run_id,
            worker_id=request.worker_id,
            success=True,
            output={"result": "stale result"},
        )

    adapter = NativeWorkerAdapter(worker, worker_id="fenced-worker")
    request = WorkerRunRequest(
        run_id=run_id,
        idempotency_key="stale-claim-cannot-complete",
        worker_id="fenced-worker",
        task_type="test",
    )

    outcome = await WorkerRunController(storage=storage).execute(
        request,
        adapter,
        worker_registry_id=worker_id,
    )

    assert outcome.state == "RUNNING"
    assert storage.run["state"] == "RUNNING"
    assert storage.run["claim_owner"] == "executor-new"
    assert storage.run["attempt_count"] == 6
    assert "result_json" not in storage.run
    assert not any(transition["state"] == "SUCCEEDED" for transition in storage.transitions)
    await adapter.close()


@pytest.mark.asyncio
async def test_controller_force_stops_runtime_if_running_transition_loses_claim() -> None:
    run_id = uuid4()
    worker_id = uuid4()
    storage = _ClaimedExecutionStorage(
        run_id,
        owner="executor-old",
        attempt_count=5,
        reclaim_on_running=True,
    )
    runtime_started = asyncio.Event()
    runtime_stopped = asyncio.Event()

    async def worker(request, _adapter):
        runtime_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            runtime_stopped.set()
        return WorkerResult(
            run_id=request.run_id,
            worker_id=request.worker_id,
            success=True,
            output={"must_not_settle": True},
        )

    class StartedAdapter(NativeWorkerAdapter):
        def __init__(self) -> None:
            super().__init__(worker, worker_id="fenced-worker")
            self.cancellations = []

        async def start(self, request):
            accepted = await super().start(request)
            await asyncio.sleep(0)
            return accepted

        async def cancel(self, request):
            self.cancellations.append(request)
            return await super().cancel(request)

    adapter = StartedAdapter()
    request = WorkerRunRequest(
        run_id=run_id,
        idempotency_key="claim-lost-after-start",
        worker_id="fenced-worker",
        task_type="test",
    )

    outcome = await WorkerRunController(storage=storage).execute(
        request,
        adapter,
        worker_registry_id=worker_id,
    )

    assert runtime_started.is_set()
    assert runtime_stopped.is_set()
    assert outcome.state == "DISPATCHING"
    assert storage.run["claim_owner"] == "executor-new"
    assert storage.run["attempt_count"] == 6
    assert len(adapter.cancellations) == 1
    assert adapter.cancellations[0].force is True
    assert not adapter._active_tasks
    await adapter.close()
