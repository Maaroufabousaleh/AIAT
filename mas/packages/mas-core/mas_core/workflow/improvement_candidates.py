"""Bounded, non-authorizing self-improvement candidate detection.

Signals can come from defects, metrics, upstream updates, cost observations,
or explicit operator goals.  Detection only normalizes and deduplicates
signals into typed :class:`ImprovementOpportunity` records; it never creates a
project, grants authority, reserves budget, or changes a deployment.  Licence
and restriction values are carried as optional provenance metadata and are not
inspected by the detector.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mas_core.workflow.self_improvement import ImprovementOpportunity, ImprovementRisk

IMPROVEMENT_CANDIDATE_SCHEMA = "aiat.self-improvement-candidate-detection.v1"


class ImprovementSignalSource(StrEnum):
    DEFECT = "defect"
    METRIC = "metric"
    UPSTREAM_UPDATE = "upstream_update"
    COST = "cost"
    OPERATOR_GOAL = "operator_goal"


class ImprovementSignalSeverity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ImprovementSignal(BaseModel):
    """One bounded observation eligible for candidate generation."""

    model_config = ConfigDict(extra="forbid")

    signal_id: str = Field(min_length=1, max_length=240)
    source: ImprovementSignalSource
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=4000)
    source_ref: str = Field(min_length=1, max_length=300)
    severity: ImprovementSignalSeverity = ImprovementSignalSeverity.MEDIUM
    owner: str | None = Field(default=None, max_length=160)
    owner_kind: Literal["human", "agent", "system"] | None = None
    company_id: UUID | None = None
    budget_usd: Decimal | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    licence_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("signal_id", "title", "description", "source_ref")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("signal text fields must not be blank")
        return value

    @field_validator("owner")
    @classmethod
    def _owner_if_present(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("signal owner must not be blank when supplied")
        return value

    @field_validator("budget_usd", mode="before")
    @classmethod
    def _finite_budget(cls, value: object) -> Decimal | None:
        if value is None:
            return None
        try:
            budget = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("signal budget_usd must be a finite non-negative decimal") from exc
        if not budget.is_finite() or budget < 0 or budget > Decimal("100000"):
            raise ValueError("signal budget_usd must be between 0 and 100000")
        return budget

    @field_validator("metadata")
    @classmethod
    def _bounded_metadata(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 20:
            raise ValueError("signal metadata is limited to 20 entries")
        if any(len(key) > 80 or len(item) > 240 for key, item in value.items()):
            raise ValueError("signal metadata keys/values exceed bounded lengths")
        return value

    @property
    def dedupe_key(self) -> str:
        return self.signal_id.casefold()

    @property
    def fingerprint(self) -> tuple[str, ...]:
        return (
            self.source.value,
            self.title.casefold(),
            self.description,
            self.source_ref,
            self.severity.value,
            self.owner,
            self.owner_kind,
            str(self.company_id) if self.company_id else "",
            str(self.budget_usd) if self.budget_usd is not None else "",
        )


class ImprovementCandidateSet(BaseModel):
    """Deterministic candidate output; it is not a project or approval."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = IMPROVEMENT_CANDIDATE_SCHEMA
    candidates: tuple[ImprovementOpportunity, ...] = ()
    deduplicated_signal_ids: tuple[str, ...] = ()
    source_counts: dict[str, int] = Field(default_factory=dict)
    licence_metadata_is_gate: bool = False
    authority_side_effects: tuple[str, ...] = (
        "no_project_created",
        "no_budget_reserved",
        "no_credentials_granted",
        "no_deployment_changed",
    )

    @model_validator(mode="after")
    def _validate_counts(self) -> ImprovementCandidateSet:
        if sum(self.source_counts.values()) != len(self.candidates):
            raise ValueError("source_counts must reconcile with candidate count")
        return self

    def canonical_project_requests(self) -> tuple[dict[str, Any], ...]:
        """Project-shaped previews, without persisting or authorizing them."""

        return tuple(candidate.canonical_project_request() for candidate in self.candidates)


_SEVERITY_TO_RISK = {
    ImprovementSignalSeverity.INFO: ImprovementRisk.LOW,
    ImprovementSignalSeverity.LOW: ImprovementRisk.LOW,
    ImprovementSignalSeverity.MEDIUM: ImprovementRisk.MEDIUM,
    ImprovementSignalSeverity.HIGH: ImprovementRisk.HIGH,
    ImprovementSignalSeverity.CRITICAL: ImprovementRisk.CRITICAL,
}
_RISK_DEFAULT_BUDGET = {
    ImprovementRisk.LOW: Decimal("5.00"),
    ImprovementRisk.MEDIUM: Decimal("15.00"),
    ImprovementRisk.HIGH: Decimal("50.00"),
    ImprovementRisk.CRITICAL: Decimal("100.00"),
}


def _opportunity_id(signal: ImprovementSignal) -> UUID:
    return uuid5(NAMESPACE_URL, f"aiat:self-improvement-candidate:{signal.dedupe_key}")


def detect_improvement_candidates(
    signals: list[ImprovementSignal] | tuple[ImprovementSignal, ...],
    *,
    default_owner: str = "cto",
    default_owner_kind: Literal["human", "agent", "system"] = "human",
    company_id: UUID | None = None,
) -> ImprovementCandidateSet:
    """Normalize deterministic candidates from bounded observations.

    Exact duplicate signal IDs are collapsed.  Reusing an ID for a different
    observation fails closed instead of silently merging unrelated evidence.
    """

    if not default_owner.strip():
        raise ValueError("default_owner must not be blank")
    by_id: dict[str, ImprovementSignal] = {}
    duplicate_ids: list[str] = []
    for signal in signals:
        key = signal.dedupe_key
        existing = by_id.get(key)
        if existing is None:
            by_id[key] = signal
            continue
        if existing.fingerprint != signal.fingerprint:
            raise ValueError(f"signal_id {signal.signal_id!r} was reused with different evidence")
        duplicate_ids.append(signal.signal_id)

    candidates: list[ImprovementOpportunity] = []
    for signal in by_id.values():
        risk = _SEVERITY_TO_RISK[signal.severity]
        owner = signal.owner or default_owner
        owner_kind = signal.owner_kind or default_owner_kind
        budget = signal.budget_usd if signal.budget_usd is not None else _RISK_DEFAULT_BUDGET[risk]
        candidates.append(
            ImprovementOpportunity(
                opportunity_id=_opportunity_id(signal),
                title=signal.title,
                description=signal.description,
                owner=owner,
                owner_kind=owner_kind,
                risk=risk,
                budget_usd=budget,
                evidence_policy="software_delivery",
                source=f"signal:{signal.source.value}",
                created_by="improvement-detector",
                created_by_kind="system",
                company_id=signal.company_id or company_id,
                licence_metadata=dict(signal.licence_metadata),
            )
        )

    candidates.sort(key=lambda item: (-list(ImprovementRisk).index(item.risk), str(item.opportunity_id)))
    counts = Counter(f"signal:{signal.source.value}" for signal in by_id.values())
    return ImprovementCandidateSet(
        candidates=tuple(candidates),
        deduplicated_signal_ids=tuple(duplicate_ids),
        source_counts=dict(sorted(counts.items())),
    )


__all__ = [
    "IMPROVEMENT_CANDIDATE_SCHEMA",
    "ImprovementCandidateSet",
    "ImprovementSignal",
    "ImprovementSignalSeverity",
    "ImprovementSignalSource",
    "detect_improvement_candidates",
]
