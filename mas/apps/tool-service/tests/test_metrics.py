"""Tests for the /metrics endpoint on the tool-service.

Verifies:
- /metrics returns HTTP 200
- Response uses the Prometheus text exposition format
- Custom counters (tool_invocations_total, tool_errors_total) appear
"""

from __future__ import annotations

import pytest


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
async def test_metrics_custom_counter_tool_invocations_total(client):
    """tool_invocations_total counter must be declared in /metrics output."""
    response = await client.get("/metrics")
    body = response.text
    assert "tool_invocations_total" in body, (
        "Expected 'tool_invocations_total' counter in Prometheus metrics output"
    )


@pytest.mark.anyio
async def test_metrics_custom_counter_tool_errors_total(client):
    """tool_errors_total counter must be declared in /metrics output."""
    response = await client.get("/metrics")
    body = response.text
    assert "tool_errors_total" in body, (
        "Expected 'tool_errors_total' counter in Prometheus metrics output"
    )


@pytest.mark.anyio
async def test_metrics_no_error_on_repeated_calls(client):
    """Repeated calls to /metrics must all succeed without error."""
    for _ in range(3):
        response = await client.get("/metrics")
        assert response.status_code == 200
