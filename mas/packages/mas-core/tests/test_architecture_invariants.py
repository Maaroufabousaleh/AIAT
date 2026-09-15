"""PR A characterization tests for AIAT's current architecture.

These tests intentionally capture behavior at the audited architecture
boundaries.  They also cover the compatible runtime-observation and
cancellation-receipt hooks added after the initial characterization pass;
those hooks do not make an adapter authoritative for canonical state.
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from mas_core.agent_runtime import (
    AgentBase,
    AgentConfig,
    GovernanceModelBinding,
    ModelProvenanceError,
)
from mas_core.agent_runtime.csuite import CSuiteAgent
from mas_core.llm_gateway.models import ChatMessage, ChatResponse
from mas_core.observability.trace_evidence import build_trace_evidence
from mas_core.protocols import MessageEnvelope, MessageType
from mas_core.protocols.enums import AgentRole
from mas_core.protocols.ws import WSMessageFrame
from mas_core.worker_contract.adapters import (
    AdapterContext,
    BaseWorkerAdapter,
    NativeWorkerAdapter,
    WorkerAdapter,
)
from mas_core.worker_contract.controller import (
    WorkerRunController,
    WorkerRunError,
    WorkerRunOutcome,
)
from mas_core.worker_contract.models import (
    EventType,
    ModelProfileReference,
    WorkerCancellation,
    WorkerCancellationReceipt,
    WorkerEvent,
    WorkerResult,
    WorkerRunRequest,
    WorkerRuntimeStatus,
    WorkerToolRequest,
    WorkerUsage,
)
from mas_core.worker_registry.host_executor import (
    HostExecutionRequest,
    WorkerHostExecutor,
)
from mas_core.worker_registry.host_recovery import HostLeaseRecovery
from mas_core.workflow import (
    InvalidTransitionError,
    ProjectState,
    WorkflowController,
    WorkflowEvent,
)

PROJECT_ID = UUID("00000000-0000-4000-a000-000000000201")
WORKER_REGISTRY_ID = UUID("00000000-0000-4000-a000-000000000202")


class _RecordingProjectStorage:
    """Small storage double that records the controller's durable call."""

    def __init__(self, *, result: dict[str, object] | None = None) -> None:
        self.calls: list[tuple[str, UUID, dict[str, object]]] = []
        self.persisted_state: str | None = None
        self.result = result or {"id": PROJECT_ID, "state": "FEASIBILITY_CHECK"}

    async def transition_project(self, project_id: UUID, **kwargs: object) -> dict[str, object]:
        self.calls.append(("persist", project_id, kwargs))
        self.persisted_state = str(kwargs["new_state"])
        return {**self.result, "id": project_id, "state": self.persisted_state}


def _worker_request(*, worker_id: str = "worker-a", task_type: str = "characterize") -> WorkerRunRequest:
    return WorkerRunRequest(
        run_id=uuid4(),
        idempotency_key=f"characterize-{uuid4()}",
        worker_id=worker_id,
        task_type=task_type,
    )


@pytest.mark.asyncio
async def test_workflow_controller_persists_before_publishing_and_survives_publish_failure() -> None:
    """Characterize the current DB-commit -> event-publish consistency gap."""

    storage = _RecordingProjectStorage()
    order: list[str] = []

    async def publisher(*_: object) -> None:
        order.append("publish")
        # The fake durable write has already completed when publication starts.
        assert storage.persisted_state == ProjectState.FEASIBILITY_CHECK
        raise RuntimeError("Redis is unavailable")

    original_transition = storage.transition_project

    async def recording_transition(project_id: UUID, **kwargs: object) -> dict[str, object]:
        order.append("persist")
        return await original_transition(project_id, **kwargs)

    storage.transition_project = recording_transition  # type: ignore[method-assign]
    result = await WorkflowController(storage=storage, event_publisher=publisher).transition(
        project_id=str(PROJECT_ID),
        current_state=ProjectState.INIT,
        event=WorkflowEvent.PROJECT_CREATED,
        actor_id="orchestrator",
    )

    assert result.next_state == ProjectState.FEASIBILITY_CHECK
    assert storage.persisted_state == ProjectState.FEASIBILITY_CHECK
    assert order == ["persist", "publish"]
    # The controller still preserves the historical callback compatibility
    # behavior and does not fail the transition on a publisher exception.  The
    # real AgentStorage path now persists retryable notification intent in the
    # same transaction; this lightweight double deliberately models only the
    # callback boundary.


@pytest.mark.asyncio
async def test_workflow_controller_rejects_invalid_transition_before_storage() -> None:
    storage = MagicMock()
    storage.transition_project = AsyncMock()
    publisher = AsyncMock()

    with pytest.raises(InvalidTransitionError):
        await WorkflowController(storage=storage, event_publisher=publisher).transition(
            project_id=str(PROJECT_ID),
            current_state=ProjectState.INIT,
            event=WorkflowEvent.KPI_SAVED,
            actor_id="orchestrator",
        )

    storage.transition_project.assert_not_awaited()
    publisher.assert_not_awaited()


@pytest.mark.asyncio
async def test_workflow_controller_surfaces_expected_state_cas_rejection() -> None:
    storage = MagicMock()
    storage.transition_project = AsyncMock(return_value=None)
    publisher = AsyncMock()

    with pytest.raises(ValueError, match="state has changed"):
        await WorkflowController(storage=storage, event_publisher=publisher).transition(
            project_id=str(PROJECT_ID),
            current_state=ProjectState.INIT,
            event=WorkflowEvent.PROJECT_CREATED,
            actor_id="orchestrator",
        )

    storage.transition_project.assert_awaited_once()
    assert storage.transition_project.await_args.kwargs["expected_state"] == "INIT"
    publisher.assert_not_awaited()


