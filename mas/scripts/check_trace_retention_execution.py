"""Certify guarded retention execution and a disposable local Postgres batch.

The fixture exercises project narrowing, a typed authoritative hold snapshot,
typed backup parity and audit evidence, and explicit human confirmation through
the in-memory adapter.  ``--live`` requires an explicitly configured local
Postgres DSN, inserts only a reserved native-span fixture, verifies a
database-local backup/read-back manifest, applies one trace-scoped delete
transaction, and removes the remaining fixture rows.  It does not claim
provider-diverse retention, durable audit, erasure, or restore rollback.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa

from mas_core.memory.storage import AgentStorage
from mas_core.observability.postgres_retention import PostgresNativeTraceRetentionStore
from mas_core.observability.retention import (
    TRACE_RETENTION_PLAN_SCHEMA,
    TraceRetentionCandidate,
    TraceRetentionPlan,
    plan_native_span_retention,
)
from mas_core.observability.retention_execution import (
    TRACE_RETENTION_EXECUTION_SCHEMA,
    InMemoryRetentionLegalHoldRegistry,
    InMemoryRetentionStore,
    RetentionBackupParityEvidence,
    RetentionExecutionError,
    RetentionLegalHold,
    RetentionLegalHoldSnapshot,
    execute_retention_plan,
)
from mas_core.observability.trace_evidence import TraceRetentionPolicy

CHECK_SCHEMA = "aiat.trace-retention-execution-check.v1"
EVALUATED_AT = datetime(2026, 1, 1, tzinfo=UTC)
EXPECTED_MIGRATION = "0037_worker_host_registry"
LIVE_TRACE_ID = "aiat-retention-live-v1-trace"
LIVE_SPANS = {
    "expired-delete": "aiat-retention-live-v1-expired-delete",
    "planner-hold": "aiat-retention-live-v1-planner-hold",
    "authority-hold": "aiat-retention-live-v1-authority-hold",
    "fresh-retain": "aiat-retention-live-v1-fresh-retain",
}


def _plan() -> TraceRetentionPlan:
    return TraceRetentionPlan(
        schema_version=TRACE_RETENTION_PLAN_SCHEMA,
        evaluated_at=EVALUATED_AT,
        cutoff=EVALUATED_AT,
        policy=TraceRetentionPolicy(retention_days=30, terminal_mode="delete"),
        candidates=(
            TraceRetentionCandidate(
                record_id="archive-1",
                trace_id="trace-1",
                source_kind="audit",
                disposition="archive",
                expires_at=EVALUATED_AT,
                reason="reviewed archive",
            ),
            TraceRetentionCandidate(
                record_id="delete-1",
                trace_id="trace-1",
                source_kind="tool",
                disposition="delete",
                expires_at=EVALUATED_AT,
                reason="reviewed delete",
            ),
            TraceRetentionCandidate(
                record_id="planner-hold",
                trace_id="trace-1",
                source_kind="mail",
                disposition="delete",
                expires_at=EVALUATED_AT,
                reason="legal hold active",
                legal_hold=True,
            ),
            TraceRetentionCandidate(
                record_id="authority-hold",
                trace_id="trace-1",
                source_kind="worker",
                disposition="delete",
                expires_at=EVALUATED_AT,
                reason="expired",
            ),
            TraceRetentionCandidate(
                record_id="invalid-1",
                trace_id=None,
                source_kind=None,
                disposition="invalid",
                expires_at=None,
                reason="missing metadata",
            ),
        ),
    )


def _store() -> InMemoryRetentionStore:
    return InMemoryRetentionStore(
        records={
            record_id: {"project_id": "project-1", "status": "active"}
            for record_id in ("archive-1", "delete-1", "planner-hold", "authority-hold")
        }
    )


def _parity_evidence() -> RetentionBackupParityEvidence:
    return RetentionBackupParityEvidence(
        evidence_ref="backup://fixture",
        source_manifest_sha256="a" * 64,
        backup_manifest_sha256="a" * 64,
        restored_manifest_sha256="a" * 64,
        source_record_count=4,
        backup_record_count=4,
        restored_record_count=4,
        checked_record_count=4,
        clean_target_verified=True,
    )


def _hold_snapshot() -> RetentionLegalHoldSnapshot:
    return InMemoryRetentionLegalHoldRegistry(
        source_ref="hold-registry://fixture",
        holds=(
            RetentionLegalHold(
                hold_id="hold-authority-1",
                record_id="authority-hold",
                project_id="project-1",
                status="active",
                authority_ref="registry-entry://hold-authority-1",
            ),
        ),
    ).read_snapshot(observed_at=EVALUATED_AT)


def build_report() -> dict[str, object]:
    store = _store()
    preview = execute_retention_plan(
        _plan(),
        store=store,
        scope="project:project-1",
        project_id="project-1",
        project_by_record={record_id: "project-1" for record_id in store.records},
        authoritative_legal_hold_snapshot=_hold_snapshot(),
        actor="fixture-planner",
        actor_kind="system",
        mode="preview",
        audit_id="preview-audit",
        evaluated_at=EVALUATED_AT,
    )
    preview_unchanged = all(record["status"] == "active" for record in store.records.values())
    apply = execute_retention_plan(
        _plan(),
        store=store,
        scope="project:project-1",
        project_id="project-1",
        project_by_record={record_id: "project-1" for record_id in store.records},
        authoritative_legal_hold_snapshot=_hold_snapshot(),
        actor="fixture-operator",
        actor_kind="human",
        mode="apply",
        confirm=True,
        backup_parity_evidence=_parity_evidence(),
        audit_id="apply-audit",
        evaluated_at=EVALUATED_AT,
    )
    safe = (
        preview.status == "preview"
        and preview.mutation_performed is False
        and preview.action_count == 2
        and preview.held_count == 2
        and preview.invalid_count == 1
        and preview_unchanged
        and apply.status == "applied"
        and apply.mutation_performed is True
        and apply.archived_count == 1
        and apply.deleted_count == 1
        and store.records.get("archive-1", {}).get("status") == "archived"
        and "delete-1" not in store.records
        and store.records.get("planner-hold", {}).get("status") == "active"
        and store.records.get("authority-hold", {}).get("status") == "active"
        and len(store.audit_records) == 1
    )
    return {
        "schema_version": CHECK_SCHEMA,
        "execution_schema": TRACE_RETENTION_EXECUTION_SCHEMA,
        "plan_schema": TRACE_RETENTION_PLAN_SCHEMA,
        "status": "pass" if safe else "fail",
        "preview": {
            "status": preview.status,
            "mutation_performed": preview.mutation_performed,
            "action_count": preview.action_count,
            "held_count": preview.held_count,
            "invalid_count": preview.invalid_count,
        },
        "apply": {
            "status": apply.status,
            "mutation_performed": apply.mutation_performed,
            "archived_count": apply.archived_count,
            "deleted_count": apply.deleted_count,
            "held_count": apply.held_count,
            "backup_parity_verified": apply.backup_parity_verified,
            "legal_hold_snapshot_verified": apply.legal_hold_snapshot_ref is not None,
            "audit_count": len(store.audit_records),
        },
        "licence_metadata_is_gate": False,
        "scope": "deterministic in-memory rehearsal; no database, network, or provider state changed",
    }


def _normalize_dsn(raw: str | None) -> str | None:
    value = str(raw or "").strip()
    if not value or "${" in value or "}" in value:
        return None
    if value.startswith("postgresql://"):
        return "postgresql+asyncpg://" + value.removeprefix("postgresql://")
    if value.startswith("postgres://"):
        return "postgresql+asyncpg://" + value.removeprefix("postgres://")
    return value if value.startswith("postgresql+asyncpg://") else None


async def _migration_version(storage: AgentStorage) -> str | None:
    async with storage.engine.connect() as connection:
        return await connection.scalar(sa.text("SELECT version_num FROM alembic_version"))


async def _live_rows(storage: AgentStorage) -> list[dict[str, Any]]:
    return await storage.list_native_trace_spans_by_trace(LIVE_TRACE_ID, limit=100)


async def _live_cleanup(storage: AgentStorage) -> int:
    async with storage.engine.begin() as connection:
        result = await connection.execute(
            sa.text("DELETE FROM native_trace_spans WHERE trace_id = :trace_id"),
            {"trace_id": LIVE_TRACE_ID},
        )
    return int(result.rowcount or 0)


async def _insert_live_fixture(storage: AgentStorage) -> None:
    now = datetime.now(UTC)
    old = now - timedelta(days=45)
    fresh = now - timedelta(days=2)
    rows = (
        ("expired-delete", "audit", old, {}, "retention.delete"),
        ("planner-hold", "mail", old, {"legal_hold": True}, "retention.planner_hold"),
        ("authority-hold", "worker", old, {}, "retention.authority_hold"),
        ("fresh-retain", "tool", fresh, {}, "retention.retain"),
    )
    for key, source_kind, started_at, attributes, operation in rows:
        await storage.create_native_trace_span(
            trace_id=LIVE_TRACE_ID,
            span_id=LIVE_SPANS[key],
            source_kind=source_kind,
            operation=operation,
            service="retention-fixture",
            status="success",
            started_at=started_at,
            ended_at=started_at,
            duration_ms=1,
            sampled=True,
            retention_until=started_at + timedelta(days=30),
            attributes={**attributes, "fixture": "aiat-retention-live-v1"},
        )


def _live_blocked(
    reason: str,
    *,
    database_configured: bool = False,
    local_database_access_performed: bool = False,
    mutation_performed: bool = False,
    migration_version: str | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": CHECK_SCHEMA,
        "execution_schema": TRACE_RETENTION_EXECUTION_SCHEMA,
        "mode": "local-postgres-retention",
        "status": "blocked",
        "reason": reason,
        "database_configured": database_configured,
        "local_database_access_performed": local_database_access_performed,
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "mutation_performed": mutation_performed,
        "licence_metadata_is_gate": False,
        "scope": "reserved native_trace_spans fixture and trace-scoped local Postgres transaction",
    }
    if migration_version is not None:
        report["migration_version"] = migration_version
    return report


async def _run_live(raw_dsn: str | None) -> dict[str, Any]:
    normalized_dsn = _normalize_dsn(raw_dsn)
    if normalized_dsn is None:
        return _live_blocked("retention_evidence_database_not_configured")

    storage = AgentStorage(normalized_dsn)
    migration_version: str | None = None
    cleanup_count = 0
    remaining_count: int | None = None
    try:
        await storage.connect()
        migration_version = await _migration_version(storage)
        if migration_version != EXPECTED_MIGRATION:
            return _live_blocked(
                "retention_evidence_migration_not_at_head",
                database_configured=True,
                local_database_access_performed=True,
                migration_version=migration_version,
            )

        await _live_cleanup(storage)
        await _insert_live_fixture(storage)
        initial_rows = await _live_rows(storage)
        evaluated_at = datetime.now(UTC)
        policy = TraceRetentionPolicy(retention_days=30, terminal_mode="delete")
        plan = plan_native_span_retention(
            initial_rows,
            policy,
            evaluated_at=evaluated_at,
            limit=100,
        )
        authority_record_id = next(
            candidate.record_id
            for candidate in plan.candidates
            if candidate.trace_id == LIVE_TRACE_ID
            and candidate.source_kind == "worker"
        )
        hold_snapshot = InMemoryRetentionLegalHoldRegistry(
            source_ref="hold-registry://aiat-retention-live-v1",
            holds=(
                RetentionLegalHold(
                    hold_id="aiat-retention-live-v1-authority-hold",
                    record_id=authority_record_id,
                    status="active",
                    authority_ref="registry-entry://aiat-retention-live-v1-authority-hold",
                ),
            ),
        ).read_snapshot(observed_at=evaluated_at)
        store = PostgresNativeTraceRetentionStore(
            normalized_dsn,
            trace_id=LIVE_TRACE_ID,
        )
        preview = execute_retention_plan(
            plan,
            store=store,
            scope=f"trace:{LIVE_TRACE_ID}",
            actor="fixture-planner",
            actor_kind="system",
            mode="preview",
            authoritative_legal_hold_snapshot=hold_snapshot,
            audit_id="aiat-retention-live-v1-preview",
            evaluated_at=evaluated_at,
        )
        preview_count = len(await _live_rows(storage))
        action_ids = tuple(
            candidate.record_id
            for candidate in plan.candidates
            if candidate.disposition == "delete" and candidate.record_id != authority_record_id
        )
        backup = store.prepare_backup_parity(
            action_ids,
            evidence_ref="backup://aiat-retention-live-v1",
        )
        applied = execute_retention_plan(
            plan,
            store=store,
            scope=f"trace:{LIVE_TRACE_ID}",
            actor="fixture-operator",
            actor_kind="human",
            mode="apply",
            confirm=True,
            backup_parity_evidence=backup,
            authoritative_legal_hold_snapshot=hold_snapshot,
            audit_id="aiat-retention-live-v1-apply",
            evaluated_at=evaluated_at,
        )
        after_apply_rows = await _live_rows(storage)
        cleanup_count = await _live_cleanup(storage)
        remaining_count = len(await _live_rows(storage))
        passed = (
            migration_version == EXPECTED_MIGRATION
            and len(initial_rows) == 4
            and plan.counts["delete"] == 2
            and plan.counts["legal_hold"] == 1
            and preview.mutation_performed is False
            and preview.action_count == 1
            and preview.held_count == 2
            and preview_count == 4
            and backup.source_record_count == 1
            and backup.clean_target_verified is True
            and applied.status == "applied"
            and applied.mutation_performed is True
            and applied.deleted_count == 1
            and applied.held_count == 2
            and len(after_apply_rows) == 3
            and cleanup_count == 3
            and remaining_count == 0
        )
        return {
            "schema_version": CHECK_SCHEMA,
            "execution_schema": TRACE_RETENTION_EXECUTION_SCHEMA,
            "mode": "local-postgres-retention",
            "status": "pass" if passed else "fail",
            "migration_version": migration_version,
            "initial_row_count": len(initial_rows),
            "plan_counts": plan.counts,
            "preview": {
                "mutation_performed": preview.mutation_performed,
                "action_count": preview.action_count,
                "held_count": preview.held_count,
                "row_count_after_preview": preview_count,
            },
            "backup": {
                "record_count": backup.source_record_count,
                "parity_verified": applied.backup_parity_verified,
                "clean_target_verified": backup.clean_target_verified,
            },
            "apply": {
                "status": applied.status,
                "mutation_performed": applied.mutation_performed,
                "deleted_count": applied.deleted_count,
                "held_count": applied.held_count,
                "row_count_after_apply": len(after_apply_rows),
            },
            "cleanup": {
                "deleted_count": cleanup_count,
                "remaining_count": remaining_count,
            },
            "database_configured": True,
            "local_database_access_performed": True,
            "external_network_access_performed": False,
            "external_provider_mutation_performed": False,
            "mutation_performed": True,
            "licence_metadata_is_gate": False,
            "scope": "reserved native_trace_spans fixture and one trace-scoped local Postgres transaction",
            "boundary": "local Postgres backup/read-back/delete only; no durable audit, erasure, provider, or restore rollback",
        }
    except (RetentionExecutionError, TypeError, ValueError) as exc:
        return {
            **_live_blocked(
                "local_postgres_retention_evidence_failed",
                database_configured=True,
                local_database_access_performed=True,
                mutation_performed=False,
                migration_version=migration_version,
            ),
            "failure_type": type(exc).__name__,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            **_live_blocked(
                "local_postgres_retention_evidence_failed",
                database_configured=True,
                local_database_access_performed=True,
                migration_version=migration_version,
            ),
            "failure_type": type(exc).__name__,
        }
    finally:
        if getattr(storage, "_engine", None) is not None:
            with suppress(Exception):
                await _live_cleanup(storage)
            with suppress(Exception):
                await storage.close()


def build_live_report(raw_dsn: str | None) -> dict[str, Any]:
    """Run the local Postgres certificate from a synchronous CLI boundary."""

    return asyncio.run(_run_live(raw_dsn))


def _blocked(reason: str) -> dict[str, object]:
    return {
        "schema_version": CHECK_SCHEMA,
        "execution_schema": TRACE_RETENTION_EXECUTION_SCHEMA,
        "status": "blocked",
        "reason": reason,
        "mutation_performed": False,
        "scope": "live retention adapter is not configured; no storage mutation performed",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument(
        "--live",
        action="store_true",
        help="run the reserved local Postgres retention certificate",
    )
    parser.add_argument(
        "--dsn",
        default=os.getenv(
            "AIAT_RETENTION_EVIDENCE_DSN",
            os.getenv("PGBOUNCER_DSN", os.getenv("POSTGRES_DSN", "")),
        ),
        help="Postgres DSN; defaults to AIAT_RETENTION_EVIDENCE_DSN/PGBOUNCER_DSN/POSTGRES_DSN",
    )
    args = parser.parse_args(argv)
    try:
        report = build_live_report(args.dsn) if args.live else build_report()
    except (RetentionExecutionError, TypeError, ValueError) as exc:
        report = _blocked(f"retention execution fixture failed: {type(exc).__name__}")
        report["detail"] = str(exc)[:160]
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"trace retention execution: {report['status']} — {report.get('reason', report.get('scope', ''))}")
    return 2 if report["status"] == "blocked" else (0 if report["status"] == "pass" else 1)


if __name__ == "__main__":
    sys.exit(main())
