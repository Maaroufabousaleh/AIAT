"""Bounded incident summaries over the authoritative trace-evidence read model.

The incident projection is an operator view, not a second audit store and not
an execution or release gate.  It consumes only the already-normalized
``TraceEvidence`` model, keeps stable record references and scalar operational
fields, and never copies task, tool, model, provider, or request payloads.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mas_core.observability.trace_evidence import TraceEvidence, TraceEvidenceItem

TRACE_INCIDENT_SCHEMA = "aiat.trace-incident.v1"

_FAILURE_STATUSES = frozenset(
    {
        "attention",
        "blocked",
        "cancelled",
        "canceled",
        "conflict",
        "dead_letter",
        "denied",
        "error",
        "errored",
        "failed",
        "failure",
        "rejected",
        "stale",
        "timed_out",
        "timeout",
        "unavailable",
    }
)


class TraceIncidentFinding(BaseModel):
    """One bounded failure reference suitable for an operator incident view."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=160)
    source: str = Field(min_length=1, max_length=80)
    kind: str = Field(min_length=1, max_length=80)
    status: str | None = Field(default=None, max_length=80)
    status_code: int | None = Field(default=None, ge=100, le=599)
    project_id: str | None = Field(default=None, max_length=160)
    worker_run_id: str | None = Field(default=None, max_length=160)
    operation: str | None = Field(default=None, max_length=160)
    service: str | None = Field(default=None, max_length=96)
    occurred_at: str | None = Field(default=None, max_length=80)


class TraceIncident(BaseModel):
    """Secret-safe incident summary derived from one trace evidence response."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = TRACE_INCIDENT_SCHEMA
    trace_id: str = Field(min_length=1, max_length=160)
    generated_at: str | None = Field(default=None, max_length=80)
    status: Literal["clear", "attention", "not_found"]
    severity: Literal["info", "warning", "critical"]
    coverage_status: Literal["complete", "partial", "empty"]
    item_count: int = Field(default=0, ge=0)
    finding_count: int = Field(default=0, ge=0)
    source_counts: dict[str, int] = Field(default_factory=dict)
    affected_sources: list[str] = Field(default_factory=list, max_length=32)
    project_ids: list[str] = Field(default_factory=list, max_length=64)
    first_observed_at: str | None = Field(default=None, max_length=80)
    last_observed_at: str | None = Field(default=None, max_length=80)
    findings: list[TraceIncidentFinding] = Field(default_factory=list, max_length=100)
    notice_codes: list[str] = Field(default_factory=list, max_length=32)


def _normalised_status(item: TraceEvidenceItem) -> str:
    return str(item.status or "").strip().lower().replace("-", "_")


def _is_failure(item: TraceEvidenceItem) -> bool:
    status_code = item.status_code
    if status_code is not None and status_code >= 400:
        return True
    return _normalised_status(item) in _FAILURE_STATUSES


def _finding(item: TraceEvidenceItem) -> TraceIncidentFinding:
    return TraceIncidentFinding(
        id=item.id,
        source=item.source,
        kind=item.kind,
        status=item.status,
        status_code=item.status_code,
        project_id=item.project_id,
        worker_run_id=item.worker_run_id,
        operation=item.operation,
        service=item.service,
        occurred_at=item.occurred_at,
    )


def build_trace_incident(evidence: TraceEvidence | Mapping[str, object]) -> TraceIncident:
    """Classify one bounded trace response without importing raw payloads.

    ``coverage_status`` is intentionally independent from incident ``status``:
    missing instrumentation is disclosed as partial/empty coverage but never
    silently converted into a failure or a clean release result.
    """

    model = evidence if isinstance(evidence, TraceEvidence) else TraceEvidence.model_validate(evidence)
    items = list(model.items)
    findings = [_finding(item) for item in items if _is_failure(item)]
    critical = any(
        item.status_code is not None and item.status_code >= 500
        for item in items
        if _is_failure(item)
    )
    coverage_values = [str(value).strip().lower() for value in model.coverage.values()]
    observed_coverage = sum(value == "observed" for value in coverage_values)
    if not coverage_values or observed_coverage == 0:
        coverage_status: Literal["complete", "partial", "empty"] = "empty"
    elif observed_coverage < len(coverage_values):
        coverage_status = "partial"
    else:
        coverage_status = "complete"

    if model.status == "not_found":
        status: Literal["clear", "attention", "not_found"] = "not_found"
        severity: Literal["info", "warning", "critical"] = "info"
    elif findings:
        status = "attention"
        severity = "critical" if critical else "warning"
    else:
        status = "clear"
        severity = "info"

    notice_codes: list[str] = []
    if model.status == "not_found":
        notice_codes.append("TRACE_NOT_FOUND")
    if coverage_status == "empty":
        notice_codes.append("TRACE_COVERAGE_EMPTY")
    elif coverage_status == "partial":
        notice_codes.append("TRACE_COVERAGE_PARTIAL")
    if findings:
        notice_codes.append("TRACE_FAILURE_FINDINGS")
    else:
        notice_codes.append("TRACE_NO_FAILURE_FINDINGS")
    if any(
        isinstance(notice, Mapping) and notice.get("code") == "TRACE_ITEM_LIMIT_APPLIED"
        for notice in model.notices
    ):
        notice_codes.append("TRACE_ITEM_LIMIT_APPLIED")

    return TraceIncident(
        trace_id=model.trace_id,
        generated_at=model.generated_at,
        status=status,
        severity=severity,
        coverage_status=coverage_status,
        item_count=model.item_count,
        finding_count=len(findings),
        source_counts=dict(model.source_counts),
        affected_sources=sorted({finding.source for finding in findings}),
        project_ids=list(model.project_ids)[:64],
        first_observed_at=model.first_observed_at,
        last_observed_at=model.last_observed_at,
        findings=findings[-100:],
        notice_codes=notice_codes,
    )


__all__ = [
    "TRACE_INCIDENT_SCHEMA",
    "TraceIncident",
    "TraceIncidentFinding",
    "build_trace_incident",
]