def test_agent_storage_transition_uses_one_transaction_and_history() -> None:
    """Keep the current persistence contract visible without requiring Postgres."""

    from pathlib import Path

    storage_path = (
        Path(__file__).resolve().parents[1]
        / "mas_core"
        / "memory"
        / "storage.py"
    )
    source = storage_path.read_text(encoding="utf-8")
    transition_source = source[source.index("    async def transition_project("):]

    assert "async with self.engine.begin() as conn:" in transition_source
    assert "expected_state" in transition_source
    assert "t.project_state_history.insert()" in transition_source
    assert "t.project_transition_outbox.insert()" in transition_source


@pytest.mark.asyncio
async def test_specialist_worker_controller_owns_post_claim_execution_lifecycle() -> None:
    transitions: list[str] = []

    async def worker(request: WorkerRunRequest, adapter: NativeWorkerAdapter) -> WorkerResult:
        await adapter.emit_progress(request.run_id, "characterization", percent=50)
        return WorkerResult(
            run_id=request.run_id,
            worker_id=request.worker_id,
            success=True,
            output={"ok": True},
        )

    adapter = NativeWorkerAdapter(worker, worker_id="worker-a")
    controller = WorkerRunController()
    original_transition = controller.transition

    async def recording_transition(run_id: UUID, target: str, **kwargs: object) -> dict[str, object] | None:
        transitions.append(target)
        return await original_transition(run_id, target, **kwargs)

    controller.transition = recording_transition  # type: ignore[method-assign]
    request = _worker_request(worker_id="worker-a")

    outcome = await controller.execute(request, adapter)

    assert outcome.state == "SUCCEEDED"
    assert transitions == ["VALIDATING", "READY", "DISPATCHING", "RUNNING", "SUCCEEDED"]
    assert [event.event_type for event in outcome.events] == [
        EventType.ACCEPTED,
        EventType.PROGRESS,
        EventType.RESULT,
    ]
    assert (await controller.get_run(request.run_id))["state"] == "SUCCEEDED"
    await adapter.close()


@pytest.mark.asyncio
async def test_host_executor_claims_before_controller_and_releases_binding_afterward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []

    binding = {
        "run_id": str(PROJECT_ID),
        "worker_id": str(WORKER_REGISTRY_ID),
        "host_id": "worker-host-a",
        "host_plane": "worker",
        "host_status": "READY",
        "state": "COMMITTED",
        "reservation_state": "COMMITTED",
        "host_lease_generation": 2,
        "current_host_lease_generation": 2,
        "current_host_lease_valid": True,
    }

    class _Bindings:
        async def get(self, _run_id: UUID) -> dict[str, object]:
            order.append("binding")
            return binding

        async def release(self, _run_id: UUID, *, owner: str) -> dict[str, object]:
            assert owner == "host-executor"
            order.append("release")
            return {"state": "RELEASED"}

    class _Storage:
        async def claim_worker_run(self, **kwargs: object) -> dict[str, object]:
            assert kwargs["owner"] == "host-executor"
            order.append("claim")
            return {"state": "CLAIMED", "worker_id": str(WORKER_REGISTRY_ID)}

    class _Controller:
        def __init__(self, *, storage: object) -> None:
            del storage
            pass

        async def execute(self, _request: object, _adapter: object, **_: object) -> WorkerRunOutcome:
            order.append("controller")
            return WorkerRunOutcome(run_id=PROJECT_ID, state="SUCCEEDED")

    monkeypatch.setattr(
        "mas_core.worker_contract.controller.WorkerRunController",
        _Controller,
    )
    executor = WorkerHostExecutor(_Storage(), binding_service=_Bindings())  # type: ignore[arg-type]
    request = WorkerRunRequest(
        run_id=PROJECT_ID,
        idempotency_key="host-claim-characterization",
        worker_id=str(WORKER_REGISTRY_ID),
        task_type="host-claim",
    )

    result = await executor.execute(
        HostExecutionRequest(PROJECT_ID, "worker-host-a", "host-executor"),
        request,
        object(),
        worker_registry_id=WORKER_REGISTRY_ID,
    )

    assert order == ["binding", "claim", "controller", "release"]
    assert result.claimed["state"] == "CLAIMED"
    assert result.binding_after["state"] == "RELEASED"


class _FakeResult:
    def __init__(self, rows: list[dict[str, object]] | None = None, *, rowcount: int = 0) -> None:
        self._rows = rows or []
        self.rowcount = rowcount

    def mappings(self) -> _FakeResult:
        return self

    def all(self) -> list[dict[str, object]]:
        return self._rows


@pytest.mark.asyncio
async def test_host_recovery_fences_expired_host_and_expires_old_reservations() -> None:
    connection = MagicMock()
    connection.execute = AsyncMock(
        side_effect=[
            _FakeResult(
                [
                    {
                        "id": UUID("00000000-0000-4000-a000-000000000203"),
                        "host_id": "worker-host-a",
                        "status": "READY",
                        "lease_generation": 4,
                    }
                ]
            ),
            _FakeResult(rowcount=1),
            _FakeResult(rowcount=2),
        ]
    )
    engine = MagicMock()
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=None)
    engine.begin.return_value = transaction

    report = await HostLeaseRecovery(SimpleNamespace(engine=engine)).reconcile_expired_hosts()

    assert report["status"] == "RECOVERED"
    assert report["recovered_host_count"] == 1
    assert report["expired_reservation_count"] == 2
    assert report["hosts"] == [
        {
            "host_id": "worker-host-a",
            "from_status": "READY",
            "previous_lease_generation": 4,
            "lease_generation": 5,
            "expired_reservation_count": 2,
        }
    ]
    assert report["worker_dispatch_performed"] is False
    assert connection.execute.await_count == 3


