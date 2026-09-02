"""Secret-safe trace evidence projections over existing durable records.

AIAT joins trace-bearing task, usage, model, artifact, integration, and
worker-run records into one bounded operator read model without creating a
second audit store. Raw task payloads, tool inputs/outputs, provider headers,
and error bodies are never returned from the projection.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TRACE_EVIDENCE_SCHEMA = "aiat.trace-evidence.v1"
TRACE_RETENTION_SCHEMA = "aiat.trace-retention-policy.v1"


class TraceRetentionPolicy(BaseModel):
    """Bounded trace sampling/retention metadata used by the read surface."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = TRACE_RETENTION_SCHEMA
    retention_days: int = Field(default=3650, ge=1, le=36500)
    sample_rate: float = Field(default=1.0, ge=0, le=1, allow_inf_nan=False)
    terminal_mode: Literal["archive", "delete"] = "archive"
    source: Literal["company_manifest", "default"] = "default"


class TraceEvidenceItem(BaseModel):
    """One secret-safe row in a trace evidence response."""

    model_config = ConfigDict(extra="forbid")

    id: str
    source: Literal[
        "api_requests",
        "task_log",
        "project_usage_events",
        "worker_run_transitions",
        "worker_usage_records",
        "worker_artifacts",
        "pm_inbox_events",
        "integration_evidence",
        "native_spans",
    ]
    kind: str
    status: str | None = None
    project_id: str | None = None
    agent_id: str | None = None
    team_id: str | None = None
    worker_run_id: str | None = None
    event_type: str | None = None
    model: str | None = None
    provider_id: str | None = None
    exact_model_id: str | None = None
    tool_name: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    operation: str | None = None
    service: str | None = None
    sampled: bool | None = None
    occurred_at: str | None = None
    duration_ms: float | None = None
    cost_usd: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    artifact_id: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    connection_id: str | None = None
    request_method: str | None = None
    route: str | None = None
    status_code: int | None = Field(default=None, ge=100, le=599)


class TraceEvidence(BaseModel):
    """Canonical bounded cross-service trace evidence read model."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = TRACE_EVIDENCE_SCHEMA
    trace_id: str
    generated_at: str | None = None
    status: Literal["observed", "not_found"]
    item_count: int = 0
    source_counts: dict[str, int] = Field(default_factory=dict)
    project_ids: list[str] = Field(default_factory=list)
    first_observed_at: str | None = None
    last_observed_at: str | None = None
    retention: TraceRetentionPolicy = Field(default_factory=TraceRetentionPolicy)
    items: list[TraceEvidenceItem] = Field(default_factory=list)
    coverage: dict[str, str] = Field(default_factory=dict)
    notices: list[dict[str, str]] = Field(default_factory=list)


def _text(value: Any, *, max_length: int = 512) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered[:max_length] or None


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _integer(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return _text(value, max_length=80)


def _task_project_id(row: Mapping[str, Any]) -> str | None:
    """Extract only a project identifier from a task's bounded JSON fields."""

    for field in ("input", "output"):
        value = row.get(field)
        if isinstance(value, Mapping) and value.get("project_id") is not None:
            return _text(value.get("project_id"))
    return _text(row.get("project_id"))


def trace_retention_from_manifest(record: Mapping[str, Any] | None) -> TraceRetentionPolicy:
    """Project the active company manifest's trace policy, with safe defaults."""

    raw_manifest: Any = record.get("manifest_json") if isinstance(record, Mapping) else None
    if not isinstance(raw_manifest, Mapping) and isinstance(record, Mapping):
        nested = record.get("manifest")
        raw_manifest = nested if isinstance(nested, Mapping) else None
    retention = raw_manifest.get("retention") if isinstance(raw_manifest, Mapping) else None
    if not isinstance(retention, Mapping):
        return TraceRetentionPolicy()
    try:
        return TraceRetentionPolicy(
            retention_days=int(retention.get("trace_days", 3650)),
            sample_rate=float(retention.get("trace_sample_rate", 1.0)),
            terminal_mode=str(retention.get("terminal_mode", "archive")),
            source="company_manifest",
        )
    except (TypeError, ValueError):
        # A malformed optional metadata section must not make trace reads
        # unavailable; use the explicit conservative default and disclose it.
        return TraceRetentionPolicy()


