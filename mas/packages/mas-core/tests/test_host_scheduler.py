from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from mas_core.worker_registry.host_reservations import ReservationRejected
from mas_core.worker_registry.host_scheduler import (
    HostScheduler,
    HostScheduleRequest,
    SchedulerRejected,
)
from mas_core.worker_registry.placement import (
    HostCapacity,
    WorkerHostSnapshot,
    WorkerPlacementRequest,
)


def _host(host_id: str, *, priority: int, slots: int = 2) -> WorkerHostSnapshot:
    return WorkerHostSnapshot(
        host_id=host_id,
        status="READY",
        labels=(("pool", "worker"),),
        capabilities=frozenset({"native"}),
        sandbox_profiles=frozenset({"gvisor"}),
        isolation_modes=frozenset({"gvisor"}),
        capacity=HostCapacity(
            slots_total=slots,
            memory_bytes_total=4 * 1024**3,
            gpu_total=0,
        ),
        lease_valid=True,
        priority=priority,
    )


def _request(key: str = "schedule-1") -> HostScheduleRequest:
    return HostScheduleRequest(
        schedule_key=key,
        owner="scheduler",
        placement=WorkerPlacementRequest(
            worker_id="worker-1",
            required_capabilities=frozenset({"native"}),
            required_labels=(("pool", "worker"),),
            required_sandbox_profile="gvisor",
            required_isolation_mode="gvisor",
            slots=1,
        ),
        reservation_id=UUID("00000000-0000-4000-a000-000000000991"),
    )


class _FakeRegistry:
    def __init__(self, snapshots: tuple[WorkerHostSnapshot, ...]) -> None:
        self.snapshots = snapshots
        self.calls = 0

    async def list_placement_snapshots(self) -> tuple[WorkerHostSnapshot, ...]:
        self.calls += 1
        return self.snapshots


class _FakeLedger:
    def __init__(self, *, replay: dict[str, object] | None = None) -> None:
        self.replay = replay
        self.reserved: list[str] = []

    async def get_by_key(self, key: str) -> dict[str, object] | None:
        return self.replay

    async def reserve(self, *, host_id: str, **_: object) -> dict[str, object]:
        if host_id == "host-a":
            raise ReservationRejected("capacity_slots_exhausted")
        self.reserved.append(host_id)
        return {
            "id": "reservation-b",
            "host_id": host_id,
            "owner": "scheduler",
            "reservation_key": "schedule-1",
            "state": "RESERVED",
            "idempotent_replay": False,
            "lease_valid": True,
            "resources": {"slots": 1, "memory_bytes": 0, "gpu_count": 0},
        }


@pytest.mark.asyncio
async def test_scheduler_falls_back_after_row_locked_reservation_rejection() -> None:
    scheduler = HostScheduler(SimpleNamespace())
    registry = _FakeRegistry((_host("host-a", priority=2), _host("host-b", priority=1)))
    ledger = _FakeLedger()
    scheduler._registry = registry  # type: ignore[attr-defined]
    scheduler._ledger = ledger  # type: ignore[attr-defined]

    report = await scheduler.schedule(_request())

    assert report["status"] == "RESERVED"
    assert report["selected_host_id"] == "host-a"
    assert report["scheduled_host_id"] == "host-b"
    assert report["attempts"] == [{"host_id": "host-a", "reason_code": "capacity_slots_exhausted"}]
    assert report["mutation_performed"] is True
    assert report["worker_dispatch_performed"] is False
    assert report["licence_metadata_is_gate"] is False
    assert ledger.reserved == ["host-b"]


@pytest.mark.asyncio
async def test_scheduler_replays_existing_key_without_refreshing_hosts() -> None:
    now = datetime.now(tz=UTC)
    replay = {
        "id": "reservation-a",
        "host_id": "host-a",
        "owner": "scheduler",
        "reservation_key": "schedule-1",
        "state": "RESERVED",
        "lease_valid": True,
        "created_at": now - timedelta(seconds=1),
    }
    scheduler = HostScheduler(SimpleNamespace())
    registry = _FakeRegistry(())
    scheduler._registry = registry  # type: ignore[attr-defined]
    scheduler._ledger = _FakeLedger(replay=replay)  # type: ignore[attr-defined]

    report = await scheduler.schedule(_request())

    assert report["status"] == "REPLAYED"
    assert report["scheduled_host_id"] == "host-a"
    assert report["reservation"]["idempotent_replay"] is True
    assert report["mutation_performed"] is False
    assert registry.calls == 0


@pytest.mark.asyncio
async def test_scheduler_rejects_key_owned_by_another_actor() -> None:
    scheduler = HostScheduler(SimpleNamespace())
    scheduler._registry = _FakeRegistry(())  # type: ignore[attr-defined]
    scheduler._ledger = _FakeLedger(  # type: ignore[attr-defined]
        replay={"id": "reservation-a", "host_id": "host-a", "owner": "other"}
    )

    with pytest.raises(SchedulerRejected, match="schedule_key_conflict"):
        await scheduler.schedule(_request())
