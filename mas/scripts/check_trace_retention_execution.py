"""Rehearse guarded retention execution without a live storage mutation.

The fixture exercises project narrowing, authoritative hold IDs, backup parity,
audit metadata, and explicit human confirmation through the in-memory adapter.
``--live`` is intentionally fail-closed until a reviewed storage/recovery
adapter is connected; no provider or database is selected by this checker.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime

from mas_core.observability.retention import (
    TRACE_RETENTION_PLAN_SCHEMA,
    TraceRetentionCandidate,
    TraceRetentionPlan,
)
from mas_core.observability.retention_execution import (
    TRACE_RETENTION_EXECUTION_SCHEMA,
    InMemoryRetentionStore,
    RetentionBackupParityEvidence,
    RetentionExecutionError,
    execute_retention_plan,
)
from mas_core.observability.trace_evidence import TraceRetentionPolicy

CHECK_SCHEMA = "aiat.trace-retention-execution-check.v1"
EVALUATED_AT = datetime(2026, 1, 1, tzinfo=UTC)


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


def build_report() -> dict[str, object]:
    store = _store()
    preview = execute_retention_plan(
        _plan(),
        store=store,
        scope="project:project-1",
        project_id="project-1",
        project_by_record={record_id: "project-1" for record_id in store.records},
        authoritative_legal_hold_ids={"authority-hold"},
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
        authoritative_legal_hold_ids={"authority-hold"},
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
            "audit_count": len(store.audit_records),
        },
        "licence_metadata_is_gate": False,
        "scope": "deterministic in-memory rehearsal; no database, network, or provider state changed",
    }


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
        help="require a reviewed live storage/recovery adapter (currently blocked)",
    )
    args = parser.parse_args(argv)
    try:
        report = _blocked("live retention execution adapter is not configured") if args.live else build_report()
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
