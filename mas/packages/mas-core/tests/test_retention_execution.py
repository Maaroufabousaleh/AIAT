from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from mas_core.observability.retention import (
    TRACE_RETENTION_PLAN_SCHEMA,
    TraceRetentionCandidate,
    TraceRetentionPlan,
)
from mas_core.observability.retention_execution import (
    TRACE_RETENTION_EXECUTION_SCHEMA,
    InMemoryRetentionStore,
    RetentionAction,
    RetentionBackupParityEvidence,
    RetentionExecutionAudit,
    RetentionExecutionError,
    RetentionLegalHold,
    RetentionLegalHoldSnapshot,
    execute_retention_plan,
)
from mas_core.observability.trace_evidence import TraceRetentionPolicy

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _plan() -> TraceRetentionPlan:
    return TraceRetentionPlan(
        schema_version=TRACE_RETENTION_PLAN_SCHEMA,
        evaluated_at=NOW,
        cutoff=NOW,
        policy=TraceRetentionPolicy(retention_days=30, terminal_mode="delete"),
        candidates=(
            TraceRetentionCandidate(
                record_id="archive-1",
                trace_id="trace-1",
                source_kind="audit",
                disposition="archive",
                expires_at=NOW,
                reason="reviewed archive",
            ),
            TraceRetentionCandidate(
                record_id="delete-1",
                trace_id="trace-1",
                source_kind="tool",
                disposition="delete",
                expires_at=NOW,
                reason="reviewed delete",
            ),
            TraceRetentionCandidate(
                record_id="planner-hold",
                trace_id="trace-1",
                source_kind="mail",
                disposition="delete",
                expires_at=NOW,
                reason="legal hold active",
                legal_hold=True,
            ),
            TraceRetentionCandidate(
                record_id="authority-hold",
                trace_id="trace-1",
                source_kind="worker",
                disposition="delete",
                expires_at=NOW,
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


def _hold_snapshot(
    *,
    authority_project_id: str | None = "project-1",
    status: str = "active",
) -> RetentionLegalHoldSnapshot:
    return RetentionLegalHoldSnapshot(
        schema_version="aiat.trace-retention-hold-registry.v1",
        source_ref="hold-registry://fixture",
        observed_at=NOW,
        holds=(
            RetentionLegalHold(
                hold_id="hold-authority-1",
                record_id="authority-hold",
                project_id=authority_project_id,
                status=status,  # type: ignore[arg-type]
                authority_ref="registry-entry://hold-authority-1",
            ),
        ),
    )


def test_preview_is_non_mutating_and_resolves_holds() -> None:
    store = _store()
    before = {key: dict(value) for key, value in store.records.items()}

    result = execute_retention_plan(
        _plan(),
        store=store,
        scope="project:project-1",
        project_id="project-1",
        project_by_record={record_id: "project-1" for record_id in store.records},
        authoritative_legal_hold_snapshot=_hold_snapshot(),
        actor="planner",
        actor_kind="system",
        mode="preview",
        audit_id="preview-audit",
        evaluated_at=NOW,
    )

    assert result.schema_version == TRACE_RETENTION_EXECUTION_SCHEMA
    assert result.status == "preview"
    assert result.mutation_performed is False
    assert result.action_count == 2
    assert result.archived_count == 1
    assert result.deleted_count == 1
    assert result.held_count == 2
    assert result.invalid_count == 1
    assert store.records == before
    assert store.audit_records == []


def test_apply_requires_human_confirmation_and_backup_parity() -> None:
    store = _store()
    kwargs = {
        "store": store,
        "scope": "project:project-1",
        "project_id": "project-1",
        "project_by_record": {record_id: "project-1" for record_id in store.records},
        "authoritative_legal_hold_snapshot": _hold_snapshot(),
        "actor": "operator",
        "mode": "apply",
        "audit_id": "apply-audit",
        "evaluated_at": NOW,
    }

    with pytest.raises(RetentionExecutionError, match="explicit confirmation"):
        execute_retention_plan(_plan(), confirm=False, **kwargs)
    with pytest.raises(RetentionExecutionError, match="backup parity"):
        execute_retention_plan(_plan(), confirm=True, **kwargs)
    with pytest.raises(RetentionExecutionError, match="human actor"):
        execute_retention_plan(
            _plan(),
            confirm=True,
            backup_parity_evidence=_parity_evidence(),
            actor="operator",
            actor_kind="system",
            **{key: value for key, value in kwargs.items() if key != "actor"},
        )
    assert all(record["status"] == "active" for record in store.records.values())
    assert store.audit_records == []


def test_apply_is_project_scoped_and_audited() -> None:
    store = _store()
    result = execute_retention_plan(
        _plan(),
        store=store,
        scope="project:project-1",
        project_id="project-1",
        project_by_record={record_id: "project-1" for record_id in store.records},
        authoritative_legal_hold_snapshot=_hold_snapshot(),
        actor="operator",
        actor_kind="human",
        mode="apply",
        confirm=True,
        backup_parity_evidence=_parity_evidence(),
        audit_id="apply-audit",
        evaluated_at=NOW,
    )

    assert result.status == "applied"
    assert result.mutation_performed is True
    assert result.backup_parity_verified is True
    assert result.legal_hold_snapshot_ref == "hold-registry://fixture"
    assert store.records["archive-1"]["status"] == "archived"
    assert "delete-1" not in store.records
    assert store.records["planner-hold"]["status"] == "active"
    assert store.records["authority-hold"]["status"] == "active"
    assert store.audit_records == [
        {
            "schema_version": TRACE_RETENTION_EXECUTION_SCHEMA,
            "audit_id": "apply-audit",
            "scope": "project:project-1",
            "actor": "operator",
            "actor_kind": "human",
            "action_count": 2,
            "backup_evidence_ref": "backup://fixture",
            "backup_manifest_sha256": "a" * 64,
            "backup_record_count": 4,
            "clean_target_verified": True,
            "legal_hold_snapshot_ref": "hold-registry://fixture",
            "active_legal_hold_count": 1,
            "evaluated_at": NOW.isoformat(),
        }
    ]


def test_retention_audit_envelope_is_typed_and_bounded() -> None:
    audit = RetentionExecutionAudit(
        schema_version=TRACE_RETENTION_EXECUTION_SCHEMA,
        audit_id="typed-audit",
        scope="project:project-1",
        actor="operator",
        actor_kind="human",
        action_count=2,
        backup_evidence_ref="backup://fixture",
        backup_manifest_sha256="A" * 64,
        backup_record_count=4,
        clean_target_verified=True,
        legal_hold_snapshot_ref="hold-registry://fixture",
        active_legal_hold_count=1,
        evaluated_at=NOW,
    )

    assert audit.as_dict() == {
        "schema_version": TRACE_RETENTION_EXECUTION_SCHEMA,
        "audit_id": "typed-audit",
        "scope": "project:project-1",
        "actor": "operator",
        "actor_kind": "human",
        "action_count": 2,
        "backup_evidence_ref": "backup://fixture",
        "backup_manifest_sha256": "a" * 64,
        "backup_record_count": 4,
        "clean_target_verified": True,
        "legal_hold_snapshot_ref": "hold-registry://fixture",
        "active_legal_hold_count": 1,
        "evaluated_at": NOW.isoformat(),
    }

    with pytest.raises(RetentionExecutionError, match="backup manifest"):
        replace(audit, backup_manifest_sha256="not-a-digest").validate()


def test_project_scope_mismatch_fails_before_adapter_mutation() -> None:
    store = _store()
    before = {key: dict(value) for key, value in store.records.items()}
    with pytest.raises(RetentionExecutionError, match="outside the selected project"):
        execute_retention_plan(
            _plan(),
            store=store,
            scope="project:project-1",
            project_id="project-1",
            project_by_record={
                **{record_id: "project-1" for record_id in store.records},
                "delete-1": "project-2",
            },
            actor="operator",
            actor_kind="human",
            mode="apply",
            confirm=True,
            backup_parity_evidence=_parity_evidence(),
            authoritative_legal_hold_snapshot=_hold_snapshot(),
            audit_id="scope-audit",
            evaluated_at=NOW,
        )
    assert store.records == before
    assert store.audit_records == []


def test_apply_requires_authoritative_hold_snapshot() -> None:
    store = _store()
    with pytest.raises(RetentionExecutionError, match="authoritative legal-hold snapshot"):
        execute_retention_plan(
            _plan(),
            store=store,
            scope="project:project-1",
            project_id="project-1",
            project_by_record={record_id: "project-1" for record_id in store.records},
            actor="operator",
            actor_kind="human",
            mode="apply",
            confirm=True,
            backup_parity_evidence=_parity_evidence(),
            audit_id="missing-hold-snapshot-audit",
            evaluated_at=NOW,
        )
    assert all(record["status"] == "active" for record in store.records.values())
    assert store.audit_records == []


def test_hold_snapshot_rejects_duplicate_record_ids() -> None:
    snapshot = RetentionLegalHoldSnapshot(
        schema_version="aiat.trace-retention-hold-registry.v1",
        source_ref="hold-registry://duplicate",
        observed_at=NOW,
        holds=(
            RetentionLegalHold(
                hold_id="hold-1",
                record_id="same-record",
                status="active",
                authority_ref="registry-entry://hold-1",
            ),
            RetentionLegalHold(
                hold_id="hold-2",
                record_id="same-record",
                status="released",
                authority_ref="registry-entry://hold-2",
            ),
        ),
    )
    with pytest.raises(RetentionExecutionError, match="duplicate legal hold record ID"):
        snapshot.validate()


def test_project_scoped_hold_mismatch_fails_closed_before_mutation() -> None:
    store = _store()
    before = {key: dict(value) for key, value in store.records.items()}
    with pytest.raises(RetentionExecutionError, match="legal hold is outside"):
        execute_retention_plan(
            _plan(),
            store=store,
            scope="project:project-1",
            project_id="project-1",
            project_by_record={record_id: "project-1" for record_id in store.records},
            authoritative_legal_hold_snapshot=_hold_snapshot(
                authority_project_id="project-2"
            ),
            actor="operator",
            actor_kind="human",
            mode="apply",
            confirm=True,
            backup_parity_evidence=_parity_evidence(),
            audit_id="hold-scope-audit",
            evaluated_at=NOW,
        )
    assert store.records == before
    assert store.audit_records == []


def test_released_hold_does_not_suppress_retention_action() -> None:
    store = _store()
    result = execute_retention_plan(
        _plan(),
        store=store,
        scope="project:project-1",
        project_id="project-1",
        project_by_record={record_id: "project-1" for record_id in store.records},
        authoritative_legal_hold_snapshot=_hold_snapshot(status="released"),
        actor="planner",
        actor_kind="system",
        mode="preview",
        audit_id="released-hold-audit",
        evaluated_at=NOW,
    )
    assert result.action_count == 3
    assert result.held_count == 1
    assert result.mutation_performed is False


def test_apply_rejects_manifest_drift_or_unverified_restore_target() -> None:
    store = _store()
    mismatched = replace(_parity_evidence(), restored_manifest_sha256="b" * 64)
    with pytest.raises(RetentionExecutionError, match="manifest digests"):
        execute_retention_plan(
            _plan(),
            store=store,
            scope="project:project-1",
            project_id="project-1",
            project_by_record={record_id: "project-1" for record_id in store.records},
            actor="operator",
            actor_kind="human",
            mode="apply",
            confirm=True,
            backup_parity_evidence=mismatched,
            authoritative_legal_hold_snapshot=_hold_snapshot(),
            audit_id="drift-audit",
            evaluated_at=NOW,
        )

    unclean = RetentionBackupParityEvidence(
        evidence_ref="backup://fixture",
        source_manifest_sha256="a" * 64,
        backup_manifest_sha256="a" * 64,
        restored_manifest_sha256="a" * 64,
        source_record_count=4,
        backup_record_count=4,
        restored_record_count=4,
        checked_record_count=4,
        clean_target_verified=False,
    )
    with pytest.raises(RetentionExecutionError, match="clean restore target"):
        execute_retention_plan(
            _plan(),
            store=store,
            scope="project:project-1",
            project_id="project-1",
            project_by_record={record_id: "project-1" for record_id in store.records},
            actor="operator",
            actor_kind="human",
            mode="apply",
            confirm=True,
            backup_parity_evidence=unclean,
            authoritative_legal_hold_snapshot=_hold_snapshot(),
            audit_id="unclean-audit",
            evaluated_at=NOW,
        )
    assert all(record["status"] == "active" for record in store.records.values())
    assert store.audit_records == []


def test_in_memory_adapter_rejects_partial_unknown_action() -> None:
    store = InMemoryRetentionStore(
        records={"known": {"project_id": "project-1", "status": "active"}}
    )
    with pytest.raises(RetentionExecutionError, match="missing"):
        store.apply_retention_actions(
            [
                # The first action is valid, but the complete batch must be
                # rejected before any state is replaced.
                RetentionAction(record_id="known", action="delete"),
                RetentionAction(record_id="unknown", action="delete"),
            ],
            audit={"audit_id": "atomic-audit"},
        )
    assert store.records["known"]["status"] == "active"
    assert store.audit_records == []


def test_in_memory_adapter_rejects_unsupported_action() -> None:
    store = InMemoryRetentionStore(
        records={"known": {"project_id": "project-1", "status": "active"}}
    )
    with pytest.raises(RetentionExecutionError, match="unsupported"):
        store.apply_retention_actions(
            [RetentionAction(record_id="known", action="purge")],  # type: ignore[arg-type]
            audit={"audit_id": "invalid-action-audit"},
        )
    assert store.records["known"]["status"] == "active"
    assert store.audit_records == []
