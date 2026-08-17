"""Guarded retention execution over an explicit storage adapter.

The read-only retention planner deliberately stops before storage mutation.  A
future recovery worker can use this contract to apply an already reviewed plan
without moving authority into the planner: project scope, authoritative hold
IDs, backup/read-back evidence, and human confirmation are all required before
``apply`` mode reaches an adapter.  The in-memory adapter is a deterministic
rehearsal, not a production database implementation.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from .retention import TRACE_RETENTION_PLAN_SCHEMA, TraceRetentionPlan

TRACE_RETENTION_EXECUTION_SCHEMA = "aiat.trace-retention-execution.v1"
RetentionExecutionMode = Literal["preview", "apply"]
RetentionExecutionStatus = Literal["preview", "applied"]
RetentionActionKind = Literal["archive", "delete"]
RetentionActorKind = Literal["human", "system"]


class RetentionExecutionError(ValueError):
    """Raised when a retention plan cannot cross the mutation boundary."""


@dataclass(frozen=True, slots=True)
class RetentionAction:
    """One validated storage action derived from a retention plan."""

    record_id: str
    action: RetentionActionKind
    project_id: str | None = None


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


def _token(value: Any, *, name: str, max_length: int = 240) -> str:
    rendered = str(value or "").strip()
    if not rendered:
        raise RetentionExecutionError(f"{name} is required")
    if len(rendered) > max_length:
        raise RetentionExecutionError(f"{name} exceeds the bounded length")
    return rendered


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
    backup_parity_verified: bool = False,
    backup_evidence_ref: str | None = None,
    project_id: str | None = None,
    project_by_record: Mapping[str, str] | None = None,
    authoritative_legal_hold_ids: Collection[str] = (),
    audit_id: str = "retention-execution-fixture-audit",
    evaluated_at: datetime | None = None,
) -> RetentionExecutionResult:
    """Preview or apply a retention plan through one guarded adapter call.

    Apply mode is intentionally narrow: it requires a project or trace scope,
    a human actor with explicit confirmation, a backup/read-back evidence
    reference, and a successful parity flag.  An authoritative hold ID or the
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

    normalized_backup_ref = (
        _token(backup_evidence_ref, name="backup_evidence_ref", max_length=240)
        if backup_evidence_ref is not None
        else None
    )
    if mode == "apply":
        if actor_kind != "human":
            raise RetentionExecutionError("apply requires a human actor")
        if not confirm:
            raise RetentionExecutionError("apply requires explicit confirmation")
        if not backup_parity_verified or normalized_backup_ref is None:
            raise RetentionExecutionError(
                "apply requires verified backup parity and an evidence reference"
            )

    hold_ids = {str(record_id).strip() for record_id in authoritative_legal_hold_ids if str(record_id).strip()}
    actions: list[RetentionAction] = []
    held_count = 0
    invalid_count = 0
    notices: list[str] = []
    for candidate in plan.candidates:
        if candidate.disposition == "invalid":
            invalid_count += 1
            continue
        if candidate.disposition == "retain":
            if candidate.legal_hold or candidate.record_id in hold_ids:
                held_count += 1
            continue
        if candidate.legal_hold or candidate.record_id in hold_ids:
            held_count += 1
            continue
        candidate_project_id: str | None = None
        if normalized_project_id is not None:
            assert project_by_record is not None
            candidate_project_id = str(project_by_record.get(candidate.record_id) or "").strip() or None
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
        mutation_performed=mode == "apply" and bool(actions),
        notices=tuple(notices[:20]),
    )


__all__ = [
    "TRACE_RETENTION_EXECUTION_SCHEMA",
    "RetentionAction",
    "RetentionActionKind",
    "RetentionActorKind",
    "RetentionExecutionError",
    "RetentionExecutionMode",
    "RetentionExecutionResult",
    "RetentionExecutionStatus",
    "RetentionMutationStore",
    "InMemoryRetentionStore",
    "execute_retention_plan",
]