def test_worker_adapter_contract_is_subordinate_to_canonical_state() -> None:
    protocol_members = set(WorkerAdapter.__dict__)
    assert "transition_project" not in protocol_members
    assert "transition_worker_run" not in protocol_members
    assert "claim_worker_run" not in protocol_members

    adapter_source = inspect.getsource(BaseWorkerAdapter)
    assert "transition_project" not in adapter_source
    assert "transition_worker_run" not in adapter_source


class _BlockingAdapter(BaseWorkerAdapter):
    runtime_type = "characterization-blocking"

    def __init__(self, started: asyncio.Event) -> None:
        super().__init__(worker_id="restart-worker")
        self.started = started
        self.release = asyncio.Event()

    async def _execute(self, request: WorkerRunRequest) -> WorkerResult:
        self.started.set()
        await self.release.wait()
        return WorkerResult(
            run_id=request.run_id,
            worker_id=request.worker_id,
            success=True,
            output={"completed": True},
        )


@pytest.mark.asyncio
async def test_generic_adapter_restart_loses_process_local_acceptance_and_task_bookkeeping() -> None:
    started = asyncio.Event()
    adapter = _BlockingAdapter(started)
    request = _worker_request(worker_id="restart-worker")

    accepted = await adapter.start(request)
    await asyncio.wait_for(started.wait(), timeout=1)
    assert accepted.run_id == request.run_id
    assert request.run_id in adapter._active_tasks
    assert request.idempotency_key in adapter._accepted_by_key

    restarted_adapter = _BlockingAdapter(asyncio.Event())
    assert request.run_id not in restarted_adapter._active_tasks
    assert request.idempotency_key not in restarted_adapter._accepted_by_key

    running_task = adapter._active_tasks[request.run_id]
    await adapter.cancel(
        WorkerCancellation(
            run_id=request.run_id,
            reason="characterization cleanup",
            requested_by="test",
            force=True,
        )
    )
    await asyncio.wait_for(running_task, timeout=1)
    await adapter.close()
    await restarted_adapter.close()


@pytest.mark.asyncio
async def test_adapter_reconciliation_exposes_local_state_and_fresh_process_is_unknown() -> None:
    started = asyncio.Event()
    adapter = _BlockingAdapter(started)
    request = _worker_request(worker_id="restart-worker")

    await adapter.start(request)
    await asyncio.wait_for(started.wait(), timeout=1)

    observed = await adapter.reconcile(request.run_id)
    assert isinstance(observed, WorkerRuntimeStatus)
    assert observed.status == "RUNNING"
    assert observed.terminal is False

    restarted_adapter = _BlockingAdapter(asyncio.Event())
    restarted_observed = await restarted_adapter.reconcile(request.run_id)
    assert restarted_observed.status == "UNKNOWN"
    assert restarted_observed.terminal is False
    assert restarted_observed.details["reason"] == "run is not known to this adapter process"

    running_task = adapter._active_tasks[request.run_id]
    receipt = await adapter.cancel(
        WorkerCancellation(
            run_id=request.run_id,
            reason="characterization cleanup",
            requested_by="test",
            force=True,
        )
    )
    assert isinstance(receipt, WorkerCancellationReceipt)
    assert receipt.accepted is True
    assert receipt.terminal is True
    assert receipt.runtime_status == "CANCELLED"
    await asyncio.wait_for(running_task, timeout=1)

    terminal = await adapter.reconcile(request.run_id)
    assert terminal.status == "CANCELLED"
    assert terminal.terminal is True
    await adapter.close()
    await restarted_adapter.close()


@pytest.mark.asyncio
async def test_force_cancel_immediately_after_start_emits_terminal_event_and_closes_stream() -> None:
    adapter = _BlockingAdapter(asyncio.Event())
    request = _worker_request(worker_id="immediate-cancel-worker")

    try:
        await adapter.start(request)
        receipt = await adapter.cancel(
            WorkerCancellation(
                run_id=request.run_id,
                reason="cancel before worker gets a scheduling turn",
                requested_by="test",
                force=True,
            )
        )
        events = [event async for event in adapter.events(request.run_id)]
    finally:
        await adapter.close()

    assert receipt.accepted is True
    assert receipt.terminal is True
    assert receipt.runtime_status == "CANCELLED"
    assert events[-1].event_type == EventType.CANCELLED
    assert events[-1].error is not None
    assert events[-1].error.code == "CANCELLED"
    assert events[-1].error.terminal is True


@pytest.mark.asyncio
async def test_controller_reconciliation_is_observational_and_does_not_change_run_state() -> None:
    started = asyncio.Event()
    adapter = _BlockingAdapter(started)
    request = _worker_request(worker_id="restart-worker")
    controller = WorkerRunController()
    await controller.create_run(request)
    await adapter.start(request)
    await asyncio.wait_for(started.wait(), timeout=1)

    observed = await controller.reconcile(request.run_id, adapter)

    assert observed.status == "RUNNING"
    assert (await controller.get_run(request.run_id))["state"] == "CREATED"

    await adapter.cancel(
        WorkerCancellation(
            run_id=request.run_id,
            reason="characterization cleanup",
            requested_by="test",
            force=True,
        )
    )
    await adapter.close()


