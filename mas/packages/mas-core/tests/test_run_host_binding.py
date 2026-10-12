from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from mas_core.worker_registry.host_reservations import HostCapacityReservationLedger
from mas_core.worker_registry.placement import WorkerPlacementRequest
from mas_core.worker_registry.run_host_binding import (
    RUN_HOST_BINDING_SCHEMA,
    RunHostBindingIdentity,
    RunHostBindingRecoveryRejected,
    RunHostBindingRejected,
    RunHostBindingRequest,
    WorkerRunHostBindingService,
    public_run_host_binding,
)

RELEASE_HOST_UUID = UUID("00000000-0000-4000-a000-000000000051")
RELEASE_RESERVATION_ID = UUID("00000000-0000-4000-a000-000000000052")


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


class _AllMappingResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def mappings(self) -> _AllMappingResult:
        return self

    def all(self) -> list[dict[str, object]]:
        return self.rows


@pytest.mark.asyncio
async def test_host_queue_read_is_exact_host_fenced_bounded_and_payload_free() -> None:
    now = datetime.now(tz=UTC)
    row = {
        "id": UUID("00000000-0000-4000-a000-000000000040"),
        "run_id": UUID("00000000-0000-4000-a000-000000000041"),
        "worker_id": UUID("00000000-0000-4000-a000-000000000042"),
        "host_id": UUID("00000000-0000-4000-a000-000000000043"),
        "host_key": "worker-host-a",
        "host_plane": "worker",
        "host_status": "READY",
        "reservation_id": UUID("00000000-0000-4000-a000-000000000044"),
        "host_lease_generation": 3,
        "current_host_lease_generation": 3,
        "current_host_lease_expires_at": now + timedelta(minutes=1),
        "assignment_key": "run-host-queue-1",
        "owner": "worker-host-a",
        "state": "COMMITTED",
        "reservation_state": "COMMITTED",
        "metadata": {"authorization": "secret-like-value"},
        "created_at": now,
        "committed_at": now,
        "released_at": None,
        # A permissive fake result must not cause request contents to escape.
        "task_input": {"prompt": "private task input"},
    }
    connection = MagicMock()
    connection.execute = AsyncMock(return_value=_AllMappingResult([row]))
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.connect.return_value = transaction
    service = WorkerRunHostBindingService(SimpleNamespace(engine=engine))  # type: ignore[arg-type]
    service._now = lambda: now  # type: ignore[method-assign]

    result = await service.list_queued_committed_for_host("worker-host-a", limit=7)

    assert len(result) == 1
    assert result[0]["host_id"] == "worker-host-a"
    assert result[0]["state"] == "COMMITTED"
    assert result[0]["reservation_state"] == "COMMITTED"
    assert result[0]["current_host_lease_valid"] is True
    assert result[0]["metadata"] == {}
    assert "task_input" not in result[0]
    assert "result_json" not in result[0]

    query = connection.execute.await_args.args[0]
    compiled = query.compile(dialect=postgresql.dialect())
    sql = " ".join(str(compiled).replace('"', "").split())
    params = compiled.params
    assert params["host_id_1"] == "worker-host-a"
    assert params["host_plane_1"] == "worker"
    assert params["status_1"] == "READY"
    assert params["state_1"] == "COMMITTED"
    assert params["state_2"] == "COMMITTED"
    assert params["state_3"] == "QUEUED"
    assert params["lease_expires_at_1"] == now
    assert params["param_1"] == 7
    assert "worker_hosts.host_id = %(host_id_1)s" in sql
    assert "worker_hosts.host_plane = %(host_plane_1)s" in sql
    assert "worker_hosts.status = %(status_1)s" in sql
    assert "worker_hosts.lease_expires_at IS NOT NULL" in sql
    assert "worker_hosts.lease_expires_at > %(lease_expires_at_1)s" in sql
    assert "worker_run_host_bindings.host_lease_generation = worker_hosts.lease_generation" in sql
    assert "worker_run_host_bindings.state = %(state_1)s" in sql
    assert "worker_host_reservations.state = %(state_2)s" in sql
    assert "worker_runs.state = %(state_3)s" in sql
    assert "ORDER BY worker_run_host_bindings.created_at ASC, worker_run_host_bindings.id ASC" in sql
    assert "LIMIT %(param_1)s" in sql
    assert "worker_run_host_bindings.metadata" not in sql
    assert "worker_runs.request_json" not in sql
    assert "worker_runs.result_json" not in sql


