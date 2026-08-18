from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from mas_core.worker_registry.host_registry import (
    WorkerHostRegistry,
    host_snapshot_row,
    public_host_row,
    token_sha256,
)


def _row(**overrides: object) -> dict[str, object]:
    now = datetime.now(tz=UTC)
    values: dict[str, object] = {
        "id": "host-row",
        "host_id": "host-a",
        "status": "READY",
        "auth_token_sha256": token_sha256("secret-token"),
        "labels": {"zone": "a"},
        "capabilities": ["native", "gpu"],
        "sandbox_profile": "gvisor",
        "isolation_mode": "gvisor",
        "capacity": {
            "slots_total": 4,
            "slots_used": 1,
            "memory_bytes_total": 4096,
            "memory_bytes_used": 512,
            "gpu_total": 1,
            "gpu_used": 0,
        },
        "priority": 2,
        "lease_owner": "host-a",
        "lease_expires_at": now + timedelta(minutes=1),
        "heartbeat_at": now,
        "last_seen_at": now,
        "created_at": now,
        "updated_at": now,
        "metadata": {"provider": "local"},
    }
    values.update(overrides)
    return values


def test_public_host_projection_removes_credential_material() -> None:
    row = public_host_row(_row())

    assert row["host_id"] == "host-a"
    assert row["lease_valid"] is True
    assert "auth_token_sha256" not in row
    assert "lease_owner" not in row
    assert row["capacity"]["slots_total"] == 4


def test_host_snapshot_projection_exposes_placement_profiles() -> None:
    row = host_snapshot_row(_row())

    assert row["sandbox_profiles"] == ["gvisor"]
    assert row["isolation_modes"] == ["gvisor"]
    assert row["lease_valid"] is True


def test_expired_host_lease_is_invalid_without_status_mutation() -> None:
    row = public_host_row(_row(lease_expires_at=datetime.now(tz=UTC) - timedelta(seconds=1)))

    assert row["status"] == "READY"
    assert row["lease_valid"] is False


def test_token_digest_is_stable_and_registry_requires_storage() -> None:
    assert token_sha256("secret-token") == token_sha256("secret-token")
    assert token_sha256("secret-token") != "secret-token"
    with pytest.raises(ValueError, match="registration_token"):
        token_sha256("")
    assert WorkerHostRegistry is not None
