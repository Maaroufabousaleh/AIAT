"""Durable mediated-tool effect replay and ambiguity tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql

from mas_core.memory.storage import AgentStorage
from mas_core.worker_contract import (
    ArtifactKind,
    EventType,
    WorkerArtifact,
    WorkerEvent,
    WorkerResult,
    WorkerRunController,
    WorkerRunRequest,
    WorkerToolRequest,
    WorkerToolResponse,
)
from mas_core.worker_contract.controller import WorkerRunError


def _mock_storage_with_run(run_row: dict[str, Any], effect_row: dict[str, Any] | None = None):
    worker_result = MagicMock()
    worker_result.mappings.return_value.first.return_value = run_row
    effect_result = MagicMock()
    effect_result.mappings.return_value.first.return_value = effect_row
    connection = MagicMock()
    connection.execute = AsyncMock(side_effect=[worker_result, effect_result])
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction
    storage = AgentStorage.__new__(AgentStorage)
    storage._engine = engine
    return storage, engine, connection


@pytest.mark.asyncio
async def test_effect_evidence_read_is_bounded_and_omits_payload_columns() -> None:
    run_id = uuid4()
    expected = {
        "id": uuid4(),
        "run_id": run_id,
        "request_id": uuid4(),
        "tool_name": "opencode.workspace_write",
        "request_sha256": "a" * 64,
        "state": "COMPLETED",
        "response_success": True,
        "created_at": None,
        "updated_at": None,
        "completed_at": None,
    }
    result = MagicMock()
    result.mappings.return_value.all.return_value = [expected]
    connection = MagicMock()
    connection.execute = AsyncMock(return_value=result)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=connection)
    context.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.connect.return_value = context
    storage = AgentStorage.__new__(AgentStorage)
    storage._engine = engine

    rows = await storage.list_worker_tool_effects(run_id, limit=20)

    assert rows == [expected]
    query = connection.execute.await_args.args[0]
    sql = str(query.compile(dialect=postgresql.dialect()))
    selected_column_names = [column.key for column in query.selected_columns]
    assert selected_column_names == [
        "id",
        "run_id",
        "request_id",
        "tool_name",
        "request_sha256",
        "state",
        "response_success",
        "created_at",
        "updated_at",
        "completed_at",
    ]
    assert "response_json ->>" in sql
    assert "worker_tool_effects.idempotency_key" not in sql
    assert "LIMIT" in sql

    with pytest.raises(ValueError, match="between 1 and 1000"):
        await storage.list_worker_tool_effects(run_id, limit=0)
    with pytest.raises(ValueError, match="between 1 and 1000"):
        await storage.list_worker_tool_effects(run_id, limit=True)  # type: ignore[arg-type]
    assert connection.execute.await_count == 1


@pytest.mark.asyncio
async def test_storage_blocks_new_key_while_run_has_unresolved_effect() -> None:
    run_id = uuid4()
    pending_effect = {
        "id": uuid4(),
        "run_id": run_id,
        "request_id": uuid4(),
        "idempotency_key": "previous-session-request",
        "tool_name": "repository.write",
        "request_sha256": "b" * 64,
        "state": "IN_FLIGHT",
        "response_json": None,
    }
    storage, _engine, connection = _mock_storage_with_run(
        {
            "id": run_id,
            "state": "RUNNING",
            "claim_owner": "executor-a",
            "attempt_count": 3,
            "lease_expires_at": datetime.now(tz=UTC) + timedelta(minutes=2),
            "cancel_requested_at": None,
        },
        pending_effect,
    )

    effect = await storage.begin_worker_tool_effect(
        run_id=run_id,
        request_id=uuid4(),
        idempotency_key="new-session-retry",
        tool_name="repository.write",
        request_sha256="a" * 64,
        claim_owner="executor-a",
        attempt_count=3,
    )

    assert effect == {"disposition": "AMBIGUOUS"}
    assert connection.execute.await_count == 2
    claim_query = connection.execute.await_args_list[0].args[0]
    assert "FOR UPDATE" in str(claim_query.compile(dialect=postgresql.dialect()))
    unresolved_query = connection.execute.await_args_list[1].args[0]
    unresolved_sql = str(unresolved_query.compile(dialect=postgresql.dialect()))
    assert "worker_tool_effects.run_id" in unresolved_sql
    assert "worker_tool_effects.state IN" in unresolved_sql
    assert "worker_tool_effects.response_json" not in unresolved_sql
    assert "FOR UPDATE" in unresolved_sql


@pytest.mark.asyncio
async def test_storage_completion_locks_current_run_claim_before_settlement() -> None:
    run_id = uuid4()
    effect_id = uuid4()
    owner = "executor-a"
    attempt = 4
    effect_row = {
        "id": effect_id,
        "run_id": run_id,
        "idempotency_key": "request-4",
        "tool_name": "repository.read",
        "request_sha256": "a" * 64,
        "state": "COMPLETED",
        "response_json": {"success": True},
    }
    storage, _engine, connection = _mock_storage_with_run(
        {
            "id": run_id,
            "state": "RUNNING",
            "claim_owner": owner,
            "attempt_count": attempt,
            "lease_expires_at": datetime.now(tz=UTC) + timedelta(minutes=2),
            "cancel_requested_at": None,
        },
        effect_row,
    )

    settled = await storage.complete_worker_tool_effect(
        effect_id,
        run_id=run_id,
        claim_owner=owner,
        attempt_count=attempt,
        state="COMPLETED",
        response_json={"success": True},
    )

    assert settled == effect_row
    assert connection.execute.await_count == 2
    claim_query = connection.execute.await_args_list[0].args[0]
    claim_sql = str(claim_query.compile(dialect=postgresql.dialect()))
    assert "FROM worker_runs" in claim_sql
    assert "FOR UPDATE" in claim_sql
    completion_query = connection.execute.await_args_list[1].args[0]
    completion_sql = str(completion_query.compile(dialect=postgresql.dialect()))
    assert "UPDATE worker_tool_effects" in completion_sql
    assert "worker_tool_effects.run_id" in completion_sql
    assert "worker_tool_effects.state" in completion_sql
    assert "IN_FLIGHT" in completion_query.compile(dialect=postgresql.dialect()).params.values()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("current_owner", "current_attempt", "lease_expires_at", "cancel_requested_at"),
    [
        (
            "executor-new",
            5,
            datetime.now(tz=UTC) + timedelta(minutes=2),
            None,
        ),
        (
            "executor-a",
            4,
            datetime.now(tz=UTC) - timedelta(seconds=1),
            None,
        ),
        (
            "executor-a",
            4,
            datetime.now(tz=UTC) + timedelta(minutes=2),
            datetime.now(tz=UTC),
        ),
    ],
    ids=["reassigned-claim", "expired-lease", "cancellation-requested"],
)
async def test_storage_completion_rejects_noncurrent_claim_without_effect_update(
    current_owner: str,
    current_attempt: int,
    lease_expires_at: datetime,
    cancel_requested_at: datetime | None,
) -> None:
    run_id = uuid4()
    effect_id = uuid4()
    storage, _engine, connection = _mock_storage_with_run(
        {
            "id": run_id,
            "state": "RUNNING",
            "claim_owner": current_owner,
            "attempt_count": current_attempt,
            "lease_expires_at": lease_expires_at,
            "cancel_requested_at": cancel_requested_at,
        }
    )

    settled = await storage.complete_worker_tool_effect(
        effect_id,
        run_id=run_id,
        claim_owner="executor-a",
        attempt_count=4,
        state="COMPLETED",
        response_json={"success": True},
    )

    assert settled is None
    assert connection.execute.await_count == 1
    claim_query = connection.execute.await_args.args[0]
    assert "FOR UPDATE" in str(claim_query.compile(dialect=postgresql.dialect()))


@pytest.mark.asyncio
async def test_storage_completion_rejects_missing_claim_without_database_access() -> None:
    run_id = uuid4()
    storage, engine, connection = _mock_storage_with_run({"id": run_id})

    settled = await storage.complete_worker_tool_effect(
        uuid4(),
        run_id=run_id,
        claim_owner=None,
        attempt_count=0,
        state="COMPLETED",
        response_json={"success": True},
    )

    assert settled is None
    engine.begin.assert_not_called()
    connection.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_storage_ambiguous_completion_cannot_persist_success() -> None:
    run_id = uuid4()
    storage, engine, connection = _mock_storage_with_run({"id": run_id})

    settled = await storage.complete_worker_tool_effect(
        uuid4(),
        run_id=run_id,
        claim_owner=None,
        attempt_count=0,
        state="AMBIGUOUS",
        response_json={"success": True},
    )

    assert settled is None
    engine.begin.assert_not_called()
    connection.execute.assert_not_awaited()


class _EffectStore:
    """Small durable-store double shared by controller instances."""

    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, str], dict] = {}
        self.claim_owner = "executor-test"
        self.attempt_count = 1

    async def begin_worker_tool_effect(self, **values):
        key = (values["run_id"], values["idempotency_key"])
        row = self.rows.get(key)
        if row is None:
            row = {
                "id": uuid4(),
                **values,
                "state": "IN_FLIGHT",
                "response_json": None,
            }
            self.rows[key] = row
            disposition = "NEW"
        elif row["request_sha256"] != values["request_sha256"]:
            disposition = "CONFLICT"
        elif row["state"] == "COMPLETED":
            disposition = "REPLAY"
        else:
            disposition = "AMBIGUOUS"
        return {**row, "disposition": disposition}

    async def complete_worker_tool_effect(
        self,
        effect_id,
        *,
        run_id,
        claim_owner,
        attempt_count,
        state,
        response_json,
    ):
        if state == "COMPLETED" and (
            claim_owner != self.claim_owner or attempt_count != self.attempt_count
        ):
            return None
        for row in self.rows.values():
            if row["id"] == effect_id and row["run_id"] == run_id:
                row["state"] = state
                row["response_json"] = response_json
                return row
        raise AssertionError("effect row was not found")


class _DeliveryAdapter:
    def __init__(self, dispatcher) -> None:
        self.context = SimpleNamespace(tool_dispatcher=dispatcher)
        self.responses: list[WorkerToolResponse] = []

    async def deliver_tool_response(self, response: WorkerToolResponse) -> None:
        self.responses.append(response)


def _request(*, run_id: UUID, key: str, tool_name: str = "mail.send") -> WorkerToolRequest:
    return WorkerToolRequest(
        request_id=uuid4(),
        run_id=run_id,
        tool_name=tool_name,
        arguments={"to": "operator@example.test", "body": "hello"},
        idempotency_key=key,
    )


@pytest.mark.asyncio
async def test_unconfirmed_durable_effect_completion_is_not_delivered_to_worker() -> None:
    class UnsettledEffectStore(_EffectStore):
        async def complete_worker_tool_effect(self, *_args, **_kwargs):
            return None

    store = UnsettledEffectStore()
    run_id = uuid4()
    request = WorkerRunRequest(
        run_id=run_id,
        idempotency_key="effect-completion-not-confirmed",
        worker_id="worker-1",
        task_type="tool-effect",
        tool_grants=["mail.send"],
    )
    dispatched: list[str] = []

    async def dispatcher(tool_request: WorkerToolRequest) -> WorkerToolResponse:
        dispatched.append(tool_request.idempotency_key)
        return WorkerToolResponse(
            request_id=tool_request.request_id,
            run_id=tool_request.run_id,
            tool_name=tool_request.tool_name,
            success=True,
            result={"accepted": True},
        )

    tool_request = _request(run_id=run_id, key="effect-completion-not-confirmed")
    adapter = _DeliveryAdapter(dispatcher)
    controller = WorkerRunController(storage=store)

    with pytest.raises(WorkerRunError) as caught:
        await controller._mediate_tool_request(
            request,
            adapter,  # type: ignore[arg-type]
            WorkerEvent(
                run_id=run_id,
                worker_id=request.worker_id,
                event_type=EventType.TOOL_REQUEST,
                tool_request=tool_request,
            ),
        )

    assert caught.value.code == "TOOL_EFFECT_COMPLETION_UNAVAILABLE"
    assert dispatched == [tool_request.idempotency_key]
    assert adapter.responses == []
    effect = store.rows[(run_id, tool_request.idempotency_key)]
    assert effect["state"] == "IN_FLIGHT"


@pytest.mark.asyncio
async def test_unknown_durable_effect_disposition_blocks_dispatch() -> None:
    class UnknownDispositionStore(_EffectStore):
        async def begin_worker_tool_effect(self, **values):
            effect = await super().begin_worker_tool_effect(**values)
            effect["disposition"] = "UNRECOGNIZED"
            return effect

    store = UnknownDispositionStore()
    run_id = uuid4()
    request = WorkerRunRequest(
        run_id=run_id,
        idempotency_key="unknown-effect-disposition",
        worker_id="worker-1",
        task_type="tool-effect",
        tool_grants=["mail.send"],
    )
    tool_request = _request(run_id=run_id, key="unknown-effect-disposition")
    dispatcher = AsyncMock()
    adapter = _DeliveryAdapter(dispatcher)

    with pytest.raises(WorkerRunError) as caught:
        await WorkerRunController(storage=store)._mediate_tool_request(
            request,
            adapter,  # type: ignore[arg-type]
            WorkerEvent(
                run_id=run_id,
                worker_id=request.worker_id,
                event_type=EventType.TOOL_REQUEST,
                tool_request=tool_request,
            ),
        )

    assert caught.value.code == "TOOL_EFFECT_LEDGER_UNAVAILABLE"
    dispatcher.assert_not_awaited()
    assert adapter.responses == []
    effect = store.rows[(run_id, tool_request.idempotency_key)]
    assert effect["state"] == "IN_FLIGHT"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("identity_field", "mismatched_value"),
    [
        ("request_id", uuid4()),
        ("run_id", uuid4()),
        ("tool_name", "other.tool"),
    ],
    ids=["request-id", "run-id", "tool-name"],
)
async def test_mismatched_dispatch_response_is_ambiguous_and_not_replayed_as_success(
    identity_field: str,
    mismatched_value: UUID | str,
) -> None:
    store = _EffectStore()
    run_id = uuid4()
    request = WorkerRunRequest(
        run_id=run_id,
        idempotency_key="mismatched-dispatch-response",
        worker_id="worker-1",
        task_type="tool-effect",
        tool_grants=["mail.send"],
    )
    tool_request = _request(run_id=run_id, key="mismatched-dispatch-response")
    valid_response = WorkerToolResponse(
        request_id=tool_request.request_id,
        run_id=run_id,
        tool_name=tool_request.tool_name,
        success=True,
        result={"accepted": True},
    )
    mismatched_response = valid_response.model_copy(
        update={identity_field: mismatched_value}
    )
    dispatcher = AsyncMock(return_value=mismatched_response)
    adapter = _DeliveryAdapter(dispatcher)

    await WorkerRunController(storage=store)._mediate_tool_request(
        request,
        adapter,  # type: ignore[arg-type]
        WorkerEvent(
            run_id=run_id,
            worker_id=request.worker_id,
            event_type=EventType.TOOL_REQUEST,
            tool_request=tool_request,
        ),
    )

    dispatcher.assert_awaited_once_with(tool_request)
    assert len(adapter.responses) == 1
    delivered = adapter.responses[0]
    assert delivered.request_id == tool_request.request_id
    assert delivered.run_id == run_id
    assert delivered.tool_name == tool_request.tool_name
    assert delivered.success is False
    assert delivered.error is not None
    assert delivered.error.code == "TOOL_EFFECT_AMBIGUOUS"
    effect = store.rows[(run_id, tool_request.idempotency_key)]
    assert effect["state"] == "AMBIGUOUS"
    assert effect["response_json"] == delivered.model_dump(mode="json")


@pytest.mark.asyncio
async def test_missing_durable_effect_completion_rejects_before_dispatch() -> None:
    store = _EffectStore()
    store.complete_worker_tool_effect = None  # type: ignore[method-assign]
    run_id = uuid4()
    request = WorkerRunRequest(
        run_id=run_id,
        idempotency_key="effect-completion-method-missing",
        worker_id="worker-1",
        task_type="tool-effect",
        tool_grants=["mail.send"],
    )
    dispatcher = AsyncMock()
    tool_request = _request(run_id=run_id, key="effect-completion-method-missing")
    adapter = _DeliveryAdapter(dispatcher)
    controller = WorkerRunController(storage=store)

    with pytest.raises(WorkerRunError) as caught:
        await controller._mediate_tool_request(
            request,
            adapter,  # type: ignore[arg-type]
            WorkerEvent(
                run_id=run_id,
                worker_id=request.worker_id,
                event_type=EventType.TOOL_REQUEST,
                tool_request=tool_request,
            ),
        )

    assert caught.value.code == "TOOL_EFFECT_LEDGER_UNAVAILABLE"
    dispatcher.assert_not_awaited()
    assert store.rows == {}
    assert adapter.responses == []


def test_worker_tool_effect_schema_and_migration_are_present() -> None:
    mas_root = Path(__file__).resolve().parents[3]
    models = (
        mas_root
        / "packages"
        / "mas-core"
        / "mas_core"
        / "memory"
        / "models.py"
    ).read_text(encoding="utf-8")
    migration = (
        mas_root / "migrations" / "versions" / "0045_worker_tool_effects.py"
    ).read_text(encoding="utf-8")

    assert '"worker_tool_effects"' in models
    assert 'revision = "0045_worker_tool_effects"' in migration
    assert 'down_revision = "0044_worker_run_runtime_bindings"' in migration
    for field in (
        "request_id",
        "idempotency_key",
        "request_sha256",
        "state",
        "response_json",
    ):
        assert f'"{field}"' in migration


@pytest.mark.asyncio
async def test_replayed_tool_request_uses_durable_response_without_dispatching_again() -> None:
    store = _EffectStore()
    run_id = uuid4()
    request = WorkerRunRequest(
        run_id=run_id,
        idempotency_key="tool-effect-run",
        worker_id="worker-1",
        task_type="tool-effect",
        tool_grants=["mail.send"],
    )
    request._execution_claim_fence = {
        "claim_owner": "executor-test",
        "attempt_count": 1,
    }
    calls: list[str] = []

    async def dispatcher(tool_request: WorkerToolRequest) -> WorkerToolResponse:
        calls.append(tool_request.idempotency_key)
        return WorkerToolResponse(
            request_id=tool_request.request_id,
            run_id=tool_request.run_id,
            tool_name=tool_request.tool_name,
            success=True,
            result={"provider_id": "mail-1"},
        )

    first_adapter = _DeliveryAdapter(dispatcher)
    first_request = _request(run_id=run_id, key="mail-effect-1")
    first_controller = WorkerRunController(storage=store)
    await first_controller._mediate_tool_request(
        request,
        first_adapter,  # type: ignore[arg-type]
        WorkerEvent(
            run_id=run_id,
            worker_id=request.worker_id,
            event_type=EventType.TOOL_REQUEST,
            tool_request=first_request,
        ),
    )

    second_adapter = _DeliveryAdapter(dispatcher)
    second_request = _request(run_id=run_id, key="mail-effect-1")
    second_controller = WorkerRunController(storage=store)
    await second_controller._mediate_tool_request(
        request,
        second_adapter,  # type: ignore[arg-type]
        WorkerEvent(
            run_id=run_id,
            worker_id=request.worker_id,
            event_type=EventType.TOOL_REQUEST,
            tool_request=second_request,
        ),
    )

    assert calls == ["mail-effect-1"]
    assert first_adapter.responses[0].result == {"provider_id": "mail-1"}
    assert second_adapter.responses[0].request_id == second_request.request_id
    assert second_adapter.responses[0].result == {"provider_id": "mail-1"}


@pytest.mark.asyncio
async def test_in_flight_tool_effect_fails_closed_after_controller_restart() -> None:
    store = _EffectStore()
    run_id = uuid4()
    request = WorkerRunRequest(
        run_id=run_id,
        idempotency_key="ambiguous-tool-run",
        worker_id="worker-1",
        task_type="tool-effect",
        tool_grants=["mail.send"],
    )
    tool_request = _request(run_id=run_id, key="ambiguous-effect")
    first = WorkerRunController(storage=store)
    # Reserve the effect, then model a controller crash before the dispatcher
    # ran or a response was durable.  A fresh controller must not re-execute it.
    await first._begin_tool_effect(tool_request)

    calls: list[str] = []

    async def dispatcher(_tool_request: WorkerToolRequest) -> WorkerToolResponse:
        calls.append("executed")
        raise AssertionError("ambiguous effects must not be executed again")

    adapter = _DeliveryAdapter(dispatcher)
    await WorkerRunController(storage=store)._mediate_tool_request(
        request,
        adapter,  # type: ignore[arg-type]
        WorkerEvent(
            run_id=run_id,
            worker_id=request.worker_id,
            event_type=EventType.TOOL_REQUEST,
            tool_request=tool_request,
        ),
    )

    assert calls == []
    assert adapter.responses[0].success is False
    assert adapter.responses[0].error is not None
    assert adapter.responses[0].error.code == "TOOL_EFFECT_AMBIGUOUS"


@pytest.mark.asyncio
async def test_ambiguous_effect_payload_cannot_replay_success() -> None:
    store = _EffectStore()
    run_id = uuid4()
    request = WorkerRunRequest(
        run_id=run_id,
        idempotency_key="ambiguous-success-payload",
        worker_id="worker-1",
        task_type="tool-effect",
        tool_grants=["mail.send"],
    )
    tool_request = _request(run_id=run_id, key="ambiguous-success-payload")
    success_payload = WorkerToolResponse(
        request_id=tool_request.request_id,
        run_id=run_id,
        tool_name=tool_request.tool_name,
        success=True,
        result={"accepted": True},
    ).model_dump(mode="json")
    store.rows[(run_id, tool_request.idempotency_key)] = {
        "id": uuid4(),
        "run_id": run_id,
        "request_id": tool_request.request_id,
        "idempotency_key": tool_request.idempotency_key,
        "tool_name": tool_request.tool_name,
        "request_sha256": WorkerRunController._tool_request_sha256(tool_request),
        "state": "AMBIGUOUS",
        "response_json": success_payload,
    }
    adapter = _DeliveryAdapter(AsyncMock())

    await WorkerRunController(storage=store)._mediate_tool_request(
        request,
        adapter,  # type: ignore[arg-type]
        WorkerEvent(
            run_id=run_id,
            worker_id=request.worker_id,
            event_type=EventType.TOOL_REQUEST,
            tool_request=tool_request,
        ),
    )

    assert len(adapter.responses) == 1
    assert adapter.responses[0].success is False
    assert adapter.responses[0].error is not None
    assert adapter.responses[0].error.code == "TOOL_EFFECT_AMBIGUOUS"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("id", "not-a-uuid"),
        ("run_id", uuid4()),
        ("idempotency_key", "another-request"),
        ("tool_name", "another.tool"),
        ("request_sha256", "0" * 64),
    ],
    ids=["id", "run-id", "idempotency-key", "tool-name", "request-hash"],
)
async def test_replay_requires_a_matching_durable_effect_identity(
    field: str,
    wrong_value: UUID | str,
) -> None:
    class CorruptReplayStore(_EffectStore):
        async def begin_worker_tool_effect(self, **values):
            effect = await super().begin_worker_tool_effect(**values)
            if effect.get("disposition") == "REPLAY":
                effect[field] = wrong_value
            return effect

    store = CorruptReplayStore()
    run_id = uuid4()
    tool_request = _request(run_id=run_id, key="corrupt-completed-effect")
    request = WorkerRunRequest(
        run_id=run_id,
        idempotency_key="corrupt-completed-effect",
        worker_id="worker-1",
        task_type="tool-effect",
        tool_grants=[tool_request.tool_name],
    )
    stored_response = WorkerToolResponse(
        request_id=tool_request.request_id,
        run_id=run_id,
        tool_name=tool_request.tool_name,
        success=True,
        result={"accepted": True},
    )
    store.rows[(run_id, tool_request.idempotency_key)] = {
        "id": uuid4(),
        "run_id": run_id,
        "request_id": tool_request.request_id,
        "idempotency_key": tool_request.idempotency_key,
        "tool_name": tool_request.tool_name,
        "request_sha256": WorkerRunController._tool_request_sha256(tool_request),
        "state": "COMPLETED",
        "response_json": stored_response.model_dump(mode="json"),
    }
    dispatcher = AsyncMock()
    adapter = _DeliveryAdapter(dispatcher)

    with pytest.raises(WorkerRunError) as caught:
        await WorkerRunController(storage=store)._mediate_tool_request(
            request,
            adapter,  # type: ignore[arg-type]
            WorkerEvent(
                run_id=run_id,
                worker_id=request.worker_id,
                event_type=EventType.TOOL_REQUEST,
                tool_request=tool_request,
            ),
        )

    assert caught.value.code == "TOOL_EFFECT_LEDGER_UNAVAILABLE"
    dispatcher.assert_not_awaited()
    assert adapter.responses == []


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["run_id", "tool_name"], ids=["run-id", "tool-name"])
async def test_replay_rejects_completed_response_for_another_run_or_tool(field: str) -> None:
    store = _EffectStore()
    run_id = uuid4()
    tool_request = _request(run_id=run_id, key="corrupt-completed-response")
    request = WorkerRunRequest(
        run_id=run_id,
        idempotency_key="corrupt-completed-response",
        worker_id="worker-1",
        task_type="tool-effect",
        tool_grants=[tool_request.tool_name],
    )
    stored_response = WorkerToolResponse(
        request_id=tool_request.request_id,
        run_id=run_id,
        tool_name=tool_request.tool_name,
        success=True,
        result={"accepted": True},
    )
    stored_response = stored_response.model_copy(
        update={field: uuid4() if field == "run_id" else "another.tool"}
    )
    store.rows[(run_id, tool_request.idempotency_key)] = {
        "id": uuid4(),
        "run_id": run_id,
        "request_id": tool_request.request_id,
        "idempotency_key": tool_request.idempotency_key,
        "tool_name": tool_request.tool_name,
        "request_sha256": WorkerRunController._tool_request_sha256(tool_request),
        "state": "COMPLETED",
        "response_json": stored_response.model_dump(mode="json"),
    }
    dispatcher = AsyncMock()
    adapter = _DeliveryAdapter(dispatcher)

    await WorkerRunController(storage=store)._mediate_tool_request(
        request,
        adapter,  # type: ignore[arg-type]
        WorkerEvent(
            run_id=run_id,
            worker_id=request.worker_id,
            event_type=EventType.TOOL_REQUEST,
            tool_request=tool_request,
        ),
    )

    dispatcher.assert_not_awaited()
    assert len(adapter.responses) == 1
    assert adapter.responses[0].success is False
    assert adapter.responses[0].error is not None
    assert adapter.responses[0].error.code == "TOOL_EFFECT_AMBIGUOUS"


@pytest.mark.asyncio
async def test_tool_idempotency_key_conflict_does_not_dispatch() -> None:
    store = _EffectStore()
    run_id = uuid4()
    request = WorkerRunRequest(
        run_id=run_id,
        idempotency_key="conflict-tool-run",
        worker_id="worker-1",
        task_type="tool-effect",
        tool_grants=["mail.send"],
    )
    first_tool_request = _request(run_id=run_id, key="conflict-effect")
    first = WorkerRunController(storage=store)
    await first._begin_tool_effect(first_tool_request)

    conflicting = _request(run_id=run_id, key="conflict-effect")
    conflicting.arguments["body"] = "different"
    calls: list[str] = []

    async def dispatcher(_tool_request: WorkerToolRequest) -> WorkerToolResponse:
        calls.append("executed")
        raise AssertionError("conflicting idempotency keys must not dispatch")

    adapter = _DeliveryAdapter(dispatcher)
    await WorkerRunController(storage=store)._mediate_tool_request(
        request,
        adapter,  # type: ignore[arg-type]
        WorkerEvent(
            run_id=run_id,
            worker_id=request.worker_id,
            event_type=EventType.TOOL_REQUEST,
            tool_request=conflicting,
        ),
    )

    assert calls == []
    assert adapter.responses[0].error is not None
    assert adapter.responses[0].error.code == "TOOL_IDEMPOTENCY_CONFLICT"


@pytest.mark.asyncio
async def test_reclaimed_attempt_is_rejected_before_mediated_tool_dispatch() -> None:
    class ReclaimedStore(_EffectStore):
        async def begin_worker_tool_effect(self, **values):
            if (
                values.get("claim_owner") != "executor-new"
                or values.get("attempt_count") != 2
            ):
                return {"disposition": "CLAIM_LOST"}
            return await super().begin_worker_tool_effect(**values)

    store = ReclaimedStore()
    run_id = uuid4()
    request = WorkerRunRequest(
        run_id=run_id,
        idempotency_key="stale-attempt-run",
        worker_id="worker-1",
        task_type="tool-effect",
        tool_grants=["mail.send"],
    )
    tool_request = _request(run_id=run_id, key="stale-attempt-effect")
    calls: list[str] = []

    async def dispatcher(_tool_request: WorkerToolRequest) -> WorkerToolResponse:
        calls.append("dispatched")
        raise AssertionError("a reclaimed worker attempt must not dispatch")

    adapter = _DeliveryAdapter(dispatcher)
    with pytest.raises(WorkerRunError, match="claim is no longer current") as caught:
        await WorkerRunController(storage=store)._mediate_tool_request(
            request,
            adapter,  # type: ignore[arg-type]
            WorkerEvent(
                run_id=run_id,
                worker_id=request.worker_id,
                event_type=EventType.TOOL_REQUEST,
                tool_request=tool_request,
            ),
            claim_fence={"claim_owner": "executor-old", "attempt_count": 1},
        )

    assert caught.value.code == "WORKER_RUN_CLAIM_LOST"
    assert calls == []
    assert store.rows == {}


@pytest.mark.asyncio
async def test_cross_run_tool_request_is_rejected_before_effect_reservation() -> None:
    store = _EffectStore()
    controller_run_id = uuid4()
    foreign_run_id = uuid4()
    request = WorkerRunRequest(
        run_id=controller_run_id,
        idempotency_key="cross-run-tool-run",
        worker_id="worker-1",
        task_type="tool-effect",
        tool_grants=["mail.send"],
    )
    tool_request = _request(run_id=foreign_run_id, key="foreign-effect")
    adapter = _DeliveryAdapter(AsyncMock())
    controller = WorkerRunController(storage=store)

    with pytest.raises(WorkerRunError) as caught:
        await controller._mediate_tool_request(
            request,
            adapter,  # type: ignore[arg-type]
            WorkerEvent(
                run_id=controller_run_id,
                worker_id=request.worker_id,
                event_type=EventType.TOOL_REQUEST,
                tool_request=tool_request,
            ),
        )

    assert caught.value.code == "TOOL_REQUEST_SCOPE_MISMATCH"
    assert store.rows == {}
    assert adapter.responses == []


@pytest.mark.asyncio
async def test_claim_loss_before_atomic_result_evidence_cleans_attempt_artifact() -> None:
    run_id = uuid4()
    request = WorkerRunRequest(
        run_id=run_id,
        idempotency_key="result-evidence-claim-loss",
        worker_id="worker-1",
        task_type="evidence",
    )
    request._execution_claim_fence = {
        "claim_owner": "executor-old",
        "attempt_count": 4,
    }
    digest = "a" * 64
    artifact = WorkerArtifact(
        kind=ArtifactKind.FILE,
        name="out.txt",
        uri="out.txt",
        sha256=digest,
        size_bytes=3,
        metadata={
            "worker_run_id": str(run_id),
            "worker_run_attempt": 4,
            "blob_bucket": "worker-artifacts",
            "blob_key": f"project/worker-runs/{run_id}/attempt-4/out.txt",
            "blob_sha256": digest,
            "blob_size_bytes": 3,
            "blob_content_type": "text/plain",
        },
    )
    result = WorkerResult(
        run_id=run_id,
        worker_id="worker-1",
        success=True,
        artifacts=[artifact],
    )
    atomic_writer = AsyncMock(return_value=None)
    registrar = AsyncMock()
    storage = SimpleNamespace(
        persist_worker_result_evidence_claim_fenced=atomic_writer,
    )
    adapter = SimpleNamespace(context=SimpleNamespace(artifact_registrar=registrar))

    with pytest.raises(WorkerRunError) as caught:
        await WorkerRunController(storage=storage)._persist_result_evidence(
            request,
            result,
            adapter=adapter,  # type: ignore[arg-type]
        )

    assert caught.value.code == "WORKER_RUN_CLAIM_LOST"
    atomic_writer.assert_awaited_once()
    assert atomic_writer.await_args.kwargs["claim_owner"] == "executor-old"
    assert atomic_writer.await_args.kwargs["attempt_count"] == 4
    registrar.assert_awaited_once_with(
        artifact,
        claim_fence=request.execution_claim_fence,
        cleanup=True,
    )


@pytest.mark.asyncio
async def test_project_usage_is_idempotent_per_claimed_attempt() -> None:
    run_id = uuid4()
    project_id = uuid4()
    project_usage = AsyncMock(return_value={})
    storage = SimpleNamespace(
        persist_worker_result_evidence_claim_fenced=AsyncMock(
            return_value={"artifacts": [], "usage": {}}
        ),
        record_project_usage=project_usage,
    )
    result = WorkerResult(
        run_id=run_id,
        worker_id="worker-1",
        success=True,
        usage={"prompt_tokens": 10, "completion_tokens": 5, "cost_usd": 0.02},
    )

    for attempt_count in (2, 3):
        request = WorkerRunRequest(
            run_id=run_id,
            idempotency_key=f"usage-attempt-{attempt_count}",
            worker_id="worker-1",
            task_type="usage-accounting",
            project_id=project_id,
        )
        request._execution_claim_fence = {
            "claim_owner": f"executor-{attempt_count}",
            "attempt_count": attempt_count,
        }
        await WorkerRunController(storage=storage)._persist_result_evidence(
            request,
            result,
        )

    keys = [
        call.kwargs["idempotency_key"]
        for call in project_usage.await_args_list
    ]
    assert keys == [
        f"worker-run:{run_id}:attempt:2:usage",
        f"worker-run:{run_id}:attempt:3:usage",
    ]
