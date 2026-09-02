"""Tests for the /metrics endpoint on the orchestrator-api.

Verifies:
- /metrics returns HTTP 200
- Response uses the Prometheus text exposition format
- Custom counters (projects_created_total, workflow_transitions_total) appear
- Core MAS metrics (mas_project_state, mas_dlq_depth) appear
"""

from __future__ import annotations

import pytest

from mas_core.observability.metrics import (
    MAS_PROJECT_STATE,
    metric_label_inventory,
    metric_label_policy_inventory,
    metric_series_budget_status,
    reconcile_project_state_metrics,
    record_project_state_transition,
)


@pytest.mark.anyio
async def test_metrics_endpoint_returns_200(client):
    """GET /metrics must return HTTP 200."""
    response = await client.get("/metrics")
    assert response.status_code == 200


@pytest.mark.anyio
async def test_metrics_content_type_is_prometheus(client):
    """Response Content-Type must indicate Prometheus text format."""
    response = await client.get("/metrics")
    content_type = response.headers.get("content-type", "")
    assert "text/plain" in content_type


@pytest.mark.anyio
async def test_metrics_body_is_valid_prometheus_text(client):
    """Response body must contain valid Prometheus text exposition lines."""
    response = await client.get("/metrics")
    body = response.text

    lines = [line for line in body.splitlines() if line.strip()]
    assert len(lines) > 0, "Metrics body must not be empty"

    has_prom_content = any(
        line.startswith("# HELP")
        or line.startswith("# TYPE")
        or (not line.startswith("#") and " " in line)
        for line in lines
    )
    assert has_prom_content, "Metrics body must contain Prometheus text format entries"


@pytest.mark.anyio
async def test_metrics_custom_counter_projects_created_total(client):
    """projects_created_total counter must be declared in /metrics output."""
    response = await client.get("/metrics")
    body = response.text
    assert "projects_created_total" in body, (
        "Expected 'projects_created_total' counter in Prometheus metrics output"
    )


@pytest.mark.anyio
async def test_metrics_custom_counter_workflow_transitions_total(client):
    """workflow_transitions_total counter must be declared in /metrics output."""
    response = await client.get("/metrics")
    body = response.text
    assert "workflow_transitions_total" in body, (
        "Expected 'workflow_transitions_total' counter in Prometheus metrics output"
    )


@pytest.mark.anyio
async def test_metrics_mas_project_state_gauge(client):
    """mas_project_state gauge from mas_core.observability.metrics must appear."""
    response = await client.get("/metrics")
    body = response.text
    assert "mas_project_state" in body, (
        "Expected 'mas_project_state' gauge in Prometheus metrics output"
    )


@pytest.mark.anyio
async def test_metrics_no_error_on_repeated_calls(client):
    """Repeated calls to /metrics must all succeed without error."""
    for _ in range(3):
        response = await client.get("/metrics")
        assert response.status_code == 200


@pytest.mark.anyio
async def test_metrics_reconciles_durable_project_states_before_scrape(client, monkeypatch):
    """A configured durable store refreshes aggregate state before rendering."""

    from orchestrator_api import main

    storage = object()
    calls: list[object] = []

    async def fake_reconcile(candidate):
        calls.append(candidate)

    monkeypatch.setattr(main.app.state, "storage", storage)
    monkeypatch.setattr(main, "_reconcile_project_state_metrics", fake_reconcile)

    response = await client.get("/metrics")

    assert response.status_code == 200
    assert calls == [storage]


def test_project_state_metric_uses_bounded_labels_and_budget():
    """Project IDs must not create unbounded Prometheus series."""
    assert MAS_PROJECT_STATE._labelnames == ("state",)
    status = metric_series_budget_status()
    assert status["passed"] is True
    assert status["family_budgets"]["mas_project_state"] == 32


def test_aiat_metric_label_inventory_has_no_raw_project_identifier():
    """Every AIAT metric keeps project drill-down out of Prometheus labels."""

    inventory = metric_label_inventory()
    assert inventory
    assert all("project_id" not in labels for labels in inventory.values())


def test_aiat_metric_label_policies_classify_every_declared_label_as_bounded():
    """Every AIAT label has an explicit cardinality classification."""

    policies = metric_label_policy_inventory()
    assert policies
    assert all(
        policy["classification"] == "bounded"
        for family in policies.values()
        for policy in family.values()
    )


def test_project_state_metric_is_aggregate_presence_not_last_transition():
    """Leaving one of two projects in a state must not clear that state."""

    reconcile_project_state_metrics(["IN_PROGRESS", "IN_PROGRESS", "FAILED"])
    record_project_state_transition("IN_PROGRESS", "COMPLETED")

    counts = reconcile_project_state_metrics(["IN_PROGRESS", "FAILED", "COMPLETED"])
    assert counts["IN_PROGRESS"] == 1
    assert MAS_PROJECT_STATE.labels(state="IN_PROGRESS")._value.get() == 1
    assert MAS_PROJECT_STATE.labels(state="COMPLETED")._value.get() == 1

    # Keep the process-global test registry clean for later endpoint tests.
    reconcile_project_state_metrics([])


def test_project_state_metric_many_projects_remains_bounded():
    """A large project population creates no project-ID metric series."""

    states = [
        state
        for index in range(10_000)
        for state in ("INIT", "IN_PROGRESS", "FAILED")
        if index % 3 == 0 or state != "FAILED"
    ]
    reconcile_project_state_metrics(states)
    status = metric_series_budget_status()

    assert status["passed"] is True
    assert status["family_counts"]["mas_project_state"] <= 32
    assert MAS_PROJECT_STATE._labelnames == ("state",)

    reconcile_project_state_metrics([])
