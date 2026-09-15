from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from mas_core.worker_registry.host_reservations import HostCapacityReservationLedger
from mas_core.worker_registry.placement import WorkerPlacementRequest
from mas_core.worker_registry.run_host_binding import (
    RUN_HOST_BINDING_SCHEMA,
    RunHostBindingRecoveryRejected,
    RunHostBindingRejected,
    RunHostBindingRequest,
    WorkerRunHostBindingService,
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


class _FirstMappingResult:
    def __init__(self, row: dict[str, object] | None = None, *, rowcount: int = 0) -> None:
        self.row = row
        self.rowcount = rowcount

    def mappings(self) -> _FirstMappingResult:
        return self

    def first(self) -> dict[str, object] | None:
        return self.row


@pytest.mark.asyncio
async def test_reservation_transition_can_share_the_binding_transaction() -> None:
    now = datetime.now(tz=UTC)
    reservation_id = UUID("00000000-0000-4000-a000-000000000020")
    row = {
        "id": reservation_id,
        "host_id": UUID("00000000-0000-4000-a000-000000000021"),
        "host_key": "worker-host-a",
        "owner": "scheduler",
        "state": "RESERVED",
        "host_lease_generation": 4,
        "current_lease_generation": 4,
        "lease_expires_at": now + timedelta(seconds=1),
        "resource_json": {"slots": 1},
        "metadata": {},
        "created_at": now,
    }
    updated = {**row, "state": "COMMITTED", "lease_expires_at": None}

    connection = MagicMock()
    connection.execute = AsyncMock(
        side_effect=[
            _FirstMappingResult(row),
            _FirstMappingResult(rowcount=1),
            _FirstMappingResult(updated),
        ]
    )
    ledger = HostCapacityReservationLedger(SimpleNamespace())  # type: ignore[arg-type]

    result, idempotent_replay = await ledger.transition_in_transaction(
        connection,
        reservation_id,
        owner="scheduler",
        target="COMMITTED",
        now=now,
    )

    assert result["state"] == "COMMITTED"
    assert idempotent_replay is False
    assert connection.execute.await_count == 3


@pytest.mark.asyncio
async def test_binding_settlement_updates_reservation_and_binding_on_one_connection() -> None:
    run_id = UUID("00000000-0000-4000-a000-000000000021")
    reservation_id = UUID("00000000-0000-4000-a000-000000000022")
    assigned = {
        "run_id": run_id,
        "owner": "dispatcher",
        "state": "ASSIGNED",
        "reservation_id": reservation_id,
    }
    committed = {**assigned, "state": "COMMITTED"}

    connection = MagicMock()
    connection.execute = AsyncMock(return_value=SimpleNamespace(rowcount=1))
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction
    storage = SimpleNamespace(engine=engine)
    service = WorkerRunHostBindingService(storage)  # type: ignore[arg-type]
    service.get = AsyncMock(side_effect=[assigned, committed])  # type: ignore[method-assign]
    service._fetch = AsyncMock(return_value=assigned)  # type: ignore[method-assign]
    service._ledger.transition_in_transaction = AsyncMock(  # type: ignore[method-assign]
        return_value=(assigned, False)
    )

    result = await service.commit(run_id, owner="dispatcher")

    transition = service._ledger.transition_in_transaction
    transition.assert_awaited_once()
    assert transition.await_args.args[0] is connection
    assert transition.await_args.kwargs["target"] == "COMMITTED"
    assert result["state"] == "COMMITTED"
    assert result["idempotent_replay"] is False


@pytest.mark.asyncio
async def test_binding_replay_repairs_one_sided_reservation_settlement() -> None:
    run_id = UUID("00000000-0000-4000-a000-000000000027")
    reservation_id = UUID("00000000-0000-4000-a000-000000000028")
    existing = {
        "run_id": run_id,
        "owner": "dispatcher",
        "state": "COMMITTED",
        "reservation_state": "RESERVED",
        "reservation_id": reservation_id,
    }
    settled = {**existing, "reservation_state": "COMMITTED"}

    connection = MagicMock()
    connection.execute = AsyncMock(return_value=SimpleNamespace(rowcount=1))
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction
    service = WorkerRunHostBindingService(SimpleNamespace(engine=engine))  # type: ignore[arg-type]
    service.get = AsyncMock(side_effect=[existing, settled])  # type: ignore[method-assign]
    service._fetch = AsyncMock(return_value=settled)  # type: ignore[method-assign]
    service._ledger.transition_in_transaction = AsyncMock(  # type: ignore[method-assign]
        return_value=(settled, False)
    )

    result = await service.commit(run_id, owner="dispatcher")

    service._ledger.transition_in_transaction.assert_awaited_once()  # type: ignore[attr-defined]
    assert result["reservation_state"] == "COMMITTED"
    assert result["idempotent_replay"] is True


@pytest.mark.asyncio
async def test_host_loss_reassignment_settles_replacement_on_one_connection() -> None:
    run_id = UUID("00000000-0000-4000-a000-000000000029")
    worker_id = UUID("00000000-0000-4000-a000-000000000030")
    old_reservation_id = UUID("00000000-0000-4000-a000-000000000031")
    new_reservation_id = UUID("00000000-0000-4000-a000-000000000032")
    existing = {
        "run_id": run_id,
        "worker_id": worker_id,
        "host_id": "worker-host-a",
        "reservation_id": old_reservation_id,
        "host_lease_generation": 1,
        "assignment_key": "binding-old",
        "owner": "dispatcher",
        "state": "COMMITTED",
        "reservation_state": "EXPIRED",
        "current_host_lease_valid": False,
        "metadata": {},
    }
    settled = {
        **existing,
        "host_id": "worker-host-b",
        "reservation_id": new_reservation_id,
        "host_lease_generation": 2,
        "assignment_key": "binding-recovery",
        "state": "COMMITTED",
        "reservation_state": "COMMITTED",
    }
    connection = MagicMock()
    connection.execute = AsyncMock(
        side_effect=[
            _FirstMappingResult({"state": "COMMITTED"}),
            SimpleNamespace(rowcount=1),
            SimpleNamespace(rowcount=1),
        ]
    )
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction
    storage = SimpleNamespace(
        engine=engine,
        get_worker_run=AsyncMock(return_value={"state": "QUEUED"}),
    )
    service = WorkerRunHostBindingService(storage)  # type: ignore[arg-type]
    service.get = AsyncMock(side_effect=[existing, settled])  # type: ignore[method-assign]
    service._scheduler.schedule = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "status": "RESERVED",
            "reservation": {
                "id": new_reservation_id,
                "host_uuid": UUID("00000000-0000-4000-a000-000000000033"),
                "host_id": "worker-host-b",
                "host_lease_generation": 2,
                "idempotent_replay": False,
            },
        }
    )
    service._ledger.transition_in_transaction = AsyncMock(  # type: ignore[method-assign]
        return_value=({}, False)
    )
    service._ledger.release = AsyncMock()  # type: ignore[method-assign]

    result = await service.reassign_after_host_loss(
        RunHostBindingRequest(
            run_id=run_id,
            worker_id=worker_id,
            assignment_key="binding-recovery",
            owner="dispatcher",
            placement=_placement(),
        )
    )

    transition = service._ledger.transition_in_transaction
    transition.assert_awaited_once()
    assert transition.await_args.args[0] is connection
    assert transition.await_args.kwargs["target"] == "COMMITTED"
    assert result["reservation_id"] == new_reservation_id
    service._ledger.release.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_host_loss_reassignment_cleans_up_new_same_host_reservation() -> None:
    run_id = UUID("00000000-0000-4000-a000-000000000034")
    worker_id = UUID("00000000-0000-4000-a000-000000000035")
    old_reservation_id = UUID("00000000-0000-4000-a000-000000000036")
    new_reservation_id = UUID("00000000-0000-4000-a000-000000000037")
    existing = {
        "run_id": run_id,
        "worker_id": worker_id,
        "host_id": "worker-host-a",
        "reservation_id": old_reservation_id,
        "host_lease_generation": 1,
        "assignment_key": "binding-old",
        "owner": "dispatcher",
        "state": "COMMITTED",
        "reservation_state": "EXPIRED",
        "current_host_lease_valid": False,
        "metadata": {},
    }
    storage = SimpleNamespace(get_worker_run=AsyncMock(return_value={"state": "QUEUED"}))
    service = WorkerRunHostBindingService(storage)  # type: ignore[arg-type]
    service.get = AsyncMock(return_value=existing)  # type: ignore[method-assign]
    service._scheduler.schedule = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "status": "RESERVED",
            "reservation": {
                "id": new_reservation_id,
                "host_uuid": UUID("00000000-0000-4000-a000-000000000038"),
                "host_id": "worker-host-a",
                "host_lease_generation": 2,
                "idempotent_replay": False,
            },
        }
    )
    service._ledger.release = AsyncMock(return_value={"state": "RELEASED"})  # type: ignore[method-assign]

    with pytest.raises(
        RunHostBindingRecoveryRejected,
        match="host_recovery_same_host_selected",
    ):
        await service.reassign_after_host_loss(
            RunHostBindingRequest(
                run_id=run_id,
                worker_id=worker_id,
                assignment_key="binding-recovery",
                owner="dispatcher",
                placement=_placement(),
            )
        )

    service._ledger.release.assert_awaited_once_with(  # type: ignore[attr-defined]
        new_reservation_id,
        owner="dispatcher",
    )


