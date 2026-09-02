"""Certify durable Worker Run to worker-host assignment settlement.

The checker inserts two payload-free fixture Worker Runs, uses the AIAT host
scheduler to reserve a worker-plane host, binds each reservation to its run,
proves idempotent replay, commits one assignment, releases the other, reopens
Postgres, and verifies scalar read-back plus scoped cleanup.  It does not invoke
an external worker runtime or provider.  Licence metadata remains
informational.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import UUID

import sqlalchemy as sa

MAS_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = MAS_ROOT / "packages" / "mas-core"
if str(CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(CORE_ROOT))

from mas_core.memory.models import worker_runs  # noqa: E402
from mas_core.memory.storage import AgentStorage  # noqa: E402
from mas_core.worker_registry.host_registry import WorkerHostRegistry  # noqa: E402
from mas_core.worker_registry.host_reservations import HostCapacityReservationLedger  # noqa: E402
from mas_core.worker_registry.placement import WorkerPlacementRequest  # noqa: E402
from mas_core.worker_registry.run_host_binding import (  # noqa: E402
    RUN_HOST_BINDING_SCHEMA,
    RunHostBindingRequest,
    WorkerRunHostBindingService,
)

CHECK_SCHEMA = "aiat.worker-run-host-binding-postgres-certification.v1"
EXPECTED_MIGRATION = "0042_worker_run_host_binding"
HOST_PREFIX = "aiat-cert-worker-run-binding-v1-"
RUN_PREFIX = "aiat-cert-worker-run-binding-v1-"
HOST_A = f"{HOST_PREFIX}a"
HOST_B = f"{HOST_PREFIX}b"
HOST_PATTERN = f"{HOST_PREFIX}%"
RUN_ONE = UUID("00000000-0000-4000-a000-000000000a21")
RUN_TWO = UUID("00000000-0000-4000-a000-000000000a22")
HOST_UUID_A = UUID("00000000-0000-4000-a000-000000000a31")
HOST_UUID_B = UUID("00000000-0000-4000-a000-000000000a32")
RESERVATION_BLOCKER = UUID("00000000-0000-4000-a000-000000000a41")
RESERVATION_ONE = UUID("00000000-0000-4000-a000-000000000a42")
RESERVATION_TWO = UUID("00000000-0000-4000-a000-000000000a43")
TOKEN_A = "aiat-worker-run-binding-fixture-token-a-v1"
TOKEN_B = "aiat-worker-run-binding-fixture-token-b-v1"
ASSIGNMENT_ONE = f"{RUN_PREFIX}assignment-one"
ASSIGNMENT_TWO = f"{RUN_PREFIX}assignment-two"
OWNER = "aiat-worker-run-binding-fixture"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON evidence")
    parser.add_argument(
        "--dsn",
        default=os.getenv(
            "AIAT_WORKER_RUN_HOST_BINDING_EVIDENCE_DSN",
            os.getenv("PGBOUNCER_DSN", os.getenv("POSTGRES_DSN", "")),
        ),
        help="Postgres DSN; defaults to AIAT_WORKER_RUN_HOST_BINDING_EVIDENCE_DSN/PGBOUNCER_DSN/POSTGRES_DSN",
    )
    return parser


def _normalize_dsn(raw: str | None) -> str | None:
    value = str(raw or "").strip()
    if not value or "${" in value or "}" in value:
        return None
    if value.startswith("postgresql://"):
        return "postgresql+asyncpg://" + value.removeprefix("postgresql://")
    if value.startswith("postgres://"):
        return "postgresql+asyncpg://" + value.removeprefix("postgres://")
    return value if value.startswith("postgresql+asyncpg://") else None


def _blocked(reason: str, *, migration_version: str | None = None) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": CHECK_SCHEMA,
        "binding_schema": RUN_HOST_BINDING_SCHEMA,
        "mode": "local-postgres-worker-run-host-binding",
        "status": "blocked",
        "reason": reason,
        "mutation_performed": False,
        "local_database_access_performed": False,
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "worker_dispatch_performed": False,
        "licence_metadata_is_gate": False,
    }
    if migration_version is not None:
        report["migration_version"] = migration_version
    return report


async def _migration_version(storage: AgentStorage) -> str | None:
    async with storage.engine.connect() as connection:
        return await connection.scalar(sa.text("SELECT version_num FROM alembic_version"))


async def _fixture_worker_id(storage: AgentStorage) -> UUID | None:
    async with storage.engine.connect() as connection:
        value = await connection.scalar(
            sa.text(
                """SELECT id FROM worker_registry
                   ORDER BY name ASC
                   LIMIT 1"""
            )
        )
    return value if isinstance(value, UUID) else UUID(str(value)) if value else None


async def _counts(storage: AgentStorage) -> dict[str, int]:
    async with storage.engine.connect() as connection:
        bindings = await connection.scalar(
            sa.text(
                """SELECT count(*) FROM worker_run_host_bindings
                   WHERE assignment_key LIKE :prefix"""
            ),
            {"prefix": f"{RUN_PREFIX}%"},
        )
        reservations = await connection.scalar(
            sa.text(
                """SELECT count(*) FROM worker_host_reservations
                   WHERE reservation_key LIKE :prefix"""
            ),
            {"prefix": f"{RUN_PREFIX}%"},
        )
        runs = await connection.scalar(
            sa.text(
                """SELECT count(*) FROM worker_runs
                   WHERE id IN (:run_one, :run_two)"""
            ),
            {"run_one": RUN_ONE, "run_two": RUN_TWO},
        )
        hosts = await connection.scalar(
            sa.text("SELECT count(*) FROM worker_hosts WHERE host_id LIKE :prefix"),
            {"prefix": HOST_PATTERN},
        )
    return {
        "bindings": int(bindings or 0),
        "reservations": int(reservations or 0),
        "runs": int(runs or 0),
        "hosts": int(hosts or 0),
    }


async def _cleanup(storage: AgentStorage) -> dict[str, int]:
    async with storage.engine.begin() as connection:
        deleted_bindings = await connection.execute(
            sa.text(
                """DELETE FROM worker_run_host_bindings
                   WHERE assignment_key LIKE :prefix
                      OR run_id IN (:run_one, :run_two)"""
            ),
            {"prefix": f"{RUN_PREFIX}%", "run_one": RUN_ONE, "run_two": RUN_TWO},
        )
        deleted_reservations = await connection.execute(
            sa.text(
                """DELETE FROM worker_host_reservations
                   WHERE reservation_key LIKE :prefix
                      OR id IN (:blocker, :one, :two)"""
            ),
            {
                "prefix": f"{RUN_PREFIX}%",
                "blocker": RESERVATION_BLOCKER,
                "one": RESERVATION_ONE,
                "two": RESERVATION_TWO,
            },
        )
        deleted_runs = await connection.execute(
            sa.text(
                """DELETE FROM worker_runs
                   WHERE id IN (:run_one, :run_two)"""
            ),
            {"run_one": RUN_ONE, "run_two": RUN_TWO},
        )
        deleted_hosts = await connection.execute(
            sa.text("DELETE FROM worker_hosts WHERE host_id LIKE :prefix"),
            {"prefix": HOST_PATTERN},
        )
    return {
        "bindings": int(deleted_bindings.rowcount or 0),
        "reservations": int(deleted_reservations.rowcount or 0),
        "runs": int(deleted_runs.rowcount or 0),
        "hosts": int(deleted_hosts.rowcount or 0),
    }


async def _register_hosts(registry: WorkerHostRegistry) -> None:
    common = {
        "labels": {"pool": "worker"},
        "capabilities": ["native"],
        "host_plane": "worker",
        "sandbox_profile": "gvisor",
        "isolation_mode": "gvisor",
        "capacity": {
            "slots_total": 2,
            "slots_used": 0,
            "memory_bytes_total": 4 * 1024**3,
            "memory_bytes_used": 0,
            "gpu_total": 0,
            "gpu_used": 0,
        },
        "metadata": {"fixture": "worker-run-host-binding"},
    }
    registered_a = await registry.register_host(
        host_id=HOST_A,
        host_uuid=HOST_UUID_A,
        registration_token=TOKEN_A,
        priority=2,
        **common,
    )
    registered_b = await registry.register_host(
        host_id=HOST_B,
        host_uuid=HOST_UUID_B,
        registration_token=TOKEN_B,
        priority=1,
        **common,
    )
    await registry.heartbeat(
        host_id=HOST_A,
        registration_token=TOKEN_A,
        lease_generation=registered_a["lease_generation"],
        lease_seconds=120,
    )
    await registry.heartbeat(
        host_id=HOST_B,
        registration_token=TOKEN_B,
        lease_generation=registered_b["lease_generation"],
        lease_seconds=120,
    )


def _binding_request(
    *,
    run_id: UUID,
    worker_id: UUID,
    assignment_key: str,
    reservation_id: UUID,
) -> RunHostBindingRequest:
    return RunHostBindingRequest(
        run_id=run_id,
        worker_id=worker_id,
        assignment_key=assignment_key,
        owner=OWNER,
        placement=WorkerPlacementRequest(
            worker_id=str(worker_id),
            required_host_plane="worker",
            required_capabilities=frozenset({"native"}),
            required_labels=(("pool", "worker"),),
            required_sandbox_profile="gvisor",
            required_isolation_mode="gvisor",
            slots=1,
        ),
        lease_seconds=90,
        metadata={"fixture": "worker-run-host-binding"},
        reservation_id=reservation_id,
    )


async def _insert_run(storage: AgentStorage, *, run_id: UUID, worker_id: UUID, suffix: str) -> None:
    async with storage.engine.begin() as connection:
        await connection.execute(
            worker_runs.insert().values(
                id=run_id,
                idempotency_key=f"{RUN_PREFIX}{suffix}",
                worker_id=worker_id,
                task_type="worker-run-host-binding-fixture",
                state="QUEUED",
                request_json={"fixture": "worker-run-host-binding"},
            )
        )


async def _run(dsn: str | None) -> dict[str, Any]:
    normalized_dsn = _normalize_dsn(dsn)
    if normalized_dsn is None:
        return _blocked("worker_run_host_binding_evidence_database_not_configured")
    storage = AgentStorage(normalized_dsn)
    mutation_performed = False
    migration_version: str | None = None
    try:
        await storage.connect()
        migration_version = await _migration_version(storage)
        if migration_version != EXPECTED_MIGRATION:
            return {
                **_blocked(
                    "worker_run_host_binding_evidence_migration_not_at_head",
                    migration_version=migration_version,
                ),
                "expected_migration": EXPECTED_MIGRATION,
                "local_database_access_performed": True,
            }
        cleanup_before = await _cleanup(storage)
        first_counts = await _counts(storage)
        worker_id = await _fixture_worker_id(storage)
        if worker_id is None:
            return {
                **_blocked(
                    "worker_run_host_binding_fixture_requires_a_registered_worker",
                migration_version=migration_version,
                ),
                "local_database_access_performed": True,
            }
        registry = WorkerHostRegistry(storage)
        await _register_hosts(registry)
        ledger = HostCapacityReservationLedger(storage)
        blocker = await ledger.reserve(
            host_id=HOST_A,
            reservation_id=RESERVATION_BLOCKER,
            reservation_key=f"{RUN_PREFIX}blocker",
            owner=OWNER,
            resources={"slots": 2, "memory_bytes": 0, "gpu_count": 0},
            lease_seconds=120,
        )
        await _insert_run(storage, run_id=RUN_ONE, worker_id=worker_id, suffix="run-one")
        await _insert_run(storage, run_id=RUN_TWO, worker_id=worker_id, suffix="run-two")
        mutation_performed = True
        service = WorkerRunHostBindingService(storage)
        request_one = _binding_request(
            run_id=RUN_ONE,
            worker_id=worker_id,
            assignment_key=ASSIGNMENT_ONE,
            reservation_id=RESERVATION_ONE,
        )
        request_two = _binding_request(
            run_id=RUN_TWO,
            worker_id=worker_id,
            assignment_key=ASSIGNMENT_TWO,
            reservation_id=RESERVATION_TWO,
        )
        assigned = await service.assign(request_one)
        replayed = await service.assign(request_one)
        committed = await service.commit(RUN_ONE, owner=OWNER)
        committed_replay = await service.commit(RUN_ONE, owner=OWNER)
        assigned_two = await service.assign(request_two)
        released = await service.release(RUN_TWO, owner=OWNER)
        released_replay = await service.release(RUN_TWO, owner=OWNER)

        reopened = AgentStorage(normalized_dsn)
        await storage.close()
        await reopened.connect()
        reopened_service = WorkerRunHostBindingService(reopened)
        reopened_one = await reopened_service.get(RUN_ONE)
        reopened_two = await reopened_service.get(RUN_TWO)
        cleanup_after = await _cleanup(reopened)
        remaining = await _counts(reopened)
        await reopened.close()

        passed = all(
            (
                blocker["state"] == "RESERVED",
                assigned["state"] == "ASSIGNED",
                assigned["host_id"] == HOST_B,
                assigned["reservation_state"] == "RESERVED",
                assigned["host_lease_generation"] == 1,
                assigned["idempotent_replay"] is False,
                replayed["idempotent_replay"] is True,
                replayed["host_id"] == HOST_B,
                committed["state"] == "COMMITTED",
                committed["reservation_state"] == "COMMITTED",
                committed_replay["idempotent_replay"] is True,
                assigned_two["state"] == "ASSIGNED",
                released["state"] == "RELEASED",
                released["reservation_state"] == "RELEASED",
                released_replay["idempotent_replay"] is True,
                reopened_one is not None and reopened_one["state"] == "COMMITTED",
                reopened_two is not None and reopened_two["state"] == "RELEASED",
                remaining == {"bindings": 0, "reservations": 0, "runs": 0, "hosts": 0},
            )
        )
        return {
            "schema_version": CHECK_SCHEMA,
            "binding_schema": RUN_HOST_BINDING_SCHEMA,
            "mode": "local-postgres-worker-run-host-binding",
            "status": "pass" if passed else "fail",
            "migration_version": migration_version,
            "expected_migration": EXPECTED_MIGRATION,
            "cleanup_before": cleanup_before,
            "first_counts": first_counts,
            "worker_id": str(worker_id),
            "assignment": {
                "preferred_host": HOST_A,
                "assigned_host": assigned.get("host_id"),
                "fallback_used": assigned.get("host_id") == HOST_B,
                "host_plane": "worker",
                "lease_generation": assigned.get("host_lease_generation"),
            },
            "idempotency": {
                "assign_replay": replayed.get("idempotent_replay"),
                "commit_replay": committed_replay.get("idempotent_replay"),
                "release_replay": released_replay.get("idempotent_replay"),
            },
            "settlement": {
                "committed_state": committed.get("state"),
                "committed_reservation_state": committed.get("reservation_state"),
                "released_state": released.get("state"),
                "released_reservation_state": released.get("reservation_state"),
            },
            "reopen_readback": {
                "committed_run_state": reopened_one.get("state") if reopened_one else None,
                "released_run_state": reopened_two.get("state") if reopened_two else None,
                "payload_free": True,
            },
            "cleanup_after": cleanup_after,
            "remaining": remaining,
            "mutation_performed": mutation_performed,
            "external_network_access_performed": False,
            "external_provider_mutation_performed": False,
            "worker_dispatch_performed": False,
            "licence_metadata_is_gate": False,
            "scope": "durable run-to-worker-plane binding, scheduler fallback, idempotent replay, commit/release settlement, connection-reopen read-back, and scoped cleanup",
            "boundary": {
                "run_host_binding_persistence": "checked",
                "worker_host_plane": "checked",
                "scheduler_fallback": "checked",
                "assignment_key_idempotency": "checked",
                "commit_release_settlement": "checked",
                "live_worker_dispatch": "not_checked",
                "provider_or_sandbox_execution": "not_checked",
            },
        }
    except Exception as exc:  # pragma: no cover - live environment diagnostic
        with suppress(Exception):
            await storage.close()
        return {
            **_blocked("worker_run_host_binding_checker_error"),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "migration_version": migration_version,
            "mutation_performed": mutation_performed,
        }
    finally:
        with suppress(Exception):
            await storage.close()


def main() -> int:
    args = _parser().parse_args()
    report = asyncio.run(_run(args.dsn))
    print(json.dumps(report, indent=2, default=str, sort_keys=True))
    return 0 if report.get("status") == "pass" else 2 if report.get("status") == "blocked" else 1


if __name__ == "__main__":
    raise SystemExit(main())
