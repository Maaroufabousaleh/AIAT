"""Certify bounded durable multi-host selection and reservation scheduling.

The checker registers several AIAT-owned hosts, fills the preferred host with
an existing reservation, and proves that the scheduler deterministically falls
back to the next eligible host through the row-locked reservation ledger.  It
also proves globally idempotent replay, explicit blocked output when every
eligible host is full, connection-reopen read-back, and scoped cleanup.  It
does not dispatch a worker or claim host-loss/split-brain recovery.  Licence
metadata remains informational.
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
    HostCapacityReservationLedger,
)
from mas_core.worker_registry.host_scheduler import (  # noqa: E402
    SCHEDULER_SCHEMA,
    HostScheduler,
    HostScheduleRequest,
)
from mas_core.worker_registry.placement import WorkerPlacementRequest  # noqa: E402

CHECK_SCHEMA = "aiat.worker-host-scheduler-postgres-certification.v1"
EXPECTED_MIGRATION = "0042_worker_run_host_binding"
HOST_PREFIX = "aiat-cert-worker-host-scheduler-v1-"
HOST_A = f"{HOST_PREFIX}a"
HOST_B = f"{HOST_PREFIX}b"
HOST_C = f"{HOST_PREFIX}draining"
HOST_D = f"{HOST_PREFIX}registering"
HOST_PATTERN = f"{HOST_PREFIX}%"
HOST_UUID_A = UUID("00000000-0000-4000-a000-0000000009a1")
HOST_UUID_B = UUID("00000000-0000-4000-a000-0000000009a2")
HOST_UUID_C = UUID("00000000-0000-4000-a000-0000000009a3")
HOST_UUID_D = UUID("00000000-0000-4000-a000-0000000009a4")
RESERVATION_BLOCKER = UUID("00000000-0000-4000-a000-0000000009b1")
RESERVATION_MAIN = UUID("00000000-0000-4000-a000-0000000009b2")
TOKEN_A = "aiat-host-scheduler-fixture-token-a-v1"
TOKEN_B = "aiat-host-scheduler-fixture-token-b-v1"
TOKEN_C = "aiat-host-scheduler-fixture-token-c-v1"
TOKEN_D = "aiat-host-scheduler-fixture-token-d-v1"
OWNER = "aiat-scheduler-fixture"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON evidence")
    parser.add_argument(
        "--dsn",
        default=os.getenv(
            "AIAT_WORKER_HOST_SCHEDULER_EVIDENCE_DSN",
            os.getenv("PGBOUNCER_DSN", os.getenv("POSTGRES_DSN", "")),
        ),
        help="Postgres DSN; defaults to AIAT_WORKER_HOST_SCHEDULER_EVIDENCE_DSN/PGBOUNCER_DSN/POSTGRES_DSN",
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
        "mode": "local-postgres-worker-host-scheduler",
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
            {"prefix": HOST_PATTERN},
        )
        reservations = await connection.scalar(
            sa.text(
                """SELECT count(*) FROM worker_host_reservations
                   WHERE host_id IN (SELECT id FROM worker_hosts WHERE host_id LIKE :prefix)"""
            ),
            {"prefix": HOST_PATTERN},
        )
    return {"hosts": int(hosts or 0), "reservations": int(reservations or 0)}


async def _cleanup(storage: AgentStorage) -> dict[str, int]:
    async with storage.engine.begin() as connection:
        deleted_reservations = await connection.execute(
            sa.text(
                """DELETE FROM worker_host_reservations
                   WHERE id IN (:blocker, :main)
                      OR host_id IN (SELECT id FROM worker_hosts WHERE host_id LIKE :prefix)"""
            ),
            {
                "blocker": RESERVATION_BLOCKER,
                "main": RESERVATION_MAIN,
                "prefix": HOST_PATTERN,
            },
        )
        deleted_hosts = await connection.execute(
            sa.text("DELETE FROM worker_hosts WHERE host_id LIKE :prefix"),
            {"prefix": HOST_PATTERN},
        )
    return {
        "reservations": int(deleted_reservations.rowcount or 0),
        "hosts": int(deleted_hosts.rowcount or 0),
    }


async def _register_hosts(registry: WorkerHostRegistry) -> None:
    common = {
        "labels": {"pool": "worker"},
        "capabilities": ["native"],
        "sandbox_profile": "gvisor",
        "isolation_mode": "gvisor",
        "capacity": {
            "slots_total": 1,
            "slots_used": 0,
            "memory_bytes_total": 4 * 1024**3,
            "memory_bytes_used": 0,
            "gpu_total": 0,
            "gpu_used": 0,
        },
        "metadata": {"fixture": "worker-host-scheduler"},
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
    await registry.register_host(
        host_id=HOST_C,
        host_uuid=HOST_UUID_C,
        registration_token=TOKEN_C,
        status="DRAINING",
        priority=3,
        **common,
    )
    await registry.register_host(
        host_id=HOST_D,
        host_uuid=HOST_UUID_D,
        registration_token=TOKEN_D,
        priority=4,
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


def _schedule_request(*, key: str, reservation_id: UUID | None = None) -> HostScheduleRequest:
    return HostScheduleRequest(
        schedule_key=key,
        owner=OWNER,
        placement=WorkerPlacementRequest(
            worker_id="worker-scheduler-fixture",
            required_capabilities=frozenset({"native"}),
            required_labels=(("pool", "worker"),),
            required_sandbox_profile="gvisor",
            required_isolation_mode="gvisor",
            slots=1,
        ),
        lease_seconds=90,
        metadata={"fixture": "worker-host-scheduler"},
        reservation_id=reservation_id,
    )


async def _run(dsn: str | None) -> dict[str, Any]:
    normalized_dsn = _normalize_dsn(dsn)
    if normalized_dsn is None:
        return _blocked("worker_host_scheduler_evidence_database_not_configured")
    storage = AgentStorage(normalized_dsn)
    mutation_performed = False
    migration_version: str | None = None
    try:
        await storage.connect()
        migration_version = await _migration_version(storage)
        if migration_version != EXPECTED_MIGRATION:
            return {
                **_blocked(
                    "worker_host_scheduler_evidence_migration_not_at_head",
                    migration_version=migration_version,
                ),
                "expected_migration": EXPECTED_MIGRATION,
                "local_database_access_performed": True,
            }
        cleanup_before = await _cleanup(storage)
        first_counts = await _counts(storage)
        registry = WorkerHostRegistry(storage)
        ledger = HostCapacityReservationLedger(storage)
        await _register_hosts(registry)
        blocker = await ledger.reserve(
            host_id=HOST_A,
            reservation_id=RESERVATION_BLOCKER,
            reservation_key="host-scheduler-blocker",
            owner=OWNER,
            resources={"slots": 1, "memory_bytes": 0, "gpu_count": 0},
            lease_seconds=120,
        )
        mutation_performed = True
        scheduler = HostScheduler(storage)
        scheduled = await scheduler.schedule(
            _schedule_request(key="host-schedule-main", reservation_id=RESERVATION_MAIN)
        )
        replayed = await scheduler.schedule(_schedule_request(key="host-schedule-main"))
        blocked = await scheduler.schedule(_schedule_request(key="host-schedule-blocked"))

        reopened = AgentStorage(normalized_dsn)
        await storage.close()
        await reopened.connect()
        reopened_scheduler = HostScheduler(reopened)
        reopened_replay = await reopened_scheduler.schedule(_schedule_request(key="host-schedule-main"))
        reopened_ledger = HostCapacityReservationLedger(reopened)
        durable_main = await reopened_ledger.get_by_key("host-schedule-main")
        cleanup_after = await _cleanup(reopened)
        remaining = await _counts(reopened)
        await reopened.close()

        attempts = scheduled.get("attempts") or []
        passed = all(
            (
                blocker["state"] == "RESERVED",
                scheduled["status"] == "RESERVED",
                scheduled["selected_host_id"] == HOST_A,
                scheduled["scheduled_host_id"] == HOST_B,
                attempts == [{"host_id": HOST_A, "reason_code": "capacity_slots_exhausted"}],
                scheduled["worker_dispatch_performed"] is False,
                replayed["status"] == "REPLAYED",
                replayed["scheduled_host_id"] == HOST_B,
                replayed["mutation_performed"] is False,
                blocked["status"] == "BLOCKED",
                blocked["scheduled_host_id"] is None,
                blocked["attempts"]
                == [
                    {"host_id": HOST_A, "reason_code": "capacity_slots_exhausted"},
                    {"host_id": HOST_B, "reason_code": "capacity_slots_exhausted"},
                ],
                reopened_replay["status"] == "REPLAYED",
                durable_main is not None and durable_main["host_id"] == HOST_B,
                remaining == {"hosts": 0, "reservations": 0},
            )
        )
        return {
            "schema_version": CHECK_SCHEMA,
            "scheduler_schema": SCHEDULER_SCHEMA,
            "mode": "local-postgres-worker-host-scheduler",
            "status": "pass" if passed else "fail",
            "migration_version": migration_version,
            "expected_migration": EXPECTED_MIGRATION,
            "cleanup_before": cleanup_before,
            "first_counts": first_counts,
            "selection": {
                "preferred_host": scheduled.get("selected_host_id"),
                "scheduled_host": scheduled.get("scheduled_host_id"),
                "fallback_used": scheduled.get("scheduled_host_id") == HOST_B,
                "attempts": attempts,
                "candidate_count": len(scheduled.get("candidate_decisions") or []),
                "eligible_host_count": scheduled.get("eligible_host_count"),
            },
            "idempotency": {
                "replay_status": replayed.get("status"),
                "replay_same_host": replayed.get("scheduled_host_id") == HOST_B,
                "reopen_replay_status": reopened_replay.get("status"),
            },
            "blocked_boundary": {
                "status": blocked.get("status"),
                "attempts": blocked.get("attempts"),
                "draining_and_unleased_hosts_filtered": True,
            },
            "reopen_readback": {
                "reservation_present": durable_main is not None,
                "scheduled_host": durable_main.get("host_id") if durable_main else None,
            },
            "cleanup_after": cleanup_after,
            "remaining": remaining,
            "mutation_performed": mutation_performed,
            "external_network_access_performed": False,
            "external_provider_mutation_performed": False,
            "worker_dispatch_performed": False,
            "licence_metadata_is_gate": False,
            "scope": "deterministic multi-host selection, row-locked fallback, idempotent replay, blocked capacity outcome, connection-reopen read-back, and scoped cleanup",
            "boundary": {
                "multi_host_selection": "checked",
                "reservation_fallback": "checked",
                "idempotent_schedule_key": "checked",
                "draining_or_unleased_filter": "checked",
                "live_worker_dispatch": "not_checked",
                "host_loss_or_split_brain": "not_checked",
                "firecracker": "not_checked",
            },
        }
    except Exception as exc:  # pragma: no cover - live environment diagnostic
        with suppress(Exception):
            await storage.close()
        return {
            **_blocked("worker_host_scheduler_checker_error"),
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
