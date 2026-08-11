from __future__ import annotations

import pytest


class _OperationalStorage:
    async def list_project_usage_aggregates(self, *, company_id=None, since=None, limit=10_000):
        return [
            {
                "project_id": "project-1",
                "event_count": 10,
                "llm_calls": 4,
                "llm_failed_calls": 0,
                "llm_duration_avg_ms": 25,
                "tool_calls": 6,
                "tool_failed_calls": 1,
                "tool_duration_avg_ms": 40,
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "total_cost_usd": 2.5,
                "first_event_at": "2026-08-01T00:00:00+00:00",
                "last_event_at": "2026-08-10T00:00:00+00:00",
            }
        ]

    async def list_worker_runs(self, *, limit=10_000, **kwargs):
        return [
            {
                "id": "run-1",
                "project_id": "project-1",
                "state": "SUCCEEDED",
                "created_at": "2026-08-01T00:00:00+00:00",
                "claimed_at": "2026-08-01T00:00:01+00:00",
                "started_at": "2026-08-01T00:00:02+00:00",
                "completed_at": "2026-08-01T00:00:12+00:00",
            }
        ]

    async def list_pm_slo_observations(self, *, company_id=None, since=None, limit=10_000):
        return [
            {
                "id": "pm-1",
                "source": "pm_inbox_events",
                "status": "success",
                "duration_ms": 120,
            }
        ]

    async def list_recovery_slo_observations(self, *, company_id=None, since=None, limit=10_000):
        return [
            {
                "id": "recovery-1",
                "source": "worker_run_transitions",
                "status": "success",
            }
        ]

    async def list_api_request_observations(self, *, since=None, limit=20_000):
        return [
            {
                "id": "api-1",
                "outcome": "success",
                "duration_ms": 15,
            },
            {
                "id": "api-2",
                "outcome": "failure",
                "duration_ms": 20,
            },
        ]

    async def list_companies(self, *, status=None):
        return [{"id": "00000000-0000-4000-a000-000000000001", "status": "ACTIVE"}]

    async def list_company_budgets(self, company_id):
        return [{"budget_key": "llm", "limit_value": 100}]


@pytest.mark.anyio
async def test_operator_slo_report_exposes_observed_and_no_data_services(client):
    from orchestrator_api.main import app

    previous = app.state.storage
    app.state.storage = _OperationalStorage()
    try:
        response = await client.get(
            "/observability/slo",
            headers={"X-API-Key": "test-operator-key"},
        )
    finally:
        app.state.storage = previous

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "aiat.slo-report.v1"
    assert payload["status"] == "attention"
    assert next(item for item in payload["statuses"] if item["service"] == "tool_latency")["sample_count"] == 6
    assert next(item for item in payload["statuses"] if item["service"] == "pm_scm_sync")["sample_count"] == 1
    assert next(item for item in payload["statuses"] if item["service"] == "recovery")["sample_count"] == 1
    assert next(item for item in payload["statuses"] if item["service"] == "orchestrator_api")["sample_count"] == 2
    assert next(item for item in payload["statuses"] if item["service"] == "mail_delivery")["status"] == "no_data"
    assert "license" not in response.text.lower()


@pytest.mark.anyio
async def test_operator_slo_report_projects_signed_identity_mail_rows(client, monkeypatch):
    from orchestrator_api import main
    from orchestrator_api.main import app

    class StubIdentityClient:
        async def list_mail_delivery_observations(self, *, since=None, limit=20_000):
            assert since is not None
            assert limit == 20_000
            return [{"id": "mail-1", "status": "success"}]

    monkeypatch.setenv("IDENTITY_SERVICE_URL", "https://identity.example")
    monkeypatch.setenv("AIAT_IDENTITY_CLIENT_PRIVATE_KEY", "configured-for-fixture")
    monkeypatch.setattr(main, "_identity_client", lambda: StubIdentityClient())
    previous = app.state.storage
    app.state.storage = _OperationalStorage()
    try:
        response = await client.get(
            "/observability/slo",
            headers={"X-API-Key": "test-operator-key"},
        )
    finally:
        app.state.storage = previous

    assert response.status_code == 200
    payload = response.json()
    mail_status = next(item for item in payload["statuses"] if item["service"] == "mail_delivery")
    assert mail_status["sample_count"] == 1
    assert mail_status["good_count"] == 1


@pytest.mark.anyio
async def test_operator_capacity_forecast_is_bounded_and_authenticated(client):
    from orchestrator_api.main import app

    previous = app.state.storage
    app.state.storage = _OperationalStorage()
    try:
        denied = await client.get(
            "/observability/capacity/forecast",
            headers={"X-API-Key": "test-mas-key"},
        )
        response = await client.get(
            "/observability/capacity/forecast?window_days=30&forecast_days=30",
            headers={"X-API-Key": "test-operator-key"},
        )
    finally:
        app.state.storage = previous

    assert denied.status_code == 403
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "aiat.capacity-forecast.v1"
    assert payload["basis"] == "project_usage_events"
    assert payload["observed_event_count"] == 10
    assert payload["budget_source"] == "company_budgets"
    assert payload["projected_budget_headroom_usd"] is not None
