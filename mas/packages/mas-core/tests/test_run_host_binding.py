from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from mas_core.worker_registry.placement import WorkerPlacementRequest
from mas_core.worker_registry.run_host_binding import (
    RUN_HOST_BINDING_SCHEMA,
    RunHostBindingRejected,
    RunHostBindingRequest,
    public_run_host_binding,
)


def _placement() -> WorkerPlacementRequest:
    return WorkerPlacementRequest(
        worker_id="worker-1",
        required_host_plane="worker",
        required_capabilities=frozenset({"native"}),
        required_labels=(("pool", "worker"),),
        required_sandbox_profile="gvisor",
        required_isolation_mode="gvisor",
        slots=1,
    )


def test_binding_request_normalizes_uuid_identity() -> None:
    request = RunHostBindingRequest(
        run_id="00000000-0000-4000-a000-000000000001",
        worker_id="00000000-0000-4000-a000-000000000002",
        assignment_key="run-host-1",
        owner="dispatcher",
        placement=_placement(),
    )

    run_id, worker_id, key, owner = request.validate()

    assert run_id == UUID("00000000-0000-4000-a000-000000000001")
    assert worker_id == UUID("00000000-0000-4000-a000-000000000002")
    assert (key, owner) == ("run-host-1", "dispatcher")


def test_binding_request_rejects_invalid_identity_or_placement() -> None:
    request = RunHostBindingRequest(
        run_id="not-a-uuid",
        worker_id="00000000-0000-4000-a000-000000000002",
        assignment_key="run-host-1",
        owner="dispatcher",
        placement=_placement(),
    )
    with pytest.raises(ValueError, match="UUIDs"):
        request.validate()

    invalid_placement = WorkerPlacementRequest(
        worker_id="worker-1",
        required_host_plane="unknown",
    )
    invalid = RunHostBindingRequest(
        run_id="00000000-0000-4000-a000-000000000001",
        worker_id="00000000-0000-4000-a000-000000000002",
        assignment_key="run-host-1",
        owner="dispatcher",
        placement=invalid_placement,
    )
    with pytest.raises(ValueError, match="placement"):
        invalid.validate()


def test_binding_projection_is_payload_free_and_replay_explicit() -> None:
    now = datetime.now(tz=UTC)
    row = {
        "id": UUID("00000000-0000-4000-a000-000000000010"),
        "run_id": UUID("00000000-0000-4000-a000-000000000011"),
        "worker_id": UUID("00000000-0000-4000-a000-000000000012"),
        "host_id": UUID("00000000-0000-4000-a000-000000000013"),
        "host_key": "worker-host-a",
        "reservation_id": UUID("00000000-0000-4000-a000-000000000014"),
        "host_lease_generation": 3,
        "assignment_key": "run-host-1",
        "owner": "dispatcher",
        "state": "ASSIGNED",
        "reservation_state": "RESERVED",
        "reservation_lease_valid": True,
        "metadata": {"fixture": True},
        "created_at": now,
    }

    projection = public_run_host_binding(row, idempotent_replay=True)

    assert projection["schema_version"] == RUN_HOST_BINDING_SCHEMA
    assert projection["host_id"] == "worker-host-a"
    assert projection["host_lease_generation"] == 3
    assert projection["reservation_lease_valid"] is True
    assert projection["idempotent_replay"] is True
    assert "task_input" not in projection
    assert "result_json" not in projection


def test_binding_rejection_carries_stable_reason_code() -> None:
    error = RunHostBindingRejected("run_host_binding_conflict")
    assert str(error) == "run_host_binding_conflict"
    assert error.reason_code == "run_host_binding_conflict"
