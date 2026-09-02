from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from mas_core.worker_registry.host_reservations import (
    HostCapacityReservationLedger,
    normalize_resources,
    public_reservation,
)


def _row(**overrides: object) -> dict[str, object]:
    now = datetime.now(tz=UTC)
    values: dict[str, object] = {
        "id": "reservation-row",
        "host_id": "host-row",
        "host_lease_generation": 4,
        "reservation_key": "run-1",
        "owner": "scheduler",
        "resource_json": {"slots": 1, "memory_bytes": 1024, "gpu_count": 1},
        "state": "RESERVED",
        "lease_expires_at": now + timedelta(minutes=1),
        "created_at": now,
        "metadata": {"fixture": True},
    }
    values.update(overrides)
    return values


def test_resources_are_normalized_and_slots_are_required() -> None:
    assert normalize_resources({"slots": "2", "memory_bytes": 10}) == {
        "slots": 2,
        "memory_bytes": 10,
        "gpu_count": 0,
    }
    with pytest.raises(ValueError, match="slots"):
        normalize_resources({"slots": 0})


def test_public_reservation_is_bounded_and_replay_is_explicit() -> None:
    row = public_reservation(_row(), host_key="host-a", idempotent_replay=True)

    assert row["host_id"] == "host-a"
    assert row["lease_valid"] is True
    assert row["idempotent_replay"] is True
    assert row["resources"]["gpu_count"] == 1
    assert row["host_lease_generation"] == 4
    assert "auth_token_sha256" not in row


def test_expired_reservation_is_not_lease_valid() -> None:
    row = public_reservation(
        _row(lease_expires_at=datetime.now(tz=UTC) - timedelta(seconds=1))
    )

    assert row["state"] == "RESERVED"
    assert row["lease_valid"] is False
    assert HostCapacityReservationLedger is not None
