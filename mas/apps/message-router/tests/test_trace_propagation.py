"""HTTP trace propagation and context cleanup for the message router."""

from __future__ import annotations

import pytest

from mas_core.observability.tracing import current_trace_id


@pytest.mark.anyio
async def test_router_propagates_safe_trace_id_and_clears_context(client) -> None:
    response = await client.get(
        "/health",
        headers={"X-AIAT-Trace-ID": "router-flow-123"},
    )

    assert response.status_code == 200
    assert response.headers["X-AIAT-Trace-ID"] == "router-flow-123"
    assert current_trace_id() is None


@pytest.mark.anyio
async def test_router_accepts_traceparent_and_replaces_invalid_values(client) -> None:
    traceparent = "00-abcdef0123456789abcdef0123456789-0123456789abcdef-01"
    response = await client.get("/health", headers={"traceparent": traceparent})
    assert response.status_code == 200
    assert response.headers["X-AIAT-Trace-ID"] == "abcdef0123456789abcdef0123456789"

    invalid = await client.get("/health", headers={"X-AIAT-Trace-ID": "bad trace value"})
    assert invalid.status_code == 200
    generated = invalid.headers["X-AIAT-Trace-ID"]
    assert generated != "bad trace value"
    assert len(generated) == 32
    assert current_trace_id() is None