@pytest.mark.asyncio
async def test_binding_release_lock_targets_binding_row() -> None:
    now = datetime.now(tz=UTC)
    connection = MagicMock()
    connection.execute = AsyncMock(
        return_value=_FirstMappingResult(
            {
                "id": UUID("00000000-0000-4000-a000-000000000045"),
                "run_id": UUID("00000000-0000-4000-a000-000000000046"),
                "worker_id": UUID("00000000-0000-4000-a000-000000000047"),
                "host_id": UUID("00000000-0000-4000-a000-000000000048"),
                "host_key": "worker-host-a",
                "host_plane": "worker",
                "host_status": "READY",
                "lease_generation": 3,
                "current_host_lease_generation": 3,
                "current_host_lease_expires_at": now + timedelta(seconds=30),
                "reservation_id": UUID("00000000-0000-4000-a000-000000000049"),
                "reservation_state": "COMMITTED",
                "reservation_lease_expires_at": None,
                "host_lease_generation": 3,
                "assignment_key": "run-host-lock-test",
                "owner": "host-executor",
                "state": "COMMITTED",
            }
        )
    )
    service = WorkerRunHostBindingService(SimpleNamespace())  # type: ignore[arg-type]

    await service._fetch(
        connection,
        run_id=UUID("00000000-0000-4000-a000-000000000046"),
        for_update=True,
    )

    statement = connection.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = " ".join(str(compiled).replace('"', "").split())
    assert "FOR UPDATE OF worker_run_host_bindings" in sql


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("host_id", "limit"),
    [("", 1), ("worker-host-a", 0), ("worker-host-a", 1001), ("worker-host-a", True)],
)
async def test_host_queue_read_rejects_invalid_host_or_limit(
    host_id: str,
    limit: int,
) -> None:
    service = WorkerRunHostBindingService(SimpleNamespace())  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        await service.list_queued_committed_for_host(host_id, limit=limit)


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


def _release_binding_rows(
    *,
    state: str = "COMMITTED",
    reservation_state: str = "COMMITTED",
    host_uuid: UUID = RELEASE_HOST_UUID,
    reservation_id: UUID = RELEASE_RESERVATION_ID,
    host_lease_generation: int = 4,
) -> tuple[dict[str, object], dict[str, object]]:
    binding_id = UUID("00000000-0000-4000-a000-000000000050")
    run_id = UUID("00000000-0000-4000-a000-000000000053")
    public_row: dict[str, object] = {
        "id": binding_id,
        "run_id": run_id,
        "owner": "host-executor",
        "host_id": "worker-host-a",
        "host_uuid": host_uuid,
        "reservation_id": reservation_id,
        "host_lease_generation": host_lease_generation,
        "state": state,
        "reservation_state": reservation_state,
    }
    locked_row = {
        **public_row,
        "host_id": host_uuid,
        "reservation_state": reservation_state,
    }
    return public_row, locked_row


def _binding_test_engine() -> tuple[MagicMock, MagicMock]:
    connection = MagicMock()
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction
    return connection, engine


@pytest.mark.asyncio
async def test_binding_release_updates_exact_assignment_identity() -> None:
    public_row, locked_row = _release_binding_rows()
    released_row = {**public_row, "state": "RELEASED", "reservation_state": "RELEASED"}
    identity = RunHostBindingIdentity.from_binding(public_row)
    connection, engine = _binding_test_engine()
    connection.execute = AsyncMock(return_value=SimpleNamespace(rowcount=1))
    service = WorkerRunHostBindingService(SimpleNamespace(engine=engine))  # type: ignore[arg-type]
    service.get = AsyncMock(return_value=released_row)  # type: ignore[method-assign]
    service._fetch = AsyncMock(return_value=locked_row)  # type: ignore[method-assign]
    now = datetime.now(tz=UTC)
    service._now = lambda: now  # type: ignore[method-assign]
    service._ledger.transition_in_transaction = AsyncMock(  # type: ignore[method-assign]
        return_value=(locked_row, False)
    )

    result = await service.release(
        public_row["run_id"],
        owner="host-executor",
        expected_identity=identity,
    )

    assert result["state"] == "RELEASED"
    assert result["idempotent_replay"] is False
    service._fetch.assert_awaited_once_with(
        connection,
        run_id=public_row["run_id"],
        for_update=True,
    )
    service._ledger.transition_in_transaction.assert_awaited_once_with(  # type: ignore[attr-defined]
        connection,
        identity.reservation_id,
        owner="host-executor",
        target="RELEASED",
        now=now,
    )
    statement = connection.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = " ".join(str(compiled).replace('"', "").split())
    params = list(compiled.params.values())
    for field in (
        "id",
        "run_id",
        "owner",
        "reservation_id",
        "host_id",
        "host_lease_generation",
    ):
        assert f"worker_run_host_bindings.{field} =" in sql
    assert identity.binding_id in params
    assert identity.reservation_id in params
    assert identity.host_uuid in params
    assert identity.host_lease_generation in params


