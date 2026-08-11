"""Deterministic operational SLO and capacity projections.

This module is intentionally a read-model layer.  It does not make an
execution, routing, budget, or integration decision and it has no dependency
on third-party licence metadata.  Callers provide already-authorized metric
rows; the builders normalize them into bounded, versioned projections.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SLO_POLICY_SCHEMA = "aiat.slo-policy.v1"
SLO_REPORT_SCHEMA = "aiat.slo-report.v1"
CAPACITY_FORECAST_SCHEMA = "aiat.capacity-forecast.v1"

SLO_SERVICES = (
    "orchestrator_api",
    "queue_age",
    "worker_startup",
    "worker_run",
    "tool_latency",
    "model_routing",
    "pm_scm_sync",
    "mail_delivery",
    "recovery",
)


class SLOTarget(BaseModel):
    """One service-level objective and its measurement semantics."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    service: Literal[
        "orchestrator_api",
        "queue_age",
        "worker_startup",
        "worker_run",
        "tool_latency",
        "model_routing",
        "pm_scm_sync",
        "mail_delivery",
        "recovery",
    ]
    objective: float = Field(ge=0, le=1, allow_inf_nan=False)
    window: Literal["rolling_24h", "rolling_7d", "rolling_30d"] = "rolling_24h"
    max_latency_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    minimum_samples: int = Field(default=1, ge=1, le=1_000_000)
    source: Literal["aiat_default", "company_manifest"] = "aiat_default"


class SLOPolicy(BaseModel):
    """Versioned policy metadata; it is not an enforcement gate."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SLO_POLICY_SCHEMA
    policy_version: str = Field(default="2026.08", min_length=1, max_length=32)
    targets: list[SLOTarget] = Field(default_factory=list, max_length=32)
    source: Literal["aiat_default", "company_manifest"] = "aiat_default"


class SLOStatus(BaseModel):
    """Observed SLO status for one target."""

    model_config = ConfigDict(extra="forbid")

    name: str
    service: str
    objective: float = Field(ge=0, le=1, allow_inf_nan=False)
    window: str
    max_latency_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    sample_count: int = Field(default=0, ge=0)
    good_count: int = Field(default=0, ge=0)
    observed_success_rate: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    latency_p95_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    error_budget_remaining: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    status: Literal["healthy", "attention", "no_data"]
    source: str = Field(default="durable_telemetry", max_length=80)


class SLOReport(BaseModel):
    """Bounded SLO policy plus observations and coverage notices."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SLO_REPORT_SCHEMA
    generated_at: str | None = None
    policy: SLOPolicy
    statuses: list[SLOStatus] = Field(default_factory=list, max_length=32)
    status: Literal["healthy", "attention", "no_data"]
    observed_service_count: int = Field(default=0, ge=0)
    notices: list[dict[str, str]] = Field(default_factory=list, max_length=32)


