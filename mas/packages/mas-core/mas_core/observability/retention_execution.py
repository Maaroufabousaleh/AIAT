"""Guarded retention execution over an explicit storage adapter.

The read-only retention planner deliberately stops before storage mutation.  A
future recovery worker can use this contract to apply an already reviewed plan
without moving authority into the planner: project scope, a typed authoritative
hold snapshot, backup/read-back evidence, and human confirmation are all
required before ``apply`` mode reaches an adapter.  The in-memory adapter is a
deterministic rehearsal, not a production database implementation.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from .retention import TRACE_RETENTION_PLAN_SCHEMA, TraceRetentionPlan

TRACE_RETENTION_EXECUTION_SCHEMA = "aiat.trace-retention-execution.v1"
TRACE_RETENTION_HOLD_REGISTRY_SCHEMA = "aiat.trace-retention-hold-registry.v1"
RetentionExecutionMode = Literal["preview", "apply"]
RetentionExecutionStatus = Literal["preview", "applied"]
RetentionActionKind = Literal["archive", "delete"]
RetentionActorKind = Literal["human", "system"]
RetentionLegalHoldStatus = Literal["active", "released"]


class RetentionExecutionError(ValueError):
    """Raised when a retention plan cannot cross the mutation boundary."""


def _token(value: Any, *, name: str, max_length: int = 240) -> str:
    rendered = str(value or "").strip()
    if not rendered:
        raise RetentionExecutionError(f"{name} is required")
    if len(rendered) > max_length:
        raise RetentionExecutionError(f"{name} exceeds the bounded length")
    return rendered


@dataclass(frozen=True, slots=True)
class RetentionAction:
    """One validated storage action derived from a retention plan."""

    record_id: str
    action: RetentionActionKind
    project_id: str | None = None


@dataclass(frozen=True, slots=True)
class RetentionLegalHold:
    """One current or released record from an authoritative hold registry."""

    hold_id: str
    record_id: str
    status: RetentionLegalHoldStatus
    authority_ref: str
    project_id: str | None = None

    def validate(self) -> None:
        _token(self.hold_id, name="hold_id", max_length=160)
        _token(self.record_id, name="record_id", max_length=160)
        _token(self.authority_ref, name="authority_ref", max_length=240)
        if self.status not in {"active", "released"}:
            raise RetentionExecutionError(
                "legal hold status must be active or released"
            )
        if self.project_id is not None:
            _token(self.project_id, name="hold_project_id", max_length=160)

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "hold_id": _token(self.hold_id, name="hold_id", max_length=160),
            "record_id": _token(self.record_id, name="record_id", max_length=160),
            "status": self.status,
            "authority_ref": _token(self.authority_ref, name="authority_ref", max_length=240),
            "project_id": (
                _token(self.project_id, name="hold_project_id", max_length=160)
                if self.project_id is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class RetentionLegalHoldSnapshot:
    """Bounded, immutable read of the current authoritative hold registry."""

    schema_version: str
    source_ref: str
    observed_at: datetime
    holds: tuple[RetentionLegalHold, ...] = ()

    def validate(self) -> None:
        if self.schema_version != TRACE_RETENTION_HOLD_REGISTRY_SCHEMA:
            raise RetentionExecutionError("unsupported legal hold registry schema")
        _token(self.source_ref, name="hold_source_ref", max_length=240)
        if not isinstance(self.observed_at, datetime) or self.observed_at.tzinfo is None:
            raise RetentionExecutionError(
                "legal hold snapshot observed_at must be timezone-aware"
            )
        hold_ids: set[str] = set()
        record_ids: set[str] = set()
        for hold in self.holds:
            if not isinstance(hold, RetentionLegalHold):
                raise TypeError("legal hold snapshot entries must be RetentionLegalHold")
            hold.validate()
            hold_id = _token(hold.hold_id, name="hold_id", max_length=160)
            record_id = _token(hold.record_id, name="record_id", max_length=160)
            if hold_id in hold_ids:
                raise RetentionExecutionError("duplicate legal hold ID in snapshot")
            if record_id in record_ids:
                raise RetentionExecutionError(
                    "duplicate legal hold record ID in snapshot"
                )
            hold_ids.add(hold_id)
            record_ids.add(record_id)

    def active_holds_by_record(self) -> dict[str, RetentionLegalHold]:
        self.validate()
        return {
            _token(hold.record_id, name="record_id", max_length=160): hold
            for hold in self.holds
            if hold.status == "active"
        }

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "source_ref": _token(self.source_ref, name="hold_source_ref", max_length=240),
            "observed_at": self.observed_at.astimezone(UTC).isoformat(),
            "active_hold_count": sum(hold.status == "active" for hold in self.holds),
            "holds": [hold.as_dict() for hold in self.holds],
        }


@dataclass(frozen=True, slots=True)
class RetentionBackupParityEvidence:
    """Secret-safe proof that a backup was read back without drift.

    The execution boundary does not inspect provider payloads.  It accepts a
    bounded evidence record instead: source, backup, and restored manifests
    must share one SHA-256 digest, record counts must agree, every restored
    record must have been checked, and the target must have been verified
    empty before restore.  A live adapter remains responsible for producing
    this record from its own manifest/read-back transaction.
    """

    evidence_ref: str
    source_manifest_sha256: str
    backup_manifest_sha256: str
    restored_manifest_sha256: str
    source_record_count: int
    backup_record_count: int
    restored_record_count: int
    checked_record_count: int
    clean_target_verified: bool

    def validate(self) -> None:
        normalized_ref = str(self.evidence_ref or "").strip()
        if not normalized_ref:
            raise RetentionExecutionError("backup parity evidence reference is required")
        if len(normalized_ref) > 240:
            raise RetentionExecutionError("backup parity evidence reference exceeds the bounded length")
        digests = {
            name: str(value or "").strip().lower()
            for name, value in (
                ("source_manifest_sha256", self.source_manifest_sha256),
                ("backup_manifest_sha256", self.backup_manifest_sha256),
                ("restored_manifest_sha256", self.restored_manifest_sha256),
            )
        }
        for name, digest in digests.items():
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise RetentionExecutionError(
                    f"{name} must be a 64-character hexadecimal digest"
                )
        if len(set(digests.values())) != 1:
            raise RetentionExecutionError(
                "backup parity evidence manifest digests do not match"
            )
        counts = {
            name: value
            for name, value in (
                ("source_record_count", self.source_record_count),
                ("backup_record_count", self.backup_record_count),
                ("restored_record_count", self.restored_record_count),
                ("checked_record_count", self.checked_record_count),
            )
        }
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts.values()):
            raise RetentionExecutionError(
                "backup parity evidence record counts must be non-negative integers"
            )
        if len({self.source_record_count, self.backup_record_count, self.restored_record_count}) != 1:
            raise RetentionExecutionError(
                "backup parity evidence record counts do not match"
            )
        if self.checked_record_count != self.restored_record_count:
            raise RetentionExecutionError(
                "backup parity evidence checked count does not match restored count"
            )
        if self.clean_target_verified is not True:
            raise RetentionExecutionError(
                "backup parity evidence requires a verified clean restore target"
            )

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        normalized_digests = {
            name: str(value or "").strip().lower()
            for name, value in (
                ("source_manifest_sha256", self.source_manifest_sha256),
                ("backup_manifest_sha256", self.backup_manifest_sha256),
                ("restored_manifest_sha256", self.restored_manifest_sha256),
            )
        }
        return {
            "evidence_ref": str(self.evidence_ref).strip(),
            "source_manifest_sha256": normalized_digests["source_manifest_sha256"],
            "backup_manifest_sha256": normalized_digests["backup_manifest_sha256"],
            "restored_manifest_sha256": normalized_digests["restored_manifest_sha256"],
            "source_record_count": self.source_record_count,
            "backup_record_count": self.backup_record_count,
            "restored_record_count": self.restored_record_count,
            "checked_record_count": self.checked_record_count,
            "clean_target_verified": self.clean_target_verified,
        }


class RetentionMutationStore(Protocol):
    """Atomic adapter boundary used by the execution contract."""

    def apply_retention_actions(
        self,
        actions: Sequence[RetentionAction],
        *,
        audit: Mapping[str, Any],
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class RetentionExecutionResult:
    """Secret-safe result of a preview or explicitly confirmed apply."""

    schema_version: str
    mode: RetentionExecutionMode
    status: RetentionExecutionStatus
    scope: str
    actor: str
    actor_kind: RetentionActorKind
    audit_id: str
    evaluated_at: datetime
    action_ids: tuple[str, ...] = ()
    archived_count: int = 0
    deleted_count: int = 0
    held_count: int = 0
    invalid_count: int = 0
    backup_evidence_ref: str | None = None
    backup_parity_verified: bool = False
    legal_hold_snapshot_ref: str | None = None
    mutation_performed: bool = False
    notices: tuple[str, ...] = ()

    @property
    def action_count(self) -> int:
        return self.archived_count + self.deleted_count

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "mode": self.mode,
            "status": self.status,
            "scope": self.scope,
            "actor": self.actor,
            "actor_kind": self.actor_kind,
            "audit_id": self.audit_id,
            "evaluated_at": self.evaluated_at.isoformat(),
            "action_ids": list(self.action_ids),
            "action_count": self.action_count,
            "archived_count": self.archived_count,
            "deleted_count": self.deleted_count,
            "held_count": self.held_count,
            "invalid_count": self.invalid_count,
            "backup_evidence_ref": self.backup_evidence_ref,
            "backup_parity_verified": self.backup_parity_verified,
            "legal_hold_snapshot_ref": self.legal_hold_snapshot_ref,
            "mutation_performed": self.mutation_performed,
            "notices": list(self.notices),
        }


@dataclass(slots=True)
class InMemoryRetentionStore:
    """Atomic fixture adapter for retention execution rehearsals.

    ``records`` maps a bounded record ID to its project and active state.  The
    adapter validates every action before replacing the state map, so an
    invalid action cannot leave a partial fixture mutation behind.
    """

    records: dict[str, dict[str, str]] = field(default_factory=dict)
    audit_records: list[dict[str, Any]] = field(default_factory=list)

    def apply_retention_actions(
        self,
        actions: Sequence[RetentionAction],
        *,
        audit: Mapping[str, Any],
    ) -> None:
        staged = {record_id: dict(value) for record_id, value in self.records.items()}
        seen: set[str] = set()
        for action in actions:
            if action.record_id in seen:
                raise RetentionExecutionError("duplicate retention action")
            seen.add(action.record_id)
            if action.action not in {"archive", "delete"}:
                raise RetentionExecutionError(
                    f"unsupported retention action: {action.action}"
                )
            record = staged.get(action.record_id)
            if record is None:
                raise RetentionExecutionError(
                    f"retention record is missing from the adapter: {action.record_id}"
                )
            if record.get("status") != "active":
                raise RetentionExecutionError(
                    f"retention record is not active: {action.record_id}"
                )
            if action.project_id is not None and record.get("project_id") != action.project_id:
                raise RetentionExecutionError(
                    f"retention record escaped project scope: {action.record_id}"
                )

        for action in actions:
            if action.action == "archive":
                staged[action.record_id]["status"] = "archived"
            else:
                del staged[action.record_id]
        self.records = staged
        self.audit_records.append(dict(audit))


def _evaluated_at(value: datetime | None) -> datetime:
    candidate = value or datetime.now(UTC)
    if candidate.tzinfo is None:
        return candidate.replace(tzinfo=UTC)
    return candidate.astimezone(UTC)


def execute_retention_plan(
    plan: TraceRetentionPlan,
    *,
    store: RetentionMutationStore,
    scope: str,
    actor: str,
    actor_kind: RetentionActorKind = "human",
    mode: RetentionExecutionMode = "preview",
    confirm: bool = False,
    backup_parity_evidence: RetentionBackupParityEvidence | None = None,
    project_id: str | None = None,
    project_by_record: Mapping[str, str] | None = None,
    authoritative_legal_hold_snapshot: RetentionLegalHoldSnapshot | None = None,
    audit_id: str = "retention-execution-fixture-audit",
    evaluated_at: datetime | None = None,
) -> RetentionExecutionResult:
    """Preview or apply a retention plan through one guarded adapter call.

    Apply mode is intentionally narrow: it requires a project or trace scope,
    a human actor with explicit confirmation, typed backup/read-back parity
    evidence, and a typed authoritative hold snapshot.  A current hold or the
    planner's explicit boolean hold marker always suppresses an action.  The
    adapter receives one complete action set, allowing it to provide its own
    transaction/rollback boundary.
    """

    if not isinstance(plan, TraceRetentionPlan):
        raise TypeError("plan must be a TraceRetentionPlan")
    if plan.schema_version != TRACE_RETENTION_PLAN_SCHEMA:
        raise RetentionExecutionError("unsupported retention plan schema")
    normalized_scope = _token(scope, name="scope", max_length=160)
    normalized_actor = _token(actor, name="actor", max_length=160)
    normalized_audit_id = _token(audit_id, name="audit_id", max_length=160)
    if actor_kind not in {"human", "system"}:
        raise RetentionExecutionError("actor_kind must be human or system")
    if mode not in {"preview", "apply"}:
        raise RetentionExecutionError("mode must be preview or apply")
    normalized_project_id = str(project_id).strip() if project_id is not None else None
    if normalized_project_id == "":
        raise RetentionExecutionError("project_id must not be blank")
    if normalized_project_id is not None:
        if normalized_scope != f"project:{normalized_project_id}":
            raise RetentionExecutionError("project scope must match project_id")
        if not isinstance(project_by_record, Mapping):
            raise RetentionExecutionError("project scope requires project_by_record")
    elif not (
        normalized_scope.startswith("trace:") or normalized_scope.startswith("project:")
    ):
        raise RetentionExecutionError("execution scope must be trace:<id> or project:<id>")

    if backup_parity_evidence is not None and not isinstance(
        backup_parity_evidence, RetentionBackupParityEvidence
    ):
        raise TypeError("backup_parity_evidence must be RetentionBackupParityEvidence")
    if backup_parity_evidence is not None:
        backup_parity_evidence.validate()
    normalized_backup_ref = (
        _token(backup_parity_evidence.evidence_ref, name="backup_evidence_ref", max_length=240)
        if backup_parity_evidence is not None
        else None
    )
    if mode == "apply":
        if actor_kind != "human":
            raise RetentionExecutionError("apply requires a human actor")
        if not confirm:
            raise RetentionExecutionError("apply requires explicit confirmation")
        if backup_parity_evidence is None or normalized_backup_ref is None:
            raise RetentionExecutionError(
                "apply requires typed verified backup parity evidence"
            )

    if authoritative_legal_hold_snapshot is not None and not isinstance(
        authoritative_legal_hold_snapshot, RetentionLegalHoldSnapshot
    ):
        raise TypeError(
            "authoritative_legal_hold_snapshot must be RetentionLegalHoldSnapshot"
        )
    if authoritative_legal_hold_snapshot is not None:
        authoritative_legal_hold_snapshot.validate()
    normalized_hold_ref = (
        _token(
            authoritative_legal_hold_snapshot.source_ref,
            name="hold_source_ref",
            max_length=240,
        )
        if authoritative_legal_hold_snapshot is not None
        else None
    )
    active_holds = (
        authoritative_legal_hold_snapshot.active_holds_by_record()
        if authoritative_legal_hold_snapshot is not None
        else {}
    )
    if mode == "apply" and normalized_hold_ref is None:
        raise RetentionExecutionError(
            "apply requires a typed authoritative legal-hold snapshot"
        )

    actions: list[RetentionAction] = []
    held_count = 0
    invalid_count = 0
    notices: list[str] = []
    for candidate in plan.candidates:
        if candidate.disposition == "invalid":
            invalid_count += 1
            continue
        candidate_record_id = str(candidate.record_id or "").strip()
        authority_hold = active_holds.get(candidate_record_id)
        if (
            authority_hold is not None
            and normalized_project_id is not None
            and authority_hold.project_id not in {None, normalized_project_id}
        ):
            raise RetentionExecutionError(
                f"legal hold is outside the selected project: {candidate.record_id}"
            )
        has_hold = candidate.legal_hold or authority_hold is not None
        if candidate.disposition == "retain":
            if has_hold:
                held_count += 1
            continue
        if has_hold:
            held_count += 1
            continue
        candidate_project_id: str | None = None
        if normalized_project_id is not None:
            assert project_by_record is not None
            candidate_project_id = str(project_by_record.get(candidate_record_id) or "").strip() or None
            if candidate_project_id != normalized_project_id:
                raise RetentionExecutionError(
                    f"retention candidate is outside the selected project: {candidate.record_id}"
                )
        actions.append(
            RetentionAction(
                record_id=_token(candidate.record_id, name="record_id", max_length=160),
                action=candidate.disposition,
                project_id=candidate_project_id,
            )
        )

    when = _evaluated_at(evaluated_at)
    if mode == "apply":
        store.apply_retention_actions(
            actions,
            audit={
                "schema_version": TRACE_RETENTION_EXECUTION_SCHEMA,
                "audit_id": normalized_audit_id,
                "scope": normalized_scope,
                "actor": normalized_actor,
                "actor_kind": actor_kind,
                "action_count": len(actions),
                "backup_evidence_ref": normalized_backup_ref,
                "backup_manifest_sha256": backup_parity_evidence.backup_manifest_sha256.strip().lower(),
                "backup_record_count": backup_parity_evidence.backup_record_count,
                "clean_target_verified": backup_parity_evidence.clean_target_verified,
                "legal_hold_snapshot_ref": normalized_hold_ref,
                "active_legal_hold_count": len(active_holds),
                "evaluated_at": when.isoformat(),
            },
        )
    else:
        notices.append("preview mode did not call the mutation adapter")

    archive_count = sum(1 for action in actions if action.action == "archive")
    delete_count = sum(1 for action in actions if action.action == "delete")
    return RetentionExecutionResult(
        schema_version=TRACE_RETENTION_EXECUTION_SCHEMA,
        mode=mode,
        status="applied" if mode == "apply" else "preview",
        scope=normalized_scope,
        actor=normalized_actor,
        actor_kind=actor_kind,
        audit_id=normalized_audit_id,
        evaluated_at=when,
        action_ids=tuple(action.record_id for action in actions),
        archived_count=archive_count,
        deleted_count=delete_count,
        held_count=held_count,
        invalid_count=invalid_count,
        backup_evidence_ref=normalized_backup_ref,
        backup_parity_verified=backup_parity_evidence is not None,
        legal_hold_snapshot_ref=normalized_hold_ref,
        mutation_performed=mode == "apply" and bool(actions),
        notices=tuple(notices[:20]),
    )


__all__ = [
    "TRACE_RETENTION_EXECUTION_SCHEMA",
    "TRACE_RETENTION_HOLD_REGISTRY_SCHEMA",
    "RetentionAction",
    "RetentionActionKind",
    "RetentionActorKind",
    "RetentionBackupParityEvidence",
    "RetentionExecutionError",
    "RetentionExecutionMode",
    "RetentionExecutionResult",
    "RetentionExecutionStatus",
    "RetentionMutationStore",
    "RetentionLegalHold",
    "RetentionLegalHoldSnapshot",
    "RetentionLegalHoldStatus",
    "InMemoryRetentionStore",
    "execute_retention_plan",
]