@pytest.mark.asyncio
async def test_controller_reconciliation_fails_closed_on_adapter_scope_mismatch() -> None:
    controller = WorkerRunController()
    request = _worker_request(worker_id="reconcile-scope-worker")
    await controller.create_run(request)

    class WrongRunAdapter(_BlockingAdapter):
        async def reconcile(self, _run_id, *, runtime_run_id=None):
            return WorkerRuntimeStatus(
                run_id=uuid4(),
                status="RUNNING",
                runtime_run_id=runtime_run_id,
            )

    observed = await controller.reconcile(request.run_id, WrongRunAdapter(asyncio.Event()))

    assert observed.run_id == request.run_id
    assert observed.status == "UNKNOWN"
    assert observed.details["reason"] == "adapter returned a different run ID"
    assert (await controller.get_run(request.run_id))["state"] == "CREATED"


@pytest.mark.asyncio
async def test_controller_does_not_settle_unknown_terminal_cancellation_receipt() -> None:
    controller = WorkerRunController()
    request = _worker_request(worker_id="cancel-unknown-worker")
    await controller.create_run(request)
    for target in ("VALIDATING", "READY", "DISPATCHING", "RUNNING"):
        current = str((await controller.get_run(request.run_id))["state"])
        await controller.transition(request.run_id, target, expected=current)

    class UnknownTerminalCancellationAdapter:
        capabilities = _BlockingAdapter(asyncio.Event()).capabilities

        async def cancel(self, _request):
            return WorkerCancellationReceipt(
                run_id=request.run_id,
                accepted=True,
                terminal=True,
                runtime_status="UNKNOWN",
            )

    row = await controller.cancel(
        request.run_id,
        UnknownTerminalCancellationAdapter(),  # type: ignore[arg-type]
        reason="unknown terminal state",
        requested_by="operator",
    )

    assert row is not None and row["state"] == "RUNNING"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "transition_path",
    [
        ("VALIDATING", "READY", "DISPATCHING", "RUNNING", "PAUSING"),
        ("VALIDATING", "READY", "DISPATCHING", "RUNNING", "PAUSING", "PAUSED"),
        ("VALIDATING", "READY", "DISPATCHING", "RUNNING", "PAUSING", "PAUSED", "RESUMING"),
    ],
)
async def test_controller_settles_terminal_cancellation_from_all_active_phases(
    transition_path: tuple[str, ...],
) -> None:
    controller = WorkerRunController()
    request = _worker_request(worker_id="cancel-phase-worker")
    await controller.create_run(request)
    for target in transition_path:
        current = str((await controller.get_run(request.run_id))["state"])
        await controller.transition(request.run_id, target, expected=current)

    class TerminalCancellationAdapter:
        capabilities = _BlockingAdapter(asyncio.Event()).capabilities

        async def cancel(self, _request):
            return WorkerCancellationReceipt(
                run_id=request.run_id,
                accepted=True,
                terminal=True,
                runtime_status="CANCELLED",
            )

    row = await controller.cancel(
        request.run_id,
        TerminalCancellationAdapter(),  # type: ignore[arg-type]
        reason="cancel active phase",
        requested_by="operator",
    )

    assert row is not None and row["state"] == "CANCELLED"


@pytest.mark.asyncio
async def test_controller_rejects_cancellation_receipt_for_another_run() -> None:
    controller = WorkerRunController()
    request = _worker_request(worker_id="cancel-scope-worker")
    await controller.create_run(request)
    for target in ("VALIDATING", "READY", "DISPATCHING", "RUNNING"):
        current = str((await controller.get_run(request.run_id))["state"])
        await controller.transition(request.run_id, target, expected=current)

    class WrongRunCancellationAdapter:
        capabilities = _BlockingAdapter(asyncio.Event()).capabilities

        async def cancel(self, _request):
            return WorkerCancellationReceipt(
                run_id=uuid4(),
                accepted=True,
                terminal=True,
                runtime_status="CANCELLED",
            )

    with pytest.raises(WorkerRunError, match="does not match"):
        await controller.cancel(
            request.run_id,
            WrongRunCancellationAdapter(),  # type: ignore[arg-type]
            reason="scope mismatch",
            requested_by="operator",
        )


class _CancellationRequestOnlyAdapter:
    def __init__(self) -> None:
        self.requested: WorkerCancellation | None = None
        self.runtime_terminated = False

    async def cancel(self, request: WorkerCancellation) -> None:
        self.requested = request
        # The current contract returns None; no runtime termination proof is
        # available to the controller from this acknowledgement.


@pytest.mark.asyncio
async def test_controller_marks_cancelled_after_request_ack_without_runtime_termination_proof() -> None:
    controller = WorkerRunController()
    request = _worker_request(worker_id="cancel-worker")
    await controller.create_run(request)
    for target in ("VALIDATING", "READY", "DISPATCHING", "RUNNING"):
        current = str((await controller.get_run(request.run_id))["state"])
        await controller.transition(request.run_id, target, expected=current)

    adapter = _CancellationRequestOnlyAdapter()
    row = await controller.cancel(
        request.run_id,
        adapter,  # type: ignore[arg-type]
        reason="operator requested cancellation",
        requested_by="operator",
    )

    assert adapter.requested is not None
    assert adapter.runtime_terminated is False
    assert row is not None and row["state"] == "CANCELLED"


