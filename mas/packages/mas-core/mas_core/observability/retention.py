"""Deterministic, payload-free trace-retention planning.

The trace read model exposes retention policy metadata, while actual deletion
or archival is an operational storage action.  This module keeps the decision
boundary explicit and testable: it classifies bounded native-span metadata
without reading payloads and never mutates a store.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .trace_evidence import TraceRetentionPolicy

TRACE_RETENTION_PLAN_SCHEMA = "aiat.trace-retention-plan.v1"
RetentionDisposition = Literal["retain", "archive", "delete", "invalid"]


class TraceRetentionCounts(BaseModel):
    """Stable count fields for one bounded retention-plan response."""

    model_config = ConfigDict(extra="forbid")

    retain: int = Field(default=0, ge=0)
    archive: int = Field(default=0, ge=0)
    delete: int = Field(default=0, ge=0)
    invalid: int = Field(default=0, ge=0)


class TraceRetentionCandidateResponse(BaseModel):
    """Secret-safe metadata for one retention decision."""

    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(min_length=1, max_length=160)
    trace_id: str | None = Field(default=None, max_length=160)
    source_kind: str | None = Field(default=None, max_length=80)
    disposition: RetentionDisposition
    expires_at: str | None = Field(default=None, max_length=80)
    reason: str = Field(min_length=1, max_length=240)


class TraceRetentionPlanResponse(BaseModel):
    """Versioned read-only retention-plan API response."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = TRACE_RETENTION_PLAN_SCHEMA
    evaluated_at: str = Field(min_length=1, max_length=80)
    cutoff: str = Field(min_length=1, max_length=80)
    policy: TraceRetentionPolicy
    counts: TraceRetentionCounts
    deletion_ids: list[str] = Field(default_factory=list, max_length=20_000)
    notices: list[str] = Field(default_factory=list, max_length=100)
    candidates: list[TraceRetentionCandidateResponse] = Field(default_factory=list, max_length=20_000)
    mode: Literal["read-only-plan"] = "read-only-plan"
    mutation_performed: Literal[False] = False
    trace_id: str | None = Field(default=None, max_length=160)
    scope: str = Field(min_length=1, max_length=96)


def _utc(value: Any) -> datetime | None:
    candidate: datetime | None
    if isinstance(value, datetime):
        candidate = value
    elif isinstance(value, str) and value.strip():
        try:
            candidate = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if candidate.tzinfo is None:
        return candidate.replace(tzinfo=UTC)
    return candidate.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class TraceRetentionCandidate:
    """One bounded native-span retention decision."""

    record_id: str
    trace_id: str | None
    source_kind: str | None
    disposition: RetentionDisposition
    expires_at: datetime | None
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "trace_id": self.trace_id,
            "source_kind": self.source_kind,
            "disposition": self.disposition,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class TraceRetentionPlan:
    """A deterministic retention decision set; it does not mutate storage."""

    schema_version: str
    evaluated_at: datetime
    cutoff: datetime
    policy: TraceRetentionPolicy
    candidates: tuple[TraceRetentionCandidate, ...]
    notices: tuple[str, ...] = ()

    @property
    def counts(self) -> dict[str, int]:
        return {
            disposition: sum(
                1 for candidate in self.candidates if candidate.disposition == disposition
            )
            for disposition in ("retain", "archive", "delete", "invalid")
        }

    @property
    def deletion_ids(self) -> tuple[str, ...]:
        """Return only IDs explicitly classified for deletion."""

        return tuple(
            candidate.record_id
            for candidate in self.candidates
            if candidate.disposition == "delete"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evaluated_at": self.evaluated_at.isoformat(),
            "cutoff": self.cutoff.isoformat(),
            "policy": self.policy.model_dump(mode="json"),
            "counts": self.counts,
            "deletion_ids": list(self.deletion_ids),
            "notices": list(self.notices),
            "candidates": [candidate.as_dict() for candidate in self.candidates],
        }


def plan_native_span_retention(
    rows: Iterable[Mapping[str, Any]],
    policy: TraceRetentionPolicy,
    *,
    evaluated_at: datetime,
    limit: int = 10_000,
) -> TraceRetentionPlan:
    """Classify native-span rows using only bounded metadata.

    ``retention_until`` is authoritative when present.  Otherwise the expiry
    is derived from ``started_at`` and the policy's retention period.  Invalid
    rows are retained in the report as ``invalid`` and are never returned as
    deletion candidates.  The caller must separately apply a deletion or
    archive operation after human/operator review.
    """

    if not isinstance(policy, TraceRetentionPolicy):
        raise TypeError("policy must be a TraceRetentionPolicy")
    normalized_now = _utc(evaluated_at)
    if normalized_now is None:
        raise ValueError("evaluated_at must be a valid datetime")
    cutoff = normalized_now - timedelta(days=policy.retention_days)
    candidates: list[TraceRetentionCandidate] = []
    notices: list[str] = []
    max_rows = max(1, min(int(limit), 20_000))

    for index, raw in enumerate(rows):
        if len(candidates) >= max_rows:
            notices.append("retention plan truncated at the configured row limit")
            break
        if not isinstance(raw, Mapping):
            candidates.append(
                TraceRetentionCandidate(
                    record_id=f"row-{index}",
                    trace_id=None,
                    source_kind=None,
                    disposition="invalid",
                    expires_at=None,
                    reason="row is not a metadata mapping",
                )
            )
            continue

        record_id = str(raw.get("id") or raw.get("span_id") or f"row-{index}").strip()
        trace_id = str(raw.get("trace_id") or "").strip() or None
        source_kind = str(raw.get("source_kind") or "").strip() or None
        started_at = _utc(raw.get("started_at") or raw.get("created_at"))
        explicit_expiry = _utc(raw.get("retention_until"))
        if not trace_id or not source_kind or started_at is None:
            candidates.append(
                TraceRetentionCandidate(
                    record_id=record_id,
                    trace_id=trace_id,
                    source_kind=source_kind,
                    disposition="invalid",
                    expires_at=explicit_expiry,
                    reason="missing safe trace/source/timestamp metadata",
                )
            )
            notices.append(f"row {record_id!r} is not eligible for automatic retention action")
            continue

        expires_at = explicit_expiry or (started_at + timedelta(days=policy.retention_days))
        if expires_at <= normalized_now:
            disposition: RetentionDisposition = (
                "delete" if policy.terminal_mode == "delete" else "archive"
            )
            reason = (
                "explicit retention_until elapsed"
                if explicit_expiry is not None
                else "policy retention period elapsed"
            )
        else:
            disposition = "retain"
            reason = "retention period has not elapsed"
        candidates.append(
            TraceRetentionCandidate(
                record_id=record_id,
                trace_id=trace_id,
                source_kind=source_kind,
                disposition=disposition,
                expires_at=expires_at,
                reason=reason,
            )
        )

    candidates.sort(
        key=lambda candidate: (
            candidate.expires_at or datetime.max.replace(tzinfo=UTC),
            candidate.record_id,
        )
    )
    return TraceRetentionPlan(
        schema_version=TRACE_RETENTION_PLAN_SCHEMA,
        evaluated_at=normalized_now,
        cutoff=cutoff,
        policy=policy,
        candidates=tuple(candidates),
        notices=tuple(notices[:100]),
    )


__all__ = [
    "TRACE_RETENTION_PLAN_SCHEMA",
    "RetentionDisposition",
    "TraceRetentionCounts",
    "TraceRetentionCandidate",
    "TraceRetentionCandidateResponse",
    "TraceRetentionPlan",
    "TraceRetentionPlanResponse",
    "plan_native_span_retention",
]
