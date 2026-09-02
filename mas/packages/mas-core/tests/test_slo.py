from __future__ import annotations

from mas_core.observability.slo import (
    CAPACITY_FORECAST_SCHEMA,
    SLO_POLICY_SCHEMA,
    SLO_REPORT_SCHEMA,
    build_capacity_forecast,
    build_slo_report,
    default_slo_policy,
)


def test_default_policy_covers_operational_services_without_gate_inputs():
    policy = default_slo_policy()

    assert policy.schema_version == SLO_POLICY_SCHEMA
    assert len(policy.targets) == 9
    assert {target.service for target in policy.targets} == {
        "orchestrator_api",
        "queue_age",
        "worker_startup",
        "worker_run",
        "tool_latency",
        "model_routing",
        "pm_scm_sync",
        "mail_delivery",
        "recovery",
    }
    assert all("license" not in target.model_dump() for target in policy.targets)


def test_slo_report_is_deterministic_and_reports_missing_sources():
    report = build_slo_report(
        [
            {"service": "orchestrator_api", "status": "success", "latency_ms": 20},
            {"service": "tool_latency", "total": 10, "good": 10, "latency_samples_ms": [5, 10, 15]},
            {"service": "model_routing", "total": 2, "good": 1, "avg_latency_ms": 30},
            {"service": "unknown", "status": "failed", "secret": "not returned"},
        ],
        generated_at="2026-08-10T00:00:00+00:00",
    )

    assert report.schema_version == SLO_REPORT_SCHEMA
    assert report.generated_at == "2026-08-10T00:00:00+00:00"
    assert report.status == "attention"
    assert next(item for item in report.statuses if item.service == "tool_latency").status == "healthy"
    assert next(item for item in report.statuses if item.service == "model_routing").status == "attention"
    assert next(item for item in report.statuses if item.service == "pm_scm_sync").status == "no_data"
    assert "not returned" not in report.model_dump_json()

    partial = build_slo_report([{"service": "orchestrator_api", "status": "success"}])
    assert partial.status == "no_data"


def test_capacity_forecast_uses_durable_usage_aggregates_and_budget_headroom():
    forecast = build_capacity_forecast(
        [
            {
                "project_id": "project-a",
                "event_count": 10,
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_cost_usd": 2.5,
                "first_event_at": "2026-08-01T00:00:00+00:00",
                "last_event_at": "2026-08-10T00:00:00+00:00",
            },
            {
                "project_id": "project-b",
                "event_count": 5,
                "total_tokens": 50,
                "total_cost_usd": 1.5,
                "first_event_at": "2026-08-05T00:00:00+00:00",
                "last_event_at": "2026-08-10T00:00:00+00:00",
            },
        ],
        window_days=30,
        forecast_days=30,
        budget_limit_usd=20,
        budget_source="company_budgets",
        generated_at="2026-08-10T00:00:00+00:00",
    )

    assert forecast.schema_version == CAPACITY_FORECAST_SCHEMA
    assert forecast.active_project_count == 2
    assert forecast.observed_event_count == 15
    assert forecast.observed_total_tokens == 200
    assert forecast.observed_cost_usd == 4.0
    assert forecast.projected_cost_usd > forecast.observed_cost_usd
    assert forecast.projected_budget_headroom_usd == round(20 - forecast.projected_cost_usd, 8)
    assert forecast.status == "clear"
    assert forecast.confidence == "medium"


def test_capacity_forecast_is_explicit_when_usage_is_unavailable():
    forecast = build_capacity_forecast([], budget_limit_usd=5, budget_source="company_budgets")

    assert forecast.status == "insufficient_data"
    assert forecast.confidence == "none"
    assert forecast.observed_event_count == 0
    assert forecast.projected_cost_usd == 0
    assert any(notice["code"] == "CAPACITY_USAGE_NOT_OBSERVED" for notice in forecast.notices)