@pytest.mark.asyncio
async def test_controller_keeps_active_run_nonterminal_until_runtime_cancellation_event() -> None:
    started = asyncio.Event()
    adapter = _BlockingAdapter(started)
    request = _worker_request(worker_id="restart-worker")
    controller = WorkerRunController()
    execution = asyncio.create_task(controller.execute(request, adapter))

    await asyncio.wait_for(started.wait(), timeout=1)
    for _ in range(100):
        if (await controller.get_run(request.run_id) or {}).get("state") == "RUNNING":
            break
        await asyncio.sleep(0.001)

    acknowledged = await controller.cancel(
        request.run_id,
        adapter,
        reason="wait for runtime cancellation event",
        requested_by="operator",
        force=True,
    )

    assert acknowledged is not None
    assert acknowledged["state"] == "CANCELLED"
    outcome = await asyncio.wait_for(execution, timeout=1)
    assert outcome.state == "CANCELLED"
    assert (await controller.get_run(request.run_id))["state"] == "CANCELLED"
    await adapter.close()


class _FakeGateway:
    def __init__(self, *, response_model: str = "provider/governance-model") -> None:
        self.calls: list[dict[str, object]] = []
        self.fallback_calls: list[dict[str, object]] = []
        self.response_model = response_model

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def chat_completion(self, _messages: list[dict[str, object]], **kwargs: object) -> ChatResponse:
        self.calls.append(kwargs)
        return ChatResponse(
            model=self.response_model,
            message=ChatMessage(role="assistant", content="done"),
        )

    async def chat_completion_with_fallback(
        self,
        _messages: list[dict[str, object]],
        **kwargs: object,
    ) -> ChatResponse:
        self.fallback_calls.append(kwargs)
        return await self.chat_completion(_messages, **kwargs)


class _GovernanceProbeAgent(AgentBase):
    async def handle_message(self, _envelope: object) -> None:
        return None


class _DispatchGovernanceProbeAgent(_GovernanceProbeAgent):
    async def handle_message(self, _envelope: object) -> None:
        await self.think(messages=[{"role": "user", "content": "dispatch"}])


@pytest.mark.asyncio
async def test_governance_agent_uses_direct_gateway_model_path_without_worker_snapshot() -> None:
    gateway = _FakeGateway()
    agent = _GovernanceProbeAgent(
        AgentConfig(
            agent_id="governance-probe",
            team_id="exec_ceo",
            agent_role=AgentRole.ADMIN,
            agent_secret="test-secret",
            router_url="http://router.test",
            llm_model="governance-model",
            llm_use_fallback=False,
            max_think_iterations=1,
        ),
        llm_client=gateway,  # type: ignore[arg-type]
    )

    await agent.think(messages=[{"role": "user", "content": "characterize"}])

    assert len(gateway.calls) == 1
    assert gateway.calls[0]["model"] == "governance-model"
    assert "resolution_snapshot_id" not in gateway.calls[0]
    assert "model_resolution_snapshot_id" not in inspect.signature(AgentBase.think).parameters


@pytest.mark.asyncio
async def test_governance_agent_can_fail_closed_when_runtime_requires_snapshot() -> None:
    gateway = _FakeGateway()
    agent = _GovernanceProbeAgent(
        AgentConfig(
            agent_id="governance-probe-required",
            team_id="exec_ceo",
            agent_role=AgentRole.ADMIN,
            agent_secret="test-secret",
            router_url="http://router.test",
            llm_model="governance-model",
            llm_use_fallback=True,
            max_think_iterations=1,
            require_model_resolution_binding=True,
        ),
        llm_client=gateway,  # type: ignore[arg-type]
    )

    with pytest.raises(
        ModelProvenanceError,
        match="requires an AIAT model-resolution binding",
    ):
        await agent.think(messages=[{"role": "user", "content": "must bind"}])

    assert gateway.calls == []
    assert gateway.fallback_calls == []


@pytest.mark.asyncio
async def test_governance_agent_bound_model_uses_exact_model_and_records_provenance() -> None:
    gateway = _FakeGateway()
    storage = SimpleNamespace(record_project_usage=AsyncMock(return_value={}))
    binding = GovernanceModelBinding(
        resolution_snapshot_id=UUID("00000000-0000-4000-a000-000000000205"),
        profile_id="governance-profile",
        profile_version="2026-09-13",
        provider_id="provider",
        exact_model_id="provider/governance-model",
    )
    agent = _GovernanceProbeAgent(
        AgentConfig(
            agent_id="governance-probe-bound",
            team_id="exec_ceo",
            agent_role=AgentRole.ADMIN,
            agent_secret="test-secret",
            router_url="http://router.test",
            llm_model="unmanaged-config-value",
            llm_use_fallback=True,
            max_think_iterations=1,
            model_resolution_binding=binding,
        ),
        storage=storage,
        llm_client=gateway,  # type: ignore[arg-type]
    )
    agent._current_envelope = SimpleNamespace(
        project_id=str(PROJECT_ID),
        correlation_id=UUID("00000000-0000-4000-a000-000000000206"),
        message_id=UUID("00000000-0000-4000-a000-000000000207"),
    )

    await agent.think(messages=[{"role": "user", "content": "bound"}])

    assert gateway.fallback_calls == []
    assert len(gateway.calls) == 1
    assert gateway.calls[0]["model"] == "provider/governance-model"
    details = storage.record_project_usage.await_args.kwargs["details"]
    assert details == {
        "provenance_status": "bound",
        "runtime_plane": "governance_agent",
        "resolution_snapshot_id": str(binding.resolution_snapshot_id),
        "profile_id": "governance-profile",
        "profile_version": "2026-09-13",
        "provider_id": "provider",
        "exact_model_id": "provider/governance-model",
    }


