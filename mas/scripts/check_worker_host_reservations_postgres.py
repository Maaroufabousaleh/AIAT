"""Certify durable worker-host capacity reservation and settlement semantics.

The checker registers one reserved host through the canonical host registry,
renews its lease, and drives the AIAT-owned reservation ledger through
idempotent replay, row-locked capacity exhaustion, commit, release, expiry,
capacity projection, connection-reopen read-back, and scoped cleanup.  It does
not select among hosts, dispatch workers, call providers, or claim host-loss
recovery.  Licence metadata remains informational.
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

from mas_core.memory.storage import AgentStorage  # noqa: E402
from mas_core.worker_registry.host_registry import WorkerHostRegistry  # noqa: E402
from mas_core.worker_registry.host_reservations import (  # noqa: E402
    RESERVATION_SCHEMA,
    HostCapacityReservationLedger,
    ReservationRejected,
)

CHECK_SCHEMA = "aiat.worker-host-reservations-postgres-certification.v1"
EXPECTED_MIGRATION = "0042_worker_run_host_binding"
HOST_ID = "aiat-cert-worker-host-reservations-v1"
HOST_PREFIX = f"{HOST_ID}%"
HOST_UUID = UUID("00000000-0000-4000-a000-000000000981")
RESERVATION_A = UUID("00000000-0000-4000-a000-000000000982")
RESERVATION_B = UUID("00000000-0000-4000-a000-000000000983")
RESERVATION_C = UUID("00000000-0000-4000-a000-000000000984")
TOKEN = "aiat-host-reservations-fixture-token-v1"
OWNER = "aiat-scheduler-fixture"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON evidence")
    parser.add_argument(
        "--dsn",
        default=os.getenv(
            "AIAT_WORKER_HOST_RESERVATIONS_EVIDENCE_DSN",
            os.getenv("PGBOUNCER_DSN", os.getenv("POSTGRES_DSN", "")),
        ),
        help="Postgres DSN; defaults to AIAT_WORKER_HOST_RESERVATIONS_EVIDENCE_DSN/PGBOUNCER_DSN/POSTGRES_DSN",
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
        "mode": "local-postgres-worker-host-reservations",
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


async def _counts(storage: AgentStorage) -> dict[str, int]:
    async with storage.engine.connect() as connection:
        hosts = await connection.scalar(
            sa.text("SELECT count(*) FROM worker_hosts WHERE host_id LIKE :prefix"),
            {"prefix": HOST_PREFIX},
        )
        reservations = await connection.scalar(
            sa.text(
                """SELECT count(*) FROM worker_host_reservations
                   WHERE host_id IN (SELECT id FROM worker_hosts WHERE host_id LIKE :prefix)"""
            ),
            {"prefix": HOST_PREFIX},
        )
    return {"hosts": int(hosts or 0), "reservations": int(reservations or 0)}


async def _cleanup(storage: AgentStorage) -> dict[str, int]:
    async with storage.engine.begin() as connection:
        deleted_reservations = await connection.execute(
            sa.text(
                """DELETE FROM worker_host_reservations
                   WHERE id IN (:reservation_a, :reservation_b, :reservation_c)
                      OR host_id IN (SELECT id FROM worker_hosts WHERE host_id LIKE :prefix)"""
            ),
            {
                "reservation_a": RESERVATION_A,
                "reservation_b": RESERVATION_B,
                "reservation_c": RESERVATION_C,
                "prefix": HOST_PREFIX,
            },
        )
        deleted_hosts = await connection.execute(
            sa.text("DELETE FROM worker_hosts WHERE host_id LIKE :prefix"),
            {"prefix": HOST_PREFIX},
        )
    return {
        "reservations": int(deleted_reservations.rowcount or 0),
        "hosts": int(deleted_hosts.rowcount or 0),
    }


async def _expire_reservation(storage: AgentStorage, reservation_id: UUID) -> None:
    async with storage.engine.begin() as connection:
        result = await connection.execute(
            sa.text(
                """UPDATE worker_host_reservations
                   SET lease_expires_at = now() - interval '1 second'
                   WHERE id = :reservation_id AND state = 'RESERVED'"""
            ),
            {"reservation_id": reservation_id},
        )
    if result.rowcount != 1:
        raise RuntimeError("reserved fixture reservation was not active before expiry")


async def _run(dsn: str | None) -> dict[str, Any]:
    normalized_dsn = _normalize_dsn(dsn)
    if normalized_dsn is None:
        return _blocked("worker_host_reservations_evidence_database_not_configured")
    storage = AgentStorage(normalized_dsn)
    registry = WorkerHostRegistry(storage)
    ledger = HostCapacityReservationLedger(storage)
    migration_version: str | None = None
    mutation_performed = False
    try:
        await storage.connect()
        migration_version = await _migration_version(storage)
        if migration_version != EXPECTED_MIGRATION:
            return {
                **_blocked(
                    "worker_host_reservations_evidence_migration_not_at_head",
                    migration_version=migration_version,
                ),
                "expected_migration": EXPECTED_MIGRATION,
                "local_database_access_performed": True,
            }
        cleanup_before = await _cleanup(storage)
        first_counts = await _counts(storage)
        registered = await registry.register_host(
            host_id=HOST_ID,
            host_uuid=HOST_UUID,
            registration_token=TOKEN,
            labels={"zone": "local", "pool": "worker"},
            capabilities=["native"],
            sandbox_profile="gvisor",
            isolation_mode="gvisor",
            capacity={
                "slots_total": 2,
                "slots_used": 0,
                "memory_bytes_total": 4 * 1024**3,
                "memory_bytes_used": 0,
                "gpu_total": 1,
                "gpu_used": 0,
            },
            priority=1,
            metadata={"fixture": "host-reservations"},
        )
        await registry.heartbeat(
            host_id=HOST_ID,
            registration_token=TOKEN,
            lease_generation=registered["lease_generation"],
            lease_seconds=120,
        )
        mutation_performed = True

        reservation_a = await ledger.reserve(
            host_id=HOST_ID,
            reservation_id=RESERVATION_A,
            reservation_key="host-reservation-a",
            owner=OWNER,
            resources={"slots": 1, "memory_bytes": 1024**3, "gpu_count": 1},
            lease_seconds=90,
        )
        replay_a = await ledger.reserve(
            host_id=HOST_ID,
            reservation_key="host-reservation-a",
            owner=OWNER,
            resources={"slots": 1, "memory_bytes": 1024**3, "gpu_count": 1},
            lease_seconds=90,
        )
        over_capacity_rejected = False
        try:
            await ledger.reserve(
                host_id=HOST_ID,
                reservation_id=RESERVATION_C,
                reservation_key="host-reservation-c",
                owner=OWNER,
                resources={"slots": 2, "memory_bytes": 1024, "gpu_count": 0},
            )
        except ReservationRejected as exc:
            over_capacity_rejected = exc.reason_code == "capacity_slots_exhausted"

        reservation_b = await ledger.reserve(
            host_id=HOST_ID,
            reservation_id=RESERVATION_B,
            reservation_key="host-reservation-b",
            owner=OWNER,
            resources={"slots": 1, "memory_bytes": 1024**3, "gpu_count": 0},
            lease_seconds=90,
        )
        projection_full = await ledger.capacity_projection(HOST_ID)
        committed_a = await ledger.commit(RESERVATION_A, owner=OWNER)
        released_a = await ledger.release(RESERVATION_A, owner=OWNER)
        await _expire_reservation(storage, RESERVATION_B)
        expired_count = await ledger.expire_reservations()
        expired_b = await ledger.get(RESERVATION_B)
        reopened = AgentStorage(normalized_dsn)
        await storage.close()
        await reopened.connect()
        reopened_ledger = HostCapacityReservationLedger(reopened)
        durable_rows = await reopened_ledger.list_for_host(HOST_ID)
        projection_empty = await reopened_ledger.capacity_projection(HOST_ID)
        cleanup_after = await _cleanup(reopened)
        remaining = await _counts(reopened)
        await reopened.close()

        passed = all(
            (
                reservation_a["state"] == "RESERVED",
                replay_a["idempotent_replay"] is True,
                replay_a["id"] == reservation_a["id"],
                over_capacity_rejected,
                reservation_b["state"] == "RESERVED",
                projection_full["reserved"] == {"slots": 2, "memory_bytes": 2 * 1024**3, "gpu_count": 1},
                committed_a["state"] == "COMMITTED",
                released_a["state"] == "RELEASED",
                expired_count == 1,
                expired_b is not None and expired_b["state"] == "EXPIRED",
                len(durable_rows) == 2,
                projection_empty["reserved"] == {"slots": 0, "memory_bytes": 0, "gpu_count": 0},
                remaining == {"hosts": 0, "reservations": 0},
            )
        )
        return {
            "schema_version": CHECK_SCHEMA,
            "reservation_schema": RESERVATION_SCHEMA,
            "mode": "local-postgres-worker-host-reservations",
            "status": "pass" if passed else "fail",
            "migration_version": migration_version,
            "expected_migration": EXPECTED_MIGRATION,
            "cleanup_before": cleanup_before,
            "first_counts": first_counts,
            "reservation": {
                "initial_state": reservation_a["state"],
                "idempotent_replay": replay_a["idempotent_replay"],
                "replay_same_id": replay_a["id"] == reservation_a["id"],
                "over_capacity_rejected": over_capacity_rejected,
                "full_projection": projection_full,
                "committed_state": committed_a["state"],
                "released_state": released_a["state"],
                "expired_count": expired_count,
                "expired_state": expired_b["state"] if expired_b else None,
            },
            "reopen_readback": {
                "reservation_row_count": len(durable_rows),
                "reserved_after_terminal_transitions": projection_empty["reserved"],
            },
            "cleanup_after": cleanup_after,
            "remaining": remaining,
            "mutation_performed": mutation_performed,
            "external_network_access_performed": False,
            "external_provider_mutation_performed": False,
            "worker_dispatch_performed": False,
            "licence_metadata_is_gate": False,
            "scope": "durable host capacity reservation, row-locked over-capacity rejection, idempotent replay, commit/release/expiry transitions, scalar capacity projection, connection-reopen read-back, and scoped cleanup",
            "boundary": {
                "host_lease_required": "checked",
                "capacity_reservation": "checked",
                "idempotent_key": "checked",
                "commit_release": "checked",
                "expired_reservation_recovery": "checked",
                "multi_host_selection": "not_checked",
                "live_scheduler": "not_checked",
                "host_loss_or_split_brain": "not_checked",
                "worker_dispatch": "not_checked",
            },
        }
    except Exception as exc:  # pragma: no cover - live environment diagnostic
        with suppress(Exception):
            await storage.close()
        return {
            **_blocked("worker_host_reservations_checker_error"),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "migration_version": migration_version,
            "mutation_performed": mutation_performed,
        }
    finally:
        with suppress(Exception):
            await storage.close()


def build_report(*, dsn: str | None = None) -> dict[str, Any]:
    return asyncio.run(_run(dsn if dsn is not None else _parser().parse_args().dsn))


def main() -> int:
    args = _parser().parse_args()
    report = asyncio.run(_run(args.dsn))
    print(json.dumps(report, indent=2, default=str, sort_keys=True))
    return 0 if report.get("status") == "pass" else 2 if report.get("status") == "blocked" else 1


if __name__ == "__main__":
    raise SystemExit(main())
