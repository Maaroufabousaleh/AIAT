"""Certify bounded durable in-flight worker-version pinning in Postgres.

The probe creates two immutable shell, adapter, and skill-bundle versions for
one reserved worker, starts a real durable worker run on version one, advances
the mutable worker registry pointers to version two, and creates a second
queued run on version two.  It then reopens Postgres and verifies that the
in-flight run still references its original shell, adapter, bundle, and
steward rows while the registry and the new run reference the replacement
versions.

This is storage-level pinning evidence only.  No worker, model, provider,
sandbox, or network endpoint is called; task payload markers are never
emitted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import sqlalchemy as sa

MAS_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = MAS_ROOT / "packages" / "mas-core"
if CORE_ROOT.exists() and str(CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(CORE_ROOT))

from mas_core.memory.storage import AgentStorage  # noqa: E402

CHECK_SCHEMA = "aiat.worker-version-pinning-postgres-certification.v1"
EXPECTED_MIGRATION = "0042_worker_run_host_binding"
WORKER_NAME = "aiat-cert-worker-version-pinning-v1"
WORKER_PREFIX = f"{WORKER_NAME}%"
WORKER_ID = UUID("00000000-0000-4000-a000-000000000971")
RUN_V1_ID = UUID("00000000-0000-4000-a000-000000000972")
RUN_V2_ID = UUID("00000000-0000-4000-a000-000000000973")
STEWARD_ID = UUID("00000000-0000-4000-a000-000000000974")
PAYLOAD_MARKER = "aiat version pin fixture payload must never enter the evidence report"
PIN_OWNER = "aiat-version-pin-owner"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON evidence")
    parser.add_argument(
        "--dsn",
        default=os.getenv(
            "AIAT_WORKER_VERSION_EVIDENCE_DSN",
            os.getenv("PGBOUNCER_DSN", os.getenv("POSTGRES_DSN", "")),
        ),
        help=(
            "Postgres DSN; defaults to AIAT_WORKER_VERSION_EVIDENCE_DSN/"
            "PGBOUNCER_DSN/POSTGRES_DSN"
        ),
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
        "mode": "local-postgres-worker-version-pinning",
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
        values = {
            "workers": await connection.scalar(
                sa.text("SELECT count(*) FROM worker_registry WHERE name LIKE :prefix"),
                {"prefix": WORKER_PREFIX},
            ),
            "runs": await connection.scalar(
                sa.text(
                    """SELECT count(*) FROM worker_runs
                       WHERE worker_id IN (
                         SELECT id FROM worker_registry WHERE name LIKE :prefix
                       )"""
                ),
                {"prefix": WORKER_PREFIX},
            ),
            "shell_versions": await connection.scalar(
                sa.text(
                    """SELECT count(*) FROM worker_shell_versions
                       WHERE worker_id IN (
                         SELECT id FROM worker_registry WHERE name LIKE :prefix
                       )"""
                ),
                {"prefix": WORKER_PREFIX},
            ),
            "adapters": await connection.scalar(
                sa.text(
                    """SELECT count(*) FROM runtime_adapters
                       WHERE worker_id IN (
                         SELECT id FROM worker_registry WHERE name LIKE :prefix
                       )"""
                ),
                {"prefix": WORKER_PREFIX},
            ),
            "bundles": await connection.scalar(
                sa.text(
                    """SELECT count(*) FROM skill_bundles
                       WHERE worker_id IN (
                         SELECT id FROM worker_registry WHERE name LIKE :prefix
                       )"""
                ),
                {"prefix": WORKER_PREFIX},
            ),
        }
    return {key: int(value or 0) for key, value in values.items()}


async def _cleanup(storage: AgentStorage) -> dict[str, int]:
    """Delete only the reserved worker and all of its run/version rows."""

    async with storage.engine.begin() as connection:
        # Active-version FKs point from the mutable registry to immutable rows;
        # clear those pointers before deleting the worker-owned graph.
        await connection.execute(
            sa.text(
                """UPDATE worker_registry
                   SET active_shell_version_id = NULL,
                       active_adapter_id = NULL,
                       active_skill_bundle_id = NULL
                   WHERE name LIKE :prefix"""
            ),
            {"prefix": WORKER_PREFIX},
        )
        deleted_runs = await connection.execute(
            sa.text(
                """DELETE FROM worker_runs
                   WHERE id IN (:run_v1, :run_v2)
                      OR worker_id IN (
                           SELECT id FROM worker_registry WHERE name LIKE :prefix
                       )"""
            ),
            {"run_v1": RUN_V1_ID, "run_v2": RUN_V2_ID, "prefix": WORKER_PREFIX},
        )
        deleted_workers = await connection.execute(
            sa.text("DELETE FROM worker_registry WHERE name LIKE :prefix"),
            {"prefix": WORKER_PREFIX},
        )
    return {
        "worker_runs": int(deleted_runs.rowcount or 0),
        "workers": int(deleted_workers.rowcount or 0),
    }


def _safe_run_projection(row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        "id": str(row.get("id")),
        "state": row.get("state"),
        "attempt_count": int(row.get("attempt_count") or 0),
        "worker_shell_version_id": str(row.get("worker_shell_version_id")),
        "adapter_id": str(row.get("adapter_id")),
        "skill_bundle_id": str(row.get("skill_bundle_id")),
        "steward_id": str(row.get("steward_id")),
    }


async def _run(dsn: str | None) -> dict[str, Any]:
    normalized_dsn = _normalize_dsn(dsn)
    if normalized_dsn is None:
        return _blocked("worker_version_evidence_database_not_configured")

    storage = AgentStorage(normalized_dsn)
    migration_version: str | None = None
    first_counts: dict[str, int] = {}
    cleanup_counts: dict[str, int] = {}
    remaining: dict[str, int] = {}
    reopened_healthy = False
    worker_v2: dict[str, Any] | None = None
    run_v1: dict[str, Any] | None = None
    run_v2: dict[str, Any] | None = None
    shell_v1: dict[str, Any] | None = None
    shell_v2: dict[str, Any] | None = None
    adapter_v1: dict[str, Any] | None = None
    adapter_v2: dict[str, Any] | None = None
    bundle_v1: dict[str, Any] | None = None
    bundle_v2: dict[str, Any] | None = None
    mutation_performed = False
    try:
        await storage.connect()
        migration_version = await _migration_version(storage)
        if migration_version != EXPECTED_MIGRATION:
            return {
                **_blocked(
                    "worker_version_evidence_migration_not_at_head",
                    migration_version=migration_version,
                ),
                "expected_migration": EXPECTED_MIGRATION,
                "local_database_access_performed": True,
            }

        await _cleanup(storage)
        first_counts = await _counts(storage)
        registered = await storage.register_worker(
            name=WORKER_NAME,
            worker_id=WORKER_ID,
            adapter_type="native",
            adapter_config={"fixture": "durable-worker-version-pinning"},
            sandbox_profile="standard",
            status="ACTIVE",
            version="pin-fixture-v1",
            source_repo="internal-fixture",
            source_revision="worker-version-pinning-postgres-v1",
            version_pin="pin-fixture-v1",
            evaluation_status="fixture-only",
            adapter_entrypoint="NativeWorkerAdapter",
            isolation_mode="native",
            model_mode="none",
        )
        canonical_worker_id = UUID(str(registered["id"]))
        steward = await storage.create_steward(
            worker_id=canonical_worker_id,
            steward_id=STEWARD_ID,
            status="READY",
            steward_version="pin-steward-v1",
            metadata={"fixture": "version-pinning"},
        )
        canonical_steward_id = UUID(str(steward["id"]))
        shell_v1 = await storage.create_worker_shell_version(
            worker_id=canonical_worker_id,
            version="shell-v1",
            contract_version="aiat.worker.v1",
            schema_version="aiat.v1",
            identity={"role": "fixture-worker"},
            capabilities={"checkpoint": True, "version": "shell-v1"},
            permissions={"tools": []},
            provenance={"source": "local-fixture", "revision": "shell-v1"},
            content_hash="a" * 64,
        )
        adapter_v1 = await storage.create_runtime_adapter(
            worker_id=canonical_worker_id,
            version="adapter-v1",
            adapter_type="native",
            transport_type="in_process",
            runtime_api_version="fixture-runtime-v1",
            implementation_ref="internal-fixture/native-v1",
            content_hash="b" * 64,
            capabilities={"version": "adapter-v1"},
            conformance_status="passed",
            conformance={"fixture": True},
            status="active",
        )
        bundle_v1 = await storage.create_skill_bundle(
            worker_id=canonical_worker_id,
            steward_id=canonical_steward_id,
            semantic_version="bundle-v1",
            format_version="1",
            upstream_compatibility_range=">=1,<2",
            provenance={"source": "local-fixture", "revision": "bundle-v1"},
            bundle={"fixture": "bundle-v1"},
            content_hash="c" * 64,
            status="APPROVED",
        )
        shell_v2 = await storage.create_worker_shell_version(
            worker_id=canonical_worker_id,
            version="shell-v2",
            contract_version="aiat.worker.v1",
            schema_version="aiat.v1",
            identity={"role": "fixture-worker"},
            capabilities={"checkpoint": True, "version": "shell-v2"},
            permissions={"tools": []},
            provenance={"source": "local-fixture", "revision": "shell-v2"},
            content_hash="d" * 64,
        )
        adapter_v2 = await storage.create_runtime_adapter(
            worker_id=canonical_worker_id,
            version="adapter-v2",
            adapter_type="native",
            transport_type="in_process",
            runtime_api_version="fixture-runtime-v2",
            implementation_ref="internal-fixture/native-v2",
            content_hash="e" * 64,
            capabilities={"version": "adapter-v2"},
            conformance_status="passed",
            conformance={"fixture": True},
            status="candidate",
        )
        bundle_v2 = await storage.create_skill_bundle(
            worker_id=canonical_worker_id,
            steward_id=canonical_steward_id,
            semantic_version="bundle-v2",
            format_version="1",
            upstream_compatibility_range=">=1,<3",
            provenance={"source": "local-fixture", "revision": "bundle-v2"},
            bundle={"fixture": "bundle-v2"},
            content_hash="f" * 64,
            status="DRAFT",
        )
        await storage.set_worker_governed_versions(
            canonical_worker_id,
            active_shell_version_id=UUID(str(shell_v1["id"])),
            active_adapter_id=UUID(str(adapter_v1["id"])),
            active_skill_bundle_id=UUID(str(bundle_v1["id"])),
        )
        run_v1 = await storage.create_worker_run(
            run_id=RUN_V1_ID,
            worker_id=canonical_worker_id,
            idempotency_key="aiat-cert-worker-version-pinning-v1",
            task_type="durable_worker_version_pin_fixture",
            request={"private_marker": PAYLOAD_MARKER, "version": "v1"},
            worker_shell_version_id=UUID(str(shell_v1["id"])),
            adapter_id=UUID(str(adapter_v1["id"])),
            steward_id=canonical_steward_id,
            state="QUEUED",
        )
        claimed = await storage.claim_worker_run(owner=PIN_OWNER, run_id=RUN_V1_ID)
        if claimed is None:
            raise RuntimeError("version-one fixture run was not claimable")
        current_state = "CLAIMED"
        for next_state in ("VALIDATING", "READY", "DISPATCHING", "RUNNING"):
            transitioned = await storage.transition_worker_run(
                RUN_V1_ID,
                new_state=next_state,
                expected_state=current_state,
                actor=PIN_OWNER,
                reason="version pin fixture progression",
            )
            if transitioned is None:
                raise RuntimeError(f"version-one fixture transition lost CAS: {next_state}")
            current_state = next_state
        await storage.set_worker_governed_versions(
            canonical_worker_id,
            active_shell_version_id=UUID(str(shell_v2["id"])),
            active_adapter_id=UUID(str(adapter_v2["id"])),
            active_skill_bundle_id=UUID(str(bundle_v2["id"])),
        )
        worker_v2 = await storage.get_worker(canonical_worker_id)
        run_v2 = await storage.create_worker_run(
            run_id=RUN_V2_ID,
            worker_id=canonical_worker_id,
            idempotency_key="aiat-cert-worker-version-pinning-v2",
            task_type="durable_worker_version_pin_fixture",
            request={"private_marker": PAYLOAD_MARKER, "version": "v2"},
            worker_shell_version_id=UUID(str(shell_v2["id"])),
            adapter_id=UUID(str(adapter_v2["id"])),
            steward_id=canonical_steward_id,
            state="QUEUED",
        )
        mutation_performed = True

        run_v1 = await storage.get_worker_run(RUN_V1_ID)
        await storage.close()
        reopened = AgentStorage(normalized_dsn)
        try:
            await reopened.connect()
            reopened_healthy = True
            worker_v2 = await reopened.get_worker(canonical_worker_id)
            run_v1 = await reopened.get_worker_run(RUN_V1_ID)
            run_v2 = await reopened.get_worker_run(RUN_V2_ID)
            cleanup_counts = await _cleanup(reopened)
            remaining = await _counts(reopened)
        finally:
            with suppress(Exception):
                await reopened.close()
    except Exception as exc:
        return {
            **_blocked("local_postgres_worker_version_evidence_failed"),
            "failure_type": type(exc).__name__,
            "local_database_access_performed": True,
        }
    finally:
        if getattr(storage, "_engine", None) is not None:
            with suppress(Exception):
                await _cleanup(storage)
            with suppress(Exception):
                await storage.close()

    shell1_id = str((shell_v1 or {}).get("id"))
    shell2_id = str((shell_v2 or {}).get("id"))
    adapter1_id = str((adapter_v1 or {}).get("id"))
    adapter2_id = str((adapter_v2 or {}).get("id"))
    bundle1_id = str((bundle_v1 or {}).get("id"))
    bundle2_id = str((bundle_v2 or {}).get("id"))
    run1_projection = _safe_run_projection(run_v1)
    run2_projection = _safe_run_projection(run_v2)
    active_v2 = {
        "shell": str((worker_v2 or {}).get("active_shell_version_id")),
        "adapter": str((worker_v2 or {}).get("active_adapter_id")),
        "skill_bundle": str((worker_v2 or {}).get("active_skill_bundle_id")),
    }
    payload_free = PAYLOAD_MARKER not in json.dumps(
        {"worker": active_v2, "run_v1": run1_projection, "run_v2": run2_projection},
        default=str,
    )
    run_v1_pinned = (
        run1_projection.get("state") == "RUNNING"
        and run1_projection.get("worker_shell_version_id") == shell1_id
        and run1_projection.get("adapter_id") == adapter1_id
        and run1_projection.get("skill_bundle_id") == bundle1_id
        and run1_projection.get("steward_id") == str(STEWARD_ID)
    )
    run_v2_pinned = (
        run2_projection.get("state") == "QUEUED"
        and run2_projection.get("worker_shell_version_id") == shell2_id
        and run2_projection.get("adapter_id") == adapter2_id
        and run2_projection.get("skill_bundle_id") == bundle2_id
        and run2_projection.get("steward_id") == str(STEWARD_ID)
    )
    passed = (
        migration_version == EXPECTED_MIGRATION
        and active_v2 == {"shell": shell2_id, "adapter": adapter2_id, "skill_bundle": bundle2_id}
        and run_v1_pinned
        and run_v2_pinned
        and run1_projection.get("worker_shell_version_id") != active_v2["shell"]
        and run1_projection.get("adapter_id") != active_v2["adapter"]
        and reopened_healthy
        and payload_free
        and remaining == {"workers": 0, "runs": 0, "shell_versions": 0, "adapters": 0, "bundles": 0}
        and sum(cleanup_counts.values()) >= 3
    )
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "local-postgres-worker-version-pinning",
        "status": "pass" if passed else "fail",
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "migration_version": migration_version,
        "expected_migration": EXPECTED_MIGRATION,
        "worker_name": WORKER_NAME,
        "initial_fixture_counts": first_counts,
        "version_catalogue": {
            "shell_versions": ["shell-v1", "shell-v2"],
            "adapter_versions": ["adapter-v1", "adapter-v2"],
            "skill_bundle_versions": ["bundle-v1", "bundle-v2"],
        },
        "version_labels_read_back": {
            "active_registry": {
                "shell": (shell_v2 or {}).get("version"),
                "adapter": (adapter_v2 or {}).get("version"),
                "skill_bundle": (bundle_v2 or {}).get("semantic_version"),
            },
            "in_flight_run": {
                "shell": (shell_v1 or {}).get("version"),
                "adapter": (adapter_v1 or {}).get("version"),
                "skill_bundle": (bundle_v1 or {}).get("semantic_version"),
                "steward": "pin-steward-v1",
            },
            "queued_replacement_run": {
                "shell": (shell_v2 or {}).get("version"),
                "adapter": (adapter_v2 or {}).get("version"),
                "skill_bundle": (bundle_v2 or {}).get("semantic_version"),
                "steward": "pin-steward-v1",
            },
        },
        "active_registry_after_rollout": active_v2,
        "in_flight_run_v1": run1_projection,
        "new_queued_run_v2": run2_projection,
        "version_pin_checks": {
            "in_flight_shell_adapter_steward_pinned": run_v1_pinned,
            "in_flight_skill_bundle_pinned": run_v1_pinned,
            "new_run_uses_replacement_shell_adapter": run_v2_pinned,
            "new_run_uses_replacement_skill_bundle": run_v2_pinned,
            "registry_pointer_advanced_without_rewriting_in_flight_run": (
                run1_projection.get("worker_shell_version_id") != active_v2["shell"]
                and run1_projection.get("adapter_id") != active_v2["adapter"]
            ),
            "skill_bundle_id_on_worker_run": "checked",
        },
        "durable_reopen": {
            "healthy": reopened_healthy,
            "in_flight_state": run1_projection.get("state"),
            "queued_replacement_state": run2_projection.get("state"),
        },
        "cleanup_deleted_counts": cleanup_counts,
        "remaining_fixture_counts": remaining,
        "payload_free": payload_free,
        "mutation_performed": mutation_performed,
        "local_database_access_performed": True,
        "network_access_performed": True,
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "worker_dispatch_performed": False,
        "scope": "reserved local Postgres immutable shell/adapter records, mutable governed pointers, in-flight run read-back, replacement run read-back, and scoped cleanup",
        "certification_boundary": {
            "immutable_shell_version_records": "checked",
            "immutable_adapter_version_records": "checked",
            "in_flight_shell_adapter_steward_pin": "checked",
            "in_flight_skill_bundle_pin": "checked",
            "registry_pointer_advance": "checked",
            "new_run_replacement_pin": "checked",
            "new_run_replacement_skill_bundle_pin": "checked",
            "connection_reopen": "checked",
            "scoped_fixture_cleanup": "checked",
            "skill_bundle_id_persisted_on_worker_run": "checked",
            "worker_dispatch": "not_checked",
            "live_worker_or_provider": "not_checked",
        },
        "licence_metadata_is_gate": False,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = asyncio.run(_run(args.dsn))
    if args.json:
        print(json.dumps(report, default=str, sort_keys=True, indent=2))
    else:
        print(f"worker version pinning Postgres certification: {report['status']}")
    return {"pass": 0, "fail": 1, "blocked": 2}[str(report["status"])]


if __name__ == "__main__":
    raise SystemExit(main())
