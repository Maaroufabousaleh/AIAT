from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from mas_core.worker_contract.models import (
    ModelProfileReference,
    WorkerCancellation,
    WorkerCancellationReceipt,
    WorkerRunRequest,
)
from mas_core.worker_registry.host_executor import (
    HOST_EXECUTION_SCHEMA,
    HostExecutionRequest,
    WorkerHostExecutionRejected,
    WorkerHostExecutor,
)
from mas_core.worker_registry.run_host_binding import RunHostBindingIdentity

RUN_ID = UUID("00000000-0000-4000-a000-000000000101")
WORKER_ID = UUID("00000000-0000-4000-a000-000000000102")


def _binding(**overrides: object) -> dict[str, object]:
    return {
        "id": UUID("00000000-0000-4000-a000-000000000110"),
        "run_id": RUN_ID,
        "worker_id": WORKER_ID,
        "host_id": "worker-host-a",
        "host_uuid": UUID("00000000-0000-4000-a000-000000000111"),
        "reservation_id": UUID("00000000-0000-4000-a000-000000000112"),
        "owner": "host-executor",
        "host_plane": "worker",
        "host_status": "READY",
        "state": "COMMITTED",
        "reservation_state": "COMMITTED",
        "host_lease_generation": 2,
        "current_host_lease_generation": 2,
        "current_host_lease_valid": True,
        **overrides,
    }


def test_host_execution_request_normalizes_identity() -> None:
    request = HostExecutionRequest(
        run_id=str(RUN_ID),
        host_id=" worker-host-a ",
        owner=" host-executor ",
        lease_seconds=30,
    )

    assert request.validate() == (RUN_ID, "worker-host-a", "host-executor", 30)


def test_host_execution_request_rejects_invalid_lease() -> None:
    with pytest.raises(ValueError, match="between 1 and 86400"):
        HostExecutionRequest(RUN_ID, "worker-host-a", "owner", 0).validate()


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("state", "ASSIGNED", "run_host_binding_not_committed"),
        ("reservation_state", "RESERVED", "run_host_reservation_not_committed"),
        ("host_plane", "control", "run_host_plane_mismatch"),
        ("owner", "other-executor", "run_host_binding_owner_mismatch"),
        ("host_lease_generation", 1, "run_host_lease_generation_mismatch"),
        ("current_host_lease_valid", False, "run_host_lease_invalid"),
    ],
)
def test_executor_enforces_committed_worker_plane_admission(
    field: str,
    value: object,
    reason: str,
) -> None:
    with pytest.raises(WorkerHostExecutionRejected) as caught:
        WorkerHostExecutor._validate_binding(
            _binding(**{field: value}),
            run_id=RUN_ID,
            host_id="worker-host-a",
            owner="host-executor",
            worker_registry_id=WORKER_ID,
        )

    assert caught.value.reason_code == reason


def test_executor_rejects_missing_binding_and_exposes_schema() -> None:
    with pytest.raises(WorkerHostExecutionRejected) as caught:
        WorkerHostExecutor._validate_binding(
            None,
            run_id=RUN_ID,
            host_id="worker-host-a",
            owner="host-executor",
            worker_registry_id=WORKER_ID,
        )

    assert caught.value.reason_code == "run_host_binding_not_found"
    assert HOST_EXECUTION_SCHEMA == "aiat.worker-host-execution.v1"


class _SnapshotStorage:
    def __init__(self, snapshot: dict[str, object] | None) -> None:
        self.snapshot = snapshot
        self.project_ids: list[UUID | None] = []

    async def get_model_resolution_snapshot(
        self,
        _snapshot_id: UUID,
        *,
        project_id: UUID | None = None,
    ) -> dict[str, object] | None:
        self.project_ids.append(project_id)
        return self.snapshot