@pytest.mark.asyncio
async def test_shutdown_release_locks_and_checks_worker_claim_fence_atomically() -> None:
    public_row, locked_row = _release_binding_rows()
    identity = RunHostBindingIdentity.from_binding(public_row)
    execution_result = MagicMock()
    execution_result.mappings.return_value.first.return_value = {
        "claim_owner": "host-executor",
        "attempt_count": 7,
    }
    connection, engine = _binding_test_engine()
    connection.execute = AsyncMock(
        side_effect=[execution_result, SimpleNamespace(rowcount=1)]
    )
    service = WorkerRunHostBindingService(SimpleNamespace(engine=engine))  # type: ignore[arg-type]
    released_row = {
        **public_row,
        "state": "RELEASED",
        "reservation_state": "RELEASED",
    }
    service.get = AsyncMock(return_value=released_row)  # type: ignore[method-assign]
    service._fetch = AsyncMock(return_value=locked_row)  # type: ignore[method-assign]
    service._ledger.transition_in_transaction = AsyncMock(  # type: ignore[method-assign]
        return_value=(locked_row, False)
    )

    result = await service.release(
        public_row["run_id"],
        owner="host-executor",
        expected_identity=identity,
        expected_claim_owner="host-executor",
        expected_attempt_count=7,
    )

    assert result["state"] == "RELEASED"
    execution_query = connection.execute.await_args_list[0].args[0]
    compiled_query = execution_query.compile(dialect=postgresql.dialect())
    query_sql = " ".join(str(compiled_query).replace('"', "").split())
    assert "FROM worker_runs" in query_sql
    assert "FOR UPDATE" in query_sql
    service._ledger.transition_in_transaction.assert_awaited_once()  # type: ignore[attr-defined]
    assert connection.execute.await_count == 2


@pytest.mark.asyncio
async def test_shutdown_release_does_not_release_binding_after_replacement_claim() -> None:
    public_row, locked_row = _release_binding_rows()
    identity = RunHostBindingIdentity.from_binding(public_row)
    execution_result = MagicMock()
    execution_result.mappings.return_value.first.return_value = {
        "claim_owner": "replacement-executor",
        "attempt_count": 8,
    }
    connection, engine = _binding_test_engine()
    connection.execute = AsyncMock(return_value=execution_result)
    service = WorkerRunHostBindingService(SimpleNamespace(engine=engine))  # type: ignore[arg-type]
    service._fetch = AsyncMock(return_value=locked_row)  # type: ignore[method-assign]
    service._ledger.transition_in_transaction = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(RunHostBindingRejected) as caught:
        await service.release(
            public_row["run_id"],
            owner="host-executor",
            expected_identity=identity,
            expected_claim_owner="host-executor",
            expected_attempt_count=7,
        )

    assert caught.value.reason_code == "run_host_binding_execution_fence_mismatch"
    service._ledger.transition_in_transaction.assert_not_awaited()  # type: ignore[attr-defined]
    connection.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_binding_release_does_not_touch_replacement_reservation() -> None:
    public_row, _locked_old_row = _release_binding_rows()
    identity = RunHostBindingIdentity.from_binding(public_row)
    _, locked_replacement = _release_binding_rows(
        host_uuid=UUID("00000000-0000-4000-a000-000000000061"),
        reservation_id=UUID("00000000-0000-4000-a000-000000000062"),
        host_lease_generation=5,
    )
    connection, engine = _binding_test_engine()
    connection.execute = AsyncMock(return_value=SimpleNamespace(rowcount=1))
    service = WorkerRunHostBindingService(SimpleNamespace(engine=engine))  # type: ignore[arg-type]
    service.get = AsyncMock(return_value=public_row)  # type: ignore[method-assign]
    service._fetch = AsyncMock(return_value=locked_replacement)  # type: ignore[method-assign]
    service._ledger.transition_in_transaction = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(RunHostBindingRejected) as caught:
        await service.release(
            public_row["run_id"],
            owner="host-executor",
            expected_identity=identity,
        )

    assert caught.value.reason_code == "run_host_binding_release_fence_mismatch"
    service._ledger.transition_in_transaction.assert_not_awaited()  # type: ignore[attr-defined]
    connection.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_binding_release_replay_is_idempotent_for_same_assignment() -> None:
    released_row, locked_row = _release_binding_rows(
        state="RELEASED",
        reservation_state="RELEASED",
    )
    identity = RunHostBindingIdentity.from_binding(released_row)
    connection, engine = _binding_test_engine()
    connection.execute = AsyncMock(return_value=SimpleNamespace(rowcount=1))
    service = WorkerRunHostBindingService(SimpleNamespace(engine=engine))  # type: ignore[arg-type]
    service.get = AsyncMock(return_value=released_row)  # type: ignore[method-assign]
    service._fetch = AsyncMock(return_value=locked_row)  # type: ignore[method-assign]
    service._ledger.transition_in_transaction = AsyncMock()  # type: ignore[method-assign]

    result = await service.release(
        released_row["run_id"],
        owner="host-executor",
        expected_identity=identity,
    )

    assert result["state"] == "RELEASED"
    assert result["idempotent_replay"] is True
    service._ledger.transition_in_transaction.assert_not_awaited()  # type: ignore[attr-defined]
    connection.execute.assert_not_awaited()


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
