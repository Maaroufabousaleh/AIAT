"""Bounded, payload-free API request observations.

The orchestrator records one scalar observation after each HTTP request.  The
record is an operational read-model input, not an audit replacement: request
bodies, response bodies, headers, credentials, query strings, and exception
messages are intentionally excluded.  Route names are normalized so IDs do
not become an unbounded metric-like dimension.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from mas_core.observability.tracing import is_safe_trace_id

API_OBSERVATION_SCHEMA = "aiat.api-observation.v1"
API_OBSERVATION_SOURCE = "orchestrator_api"
_MAX_ROUTE_LENGTH = 160
_MAX_TEXT_LENGTH = 96
_UUID_SEGMENT = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
_HEX_SEGMENT = re.compile(r"^[0-9a-fA-F]{16,}$")
_INTEGER_SEGMENT = re.compile(r"^[0-9]+$")


class APIObservation(BaseModel):
    """Versioned scalar representation used before durable persistence."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = API_OBSERVATION_SCHEMA
    id: UUID = Field(default_factory=uuid4)
    method: str = Field(min_length=1, max_length=16)
    route: str = Field(min_length=1, max_length=_MAX_ROUTE_LENGTH)
    status_code: int = Field(ge=100, le=599)
    outcome: Literal["success", "failure"]
    duration_ms: float = Field(ge=0, le=86_400_000, allow_inf_nan=False)
    trace_id: str | None = Field(default=None, max_length=128)
    principal: str | None = Field(default=None, max_length=_MAX_TEXT_LENGTH)
    dashboard_section: str | None = Field(default=None, max_length=_MAX_TEXT_LENGTH)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: str = API_OBSERVATION_SOURCE


def _bounded_text(value: Any, *, max_length: int = _MAX_TEXT_LENGTH) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered[:max_length] or None


def normalize_api_route(path: Any, route_template: Any = None) -> str:
    """Return a bounded route fingerprint without IDs or query strings."""

    candidate = str(route_template or path or "/").split("?", 1)[0].strip()
    if not candidate.startswith("/"):
        candidate = f"/{candidate}"
    segments: list[str] = []
    for segment in candidate.split("/"):
        if not segment:
            continue
        if segment.startswith("{") and segment.endswith("}"):
            segment = ":param"
        elif _UUID_SEGMENT.fullmatch(segment) or _HEX_SEGMENT.fullmatch(segment) or _INTEGER_SEGMENT.fullmatch(segment):
            segment = ":id"
        segments.append(segment[:48])
    normalized = "/" + "/".join(segments)
    return normalized[:_MAX_ROUTE_LENGTH] or "/"


def build_api_observation(
    *,
    method: Any,
    path: Any,
    route_template: Any = None,
    status_code: Any,
    duration_ms: Any,
    trace_id: Any = None,
    principal: Any = None,
    dashboard_section: Any = None,
    occurred_at: datetime | None = None,
    observation_id: UUID | None = None,
) -> dict[str, Any]:
    """Normalize middleware values into a safe durable row."""

    try:
        status = max(100, min(599, int(status_code)))
    except (TypeError, ValueError):
        status = 500
    try:
        duration = max(0.0, min(86_400_000.0, float(duration_ms)))
    except (TypeError, ValueError):
        duration = 0.0
    trace = _bounded_text(trace_id, max_length=128)
    if trace and not is_safe_trace_id(trace):
        trace = None
    observed_at = occurred_at or datetime.now(UTC)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    else:
        observed_at = observed_at.astimezone(UTC)
    record = APIObservation(
        id=observation_id or uuid4(),
        method=_bounded_text(method, max_length=16) or "UNKNOWN",
        route=normalize_api_route(path, route_template),
        status_code=status,
        outcome="success" if status < 500 else "failure",
        duration_ms=duration,
        trace_id=trace,
        principal=_bounded_text(principal),
        dashboard_section=_bounded_text(dashboard_section),
        occurred_at=observed_at,
    )
    return record.model_dump(exclude={"schema_version"}, mode="python")


__all__ = [
    "API_OBSERVATION_SCHEMA",
    "API_OBSERVATION_SOURCE",
    "APIObservation",
    "build_api_observation",
    "normalize_api_route",
]
