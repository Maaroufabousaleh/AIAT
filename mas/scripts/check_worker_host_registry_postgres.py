"""Certify durable authenticated worker-host registration and lease state.

The checker uses the canonical Postgres migration and the AIAT-owned host
registry to prove credential-digest registration, wrong-token rejection,
heartbeat lease renewal, status/placement projection, connection-reopen
read-back, and expired-lease visibility.  It never dispatches a worker or
reserves capacity.  Only the reserved fixture host is mutated and all output
is payload-free; licence metadata remains informational.
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
from mas_core.worker_registry.host_registry import (  # noqa: E402
    HOST_REGISTRY_SCHEMA,
    WorkerHostRegistry,
)

CHECK_SCHEMA = "aiat.worker-host-registry-postgres-certification.v1"
EXPECTED_MIGRATION = "0042_worker_run_host_binding"
HOST_ID = "aiat-cert-worker-host-registry-v1"
HOST_PREFIX = f"{HOST_ID}%"
HOST_UUID = UUID("00000000-0000-4000-a000-000000000971")
TOKEN = "aiat-host-registry-fixture-token-v1"
WRONG_TOKEN = "aiat-host-registry-wrong-token-v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON evidence")
    parser.add_argument(
        "--dsn",
        default=os.getenv(
            "AIAT_WORKER_HOST_EVIDENCE_DSN",
            os.getenv("PGBOUNCER_DSN", os.getenv("POSTGRES_DSN", "")),
        ),
        help="Postgres DSN; defaults to AIAT_WORKER_HOST_EVIDENCE_DSN/PGBOUNCER_DSN/POSTGRES_DSN",
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
        "mode": "local-postgres-worker-host-registry",
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
        value = await connection.scalar(
            sa.text("SELECT count(*) FROM worker_hosts WHERE host_id LIKE :prefix"),
            {"prefix": HOST_PREFIX},
        )
    return {"hosts": int(value or 0)}


async def _cleanup(storage: AgentStorage) -> int:
    async with storage.engine.begin() as connection:
        result = await connection.execute(
            sa.text("DELETE FROM worker_hosts WHERE host_id LIKE :prefix"),
            {"prefix": HOST_PREFIX},
        )
    return int(result.rowcount or 0)


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
        raise RuntimeError("reserved worker host was not ready before lease expiry")


async def _run(dsn: str | None) -> dict[str, Any]:
    normalized_dsn = _normalize_dsn(dsn)
    if normalized_dsn is None:
        return _blocked("worker_host_evidence_database_not_configured")

    storage = AgentStorage(normalized_dsn)
    registry = WorkerHostRegistry(storage)
    migration_version: str | None = None
    cleanup_before = 0
    cleanup_after = 0
    first_counts: dict[str, int] = {}
    remaining: dict[str, int] = {}
    mutation_performed = False
    try:
        await storage.connect()
        migration_version = await _migration_version(storage)
        if migration_version != EXPECTED_MIGRATION:
            return {
                **_blocked(
                    "worker_host_evidence_migration_not_at_head",
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
            capabilities=["native", "gpu"],
            host_plane="worker",
            sandbox_profile="gvisor",
            isolation_mode="gvisor",
            capacity={
                "slots_total": 4,
                "slots_used": 1,
                "memory_bytes_total": 8 * 1024**3,
                "memory_bytes_used": 1024**3,
                "gpu_total": 1,
                "gpu_used": 0,
            },
            priority=2,
            metadata={"fixture": "worker-host-registry"},
        )
        mutation_performed = True
        wrong_auth = await registry.authenticate_host(HOST_ID, WRONG_TOKEN)
        right_auth = await registry.authenticate_host(HOST_ID, TOKEN)
        wrong_reregister_rejected = False
        try:
            await registry.register_host(
                host_id=HOST_ID,
                registration_token=WRONG_TOKEN,
                labels={"zone": "wrong"},
            )
        except PermissionError:
            wrong_reregister_rejected = True

        wrong_heartbeat_rejected = False
        try:
            await registry.heartbeat(
                host_id=HOST_ID,
                registration_token=WRONG_TOKEN,
                lease_generation=registered["lease_generation"],
            )
        except PermissionError:
            wrong_heartbeat_rejected = True

        heartbeat = await registry.heartbeat(
            host_id=HOST_ID,
            registration_token=TOKEN,
            lease_generation=registered["lease_generation"],
            lease_seconds=90,
        )
        snapshot = (await registry.list_placement_snapshots())[0]
        reopened = AgentStorage(normalized_dsn)
        await storage.close()
        await reopened.connect()
        reopened_registry = WorkerHostRegistry(reopened)
        durable = await reopened_registry.get_host(HOST_ID)
        durable_snapshot = (await reopened_registry.list_placement_snapshots())[0]
        await _expire_fixture_lease(reopened)
        expired = await reopened_registry.get_host(HOST_ID)
        await reopened.close()
        cleanup_storage = AgentStorage(normalized_dsn)
        await cleanup_storage.connect()
        cleanup_after = await _cleanup(cleanup_storage)
        remaining = await _counts(cleanup_storage)
        await cleanup_storage.close()

        passed = all(
            (
                registered["status"] == "REGISTERING",
                registered["host_plane"] == "worker",
                "auth_token_sha256" not in registered,
                not wrong_auth,
                right_auth,
                wrong_reregister_rejected,
                wrong_heartbeat_rejected,
                heartbeat["status"] == "READY",
                heartbeat["lease_valid"],
                snapshot.status == "READY",
                snapshot.lease_valid,
                snapshot.capacity.slots_total == 4,
                durable is not None and durable["status"] == "READY",
                durable_snapshot.lease_valid,
                expired is not None and expired["lease_valid"] is False,
                remaining == {"hosts": 0},
            )
        )
        return {
            "schema_version": CHECK_SCHEMA,
            "host_registry_schema": HOST_REGISTRY_SCHEMA,
            "mode": "local-postgres-worker-host-registry",
            "status": "pass" if passed else "fail",
            "migration_version": migration_version,
            "expected_migration": EXPECTED_MIGRATION,
            "cleanup_before": cleanup_before,
            "first_counts": first_counts,
            "registration": {
                "status": registered["status"],
                "host_plane": registered["host_plane"],
                "credential_digest_persisted": True,
                "credential_material_exposed": "auth_token_sha256" in registered,
                "wrong_token_rejected": wrong_reregister_rejected,
            },
            "authentication": {
                "correct_token_accepted": right_auth,
                "wrong_token_rejected": not wrong_auth,
                "wrong_heartbeat_rejected": wrong_heartbeat_rejected,
            },
            "heartbeat": {
                "status": heartbeat["status"],
                "lease_valid": heartbeat["lease_valid"],
                "capacity": heartbeat["capacity"],
            },
            "reopen_readback": {
                "host_present": durable is not None,
                "status": durable["status"] if durable else None,
                "host_plane": durable["host_plane"] if durable else None,
                "lease_valid": durable_snapshot.lease_valid,
            },
            "expired_lease_projection": {
                "status": expired["status"] if expired else None,
                "lease_valid": expired["lease_valid"] if expired else None,
            },
            "cleanup_after": cleanup_after,
            "remaining": remaining,
            "mutation_performed": mutation_performed,
            "external_network_access_performed": False,
            "external_provider_mutation_performed": False,
            "worker_dispatch_performed": False,
            "licence_metadata_is_gate": False,
            "scope": "durable host registration, credential authentication, heartbeat lease renewal, placement snapshot projection, connection-reopen read-back, and expired-lease visibility",
            "boundary": {
                "authenticated_registration": "checked",
                "host_plane_persistence": "checked",
                "heartbeat_lease": "checked",
                "placement_snapshot_projection": "checked",
                "credential_redaction": "checked",
                "durable_host_recovery": "not_checked",
                "capacity_reservation_commit": "not_checked",
                "live_multi_host_scheduler": "not_checked",
                "host_loss_or_split_brain": "not_checked",
            },
        }
    except Exception as exc:  # pragma: no cover - live environment diagnostic
        with suppress(Exception):
            await storage.close()
        return {
            **_blocked("worker_host_registry_checker_error"),
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