class CapacityForecast(BaseModel):
    """Read-only cost/token forecast over the durable project usage ledger."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = CAPACITY_FORECAST_SCHEMA
    generated_at: str | None = None
    basis: Literal["project_usage_events"] = "project_usage_events"
    window_days: int = Field(ge=1, le=3650)
    forecast_days: int = Field(ge=1, le=3650)
    active_project_count: int = Field(default=0, ge=0)
    observed_event_count: int = Field(default=0, ge=0)
    observed_total_tokens: int = Field(default=0, ge=0)
    observed_cost_usd: float = Field(default=0, ge=0, allow_inf_nan=False)
    observed_span_days: float = Field(default=0, ge=0, allow_inf_nan=False)
    average_daily_cost_usd: float = Field(default=0, ge=0, allow_inf_nan=False)
    projected_cost_usd: float = Field(default=0, ge=0, allow_inf_nan=False)
    average_daily_tokens: float = Field(default=0, ge=0, allow_inf_nan=False)
    projected_tokens: int = Field(default=0, ge=0)
    budget_limit_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    projected_budget_headroom_usd: float | None = Field(default=None, allow_inf_nan=False)
    budget_source: Literal["company_budgets", "not_configured", "caller"] = "not_configured"
    confidence: Literal["high", "medium", "low", "none"] = "none"
    status: Literal["clear", "attention", "insufficient_data"]
    notices: list[dict[str, str]] = Field(default_factory=list, max_length=32)


def default_slo_policy(*, source: Literal["aiat_default", "company_manifest"] = "aiat_default") -> SLOPolicy:
    """Return the current stable SLO targets for the platform."""

    target_data = (
        ("orchestrator_api_availability", "orchestrator_api", 0.995, 1_000.0),
        ("queue_age", "queue_age", 0.990, 60_000.0),
        ("worker_startup", "worker_startup", 0.990, 30_000.0),
        ("worker_run_completion", "worker_run", 0.950, 300_000.0),
        ("tool_latency", "tool_latency", 0.990, 30_000.0),
        ("model_routing", "model_routing", 0.990, 10_000.0),
        ("pm_scm_sync", "pm_scm_sync", 0.990, 120_000.0),
        ("mail_delivery", "mail_delivery", 0.990, 120_000.0),
        ("recovery_completion", "recovery", 0.995, 300_000.0),
    )
    return SLOPolicy(
        targets=[
            SLOTarget(
                name=name,
                service=service,
                objective=objective,
                max_latency_ms=max_latency_ms,
                source=source,
            )
            for name, service, objective, max_latency_ms in target_data
        ],
        source=source,
    )


def _finite_number(value: Any, *, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _nonnegative_int(value: Any, *, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * percentile / 100)))
    return round(ordered[index], 3)


def _normalise_observation(row: Mapping[str, Any]) -> tuple[str, int, int, list[float]] | None:
    service = str(row.get("service") or "").strip().lower()
    if service not in SLO_SERVICES:
        return None
    has_aggregate = "total" in row or "good" in row
    total = _nonnegative_int(row.get("total"), default=1 if not has_aggregate else 0)
    if "good" in row:
        good = min(total, _nonnegative_int(row.get("good")))
    else:
        status = str(row.get("status") or "success").strip().lower()
        good = total if status in {"success", "succeeded", "ok", "healthy", "true"} else 0
    latency_values: list[float] = []
    raw_samples = row.get("latency_samples_ms")
    if isinstance(raw_samples, (list, tuple)):
        for value in list(raw_samples)[:1_000]:
            latency = _finite_number(value, default=-1)
            if latency >= 0:
                latency_values.append(latency)
    for key in ("latency_ms", "p95_ms", "avg_latency_ms"):
        if not latency_values and row.get(key) is not None:
            latency = _finite_number(row.get(key), default=-1)
            if latency >= 0:
                latency_values.append(latency)
    return service, total, good, latency_values


def build_slo_report(
    observations: Iterable[Mapping[str, Any]] = (),
    *,
    policy: SLOPolicy | None = None,
    generated_at: str | None = None,
) -> SLOReport:
    """Aggregate bounded observations against the versioned default policy."""

    effective_policy = policy or default_slo_policy()
    aggregates: dict[str, dict[str, Any]] = {
        service: {"total": 0, "good": 0, "latencies": []} for service in SLO_SERVICES
    }
    for raw_row in list(observations)[:20_000]:
        if not isinstance(raw_row, Mapping):
            continue
        normalised = _normalise_observation(raw_row)
        if normalised is None:
            continue
        service, total, good, latencies = normalised
        aggregate = aggregates[service]
        aggregate["total"] += total
        aggregate["good"] += good
        aggregate["latencies"].extend(latencies[:1_000])

    statuses: list[SLOStatus] = []
    for target in effective_policy.targets:
        aggregate = aggregates[target.service]
        total = int(aggregate["total"])
        good = min(total, int(aggregate["good"]))
        latencies = list(aggregate["latencies"])[-10_000:]
        success_rate = round(good / total, 6) if total else None
        p95 = _percentile(latencies, 95)
        within_latency = target.max_latency_ms is None or p95 is None or p95 <= target.max_latency_ms
        healthy = total >= target.minimum_samples and success_rate is not None and success_rate >= target.objective and within_latency
        error_budget = None
        if total:
            allowed_bad = max(0.0, (1.0 - target.objective) * total)
            bad = max(0.0, float(total - good))
            error_budget = round(max(0.0, min(1.0, (allowed_bad - bad) / allowed_bad)) if allowed_bad else (1.0 if bad == 0 else 0.0), 6)
        statuses.append(
            SLOStatus(
                name=target.name,
                service=target.service,
                objective=target.objective,
                window=target.window,
                max_latency_ms=target.max_latency_ms,
                sample_count=total,
                good_count=good,
                observed_success_rate=success_rate,
                latency_p95_ms=p95,
                error_budget_remaining=error_budget,
                status="no_data" if total < target.minimum_samples else ("healthy" if healthy else "attention"),
                source="durable_telemetry" if total else "not_observed",
            )
        )

    observed_count = sum(status.sample_count > 0 for status in statuses)
    if any(status.status == "attention" for status in statuses):
        overall = "attention"
    elif observed_count == 0 or any(status.status == "no_data" for status in statuses):
        # A partially observed report must not be presented as an all-clear.
        # Individual statuses and notices still identify which sources are
        # missing, but the aggregate remains explicitly incomplete.
        overall = "no_data"
    else:
        overall = "healthy"
    notices: list[dict[str, str]] = []
    missing = [status.service for status in statuses if status.status == "no_data"]
    if missing:
        notices.append({"code": "SLO_SOURCES_NOT_OBSERVED", "message": "No durable observation was available for one or more SLO services."})
    notices.append({"code": "SLO_POLICY_IS_DESCRIPTIVE", "message": "SLO targets describe operational readiness; they do not block execution."})
    return SLOReport(
        generated_at=generated_at,
        policy=effective_policy,
        statuses=statuses,
        status=overall,
        observed_service_count=observed_count,
        notices=notices,
    )


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if value is None:
        return None
    try:
        rendered = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(rendered)
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def build_capacity_forecast(
    usage_rows: Iterable[Mapping[str, Any]] = (),
    *,
    window_days: int = 30,
    forecast_days: int = 30,
    budget_limit_usd: float | None = None,
    budget_source: Literal["company_budgets", "not_configured", "caller"] = "not_configured",
    generated_at: str | None = None,
) -> CapacityForecast:
    """Build a deterministic forecast from aggregate project usage rows."""

    effective_window = max(1, min(int(window_days), 3_650))
    effective_forecast = max(1, min(int(forecast_days), 3_650))
    rows = [row for row in list(usage_rows)[:10_000] if isinstance(row, Mapping)]
    event_count = sum(_nonnegative_int(row.get("event_count")) for row in rows)
    project_count = sum(1 for row in rows if _nonnegative_int(row.get("event_count")) > 0)
    total_tokens = sum(
        _nonnegative_int(row.get("total_tokens"), default=_nonnegative_int(row.get("prompt_tokens")) + _nonnegative_int(row.get("completion_tokens")))
        for row in rows
    )
    cost = sum(max(0.0, _finite_number(row.get("total_cost_usd"))) for row in rows)
    timestamps = [
        timestamp
        for row in rows
        for timestamp in (_parse_timestamp(row.get("first_event_at")), _parse_timestamp(row.get("last_event_at")))
        if timestamp is not None
    ]
    if timestamps:
        span_days = max(0.0, (max(timestamps) - min(timestamps)).total_seconds() / 86_400.0)
    else:
        span_days = 0.0
    effective_days = max(1.0, min(float(effective_window), span_days + 1.0)) if event_count else float(effective_window)
    average_daily_cost = cost / effective_days
    projected_cost = average_daily_cost * effective_forecast
    average_daily_tokens = total_tokens / effective_days
    projected_tokens = max(0, int(round(average_daily_tokens * effective_forecast)))
    normalized_budget = None if budget_limit_usd is None else max(0.0, _finite_number(budget_limit_usd))
    headroom = None if normalized_budget is None else round(normalized_budget - projected_cost, 8)
    if event_count == 0:
        status: Literal["clear", "attention", "insufficient_data"] = "insufficient_data"
        confidence: Literal["high", "medium", "low", "none"] = "none"
    else:
        status = "attention" if headroom is not None and headroom < 0 else "clear"
        confidence = "high" if span_days >= 14 and event_count >= 50 else ("medium" if span_days >= 3 and event_count >= 10 else "low")
    notices: list[dict[str, str]] = []
    if event_count == 0:
        notices.append({"code": "CAPACITY_USAGE_NOT_OBSERVED", "message": "No project_usage_events rows were available for the requested window."})
    elif confidence == "low":
        notices.append({"code": "CAPACITY_FORECAST_LOW_CONFIDENCE", "message": "Forecast is based on a short or sparse usage history; collect more durable events before treating it as capacity evidence."})
    if normalized_budget is None:
        notices.append({"code": "CAPACITY_BUDGET_NOT_CONFIGURED", "message": "No company budget limit was available, so projected headroom is not evaluated."})
    return CapacityForecast(
        generated_at=generated_at,
        window_days=effective_window,
        forecast_days=effective_forecast,
        active_project_count=project_count,
        observed_event_count=event_count,
        observed_total_tokens=total_tokens,
        observed_cost_usd=round(cost, 8),
        observed_span_days=round(span_days, 3),
        average_daily_cost_usd=round(average_daily_cost, 8),
        projected_cost_usd=round(projected_cost, 8),
        average_daily_tokens=round(average_daily_tokens, 3),
        projected_tokens=projected_tokens,
        budget_limit_usd=round(normalized_budget, 8) if normalized_budget is not None else None,
        projected_budget_headroom_usd=headroom,
        budget_source=budget_source if normalized_budget is not None else "not_configured",
        confidence=confidence,
        status=status,
        notices=notices,
    )


__all__ = [
    "CAPACITY_FORECAST_SCHEMA",
    "SLO_POLICY_SCHEMA",
    "SLO_REPORT_SCHEMA",
    "SLO_SERVICES",
    "CapacityForecast",
    "SLOPolicy",
    "SLOReport",
    "SLOStatus",
    "SLOTarget",
    "build_capacity_forecast",
    "build_slo_report",
    "default_slo_policy",
]