def _model_request(*, snapshot_id: UUID) -> WorkerRunRequest:
    return WorkerRunRequest(
        run_id=RUN_ID,
        idempotency_key="host-model-resolution-test",
        worker_id=str(WORKER_ID),
        task_type="model-resolution",
        requested_model_profile=ModelProfileReference(profile_id="profile-v1"),
        resolved_model_profile=ModelProfileReference(
            profile_id="profile-v1",
            version="v1",
            exact_model_id="model-v1",
            resolution_snapshot_id=snapshot_id,
        ),
    )


@pytest.mark.asyncio
async def test_model_snapshot_must_match_request_before_claim() -> None:
    snapshot_id = UUID("00000000-0000-4000-a000-000000000103")
    executor = WorkerHostExecutor(
        _SnapshotStorage(
            {
                "id": snapshot_id,
                "requested_profile_id": "profile-v1",
                "resolved_profile_id": "profile-v1",
                "resolved_profile_version": "v1",
                "exact_model_id": "model-v1",
                "policy_failure_code": None,
            }
        )
    )

    result = await executor._validate_model_resolution(
        _model_request(snapshot_id=snapshot_id), snapshot_id
    )

    assert result and result["exact_model_id"] == "model-v1"


@pytest.mark.asyncio
async def test_model_snapshot_mismatch_is_rejected_before_claim() -> None:
    snapshot_id = UUID("00000000-0000-4000-a000-000000000104")
    executor = WorkerHostExecutor(
        _SnapshotStorage(
            {
                "id": snapshot_id,
                "requested_profile_id": "profile-v1",
                "resolved_profile_id": "profile-v1",
                "resolved_profile_version": "v2",
                "exact_model_id": "model-v1",
                "policy_failure_code": None,
            }
        )
    )

    with pytest.raises(WorkerHostExecutionRejected) as caught:
        await executor._validate_model_resolution(
            _model_request(snapshot_id=snapshot_id), snapshot_id
        )

    assert caught.value.reason_code == "model_resolution_snapshot_reference_mismatch"


@pytest.mark.asyncio
async def test_host_model_snapshot_lookup_preserves_project_scope() -> None:
    snapshot_id = UUID("00000000-0000-4000-a000-000000000105")
    project_id = uuid4()
    storage = _SnapshotStorage(
        {
            "id": snapshot_id,
            "requested_profile_id": "profile-v1",
            "resolved_profile_id": "profile-v1",
            "resolved_profile_version": "v1",
            "exact_model_id": "model-v1",
            "policy_failure_code": None,
        }
    )
    executor = WorkerHostExecutor(storage)
    request = _model_request(snapshot_id=snapshot_id).model_copy(
        update={"project_id": project_id}
    )

    await executor._validate_model_resolution(request, snapshot_id)

    assert storage.project_ids == [project_id]


@pytest.mark.asyncio
async def test_executor_rejects_binding_owner_mismatch_before_claim() -> None:
    class _Bindings:
        async def get(self, _run_id: UUID) -> dict[str, object]:
            return _binding(owner="binding-owner")

    class _Storage:
        claim_count = 0

        async def claim_worker_run(self, **_kwargs: object) -> dict[str, object] | None:
            self.claim_count += 1
            return None

    storage = _Storage()
    executor = WorkerHostExecutor(storage, binding_service=_Bindings())  # type: ignore[arg-type]
    request = WorkerRunRequest(
        run_id=RUN_ID,
        idempotency_key="host-owner-mismatch-test",
        worker_id=str(WORKER_ID),
        task_type="host-owner-mismatch",
    )

    with pytest.raises(WorkerHostExecutionRejected) as caught:
        await executor.execute(
            HostExecutionRequest(RUN_ID, "worker-host-a", "host-executor"),
            request,
            object(),
            worker_registry_id=WORKER_ID,
        )

    assert caught.value.reason_code == "run_host_binding_owner_mismatch"
    assert storage.claim_count == 0


