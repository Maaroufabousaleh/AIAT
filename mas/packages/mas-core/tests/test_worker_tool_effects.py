"""Durable mediated-tool effect replay and ambiguity tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

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


class _EffectStore:
    """Small durable-store double shared by controller instances."""

    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, str], dict] = {}

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

    async def complete_worker_tool_effect(self, effect_id, *, state, response_json):
        for row in self.rows.values():
            if row["id"] == effect_id:
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
