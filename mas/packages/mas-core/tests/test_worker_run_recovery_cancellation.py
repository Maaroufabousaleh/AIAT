"""Regression coverage for durable cancellation during worker-run recovery."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

import mas_core.memory.storage as storage_module
from mas_core.memory.storage import AgentStorage
from mas_core.worker_contract import (
    ArtifactKind,
    EventType,
    NativeWorkerAdapter,
    WorkerArtifact,
    WorkerEvent,
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
        reclaim_on_success: bool = False,
        reclaim_on_failure: bool = False,
    ) -> None:
        self.reclaim_on_running = reclaim_on_running
        self.reclaim_on_success = reclaim_on_success
        self.reclaim_on_failure = reclaim_on_failure
        self.persisted_result_evidence: dict[str, object] | None = None
        self.atomic_success_calls: list[dict[str, object]] = []
        self.atomic_failure_calls: list[dict[str, object]] = []
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

    async def complete_worker_run_success_claim_fenced(
        self,
        *,
        run_id: UUID,
        claim_owner: str | None,
        attempt_count: int,
        artifacts: list[dict[str, object]],
        usage: dict[str, object],
        result: dict[str, object],
        replay_metadata: dict[str, object] | None = None,
        project_usage: dict[str, object] | None = None,
        reason: str = "worker emitted a normalized terminal result",
        correlation_id: str | None = None,
    ) -> dict[str, object] | None:
        self.atomic_success_calls.append(
            {
                "run_id": run_id,
                "claim_owner": claim_owner,
                "attempt_count": attempt_count,
                "artifacts": artifacts,
                "usage": usage,
                "result": result,
                "project_usage": project_usage,
            }
        )
        if self.reclaim_on_success:
            self.run.update(
                {
                    "state": "RUNNING",
                    "claim_owner": "executor-new",
                    "attempt_count": attempt_count + 1,
                    "lease_expires_at": datetime.now(tz=UTC) + timedelta(minutes=1),
                }
            )
            return None
        if (
            run_id != self.run["id"]
            or self.run.get("state") not in {"RUNNING", "PAUSING"}
            or self.run.get("claim_owner") != claim_owner
            or self.run.get("attempt_count") != attempt_count
            or self.run.get("lease_expires_at") <= datetime.now(tz=UTC)
        ):
            return None
        self.persisted_result_evidence = {"artifacts": artifacts, "usage": usage}
        self.run.update(
            {
                "state": "SUCCEEDED",
                "result_json": result,
                "claim_owner": None,
                "lease_expires_at": None,
            }
        )
        self.transitions.append(
            {
                "state": "SUCCEEDED",
                "claim_owner": claim_owner,
                "attempt_count": attempt_count,
                "reason": reason,
                "correlation_id": correlation_id,
            }
        )
        return dict(self.run)

    async def complete_worker_run_failure_claim_fenced(
        self,
        *,
        run_id: UUID,
        claim_owner: str | None,
        attempt_count: int,
        artifacts: list[dict[str, object]],
        usage: dict[str, object],
        result: dict[str, object],
        error: dict[str, object] | None,
        replay_metadata: dict[str, object] | None = None,
        project_usage: dict[str, object] | None = None,
        reason: str = "worker emitted a normalized terminal result",
        correlation_id: str | None = None,
    ) -> dict[str, object] | None:
        self.atomic_failure_calls.append(
            {
                "run_id": run_id,
                "claim_owner": claim_owner,
                "attempt_count": attempt_count,
                "artifacts": artifacts,
                "usage": usage,
                "result": result,
                "error": error,
                "project_usage": project_usage,
            }
        )
        if self.reclaim_on_failure:
            self.run.update(
                {
                    "state": "RUNNING",
                    "claim_owner": "executor-new",
                    "attempt_count": attempt_count + 1,
                    "lease_expires_at": datetime.now(tz=UTC) + timedelta(minutes=1),
                }
            )
            return None
        if (
            run_id != self.run["id"]
            or self.run.get("state") not in {"RUNNING", "PAUSING"}
            or self.run.get("claim_owner") != claim_owner
            or self.run.get("attempt_count") != attempt_count
            or self.run.get("lease_expires_at") <= datetime.now(tz=UTC)
        ):
            return None
        self.persisted_result_evidence = {"artifacts": artifacts, "usage": usage}
        self.run.update(
            {
                "state": "FAILED",
                "result_json": result,
                "error_json": error,
                "claim_owner": None,
                "lease_expires_at": None,
            }
        )
        self.transitions.append(
            {
                "state": "FAILED",
                "claim_owner": claim_owner,
                "attempt_count": attempt_count,
                "reason": reason,
                "correlation_id": correlation_id,
            }
        )
        return dict(self.run)


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


class _NormalizedFailureAdapter(NativeWorkerAdapter):
    def __init__(self, result: WorkerResult) -> None:
        self._normalized_failure = result
        super().__init__(lambda _request: result, worker_id=result.worker_id)

    async def _run_and_emit(self, request: WorkerRunRequest) -> None:
        result = self._normalized_failure
        self._terminal_status[request.run_id] = "FAILED"
        try:
            await self._emit(
                WorkerEvent(
                    run_id=request.run_id,
                    worker_id=request.worker_id,
                    event_type=EventType.ERROR,
                    result=result,
                    error=result.error,
                    usage=result.usage,
                )
            )
        finally:
            self._active_tasks.pop(request.run_id, None)
            await self._close_queue(request.run_id)


@pytest.mark.asyncio
async def test_claimed_success_commits_evidence_and_transition_in_one_transaction() -> None:
    run_id = uuid4()
    owner = "executor-current"
    attempt_count = 7
    now = datetime.now(tz=UTC)
    run_row = {
        "id": run_id,
        "state": "RUNNING",
        "claim_owner": owner,
        "attempt_count": attempt_count,
        "lease_expires_at": now + timedelta(minutes=1),
        "cancel_requested_at": None,
    }
    statements = []
    connection = MagicMock()

    class _Scalar:
        def scalar_one(self) -> int:
            return 42

    async def execute(statement):  # noqa: ANN001
        statements.append(statement)
        if statement.is_select:
            return _Result([run_row])
        if statement.is_insert and statement.table.name == "artifacts":
            return _Scalar()
        return MagicMock(rowcount=1)

    connection.execute = AsyncMock(side_effect=execute)
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction
    storage = AgentStorage.__new__(AgentStorage)
    storage._engine = engine

    result_payload = {"success": True, "output": {"answer": "done"}}
    completed = await storage.complete_worker_run_success_claim_fenced(
        run_id=run_id,
        claim_owner=owner,
        attempt_count=attempt_count,
        artifacts=[
            {
                "agent_id": "worker-1",
                "path": "result.txt",
                "metadata": {"worker_execution_attempt": attempt_count},
                "sha256": "a" * 64,
                "size_bytes": 4,
                "kind": "file",
                "uri": "result.txt",
            }
        ],
        usage={"prompt_tokens": 5, "completion_tokens": 2},
        result=result_payload,
        project_usage={
            "project_id": uuid4(),
            "company_id": uuid4(),
            "event_type": "llm",
            "worker_id": uuid4(),
            "model": "test-model",
            "prompt_tokens": 5,
            "completion_tokens": 2,
            "idempotency_key": f"worker-run:{run_id}:attempt:{attempt_count}:usage",
        },
        correlation_id="atomic-success",
    )

    assert completed is not None
    assert completed["state"] == "SUCCEEDED"
    assert completed["result_json"] == result_payload
    assert engine.begin.call_count == 1

    def table_name(statement):  # noqa: ANN001
        table = getattr(statement, "table", None)
        if table is not None:
            return table.name
        return statement.get_final_froms()[0].name

    assert [table_name(statement) for statement in statements] == [
        "worker_runs",
        "worker_runs",
        "artifacts",
        "worker_artifacts",
        "worker_usage_records",
        "project_usage_events",
        "worker_run_runtime_bindings",
        "worker_run_transitions",
    ]
    transition_insert = statements[-1]
    assert transition_insert.compile().params["from_state"] == "RUNNING"
    assert transition_insert.compile().params["to_state"] == "SUCCEEDED"
    success_update = statements[1]
    assert success_update.compile().params["state"] == "SUCCEEDED"
    assert success_update.compile().params["result_json"] == result_payload


@pytest.mark.asyncio
@pytest.mark.parametrize("stale_reason", ["reassigned", "expired"])
async def test_claimed_success_rejects_stale_claim_before_any_evidence_write(
    stale_reason: str,
) -> None:
    run_id = uuid4()
    owner = "executor-old"
    attempt_count = 7
    run_row = {
        "id": run_id,
        "state": "RUNNING",
        "claim_owner": "executor-new" if stale_reason == "reassigned" else owner,
        "attempt_count": attempt_count + 1 if stale_reason == "reassigned" else attempt_count,
        "lease_expires_at": (
            datetime.now(tz=UTC) + timedelta(minutes=1)
            if stale_reason == "reassigned"
            else datetime.now(tz=UTC) - timedelta(seconds=1)
        ),
        "cancel_requested_at": None,
    }
    connection = MagicMock()
    connection.execute = AsyncMock(return_value=_Result([run_row]))
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction
    storage = AgentStorage.__new__(AgentStorage)
    storage._engine = engine

    completed = await storage.complete_worker_run_success_claim_fenced(
        run_id=run_id,
        claim_owner=owner,
        attempt_count=attempt_count,
        artifacts=[
            {
                "agent_id": "worker-1",
                "path": "stale.txt",
                "kind": "file",
                "uri": "stale.txt",
                "sha256": "b" * 64,
            }
        ],
        usage={"prompt_tokens": 3},
        result={"success": True},
    )

    assert completed is None
    assert connection.execute.await_count == 1
    assert transaction.__aexit__.await_args.args[0] is None


@pytest.mark.asyncio
async def test_claimed_failure_commits_evidence_and_transition_in_one_transaction() -> None:
    run_id = uuid4()
    owner = "executor-current"
    attempt_count = 8
    run_row = {
        "id": run_id,
        "state": "RUNNING",
        "claim_owner": owner,
        "attempt_count": attempt_count,
        "lease_expires_at": datetime.now(tz=UTC) + timedelta(minutes=1),
        "cancel_requested_at": None,
    }
    statements = []
    connection = MagicMock()

    class _Scalar:
        def scalar_one(self) -> int:
            return 43

    async def execute(statement):  # noqa: ANN001
        statements.append(statement)
        if statement.is_select:
            return _Result([run_row])
        if statement.is_insert and statement.table.name == "artifacts":
            return _Scalar()
        return MagicMock(rowcount=1)

    connection.execute = AsyncMock(side_effect=execute)
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction
    storage = AgentStorage.__new__(AgentStorage)
    storage._engine = engine

    result_payload = {"success": False, "output": {"partial": True}}
    error_payload = {"code": "WORKER_FAILED", "message": "bounded failure"}
    completed = await storage.complete_worker_run_failure_claim_fenced(
        run_id=run_id,
        claim_owner=owner,
        attempt_count=attempt_count,
        artifacts=[
            {
                "agent_id": "worker-1",
                "path": "partial.txt",
                "metadata": {"worker_execution_attempt": attempt_count},
                "sha256": "c" * 64,
                "size_bytes": 7,
                "kind": "file",
                "uri": "partial.txt",
            }
        ],
        usage={"prompt_tokens": 3, "completion_tokens": 1},
        result=result_payload,
        error=error_payload,
        correlation_id="atomic-failure",
    )

    assert completed is not None
    assert completed["state"] == "FAILED"
    assert completed["result_json"] == result_payload
    assert completed["error_json"] == error_payload
    assert engine.begin.call_count == 1

    def table_name(statement):  # noqa: ANN001
        table = getattr(statement, "table", None)
        if table is not None:
            return table.name
        return statement.get_final_froms()[0].name

    assert [table_name(statement) for statement in statements] == [
        "worker_runs",
        "worker_runs",
        "artifacts",
        "worker_artifacts",
        "worker_usage_records",
        "worker_run_runtime_bindings",
        "worker_run_transitions",
    ]
    failed_update = statements[1]
    assert failed_update.compile().params["state"] == "FAILED"
    assert failed_update.compile().params["result_json"] == result_payload
    assert failed_update.compile().params["error_json"] == error_payload
    assert statements[-2].compile().params["status"] == "FAILED"
    transition_insert = statements[-1]
    assert transition_insert.compile().params["from_state"] == "RUNNING"
    assert transition_insert.compile().params["to_state"] == "FAILED"


@pytest.mark.asyncio
@pytest.mark.parametrize("stale_reason", ["reassigned", "expired"])
async def test_claimed_failure_rejects_stale_claim_before_any_evidence_write(
    stale_reason: str,
) -> None:
    run_id = uuid4()
    owner = "executor-old"
    attempt_count = 8
    run_row = {
        "id": run_id,
        "state": "RUNNING",
        "claim_owner": "executor-new" if stale_reason == "reassigned" else owner,
        "attempt_count": attempt_count + 1 if stale_reason == "reassigned" else attempt_count,
        "lease_expires_at": (
            datetime.now(tz=UTC) + timedelta(minutes=1)
            if stale_reason == "reassigned"
            else datetime.now(tz=UTC) - timedelta(seconds=1)
        ),
        "cancel_requested_at": None,
    }
    connection = MagicMock()
    connection.execute = AsyncMock(return_value=_Result([run_row]))
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction
    storage = AgentStorage.__new__(AgentStorage)
    storage._engine = engine

    completed = await storage.complete_worker_run_failure_claim_fenced(
        run_id=run_id,
        claim_owner=owner,
        attempt_count=attempt_count,
        artifacts=[
            {
                "agent_id": "worker-1",
                "path": "stale.txt",
                "kind": "file",
                "uri": "stale.txt",
                "sha256": "d" * 64,
            }
        ],
        usage={"prompt_tokens": 3},
        result={"success": False},
        error={"code": "WORKER_FAILED", "message": "stale result"},
    )

    assert completed is None
    assert connection.execute.await_count == 1
    assert transaction.__aexit__.await_args.args[0] is None


@pytest.mark.asyncio
async def test_controller_persists_claimed_failure_evidence_and_terminal_state_atomically() -> None:
    run_id = uuid4()
    worker_id = uuid4()
    owner = "executor-current"
    storage = _ClaimedExecutionStorage(run_id, owner=owner, attempt_count=5)
    artifact = WorkerArtifact(
        kind=ArtifactKind.FILE,
        name="partial.txt",
        uri="partial.txt",
        sha256="c" * 64,
        size_bytes=7,
    )
    error = {"code": "WORKER_FAILED", "message": "bounded failure"}

    result = WorkerResult(
        run_id=run_id,
        worker_id="fenced-worker",
        success=False,
        output={"partial": True},
        artifacts=[artifact],
        usage={"prompt_tokens": 3, "completion_tokens": 1},
        error=error,
    )
    adapter = _NormalizedFailureAdapter(result)
    request = WorkerRunRequest(
        run_id=run_id,
        idempotency_key="current-claim-failure-is-atomic",
        worker_id="fenced-worker",
        task_type="test",
    )

    outcome = await WorkerRunController(storage=storage).execute(
        request,
        adapter,
        worker_registry_id=worker_id,
    )

    assert outcome.state == "FAILED"
    assert storage.run["state"] == "FAILED"
    assert storage.run["result_json"]["success"] is False
    assert storage.run["error_json"]["code"] == "WORKER_FAILED"
    assert storage.persisted_result_evidence is not None
    assert len(storage.atomic_failure_calls) == 1
    assert storage.atomic_failure_calls[0]["claim_owner"] == owner
    assert storage.atomic_failure_calls[0]["attempt_count"] == 5
    assert storage.atomic_failure_calls[0]["artifacts"]
    assert storage.atomic_failure_calls[0]["usage"]["prompt_tokens"] == 3
    assert not storage.atomic_success_calls
    assert len([item for item in storage.transitions if item["state"] == "FAILED"]) == 1
    await adapter.close()


@pytest.mark.asyncio
async def test_controller_does_not_leave_evidence_when_claim_is_lost_at_atomic_success() -> None:
    run_id = uuid4()
    worker_id = uuid4()
    owner = "executor-old"
    storage = _ClaimedExecutionStorage(
        run_id,
        owner=owner,
        attempt_count=5,
        reclaim_on_success=True,
    )
    artifact = WorkerArtifact(
        kind=ArtifactKind.FILE,
        name="result.txt",
        uri="result.txt",
        sha256="a" * 64,
        size_bytes=7,
    )

    async def worker(request, _adapter):
        return WorkerResult(
            run_id=request.run_id,
            worker_id=request.worker_id,
            success=True,
            artifacts=[artifact],
            usage={"prompt_tokens": 3, "completion_tokens": 2},
        )

    adapter = NativeWorkerAdapter(worker, worker_id="fenced-worker")
    request = WorkerRunRequest(
        run_id=run_id,
        idempotency_key="claim-lost-at-atomic-success",
        worker_id="fenced-worker",
        task_type="test",
    )

    outcome = await WorkerRunController(storage=storage).execute(
        request,
        adapter,
        worker_registry_id=worker_id,
    )

    assert outcome.state == "RUNNING"
    assert storage.run["claim_owner"] == "executor-new"
    assert storage.run["attempt_count"] == 6
    assert "result_json" not in storage.run
    assert storage.persisted_result_evidence is None
    assert len(storage.atomic_success_calls) == 1
    assert storage.atomic_success_calls[0]["artifacts"]
    assert storage.atomic_success_calls[0]["usage"]["prompt_tokens"] == 3
    assert not any(transition["state"] == "SUCCEEDED" for transition in storage.transitions)
    await adapter.close()


@pytest.mark.asyncio
async def test_controller_does_not_leave_evidence_when_claim_is_lost_at_atomic_failure() -> None:
    run_id = uuid4()
    worker_id = uuid4()
    owner = "executor-old"
    storage = _ClaimedExecutionStorage(
        run_id,
        owner=owner,
        attempt_count=5,
        reclaim_on_failure=True,
    )
    artifact = WorkerArtifact(
        kind=ArtifactKind.FILE,
        name="partial.txt",
        uri="partial.txt",
        sha256="d" * 64,
        size_bytes=7,
    )

    result = WorkerResult(
        run_id=run_id,
        worker_id="fenced-worker",
        success=False,
        artifacts=[artifact],
        usage={"prompt_tokens": 3},
        error={"code": "WORKER_FAILED", "message": "stale failure"},
    )
    adapter = _NormalizedFailureAdapter(result)
    request = WorkerRunRequest(
        run_id=run_id,
        idempotency_key="claim-lost-at-atomic-failure",
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
    assert "error_json" not in storage.run
    assert storage.persisted_result_evidence is None
    assert len(storage.atomic_failure_calls) == 1
    assert storage.atomic_failure_calls[0]["artifacts"]
    assert storage.atomic_failure_calls[0]["usage"]["prompt_tokens"] == 3
    assert not storage.atomic_success_calls
    assert not any(
        transition["state"] == "FAILED" and transition.get("attempt_count") == 6
        for transition in storage.transitions
    )
    await adapter.close()


@pytest.mark.asyncio
async def test_claimed_success_fails_closed_without_atomic_storage_capability() -> None:
    run_id = uuid4()
    worker_id = uuid4()
    request = WorkerRunRequest(
        run_id=run_id,
        idempotency_key="atomic-success-capability-required",
        worker_id=str(worker_id),
        task_type="test",
    )
    request._execution_claim_fence = {
        "claim_owner": "executor-current",
        "attempt_count": 3,
    }
    legacy_writer = AsyncMock(return_value={"artifacts": [], "usage": {}})
    controller = WorkerRunController(
        storage=SimpleNamespace(
            persist_worker_result_evidence_claim_fenced=legacy_writer,
        )
    )

    with pytest.raises(WorkerRunError) as caught:
        await controller._persist_result_evidence(
            request,
            WorkerResult(
                run_id=run_id,
                worker_id=str(worker_id),
                success=True,
            ),
            terminal_success={"result": {"success": True}},
        )

    assert caught.value.code == "ATOMIC_SUCCESS_PERSISTENCE_UNAVAILABLE"
    legacy_writer.assert_not_awaited()


@pytest.mark.asyncio
async def test_controller_preserves_artifacts_when_terminal_commit_outcome_is_ambiguous() -> None:
    run_id = uuid4()
    request = WorkerRunRequest(
        run_id=run_id,
        idempotency_key="ambiguous-terminal-commit",
        worker_id="fenced-worker",
        task_type="test",
    )
    request._execution_claim_fence = {
        "claim_owner": "executor-current",
        "attempt_count": 4,
    }
    writer = AsyncMock(side_effect=RuntimeError("commit outcome unavailable"))
    controller = WorkerRunController(
        storage=SimpleNamespace(complete_worker_run_success_claim_fenced=writer)
    )
    cleanup = AsyncMock()
    controller._cleanup_unpersisted_artifacts = cleanup
    result = WorkerResult(
        run_id=run_id,
        worker_id="fenced-worker",
        success=True,
        artifacts=[
            WorkerArtifact(
                kind=ArtifactKind.FILE,
                name="result.txt",
                uri="result.txt",
                sha256="e" * 64,
                size_bytes=4,
            )
        ],
        usage={"prompt_tokens": 2},
    )

    with pytest.raises(RuntimeError, match="commit outcome unavailable"):
        await controller._persist_result_evidence(
            request,
            result,
            terminal_success={"result": result.model_dump(mode="json")},
        )

    writer.assert_awaited_once()
    cleanup.assert_not_awaited()
