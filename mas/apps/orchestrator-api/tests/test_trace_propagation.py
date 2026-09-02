from __future__ import annotations

import pytest

from mas_core.observability.tracing import bind_trace_id, clear_trace_context, current_trace_id


@pytest.mark.anyio
async def test_api_propagates_safe_incoming_trace_id_and_clears_context(client) -> None:
    response = await client.get(
        "/health",
        headers={"X-AIAT-Trace-ID": "operator-flow-123"},
    )

    assert response.status_code == 200
    assert response.headers["X-AIAT-Trace-ID"] == "operator-flow-123"
    assert current_trace_id() is None


@pytest.mark.anyio
async def test_api_accepts_traceparent_trace_id_and_replaces_invalid_values(client) -> None:
    traceparent = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
    response = await client.get("/health", headers={"traceparent": traceparent})
    assert response.status_code == 200
    assert response.headers["X-AIAT-Trace-ID"] == "0123456789abcdef0123456789abcdef"

    invalid = await client.get("/health", headers={"X-AIAT-Trace-ID": "bad trace value"})
    assert invalid.status_code == 200
    generated = invalid.headers["X-AIAT-Trace-ID"]
    assert generated != "bad trace value"
    assert len(generated) == 32


@pytest.mark.anyio
async def test_api_request_observation_is_recorded_without_payloads(client) -> None:
    from orchestrator_api.main import app

    class Recorder:
        def __init__(self) -> None:
            self.rows: list[dict] = []

        async def record_api_request_observation(self, **fields):
            self.rows.append(fields)

    previous = app.state.storage
    recorder = Recorder()
    app.state.storage = recorder
    try:
        response = await client.get(
            "/health?secret=never-return",
            headers={"X-AIAT-Trace-ID": "api-observation-test"},
        )
    finally:
        app.state.storage = previous

    assert response.status_code == 200
    assert len(recorder.rows) == 1
    row = recorder.rows[0]
    assert row["method"] == "GET"
    assert row["path"] == "/health"
    assert row["trace_id"] == "api-observation-test"
    assert "secret" not in str(row)
    assert "headers" not in row
    assert "body" not in row


def test_router_auth_headers_carry_bound_trace_id(monkeypatch) -> None:
    from orchestrator_api.main import _router_auth_headers

    monkeypatch.setenv("ROUTER_SECRET", "router-secret")
    bind_trace_id("api-to-router-123")
    try:
        assert _router_auth_headers() == {
            "Authorization": "Bearer orchestrator-api:router-secret",
            "X-AIAT-Trace-ID": "api-to-router-123",
        }
    finally:
        clear_trace_context()