@pytest.mark.asyncio
async def test_governance_agent_bound_model_fails_closed_on_response_mismatch() -> None:
    gateway = _FakeGateway(response_model="other-provider/other-model")
    storage = SimpleNamespace(record_project_usage=AsyncMock(return_value={}))
    binding = GovernanceModelBinding(
        resolution_snapshot_id=UUID("00000000-0000-4000-a000-000000000208"),
        profile_id="governance-profile",
        provider_id="provider",
        exact_model_id="provider/governance-model",
    )
    agent = _GovernanceProbeAgent(
        AgentConfig(
            agent_id="governance-probe-mismatch",
            team_id="exec_ceo",
            agent_role=AgentRole.ADMIN,
            agent_secret="test-secret",
            router_url="http://router.test",
            llm_use_fallback=True,
            max_think_iterations=1,
            model_resolution_binding=binding,
        ),
        storage=storage,
        llm_client=gateway,  # type: ignore[arg-type]
    )
    agent._current_envelope = SimpleNamespace(
        project_id=str(PROJECT_ID),
        correlation_id=UUID("00000000-0000-4000-a000-000000000209"),
        message_id=UUID("00000000-0000-4000-a000-000000000210"),
    )

    with pytest.raises(ModelProvenanceError, match="does not match"):
        await agent.think(messages=[{"role": "user", "content": "mismatch"}])

    details = storage.record_project_usage.await_args.kwargs["details"]
    assert details["provenance_status"] == "bound"
    assert details["error_type"] == "ModelProvenanceError"


@pytest.mark.asyncio
async def test_csuite_human_directive_uses_bound_model_without_fallback() -> None:
    """The C-suite one-turn path must honor the same model binding as think()."""

    gateway = _FakeGateway(response_model="provider/governance-model")
    binding = GovernanceModelBinding(
        resolution_snapshot_id=UUID("00000000-0000-4000-a000-000000000217"),
        profile_id="governance-profile",
        profile_version="2026-09-15",
        provider_id="provider",
        exact_model_id="provider/governance-model",
    )
    agent = CSuiteAgent(
        AgentConfig(
            agent_id="governance-directive-probe",
            team_id="exec_ceo",
            agent_role=AgentRole.ORCHESTRATOR,
            agent_secret="test-secret",
            router_url="http://router.test",
            llm_model="unmanaged-config-value",
            llm_use_fallback=True,
            model_resolution_binding=binding,
        ),
        specialization="CEO",
        llm_client=gateway,  # type: ignore[arg-type]
    )

    result = await agent._run_human_directive_turn(
        messages=[{"role": "user", "content": "summarize"}],
        project_id="operator-direct",
        tools=[],
    )

    assert result == "done"
    assert gateway.fallback_calls == []
    assert gateway.calls[0]["model"] == "provider/governance-model"


@pytest.mark.asyncio
async def test_csuite_human_directive_rejects_bound_model_response_mismatch() -> None:
    gateway = _FakeGateway(response_model="other-provider/other-model")
    binding = GovernanceModelBinding(
        resolution_snapshot_id=UUID("00000000-0000-4000-a000-000000000218"),
        profile_id="governance-profile",
        provider_id="provider",
        exact_model_id="provider/governance-model",
    )
    agent = CSuiteAgent(
        AgentConfig(
            agent_id="governance-directive-mismatch",
            team_id="exec_ceo",
            agent_role=AgentRole.ORCHESTRATOR,
            agent_secret="test-secret",
            router_url="http://router.test",
            model_resolution_binding=binding,
        ),
        specialization="CEO",
        llm_client=gateway,  # type: ignore[arg-type]
    )

    with pytest.raises(ModelProvenanceError, match="does not match"):
        await agent._run_human_directive_turn(
            messages=[{"role": "user", "content": "summarize"}],
            project_id="operator-direct",
            tools=[],
        )


@pytest.mark.asyncio
async def test_governance_binding_is_scoped_to_one_agent_dispatch() -> None:
    gateway = _FakeGateway()
    storage = SimpleNamespace(record_project_usage=AsyncMock(return_value={}))
    binding = GovernanceModelBinding(
        resolution_snapshot_id=UUID("00000000-0000-4000-a000-000000000211"),
        profile_id="governance-profile",
        profile_version="2026-09-15",
        provider_id="provider",
        exact_model_id="provider/governance-model",
    )
    agent = _DispatchGovernanceProbeAgent(
        AgentConfig(
            agent_id="governance-dispatch-probe",
            team_id="exec_ceo",
            agent_role=AgentRole.ADMIN,
            agent_secret="test-secret",
            router_url="http://router.test",
            llm_model="legacy-governance-model",
            llm_use_fallback=False,
            max_think_iterations=1,
        ),
        storage=storage,
        llm_client=gateway,  # type: ignore[arg-type]
    )

    def frame(*, snapshot_id: UUID | None, message_id: UUID) -> WSMessageFrame:
        envelope = MessageEnvelope(
            msg_type=MessageType.TASK,
            sender_id="orchestrator",
            sender_role=AgentRole.ORCHESTRATOR,
            sender_team="exec_ceo",
            recipient_id=agent.agent_id,
            project_id=str(PROJECT_ID),
            model_resolution_snapshot_id=snapshot_id,
            message_id=message_id,
        )
        return WSMessageFrame(
            entry_id=f"{message_id}-0",
            envelope=envelope,
            stream="stream:exec_ceo",
        )

    await agent._dispatch(
        frame(
            snapshot_id=binding.resolution_snapshot_id,
            message_id=UUID("00000000-0000-4000-a000-000000000212"),
        ),
        model_resolution_binding=binding,
    )
    assert gateway.calls[0]["model"] == binding.exact_model_id
    assert agent._effective_model_resolution_binding() is None

    await agent._dispatch(
        frame(
            snapshot_id=None,
            message_id=UUID("00000000-0000-4000-a000-000000000213"),
        )
    )
    assert [call["model"] for call in gateway.calls] == [
        "provider/governance-model",
        "legacy-governance-model",
    ]
    assert storage.record_project_usage.await_args_list[0].kwargs["details"][
        "provenance_status"
    ] == "bound"
    assert storage.record_project_usage.await_args_list[1].kwargs["details"][
        "provenance_status"
    ] == "unresolved"


