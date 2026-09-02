from __future__ import annotations

from uuid import UUID

import pytest

from mas_core.worker_contract.models import ModelProfileReference, WorkerRunRequest
from mas_core.worker_registry.host_executor import (
    HOST_EXECUTION_SCHEMA,
    HostExecutionRequest,
    WorkerHostExecutionRejected,
    WorkerHostExecutor,
)

RUN_ID = UUID("00000000-0000-4000-a000-000000000101")
WORKER_ID = UUID("00000000-0000-4000-a000-000000000102")


def _binding(**overrides: object) -> dict[str, object]:
    return {
        "run_id": RUN_ID,
        "worker_id": WORKER_ID,
        "host_id": "worker-host-a",
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
            worker_registry_id=WORKER_ID,
        )

    assert caught.value.reason_code == reason


def test_executor_rejects_missing_binding_and_exposes_schema() -> None:
    with pytest.raises(WorkerHostExecutionRejected) as caught:
        WorkerHostExecutor._validate_binding(
            None,
            run_id=RUN_ID,
            host_id="worker-host-a",
            worker_registry_id=WORKER_ID,
        )

    assert caught.value.reason_code == "run_host_binding_not_found"
    assert HOST_EXECUTION_SCHEMA == "aiat.worker-host-execution.v1"


class _SnapshotStorage:
    def __init__(self, snapshot: dict[str, object] | None) -> None:
        self.snapshot = snapshot

    async def get_model_resolution_snapshot(self, _snapshot_id: UUID) -> dict[str, object] | None:
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
