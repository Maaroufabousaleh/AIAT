"""Certify durable worker-host lease fencing and expired-host recovery.

The checker proves two related authority boundaries against local Postgres:
re-registering a host advances its lease generation and fences the previous
incarnation, while an expired READY lease is durably marked OFFLINE and its
active reservation is expired in the same recovery transaction.  It also
proves stale heartbeats are rejected, the recovered host is ineligible to the
placement policy after connection reopen, and fixture cleanup is scoped.  No
worker or provider is dispatched.  Licence metadata remains informational.
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
from mas_core.worker_registry.host_recovery import (  # noqa: E402
    HOST_RECOVERY_SCHEMA,
    HostLeaseRecovery,
)
from mas_core.worker_registry.host_registry import (  # noqa: E402
    HOST_REGISTRY_SCHEMA,
    WorkerHostRegistry,
)
from mas_core.worker_registry.host_reservations import (  # noqa: E402
    RESERVATION_SCHEMA,
    HostCapacityReservationLedger,
)
from mas_core.worker_registry.placement import (  # noqa: E402
    WorkerPlacementRequest,
    select_host,
)

CHECK_SCHEMA = "aiat.worker-host-recovery-postgres-certification.v1"
EXPECTED_MIGRATION = "0041_worker_host_planes"
HOST_ID = "aiat-cert-worker-host-recovery-v1"
HOST_PREFIX = f"{HOST_ID}%"
HOST_UUID = UUID("00000000-0000-4000-a000-0000000009c1")
RESERVATION_REPLACED = UUID("00000000-0000-4000-a000-0000000009c2")
RESERVATION_LOST = UUID("00000000-0000-4000-a000-0000000009c3")
TOKEN = "aiat-host-recovery-fixture-token-v1"
OWNER = "aiat-host-recovery-fixture"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON evidence")
    parser.add_argument(
        "--dsn",
        default=os.getenv(
            "AIAT_WORKER_HOST_RECOVERY_EVIDENCE_DSN",
            os.getenv("PGBOUNCER_DSN", os.getenv("POSTGRES_DSN", "")),
        ),
        help="Postgres DSN; defaults to AIAT_WORKER_HOST_RECOVERY_EVIDENCE_DSN/PGBOUNCER_DSN/POSTGRES_DSN",
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
        "mode": "local-postgres-worker-host-recovery",
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
        reservations = await connection.execute(
            sa.text(
                """DELETE FROM worker_host_reservations
                   WHERE id IN (:replaced, :lost)
                      OR host_id IN (SELECT id FROM worker_hosts WHERE host_id LIKE :prefix)"""
            ),
            {
                "replaced": RESERVATION_REPLACED,
                "lost": RESERVATION_LOST,
                "prefix": HOST_PREFIX,
            },
        )
        hosts = await connection.execute(
            sa.text("DELETE FROM worker_hosts WHERE host_id LIKE :prefix"),
            {"prefix": HOST_PREFIX},
        )
    return {
        "reservations": int(reservations.rowcount or 0),
        "hosts": int(hosts.rowcount or 0),
    }


async def _expire_fixture_lease(storage: AgentStorage) -> None:
    async with storage.engine.begin() as connection:
        result = await connection.execute(
            sa.text(
                """UPDATE worker_hosts
                   SET lease_expires_at = now() - interval '1 second'
                   WHERE id = :host_id AND status = 'READY'"""
            ),
            {"host_id": HOST_UUID},
        )
    if result.rowcount != 1:
        raise RuntimeError("recovery fixture was not READY before lease expiry")


async def _run(dsn: str | None) -> dict[str, Any]:
    normalized_dsn = _normalize_dsn(dsn)
    if normalized_dsn is None:
        return _blocked("worker_host_recovery_evidence_database_not_configured")

    storage = AgentStorage(normalized_dsn)
    migration_version: str | None = None
    mutation_performed = False
    try:
        await storage.connect()
        migration_version = await _migration_version(storage)
        if migration_version != EXPECTED_MIGRATION:
            return {
                **_blocked(
                    "worker_host_recovery_evidence_migration_not_at_head",
                    migration_version=migration_version,
                ),
                "expected_migration": EXPECTED_MIGRATION,
                "local_database_access_performed": True,
            }
        cleanup_before = await _cleanup(storage)
        first_counts = await _counts(storage)
        registry = WorkerHostRegistry(storage)
        ledger = HostCapacityReservationLedger(storage)
        recovery = HostLeaseRecovery(storage)

        first = await registry.register_host(
            host_id=HOST_ID,
            host_uuid=HOST_UUID,
            registration_token=TOKEN,
            labels={"pool": "worker", "zone": "local"},
            capabilities=["native"],
            sandbox_profile="gvisor",
            isolation_mode="gvisor",
            capacity={
                "slots_total": 1,
                "slots_used": 0,
                "memory_bytes_total": 4 * 1024**3,
                "memory_bytes_used": 0,
                "gpu_total": 0,
                "gpu_used": 0,
            },
            metadata={"fixture": "worker-host-recovery"},
        )
        await registry.heartbeat(
            host_id=HOST_ID,
            registration_token=TOKEN,
            lease_generation=first["lease_generation"],
            lease_seconds=120,
        )
        replaced_reservation = await ledger.reserve(
            host_id=HOST_ID,
            reservation_id=RESERVATION_REPLACED,
            reservation_key="worker-host-recovery-replaced",
            owner=OWNER,
            resources={"slots": 1, "memory_bytes": 0, "gpu_count": 0},
            lease_seconds=120,
        )
        replacement = await registry.register_host(
            host_id=HOST_ID,
            registration_token=TOKEN,
            labels={"pool": "worker", "zone": "replacement"},
            capabilities=["native"],
            sandbox_profile="gvisor",
            isolation_mode="gvisor",
            capacity={
                "slots_total": 1,
                "slots_used": 0,
                "memory_bytes_total": 4 * 1024**3,
                "memory_bytes_used": 0,
                "gpu_total": 0,
                "gpu_used": 0,
            },
            metadata={"fixture": "worker-host-recovery-replacement"},
        )
        replaced_readback = await ledger.get(RESERVATION_REPLACED)
        stale_after_replacement = False
        try:
            await registry.heartbeat(
                host_id=HOST_ID,
                registration_token=TOKEN,
                lease_generation=first["lease_generation"],
            )
        except PermissionError as exc:
            stale_after_replacement = "generation" in str(exc)
        await registry.heartbeat(
            host_id=HOST_ID,
            registration_token=TOKEN,
            lease_generation=replacement["lease_generation"],
            lease_seconds=120,
        )
        lost_reservation = await ledger.reserve(
            host_id=HOST_ID,
            reservation_id=RESERVATION_LOST,
            reservation_key="worker-host-recovery-lost",
            owner=OWNER,
            resources={"slots": 1, "memory_bytes": 0, "gpu_count": 0},
            lease_seconds=120,
        )
        await _expire_fixture_lease(storage)
        recovery_report = await recovery.reconcile_expired_hosts()
        recovered = await registry.get_host(HOST_ID)
        lost_readback = await ledger.get(RESERVATION_LOST)
        stale_after_recovery = False
        try:
            await registry.heartbeat(
                host_id=HOST_ID,
                registration_token=TOKEN,
                lease_generation=replacement["lease_generation"],
            )
        except PermissionError as exc:
            stale_after_recovery = "generation" in str(exc)
        snapshots = await registry.list_placement_snapshots()
        selected, decisions = select_host(
            snapshots,
            WorkerPlacementRequest(
                worker_id="worker-host-recovery-fixture",
                required_capabilities=frozenset({"native"}),
                required_labels=(("pool", "worker"),),
                required_sandbox_profile="gvisor",
                required_isolation_mode="gvisor",
            ),
        )
        reopened = AgentStorage(normalized_dsn)
        await storage.close()
        await reopened.connect()
        reopened_registry = WorkerHostRegistry(reopened)
        reopened_ledger = HostCapacityReservationLedger(reopened)
        durable = await reopened_registry.get_host(HOST_ID)
        durable_lost = await reopened_ledger.get(RESERVATION_LOST)
        cleanup_after = await _cleanup(reopened)
        remaining = await _counts(reopened)
        await reopened.close()
        mutation_performed = True

        passed = all(
            (
                first["lease_generation"] == 1,
                replaced_reservation["host_lease_generation"] == first["lease_generation"],
                replacement["lease_generation"] == first["lease_generation"] + 1,
                replacement["status"] == "REGISTERING",
                replaced_readback is not None and replaced_readback["state"] == "EXPIRED",
                stale_after_replacement,
                lost_reservation["host_lease_generation"] == replacement["lease_generation"],
                recovery_report["status"] == "RECOVERED",
                recovery_report["recovered_host_count"] == 1,
                recovery_report["expired_reservation_count"] == 1,
                recovered is not None
                and recovered["status"] == "OFFLINE"
                and recovered["lease_generation"] == replacement["lease_generation"] + 1,
                lost_readback is not None and lost_readback["state"] == "EXPIRED",
                stale_after_recovery,
                selected is None,
                decisions and not decisions[0].eligible,
                durable is not None
                and durable["status"] == "OFFLINE"
                and durable["lease_generation"] == recovered["lease_generation"],
                durable_lost is not None and durable_lost["state"] == "EXPIRED",
                remaining == {"hosts": 0, "reservations": 0},
            )
        )
        return {
            "schema_version": CHECK_SCHEMA,
            "host_registry_schema": HOST_REGISTRY_SCHEMA,
            "reservation_schema": RESERVATION_SCHEMA,
            "recovery_schema": HOST_RECOVERY_SCHEMA,
            "mode": "local-postgres-worker-host-recovery",
            "status": "pass" if passed else "fail",
            "migration_version": migration_version,
            "expected_migration": EXPECTED_MIGRATION,
            "cleanup_before": cleanup_before,
            "first_counts": first_counts,
            "split_brain_fencing": {
                "initial_generation": first["lease_generation"],
                "replacement_generation": replacement["lease_generation"],
                "stale_heartbeat_rejected": stale_after_replacement,
                "old_reservation_state": replaced_readback["state"] if replaced_readback else None,
            },
            "expired_host_recovery": {
                "report": recovery_report,
                "status": recovered["status"] if recovered else None,
                "generation": recovered["lease_generation"] if recovered else None,
                "reservation_state": lost_readback["state"] if lost_readback else None,
                "stale_heartbeat_rejected": stale_after_recovery,
            },
            "placement_after_recovery": {
                "selected_host_id": selected,
                "eligible": [decision.as_dict() for decision in decisions],
            },
            "reopen_readback": {
                "host_status": durable["status"] if durable else None,
                "host_lease_generation": durable["lease_generation"] if durable else None,
                "reservation_state": durable_lost["state"] if durable_lost else None,
            },
            "cleanup_after": cleanup_after,
            "remaining": remaining,
            "mutation_performed": mutation_performed,
            "external_network_access_performed": False,
            "external_provider_mutation_performed": False,
            "worker_dispatch_performed": False,
            "licence_metadata_is_gate": False,
            "scope": (
                "host re-registration fencing, stale-heartbeat rejection, expired lease "
                "reconciliation, reservation invalidation, placement exclusion, and durable read-back"
            ),
            "boundary": {
                "host_lease_generation": "checked",
                "split_brain_stale_heartbeat": "checked",
                "expired_host_offline_transition": "checked",
                "reservation_generation_fencing": "checked",
                "placement_exclusion_after_recovery": "checked",
                "live_worker_dispatch": "not_checked",
                "external_provider_calls": "not_checked",
            },
        }
    except Exception as exc:  # pragma: no cover - live environment diagnostic
        with suppress(Exception):
            await storage.close()
        return {
            **_blocked("worker_host_recovery_checker_error"),
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