@pytest.mark.asyncio
async def test_snapshot_bearing_direct_agent_dispatch_resolves_scoped_binding() -> None:
    """Legacy/direct AgentBase dispatch must not silently use another model."""

    snapshot_id = UUID("00000000-0000-4000-a000-000000000219")
    gateway = _FakeGateway(response_model="provider/governance-model")

    class _SnapshotStorage:
        record_project_usage = AsyncMock(return_value={})

        def __init__(self) -> None:
            self.saved: list[dict[str, object]] = []

        async def save_checkpoint(
            self,
            _agent_id: str,
            _project_id: str,
            data: dict[str, object],
        ) -> None:
            self.saved.append(data)

        async def get_model_resolution_snapshot(
            self,
            requested_snapshot_id: UUID,
            *,
            project_id: UUID | None = None,
        ) -> dict[str, object] | None:
            assert requested_snapshot_id == snapshot_id
            assert project_id == PROJECT_ID
            return {
                "id": snapshot_id,
                "project_id": PROJECT_ID,
                "resolved_profile_id": "governance-profile",
                "resolved_profile_version": "2026-09-15",
                "provider_id": "provider",
                "exact_model_id": "provider/governance-model",
            }

    storage = _SnapshotStorage()
    agent = _DispatchGovernanceProbeAgent(
        AgentConfig(
            agent_id="governance-direct-dispatch",
            team_id="exec_ceo",
            agent_role=AgentRole.ADMIN,
            agent_secret="test-secret",
            router_url="http://router.test",
            llm_model="legacy-governance-model",
            llm_use_fallback=True,
            max_think_iterations=1,
        ),
        storage=storage,
        llm_client=gateway,  # type: ignore[arg-type]
    )
    envelope = MessageEnvelope(
        msg_type=MessageType.TASK,
        sender_id="orchestrator",
        sender_role=AgentRole.ORCHESTRATOR,
        sender_team="orchestrator",
        recipient_id=agent.agent_id,
        project_id=str(PROJECT_ID),
        model_resolution_snapshot_id=snapshot_id,
        message_id=UUID("00000000-0000-4000-a000-000000000220"),
    )

    await agent._dispatch(
        WSMessageFrame(
            entry_id="direct-snapshot-1-0",
            envelope=envelope,
            stream="stream:exec_ceo",
        )
    )

    assert gateway.fallback_calls == []
    assert gateway.calls[0]["model"] == "provider/governance-model"
    assert storage.saved[0]["model_resolution_snapshot_id"] == str(snapshot_id)


@pytest.mark.asyncio
async def test_bound_governance_agent_propagates_outgoing_message_provenance() -> None:
    """Delegated governance messages retain the inbound AIAT model decision."""

    binding = GovernanceModelBinding(
        resolution_snapshot_id=UUID("00000000-0000-4000-a000-000000000214"),
        profile_id="governance-profile",
        provider_id="provider",
        exact_model_id="provider/governance-model",
    )
    agent = _GovernanceProbeAgent(
        AgentConfig(
            agent_id="governance-provenance-propagation",
            team_id="exec_ceo",
            agent_role=AgentRole.ADMIN,
            agent_secret="test-secret",
            router_url="http://router.test",
            model_resolution_binding=binding,
        )
    )
    agent._current_envelope = MessageEnvelope(
        msg_type=MessageType.TASK,
        sender_id="orchestrator",
        sender_role=AgentRole.ORCHESTRATOR,
        sender_team="exec_ceo",
        recipient_id=agent.agent_id,
        project_id=str(PROJECT_ID),
        model_resolution_snapshot_id=binding.resolution_snapshot_id,
    )
    router = SimpleNamespace(
        publish=AsyncMock(return_value="entry-1"),
        broadcast=AsyncMock(return_value={"ok": True}),
    )
    agent._router = router

    outgoing = MessageEnvelope(
        msg_type=MessageType.ADMIN_TASK,
        sender_id=agent.agent_id,
        sender_role=agent.role,
        sender_team=agent.team_id,
        recipient_team="office_cto",
        project_id=str(PROJECT_ID),
        payload={"task": "delegated"},
    )
    await agent.publish(outgoing)
    published = router.publish.await_args.args[0]
    assert published.model_resolution_snapshot_id == binding.resolution_snapshot_id
    assert outgoing.model_resolution_snapshot_id is None

    broadcast = outgoing.model_copy(update={"msg_type": MessageType.BROADCAST})
    await agent.broadcast(broadcast)
    broadcast_published = router.broadcast.await_args.args[0]
    assert broadcast_published.model_resolution_snapshot_id == binding.resolution_snapshot_id


