"""Certify bounded flow-instance recovery against local Postgres.

The checker uses the real ``AgentStorage`` flow methods and a reserved
company/project/flow namespace.  It records a failed node attempt, retries it
without deleting the historical execution, reopens Postgres, switches a second
instance while preserving bounded context, and retries a cancelled instance.
It cleans only its own rows and emits counts/status metadata; request/output
payloads, credentials, providers, workers, and external runtimes are never
included.  The checker does not claim a native watchdog, worker canary, UI
golden path, or provider recovery certificate.
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
if str(CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(CORE_ROOT))

from mas_core.memory import models as t  # noqa: E402
from mas_core.memory.storage import AgentStorage  # noqa: E402

CHECK_SCHEMA = "aiat.flow-instance-recovery-postgres-certification.v1"
EXPECTED_MIGRATION = "0042_worker_run_host_binding"
COMPANY_ID = UUID("00000000-0000-4000-a000-000000000e21")
PROJECT_ID = UUID("00000000-0000-4000-a000-000000000e22")
PROJECT_SWITCH_ID = UUID("00000000-0000-4000-a000-000000000e23")
PROJECT_CANCEL_ID = UUID("00000000-0000-4000-a000-000000000e24")
PROJECT_IDS = (PROJECT_ID, PROJECT_SWITCH_ID, PROJECT_CANCEL_ID)
COMPANY_SLUG = "aiat-cert-flow-instance-recovery-v1"
PROJECT_NAME = "aiat-cert-flow-instance-recovery-v1"
FLOW_A_NAME = "aiat-cert-flow-instance-recovery-flow-a-v1"
FLOW_B_NAME = "aiat-cert-flow-instance-recovery-flow-b-v1"
PAYLOAD_MARKER = "aiat flow recovery fixture payload must never enter this report"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON evidence")
    parser.add_argument(
        "--dsn",
        default=os.getenv(
            "AIAT_FLOW_INSTANCE_RECOVERY_EVIDENCE_DSN",
            os.getenv("PGBOUNCER_DSN", os.getenv("POSTGRES_DSN", "")),
        ),
        help="Postgres DSN; defaults to AIAT_FLOW_INSTANCE_RECOVERY_EVIDENCE_DSN/PGBOUNCER_DSN/POSTGRES_DSN",
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
        "contract_commit": os.getenv("AIAT_EVIDENCE_CONTRACT_COMMIT") or None,
        "mode": "local-postgres-flow-instance-recovery",
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


async def _cleanup(storage: AgentStorage, flow_ids: tuple[UUID, ...] = ()) -> dict[str, int]:
    """Delete only rows belonging to the reserved company/project namespace."""

    async with storage.engine.begin() as connection:
        instance_ids = (
            await connection.execute(
                sa.select(t.flow_instances.c.id).where(
                    sa.or_(
                        t.flow_instances.c.project_id.in_(PROJECT_IDS),
                        t.flow_instances.c.flow_id.in_(flow_ids) if flow_ids else sa.false(),
                    )
                )
            )
        ).scalars().all()
        instance_ids = [UUID(str(value)) for value in instance_ids]
        deleted_executions = 0
        if instance_ids:
            result = await connection.execute(
                t.flow_node_executions.delete().where(
                    t.flow_node_executions.c.instance_id.in_(instance_ids)
                )
            )
            deleted_executions = int(result.rowcount or 0)
        deleted_instances = await connection.execute(
            t.flow_instances.delete().where(t.flow_instances.c.project_id.in_(PROJECT_IDS))
        )
        deleted_projects = await connection.execute(
            t.projects.delete().where(t.projects.c.company_id == COMPANY_ID)
        )
        deleted_flows = 0
        if flow_ids:
            result = await connection.execute(t.flows.delete().where(t.flows.c.id.in_(flow_ids)))
            deleted_flows = int(result.rowcount or 0)
        else:
            result = await connection.execute(
                t.flows.delete().where(t.flows.c.name.in_((FLOW_A_NAME, FLOW_B_NAME)))
            )
            deleted_flows = int(result.rowcount or 0)
        deleted_companies = await connection.execute(
            t.companies.delete().where(t.companies.c.id == COMPANY_ID)
        )
    return {
        "flow_node_executions": deleted_executions,
        "flow_instances": int(deleted_instances.rowcount or 0),
        "projects": int(deleted_projects.rowcount or 0),
        "flows": deleted_flows,
        "companies": int(deleted_companies.rowcount or 0),
    }


async def _counts(storage: AgentStorage) -> dict[str, int]:
    async with storage.engine.connect() as connection:
        values = {
            "companies": await connection.scalar(
                sa.select(sa.func.count()).select_from(t.companies).where(t.companies.c.id == COMPANY_ID)
            ),
            "projects": await connection.scalar(
                sa.select(sa.func.count()).select_from(t.projects).where(
                    t.projects.c.company_id == COMPANY_ID
                )
            ),
            "flows": await connection.scalar(
                sa.select(sa.func.count()).select_from(t.flows).where(
                    t.flows.c.name.in_((FLOW_A_NAME, FLOW_B_NAME))
                )
            ),
            "instances": await connection.scalar(
                sa.select(sa.func.count()).select_from(t.flow_instances).where(
                    t.flow_instances.c.project_id.in_(PROJECT_IDS)
                )
            ),
            "executions": await connection.scalar(
                sa.select(sa.func.count())
                .select_from(t.flow_node_executions)
                .where(
                    t.flow_node_executions.c.instance_id.in_(
                        sa.select(t.flow_instances.c.id).where(
                            t.flow_instances.c.project_id.in_(PROJECT_IDS)
                        )
                    )
                )
            ),
        }
    return {key: int(value or 0) for key, value in values.items()}


async def _run(dsn: str | None) -> dict[str, Any]:
    normalized_dsn = _normalize_dsn(dsn)
    if normalized_dsn is None:
        return _blocked("flow_instance_recovery_evidence_database_not_configured")

    storage = AgentStorage(normalized_dsn)
    mutation_performed = False
    reopened = False
    cleanup_counts: dict[str, int] = {}
    remaining: dict[str, int] = {}
    migration_version: str | None = None
    report: dict[str, Any] | None = None
    try:
        await storage.connect()
        migration_version = await _migration_version(storage)
        if migration_version != EXPECTED_MIGRATION:
            return {
                **_blocked(
                    "flow_instance_recovery_evidence_migration_not_at_head",
                    migration_version=migration_version,
                ),
                "expected_migration": EXPECTED_MIGRATION,
                "local_database_access_performed": True,
            }

        await _cleanup(storage)
        async with storage.engine.begin() as connection:
            await connection.execute(
                t.companies.insert().values(
                    id=COMPANY_ID,
                    slug=COMPANY_SLUG,
                    name="AIAT Flow Recovery Fixture Company",
                    created_by="flow-instance-recovery-fixture",
                )
            )
        for project_id, suffix in (
            (PROJECT_ID, "retry"),
            (PROJECT_SWITCH_ID, "switch"),
            (PROJECT_CANCEL_ID, "cancel"),
        ):
            await storage.create_project(
                name=f"{PROJECT_NAME}-{suffix}",
                description="bounded flow recovery fixture",
                state="INIT",
                created_by="flow-instance-recovery-fixture",
                company_id=COMPANY_ID,
                project_id=project_id,
                config={"fixture": True, "private_marker": PAYLOAD_MARKER},
            )
        mutation_performed = True
        flow_a = await storage.create_flow(
            name=FLOW_A_NAME,
            description="flow recovery fixture A",
            created_by="flow-instance-recovery-fixture",
            definition_json={"schema_version": "1.0", "nodes": [], "edges": []},
        )
        flow_b = await storage.create_flow(
            name=FLOW_B_NAME,
            description="flow recovery fixture B",
            created_by="flow-instance-recovery-fixture",
            definition_json={"schema_version": "1.0", "nodes": [], "edges": []},
        )
        flow_a_id = UUID(str(flow_a["id"]))
        flow_b_id = UUID(str(flow_b["id"]))

        retry_instance = await storage.create_flow_instance(
            flow_id=flow_a_id,
            flow_version=int(flow_a["version"]),
            project_id=PROJECT_ID,
        )
        retry_execution = await storage.create_flow_node_execution(
            instance_id=retry_instance["id"],
            node_id="retry-node",
            node_type="task",
            node_label="Retry fixture node",
            input_json={"private_marker": PAYLOAD_MARKER},
        )
        await storage.update_flow_instance(
            retry_instance["id"],
            status="FAILED",
            active_node_ids=["retry-node"],
            context_json={"last_safe_node_id": "retry-node", "private_marker": PAYLOAD_MARKER},
            retry_count=0,
            completed_at=datetime.now(tz=UTC),
        )
        await storage.update_flow_node_execution(
            retry_execution["id"],
            status="FAILED",
            error="fixture failure",
            completed_at=datetime.now(tz=UTC),
        )
        retried = await storage.retry_flow_instance(retry_instance["id"])
        retry_history = await storage.list_flow_node_executions(instance_id=retry_instance["id"])

        switch_instance = await storage.create_flow_instance(
            flow_id=flow_a_id,
            flow_version=int(flow_a["version"]),
            project_id=PROJECT_SWITCH_ID,
        )
        await storage.update_flow_instance(
            switch_instance["id"],
            status="RUNNING",
            active_node_ids=["switch-node"],
            context_json={"preserve_marker": "switch-context-v1"},
        )
        await storage.create_flow_node_execution(
            instance_id=switch_instance["id"],
            node_id="switch-node",
            node_type="task",
            node_label="Switch fixture node",
        )
        escalated = await storage.escalate_flow_instance(
            switch_instance["id"], "operator", "fixture escalation"
        )
        switched = await storage.switch_flow_instance(
            switch_instance["id"], flow_b_id, preserve_context=True
        )
        switch_history_after = await storage.list_flow_node_executions(instance_id=switch_instance["id"])

        cancelled_instance = await storage.create_flow_instance(
            flow_id=flow_a_id,
            flow_version=int(flow_a["version"]),
            project_id=PROJECT_CANCEL_ID,
        )
        await storage.create_flow_node_execution(
            instance_id=cancelled_instance["id"],
            node_id="cancel-node",
            node_type="task",
            node_label="Cancel fixture node",
        )
        await storage.update_flow_instance(
            cancelled_instance["id"],
            status="CANCELLED",
            active_node_ids=["cancel-node"],
            completed_at=datetime.now(tz=UTC),
        )
        cancelled_retry = await storage.retry_flow_instance(cancelled_instance["id"])
        cancelled_history = await storage.list_flow_node_executions(
            instance_id=cancelled_instance["id"]
        )
        mutation_performed = True

        await storage.close()
        reopened_storage = AgentStorage(normalized_dsn)
        storage = reopened_storage
        await storage.connect()
        reopened = True
        retry_readback = await storage.get_flow_instance(retry_instance["id"])
        switch_readback = await storage.get_flow_instance(switch_instance["id"])
        cancel_readback = await storage.get_flow_instance(cancelled_instance["id"])
        retry_history_readback = await storage.list_flow_node_executions(instance_id=retry_instance["id"])
        switch_history_readback = await storage.list_flow_node_executions(instance_id=switch_instance["id"])
        cancel_history_readback = await storage.list_flow_node_executions(instance_id=cancelled_instance["id"])

        checks = {
            "retry_preserves_superseded_history": bool(
                retried
                and retried["status"] == "NOT_STARTED"
                and int(retried.get("retry_count") or 0) == 1
                and retry_history
                and retry_history[0]["status"] == "SUPERSEDED"
                and retry_history[0].get("error") == "fixture failure"
            ),
            "switch_preserves_context_and_resets_execution": bool(
                escalated
                and switched
                and switched["flow_id"] == flow_b_id
                and switched["status"] == "NOT_STARTED"
                and (switched.get("context_json") or {}).get("preserve_marker") == "switch-context-v1"
                and switch_history_after == []
            ),
            "cancelled_retry_preserves_history": bool(
                cancelled_retry
                and cancelled_retry["status"] == "NOT_STARTED"
                and cancelled_history
                and cancelled_history[0]["status"] == "SUPERSEDED"
            ),
            "connection_reopen_reads_all_instances": bool(
                retry_readback
                and retry_readback["status"] == "NOT_STARTED"
                and switch_readback
                and switch_readback["flow_id"] == flow_b_id
                and cancel_readback
                and cancel_readback["status"] == "NOT_STARTED"
            ),
            "connection_reopen_reads_execution_history": bool(
                retry_history_readback
                and retry_history_readback[0]["status"] == "SUPERSEDED"
                and switch_history_readback == []
                and cancel_history_readback
                and cancel_history_readback[0]["status"] == "SUPERSEDED"
            ),
        }
        failed = [name for name, passed in checks.items() if not passed]
        report = {
            "schema_version": CHECK_SCHEMA,
            "contract_commit": os.getenv("AIAT_EVIDENCE_CONTRACT_COMMIT") or None,
            "observed_at": datetime.now(tz=UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "mode": "local-postgres-flow-instance-recovery",
            "status": "fail" if failed else "pass",
            "migration_version": migration_version,
            "expected_migration": EXPECTED_MIGRATION,
            "checks": {name: {"status": "pass" if passed else "fail"} for name, passed in checks.items()},
            "failed_checks": failed,
            "reopened_connection": reopened,
            "mutation_performed": mutation_performed,
            "local_database_access_performed": True,
            "external_network_access_performed": False,
            "external_provider_mutation_performed": False,
            "worker_dispatch_performed": False,
            "licence_metadata_is_gate": False,
            "scope": "reserved local Postgres flow-instance recovery; payload-free status and counts only",
        }
        return report
    except (OSError, RuntimeError, sa.exc.SQLAlchemyError, ValueError) as exc:
        report = {
            **_blocked(f"flow_instance_recovery_evidence_failed: {type(exc).__name__}"),
            "local_database_access_performed": True,
            "mutation_performed": mutation_performed,
        }
        return report
    finally:
        with suppress(Exception):
            cleanup_counts = await _cleanup(storage)
            remaining = await _counts(storage)
        with suppress(Exception):
            await storage.close()
        if isinstance(report, dict):
            report["cleanup"] = cleanup_counts
            report["remaining"] = remaining


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = asyncio.run(_run(args.dsn))
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print(f"flow-instance Postgres recovery: {report['status']}")
    return 2 if report["status"] == "blocked" else (0 if report["status"] == "pass" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