@pytest.mark.asyncio
async def test_executor_releases_the_exact_binding_it_admitted(monkeypatch: pytest.MonkeyPatch) -> None:
    binding = _binding()

    class _Bindings:
        def __init__(self) -> None:
            self.get = AsyncMock(return_value=binding)
            self.release = AsyncMock(return_value={**binding, "state": "RELEASED"})

    class _Storage:
        def __init__(self) -> None:
            self.heartbeat_calls: list[dict[str, object]] = []

        async def claim_worker_run(self, **_kwargs: object) -> dict[str, object]:
            return {
                "state": "CLAIMED",
                "worker_id": WORKER_ID,
                "claim_owner": "host-executor",
                "attempt_count": 3,
            }

        async def heartbeat_worker_run(self, *_args: object, **kwargs: object) -> dict[str, object]:
            self.heartbeat_calls.append(kwargs)
            return {
                "claim_owner": kwargs["owner"],
                "attempt_count": kwargs["attempt_count"],
            }

    class _Controller:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def execute(self, *_args: object, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(state="SUCCEEDED")

    monkeypatch.setattr(
        "mas_core.worker_contract.controller.WorkerRunController",
        _Controller,
    )
    bindings = _Bindings()
    storage = _Storage()
    executor = WorkerHostExecutor(
        storage,  # type: ignore[arg-type]
        binding_service=bindings,  # type: ignore[arg-type]
    )
    request = WorkerRunRequest(
        run_id=RUN_ID,
        idempotency_key="binding-release-fence-test",
        worker_id=str(WORKER_ID),
        task_type="binding-release-fence",
    )

    result = await executor.execute(
        HostExecutionRequest(RUN_ID, "worker-host-a", "host-executor"),
        request,
        object(),
        worker_registry_id=WORKER_ID,
    )

    expected_identity = RunHostBindingIdentity.from_binding(binding)
    assert result.outcome.state == "SUCCEEDED"
    assert storage.heartbeat_calls == []
    bindings.release.assert_awaited_once_with(
        RUN_ID,
        owner="host-executor",
        expected_identity=expected_identity,
    )


class _SupervisedStorage:
    def __init__(self, heartbeat_result: dict[str, object] | None) -> None:
        self.heartbeat_result = heartbeat_result
        self.heartbeat_calls: list[dict[str, object]] = []
        self.heartbeat_seen = asyncio.Event()
        # Model the canonical durable state before a host cancellation.
        self.canonical_state = "RUNNING"
        self.terminal_settlements: list[str] = []

    async def claim_worker_run(self, **_kwargs: object) -> dict[str, object]:
        return {
            "state": "CLAIMED",
            "worker_id": WORKER_ID,
            "claim_owner": "host-executor",
            "attempt_count": 7,
        }

    async def heartbeat_worker_run(
        self,
        _run_id: UUID,
        **kwargs: object,
    ) -> dict[str, object] | None:
        self.heartbeat_calls.append(kwargs)
        self.heartbeat_seen.set()
        return self.heartbeat_result


class _SupervisedBindings:
    def __init__(
        self,
        admitted_binding: dict[str, object],
        current_binding: dict[str, object] | None = None,
    ) -> None:
        self.admitted_binding = admitted_binding
        self.current_binding = current_binding
        self.get_calls = 0
        self.current_check_seen = asyncio.Event()
        self.active_claim_fence = {
            "claim_owner": "host-executor",
            "attempt_count": 7,
        }
        self.binding_state = "COMMITTED"
        self.release_attempts: list[dict[str, object]] = []
        self.release_calls: list[RunHostBindingIdentity] = []

    async def get(self, _run_id: UUID) -> dict[str, object]:
        self.get_calls += 1
        if self.get_calls == 1:
            return self.admitted_binding
        self.current_check_seen.set()
        return self.current_binding or self.admitted_binding

    async def release(
        self,
        _run_id: UUID,
        *,
        owner: str,
        expected_identity: RunHostBindingIdentity,
        expected_claim_owner: str | None = None,
        expected_attempt_count: int | None = None,
    ) -> dict[str, object]:
        assert owner == "host-executor"
        assert expected_identity == RunHostBindingIdentity.from_binding(_binding())
        self.release_attempts.append(
            {
                "expected_claim_owner": expected_claim_owner,
                "expected_attempt_count": expected_attempt_count,
            }
        )
        if expected_claim_owner is not None and (
            self.active_claim_fence["claim_owner"] != expected_claim_owner
            or self.active_claim_fence["attempt_count"] != expected_attempt_count
        ):
            raise RuntimeError("run_host_binding_execution_fence_mismatch")
        self.release_calls.append(expected_identity)
        self.binding_state = "RELEASED"
        return {**_binding(), "state": self.binding_state}


class _SupervisedAdapter:
    def __init__(self) -> None:
        self.finished = asyncio.Event()
        self.active_claim_fence = {
            "claim_owner": "host-executor",
            "attempt_count": 7,
        }
        self.cancel_requests: list[WorkerCancellation] = []
        self.cancellations: list[WorkerCancellation] = []

    async def cancel(self, request: WorkerCancellation) -> WorkerCancellationReceipt:
        self.cancel_requests.append(request)
        if request.execution_claim_fence != self.active_claim_fence:
            return WorkerCancellationReceipt(
                run_id=request.run_id,
                accepted=False,
                terminal=False,
                runtime_status="RUNNING",
                details={"reason": "stale attempt cancellation ignored"},
            )
        self.cancellations.append(request)
        self.finished.set()
        return WorkerCancellationReceipt(
            run_id=request.run_id,
            accepted=True,
            terminal=True,
            runtime_status="CANCELLED",
        )


class _SupervisedController:
    instances: list[_SupervisedController] = []

    def __init__(self, *, storage: object) -> None:
        self.storage = storage
        self.cancel_calls: list[dict[str, object]] = []
        self.execution_cancelled = False
        self.execution_started = asyncio.Event()
        self.instances.append(self)

    async def execute(
        self,
        _request: object,
        adapter: _SupervisedAdapter,
        **_kwargs: object,
    ) -> SimpleNamespace:
        self.execution_started.set()
        try:
            await adapter.finished.wait()
        except asyncio.CancelledError:
            self.execution_cancelled = True
            raise
        state = "CANCELLED" if adapter.cancellations else "SUCCEEDED"
        self.storage.canonical_state = state
        self.storage.terminal_settlements.append(state)
        return SimpleNamespace(state=state)

    async def cancel(
        self,
        run_id: UUID,
        adapter: _SupervisedAdapter,
        **kwargs: object,
    ) -> dict[str, str]:
        self.cancel_calls.append(kwargs)
        cancellation = WorkerCancellation(
            run_id=run_id,
            reason=str(kwargs["reason"]),
            requested_by=str(kwargs["requested_by"]),
            force=bool(kwargs["force"]),
        )
        cancellation._execution_claim_fence = {
            "claim_owner": kwargs.get("claim_owner"),
            "attempt_count": kwargs.get("attempt_count"),
        }
        await adapter.cancel(cancellation)
        self.storage.canonical_state = "CANCELLED"
        self.storage.terminal_settlements.append("CANCELLED")
        return {"state": "CANCELLED"}


def _supervised_worker_request() -> WorkerRunRequest:
    return WorkerRunRequest(
        run_id=RUN_ID,
        idempotency_key="host-execution-liveness-test",
        worker_id=str(WORKER_ID),
        task_type="host-execution-liveness",
    )


async def _execute_supervised(
    monkeypatch: pytest.MonkeyPatch,
    storage: _SupervisedStorage,
    bindings: _SupervisedBindings,
    adapter: _SupervisedAdapter,
) -> tuple[object, WorkerHostExecutor]:
    monkeypatch.setattr(
        "mas_core.worker_contract.controller.WorkerRunController",
        _SupervisedController,
    )
    executor = WorkerHostExecutor(storage, binding_service=bindings)  # type: ignore[arg-type]
    monkeypatch.setattr(executor, "_heartbeat_interval", lambda _lease: 0.01)
    try:
        result = await asyncio.wait_for(
            executor.execute(
                HostExecutionRequest(RUN_ID, "worker-host-a", "host-executor", lease_seconds=3),
                _supervised_worker_request(),
                adapter,  # type: ignore[arg-type]
                worker_registry_id=WORKER_ID,
            ),
            timeout=2,
        )
    except TimeoutError as exc:
        controller = _SupervisedController.instances[-1]
        raise AssertionError(
            "supervised execution did not stop: "
            f"heartbeats={storage.heartbeat_calls!r}, "
            f"binding_reads={bindings.get_calls}, "
            f"controller_cancels={controller.cancel_calls!r}, "
            f"adapter_cancels={adapter.cancellations!r}"
        ) from exc
    return result, executor


@pytest.mark.asyncio
async def test_host_executor_terminates_runtime_when_claim_heartbeat_is_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _SupervisedStorage(None)
    bindings = _SupervisedBindings(_binding())
    adapter = _SupervisedAdapter()

    result, _executor = await _execute_supervised(monkeypatch, storage, bindings, adapter)

    assert result.outcome.state == "CANCELLED"
    assert len(storage.heartbeat_calls) == 1
    assert storage.heartbeat_calls[0] == {
        "owner": "host-executor",
        "attempt_count": 7,
        "lease_seconds": 3,
    }
    assert len(adapter.cancellations) == 1
    assert adapter.cancellations[0].force is True
    assert adapter.cancellations[0].reason == "worker run claim is no longer current"
    assert adapter.cancellations[0].execution_claim_fence == {
        "claim_owner": "host-executor",
        "attempt_count": 7,
    }
    assert bindings.get_calls == 1
    assert _SupervisedController.instances[-1].cancel_calls == []


@pytest.mark.parametrize(
    "binding_change",
    [
        {"id": UUID("00000000-0000-4000-a000-000000000199")},
        {"current_host_lease_valid": False},
        {"current_host_lease_generation": 3},
    ],
    ids=["reassigned-binding", "expired-host-lease", "changed-host-generation"],
)
@pytest.mark.asyncio
async def test_host_executor_cancels_when_binding_or_host_lease_is_lost(
    monkeypatch: pytest.MonkeyPatch,
    binding_change: dict[str, object],
) -> None:
    storage = _SupervisedStorage(
        {"claim_owner": "host-executor", "attempt_count": 7}
    )
    bindings = _SupervisedBindings(
        _binding(),
        {**_binding(), **binding_change},
    )
    adapter = _SupervisedAdapter()

    result, _executor = await _execute_supervised(monkeypatch, storage, bindings, adapter)

    assert result.outcome.state == "CANCELLED"
    assert len(storage.heartbeat_calls) == 1
    assert len(adapter.cancellations) == 1
    assert adapter.cancellations[0].force is True
    assert bindings.get_calls == 2
    # The run claim was renewed immediately before the host fence failed, so
    # the controller's canonical cancellation path can settle this attempt.
    assert len(_SupervisedController.instances[-1].cancel_calls) == 1
    cancel_request = _SupervisedController.instances[-1].cancel_calls[0]
    assert cancel_request["claim_owner"] == "host-executor"
    assert cancel_request["attempt_count"] == 7
    assert cancel_request.get("allow_unfenced_cancel") is not True


@pytest.mark.asyncio
async def test_host_executor_keeps_running_when_claim_and_binding_remain_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _SupervisedStorage(
        {"claim_owner": "host-executor", "attempt_count": 7}
    )
    bindings = _SupervisedBindings(_binding())
    adapter = _SupervisedAdapter()

    monkeypatch.setattr(
        "mas_core.worker_contract.controller.WorkerRunController",
        _SupervisedController,
    )
    executor = WorkerHostExecutor(storage, binding_service=bindings)  # type: ignore[arg-type]
    monkeypatch.setattr(executor, "_heartbeat_interval", lambda _lease: 0.01)
    execution = asyncio.create_task(
        executor.execute(
            HostExecutionRequest(RUN_ID, "worker-host-a", "host-executor", lease_seconds=3),
            _supervised_worker_request(),
            adapter,  # type: ignore[arg-type]
            worker_registry_id=WORKER_ID,
        )
    )
    await asyncio.wait_for(bindings.current_check_seen.wait(), timeout=1)
    adapter.finished.set()
    result = await asyncio.wait_for(execution, timeout=1)

    assert result.outcome.state == "SUCCEEDED"
    assert len(storage.heartbeat_calls) >= 1
    assert adapter.cancellations == []


@pytest.mark.parametrize("replacement_attempt", [None, 8], ids=["current-claim", "stale-claim"])
@pytest.mark.asyncio
async def test_host_executor_stops_only_its_runtime_and_releases_binding_on_task_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    replacement_attempt: int | None,
) -> None:
    storage = _SupervisedStorage(
        {"claim_owner": "host-executor", "attempt_count": 7}
    )
    bindings = _SupervisedBindings(_binding())
    adapter = _SupervisedAdapter()
    monkeypatch.setattr(
        "mas_core.worker_contract.controller.WorkerRunController",
        _SupervisedController,
    )
    executor = WorkerHostExecutor(storage, binding_service=bindings)  # type: ignore[arg-type]
    previous_controller_count = len(_SupervisedController.instances)
    execution = asyncio.create_task(
        executor.execute(
            HostExecutionRequest(RUN_ID, "worker-host-a", "host-executor", lease_seconds=3),
            _supervised_worker_request(),
            adapter,  # type: ignore[arg-type]
            worker_registry_id=WORKER_ID,
        )
    )

    async def wait_for_controller_start() -> _SupervisedController:
        while len(_SupervisedController.instances) <= previous_controller_count:
            await asyncio.sleep(0)
        current = _SupervisedController.instances[previous_controller_count]
        await current.execution_started.wait()
        return current

    controller = await asyncio.wait_for(wait_for_controller_start(), timeout=1)
    if replacement_attempt is not None:
        # A replacement attempt for this run ID must survive old-executor
        # cleanup. Adapter-side claim fencing rejects the stale cancellation.
        adapter.active_claim_fence = {
            "claim_owner": "host-executor",
            "attempt_count": replacement_attempt,
        }
        bindings.active_claim_fence = dict(adapter.active_claim_fence)
    execution.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(execution, timeout=1)

    controller = _SupervisedController.instances[-1]
    assert controller.execution_cancelled is True
    assert controller.cancel_calls == []
    assert len(adapter.cancel_requests) == 1
    cancellation = adapter.cancel_requests[0]
    assert cancellation.force is True
    assert cancellation.reason == "host executor stopped while worker runtime was active"
    assert cancellation.execution_claim_fence == {
        "claim_owner": "host-executor",
        "attempt_count": 7,
    }
    if replacement_attempt is None:
        assert adapter.cancellations == [cancellation]
        assert adapter.finished.is_set()
    else:
        assert adapter.cancellations == []
        assert not adapter.finished.is_set()
    # The executor stops the runtime but does not settle the canonical run;
    # the durable lease recovery loop remains responsible for requeueing it.
    assert storage.canonical_state == "RUNNING"
    assert storage.terminal_settlements == []
    assert bindings.release_attempts == [
        {"expected_claim_owner": "host-executor", "expected_attempt_count": 7}
    ]
    if replacement_attempt is None:
        assert bindings.release_calls == [RunHostBindingIdentity.from_binding(_binding())]
        assert bindings.binding_state == "RELEASED"
    else:
        assert bindings.release_calls == []
        assert bindings.binding_state == "COMMITTED"