@pytest.mark.asyncio
async def test_failed_new_binding_releases_its_unbound_reservation() -> None:
    run_id = UUID("00000000-0000-4000-a000-000000000023")
    worker_id = UUID("00000000-0000-4000-a000-000000000024")
    reservation_id = UUID("00000000-0000-4000-a000-000000000025")
    connection = MagicMock()
    connection.execute = AsyncMock(
        side_effect=RunHostBindingRejected("run_not_found")
    )
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction
    storage = SimpleNamespace(engine=engine)
    service = WorkerRunHostBindingService(storage)  # type: ignore[arg-type]
    service.get = AsyncMock(return_value=None)  # type: ignore[method-assign]
    service._run_worker_id = AsyncMock(return_value=worker_id)  # type: ignore[method-assign]
    service._scheduler.schedule = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "status": "RESERVED",
            "reservation": {
                "id": reservation_id,
                "host_uuid": UUID("00000000-0000-4000-a000-000000000026"),
                "host_lease_generation": 1,
            },
        }
    )
    service._ledger.release = AsyncMock(return_value={"state": "RELEASED"})  # type: ignore[method-assign]

    with pytest.raises(RunHostBindingRejected, match="run_not_found"):
        await service.assign(
            RunHostBindingRequest(
                run_id=run_id,
                worker_id=worker_id,
                assignment_key="binding-compensation",
                owner="dispatcher",
                placement=_placement(),
            )
        )

    service._ledger.release.assert_awaited_once_with(  # type: ignore[attr-defined]
        reservation_id,
        owner="dispatcher",
    )