@pytest.mark.asyncio
async def test_bound_governance_agent_rejects_different_outgoing_snapshot() -> None:
    binding = GovernanceModelBinding(
        resolution_snapshot_id=UUID("00000000-0000-4000-a000-000000000215"),
        profile_id="governance-profile",
        provider_id="provider",
        exact_model_id="provider/governance-model",
    )
    agent = _GovernanceProbeAgent(
        AgentConfig(
            agent_id="governance-provenance-mismatch",
            team_id="exec_ceo",
            agent_role=AgentRole.ADMIN,
            agent_secret="test-secret",
            router_url="http://router.test",
            model_resolution_binding=binding,
        )
    )
    agent._current_envelope = MessageEnvelope(
        msg_type=MessageType.TASK,
        sender_id="orchestrator",
        sender_role=AgentRole.ORCHESTRATOR,
        sender_team="exec_ceo",
        recipient_id=agent.agent_id,
        project_id=str(PROJECT_ID),
        model_resolution_snapshot_id=binding.resolution_snapshot_id,
    )
    agent._router = SimpleNamespace(publish=AsyncMock(return_value="entry-1"))
    conflicting = MessageEnvelope(
        msg_type=MessageType.ADMIN_TASK,
        sender_id=agent.agent_id,
        sender_role=agent.role,
        sender_team=agent.team_id,
        recipient_team="office_cto",
        project_id=str(PROJECT_ID),
        model_resolution_snapshot_id=UUID("00000000-0000-4000-a000-000000000216"),
        payload={"task": "conflicting"},
    )

    with pytest.raises(ModelProvenanceError, match="different model-resolution binding"):
        await agent.publish(conflicting)
    agent._router.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_specialist_worker_result_path_enforces_model_resolution_snapshot() -> None:
    snapshot_id = UUID("00000000-0000-4000-a000-000000000204")

    class _SnapshotStorage:
        async def get_model_resolution_snapshot(
            self,
            _snapshot_id: UUID,
            *,
            project_id: UUID | None = None,
        ) -> dict[str, str]:
            return {"provider_id": "approved-provider", "exact_model_id": "approved-model"}

    request = WorkerRunRequest(
        run_id=uuid4(),
        idempotency_key="specialist-model-characterization",
        worker_id="specialist-worker",
        task_type="model-provenance",
        resolved_model_profile=ModelProfileReference(
            profile_id="approved-profile",
            version="1",
            exact_model_id="approved-model",
            resolution_snapshot_id=snapshot_id,
        ),
    )
    result = WorkerResult(
        run_id=request.run_id,
        worker_id=request.worker_id,
        success=True,
        usage=WorkerUsage(provider="unapproved-provider", exact_model_id="approved-model"),
    )

    with pytest.raises(WorkerRunError, match="does not match") as caught:
        await WorkerRunController(storage=_SnapshotStorage())._validate_result_model_attribution(
            request,
            result,
            snapshot_id,
        )

    assert caught.value.code == "MODEL_USAGE_ATTRIBUTION_MISMATCH"


@pytest.mark.asyncio
async def test_worker_tool_request_is_denied_by_controller_without_direct_dispatch() -> None:
    tool_dispatcher = AsyncMock()

    async def worker(request: WorkerRunRequest, _adapter: NativeWorkerAdapter):
        yield WorkerEvent(
            run_id=request.run_id,
            worker_id=request.worker_id,
            event_type=EventType.TOOL_REQUEST,
            tool_request=WorkerToolRequest(
                run_id=request.run_id,
                tool_name="privileged.write",
                arguments={"value": "blocked"},
                idempotency_key="denied-tool-characterization",
            ),
        )
        yield WorkerResult(
            run_id=request.run_id,
            worker_id=request.worker_id,
            success=True,
            output={"done": True},
        )

    adapter = NativeWorkerAdapter(
        worker,
        worker_id="tool-worker",
        context=AdapterContext(tool_dispatcher=tool_dispatcher),
    )
    outcome = await WorkerRunController().execute(
        _worker_request(worker_id="tool-worker", task_type="tool-boundary"),
        adapter,
    )

    tool_response = next(event for event in outcome.events if event.event_type == EventType.TOOL_RESPONSE)
    assert tool_response.tool_response is not None
    assert tool_response.tool_response.success is False
    assert tool_response.tool_response.error is not None
    assert tool_response.tool_response.error.code == "TOOL_NOT_GRANTED"
    tool_dispatcher.assert_not_awaited()
    await adapter.close()


def test_canonical_evidence_can_be_built_without_optional_telemetry() -> None:
    report = build_trace_evidence(
        trace_id="trace-without-telemetry",
        worker_usage_rows=[
            {
                "id": "usage-1",
                "run_id": "run-1",
                "provider_id": "provider-1",
                "exact_model_id": "model-1",
                "prompt_tokens": 2,
                "completion_tokens": 3,
            }
        ],
        artifact_rows=[
            {
                "id": "artifact-link-1",
                "run_id": "run-1",
                "artifact_id": 7,
                "kind": "report",
                "sha256": "a" * 64,
                "size_bytes": 10,
            }
        ],
    )

    assert report.status == "observed"
    assert report.source_counts["worker_usage_records"] == 1
    assert report.source_counts["worker_artifacts"] == 1
    assert report.source_counts.get("native_spans", 0) == 0
