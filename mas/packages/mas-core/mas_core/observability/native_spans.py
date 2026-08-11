"""Bounded native trace-span records.

The trace-evidence projection already correlates several durable ledgers.  This
module adds the small, payload-free span authority needed at service
boundaries without introducing an OpenTelemetry backend or copying request
payloads into the database.  Attributes are deliberately scalar and filtered
by key so a caller cannot turn observability into a secret or body store.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from mas_core.observability.tracing import (
    is_safe_span_id,
    is_safe_trace_id,
    new_span_id,
)

NATIVE_TRACE_SPAN_SCHEMA = "aiat.native-trace-span.v1"
NATIVE_TRACE_SPAN_SOURCE_KINDS = (
    "transport",
    "model",
    "tool",
    "mail",
    "audit",
    "worker",
    "integration",
)
NATIVE_TRACE_SPAN_STATUSES = ("success", "failure", "unknown")
_SAFE_ATTRIBUTE_KEY = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
_SENSITIVE_ATTRIBUTE_KEY = re.compile(
    r"(?:secret|token|password|credential|authorization|cookie|body|payload|content|"
    r"header|query|recipient|subject|provider[_-]?id|correlation[_-]?id)",
    re.IGNORECASE,
)


class NativeTraceSpan(BaseModel):
    """One bounded native span suitable for durable storage and read models."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = NATIVE_TRACE_SPAN_SCHEMA
    id: UUID = Field(default_factory=uuid4)
    trace_id: str = Field(min_length=1, max_length=128)
    span_id: str = Field(min_length=1, max_length=128)
    parent_span_id: str | None = Field(default=None, max_length=128)
    source_kind: Literal[
        "transport",
        "model",
        "tool",
        "mail",
        "audit",
        "worker",
        "integration",
    ]
    operation: str = Field(min_length=1, max_length=160)
    service: str = Field(min_length=1, max_length=96)
    status: Literal["success", "failure", "unknown"] = "unknown"
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: float = Field(default=0.0, ge=0, le=86_400_000, allow_inf_nan=False)
    sampled: bool = True
    retention_until: datetime | None = None
    attributes: dict[str, bool | float | int | str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def _utc(value: Any, *, default: datetime | None = None) -> datetime:
    candidate = value if isinstance(value, datetime) else None
    if candidate is None and isinstance(value, str):
        try:
            candidate = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            candidate = None
    candidate = candidate or default or datetime.now(UTC)
    if candidate.tzinfo is None:
        return candidate.replace(tzinfo=UTC)
    return candidate.astimezone(UTC)


def _bounded_text(value: Any, *, max_length: int, default: str) -> str:
    text = str(value or "").strip()
    return (text[:max_length] or default)


def _safe_attributes(value: Mapping[str, Any] | None) -> dict[str, bool | float | int | str]:
    """Keep only bounded scalar attributes with non-sensitive names."""

    if not isinstance(value, Mapping):
        return {}
    result: dict[str, bool | float | int | str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip().lower()
        if not _SAFE_ATTRIBUTE_KEY.fullmatch(key) or (
            _SENSITIVE_ATTRIBUTE_KEY.search(key)
            and key not in {"prompt_tokens", "completion_tokens", "total_tokens"}
        ):
            continue
        if isinstance(raw_value, bool):
            result[key] = raw_value
        elif isinstance(raw_value, int) and not isinstance(raw_value, bool):
            result[key] = max(-2**63, min(2**63 - 1, raw_value))
        elif isinstance(raw_value, float):
            if raw_value == raw_value and abs(raw_value) != float("inf"):
                result[key] = max(-1e18, min(1e18, raw_value))
        elif isinstance(raw_value, str):
            result[key] = raw_value.strip()[:256]
    return result


def build_native_trace_span(
    *,
    trace_id: str,
    source_kind: str,
    operation: str,
    service: str,
    status: str = "unknown",
    span_id: str | None = None,
    parent_span_id: str | None = None,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
    duration_ms: float | int | None = None,
    sampled: bool = True,
    retention_until: datetime | None = None,
    attributes: Mapping[str, Any] | None = None,
    span_record_id: UUID | None = None,
) -> dict[str, Any]:
    """Normalize one native span and discard unsafe/non-scalar metadata.

    Trace/span identifiers are query keys, so malformed values are rejected.
    ``duration_ms`` is clamped to the same one-day bound as API observations;
    a missing end time is valid for an in-progress observation and is rendered
    as ``None`` rather than guessed.
    """

    normalized_trace = str(trace_id or "").strip()
    if not is_safe_trace_id(normalized_trace):
        raise ValueError("trace_id must be a bounded safe identifier")
    normalized_span = str(span_id or "").strip() or new_span_id()
    if not is_safe_span_id(normalized_span):
        raise ValueError("span_id must be a bounded safe identifier")
    normalized_parent = str(parent_span_id or "").strip() or None
    if normalized_parent and not is_safe_span_id(normalized_parent):
        normalized_parent = None
    normalized_kind = str(source_kind or "").strip().lower()
    if normalized_kind not in NATIVE_TRACE_SPAN_SOURCE_KINDS:
        raise ValueError(f"unsupported native span source_kind: {normalized_kind}")
    normalized_status = str(status or "unknown").strip().lower()
    if normalized_status not in NATIVE_TRACE_SPAN_STATUSES:
        normalized_status = "unknown"
    started = _utc(started_at)
    ended = _utc(ended_at) if ended_at is not None else None
    if ended is not None and ended < started:
        ended = started
    if duration_ms is None:
        duration = max(0.0, (ended - started).total_seconds() * 1000) if ended else 0.0
    else:
        try:
            duration = float(duration_ms)
        except (TypeError, ValueError):
            duration = 0.0
        if duration != duration or abs(duration) == float("inf"):
            duration = 0.0
        duration = max(0.0, min(86_400_000.0, duration))
    if ended is None and duration > 0:
        ended = started + timedelta(milliseconds=duration)
    record = NativeTraceSpan(
        id=span_record_id or uuid4(),
        trace_id=normalized_trace,
        span_id=normalized_span,
        parent_span_id=normalized_parent,
        source_kind=normalized_kind,  # type: ignore[arg-type]
        operation=_bounded_text(operation, max_length=160, default="unknown"),
        service=_bounded_text(service, max_length=96, default="unknown"),
        status=normalized_status,  # type: ignore[arg-type]
        started_at=started,
        ended_at=ended,
        duration_ms=duration,
        sampled=bool(sampled),
        retention_until=_utc(retention_until) if retention_until is not None else None,
        attributes=_safe_attributes(attributes),
    )
    return record.model_dump(exclude={"schema_version"}, mode="python")


__all__ = [
    "NATIVE_TRACE_SPAN_SCHEMA",
    "NATIVE_TRACE_SPAN_SOURCE_KINDS",
    "NativeTraceSpan",
    "build_native_trace_span",
]