def build_trace_evidence(
    *,
    trace_id: str,
    task_rows: Iterable[Mapping[str, Any]] = (),
    usage_rows: Iterable[Mapping[str, Any]] = (),
    transition_rows: Iterable[Mapping[str, Any]] = (),
    worker_usage_rows: Iterable[Mapping[str, Any]] = (),
    artifact_rows: Iterable[Mapping[str, Any]] = (),
    integration_rows: Iterable[Mapping[str, Any]] = (),
    integration_evidence_rows: Iterable[Mapping[str, Any]] = (),
    native_span_rows: Iterable[Mapping[str, Any]] = (),
    api_rows: Iterable[Mapping[str, Any]] = (),
    retention: TraceRetentionPolicy | None = None,
    generated_at: str | None = None,
    limit: int = 300,
) -> TraceEvidence:
    """Build a deterministic, bounded, secret-safe trace projection.

    ``limit`` caps the final response, after each source has been normalized
    and sorted by stable source/id keys. The response reports source coverage
    and keeps transport-level spans and mail-edge coverage explicit so
    operators can distinguish an empty trace from incomplete instrumentation.
    """

    max_items = max(1, min(int(limit), 1_000))
    native_rows = [row for row in native_span_rows if isinstance(row, Mapping)]
    items: list[TraceEvidenceItem] = []

    for row in api_rows:
        if not isinstance(row, Mapping):
            continue
        try:
            status_code = int(row.get("status_code")) if row.get("status_code") is not None else None
        except (TypeError, ValueError):
            status_code = None
        if status_code is not None and not 100 <= status_code <= 599:
            status_code = None
        items.append(
            TraceEvidenceItem(
                id=_text(row.get("id")) or "unknown",
                source="api_requests",
                kind="api_request",
                status=_text(row.get("outcome")),
                request_method=_text(row.get("method"), max_length=16),
                route=_text(row.get("route"), max_length=160),
                status_code=status_code,
                occurred_at=_timestamp(row.get("occurred_at")),
                duration_ms=_number(row.get("duration_ms")),
            )
        )

    for row in task_rows:
        if not isinstance(row, Mapping):
            continue
        items.append(
            TraceEvidenceItem(
                id=_text(row.get("task_id")) or "unknown",
                source="task_log",
                kind="task",
                status=_text(row.get("status")),
                project_id=_task_project_id(row),
                agent_id=_text(row.get("agent_id")),
                team_id=_text(row.get("team_id")),
                occurred_at=_timestamp(row.get("created_at") or row.get("updated_at")),
            )
        )

    for row in usage_rows:
        if not isinstance(row, Mapping):
            continue
        items.append(
            TraceEvidenceItem(
                id=_text(row.get("id")) or "unknown",
                source="project_usage_events",
                kind="usage",
                status=_text(row.get("status")),
                project_id=_text(row.get("project_id")),
                agent_id=_text(row.get("agent_id")),
                team_id=_text(row.get("team_id")),
                event_type=_text(row.get("event_type")),
                model=_text(row.get("model")),
                tool_name=_text(row.get("tool_name")),
                span_id=_text(row.get("span_id")),
                occurred_at=_timestamp(row.get("occurred_at")),
                duration_ms=_number(row.get("duration_ms")),
                cost_usd=_number(row.get("cost_usd")),
            )
        )

    for row in transition_rows:
        if not isinstance(row, Mapping):
            continue
        items.append(
            TraceEvidenceItem(
                id=_text(row.get("id")) or "unknown",
                source="worker_run_transitions",
                kind="worker_transition",
                status=_text(row.get("to_state")),
                worker_run_id=_text(row.get("run_id")),
                occurred_at=_timestamp(row.get("created_at")),
            )
        )

    for row in worker_usage_rows:
        if not isinstance(row, Mapping):
            continue
        prompt_tokens = max(0, _integer(row.get("prompt_tokens")))
        completion_tokens = max(0, _integer(row.get("completion_tokens")))
        total_tokens = max(
            0,
            _integer(row.get("total_tokens"), default=prompt_tokens + completion_tokens),
        )
        items.append(
            TraceEvidenceItem(
                id=_text(row.get("id")) or "unknown",
                source="worker_usage_records",
                kind="model_usage",
                status="observed",
                worker_run_id=_text(row.get("run_id")),
                provider_id=_text(row.get("provider_id")),
                exact_model_id=_text(row.get("exact_model_id")),
                occurred_at=_timestamp(row.get("created_at")),
                duration_ms=_number(row.get("duration_ms")),
                cost_usd=_number(row.get("cost_usd")),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            )
        )

    for row in artifact_rows:
        if not isinstance(row, Mapping):
            continue
        items.append(
            TraceEvidenceItem(
                id=_text(row.get("id")) or "unknown",
                source="worker_artifacts",
                kind="artifact",
                status="observed",
                worker_run_id=_text(row.get("run_id")),
                artifact_id=_text(row.get("artifact_id")),
                occurred_at=_timestamp(row.get("created_at")),
                size_bytes=max(0, _integer(row.get("size_bytes"))) if row.get("size_bytes") is not None else None,
                sha256=_text(row.get("sha256"), max_length=128),
            )
        )

    for row in integration_rows:
        if not isinstance(row, Mapping):
            continue
        items.append(
            TraceEvidenceItem(
                id=_text(row.get("id")) or "unknown",
                source="pm_inbox_events",
                kind="integration_event",
                status=_text(row.get("status")),
                event_type=_text(row.get("event_type")),
                connection_id=_text(row.get("connection_id")),
                occurred_at=_timestamp(row.get("received_at") or row.get("processed_at")),
            )
        )

    for row in integration_evidence_rows:
        if not isinstance(row, Mapping):
            continue
        items.append(
            TraceEvidenceItem(
                id=_text(row.get("id")) or "unknown",
                source="integration_evidence",
                kind="integration_span",
                status="observed",
                project_id=_text(row.get("project_id")),
                event_type=_text(row.get("evidence_type")),
                connection_id=_text(row.get("connection_id")),
                span_id=_text(row.get("span_id")),
                occurred_at=_timestamp(row.get("created_at")),
            )
        )

    for row in native_rows:
        items.append(
            TraceEvidenceItem(
                id=_text(row.get("id")) or "unknown",
                source="native_spans",
                kind="native_span",
                status=_text(row.get("status")),
                span_id=_text(row.get("span_id")),
                parent_span_id=_text(row.get("parent_span_id")),
                operation=_text(row.get("operation"), max_length=160),
                service=_text(row.get("service"), max_length=96),
                sampled=bool(row.get("sampled")) if row.get("sampled") is not None else None,
                occurred_at=_timestamp(row.get("started_at") or row.get("created_at")),
                duration_ms=_number(row.get("duration_ms")),
            )
        )

    raw_source_counts = {
        source: sum(item.source == source for item in items)
        for source in (
            "api_requests",
            "task_log",
            "project_usage_events",
            "worker_run_transitions",
            "worker_usage_records",
            "worker_artifacts",
            "pm_inbox_events",
            "integration_evidence",
            "native_spans",
        )
    }
    native_source_counts = {
        source_kind: sum(
            str(row.get("source_kind") or "").strip().lower() == source_kind
            for row in native_rows
        )
        for source_kind in ("transport", "model", "tool", "mail", "audit", "worker", "integration")
    }
    source_order = {
        "api_requests": 0,
        "task_log": 1,
        "project_usage_events": 2,
        "worker_run_transitions": 3,
        "worker_usage_records": 4,
        "worker_artifacts": 5,
        "pm_inbox_events": 6,
        "integration_evidence": 7,
        "native_spans": 8,
    }
    items.sort(key=lambda item: (item.occurred_at or "", source_order[item.source], item.id))
    truncated = len(items) > max_items
    items = items[-max_items:]
    timestamps = [item.occurred_at for item in items if item.occurred_at]
    project_ids = sorted({item.project_id for item in items if item.project_id})
    coverage = {
        source: "observed" if count else "empty" for source, count in raw_source_counts.items()
    }
    coverage.update(
        {
            f"native_{source_kind}_spans": "observed" if count else "empty"
            for source_kind, count in native_source_counts.items()
        }
    )
    notices = [
        {
            "code": "PARTIAL_TRACE_SOURCES",
            "message": "Native transport/model/tool/audit/integration spans are projected when writers provide trace context; mail-edge coverage and live retention enforcement remain open.",
        }
    ]
    if truncated:
        notices.append(
            {
                "code": "TRACE_ITEM_LIMIT_APPLIED",
                "message": "The trace response is bounded; source counts include rows observed before the item cap.",
            }
        )
    return TraceEvidence(
        trace_id=trace_id,
        generated_at=generated_at,
        status="observed" if items else "not_found",
        item_count=len(items),
        source_counts=raw_source_counts,
        project_ids=project_ids,
        first_observed_at=min(timestamps) if timestamps else None,
        last_observed_at=max(timestamps) if timestamps else None,
        retention=retention or TraceRetentionPolicy(),
        items=items,
        coverage=coverage,
        notices=notices,
    )


__all__ = [
    "TRACE_EVIDENCE_SCHEMA",
    "TRACE_RETENTION_SCHEMA",
    "TraceEvidence",
    "TraceEvidenceItem",
    "TraceRetentionPolicy",
    "build_trace_evidence",
    "trace_retention_from_manifest",
]
