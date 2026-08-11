"""Smoke tests for mas_tools_sdk package."""

from __future__ import annotations

import pytest

from mas_core.observability import bind_trace_id, clear_trace_context
from mas_core.protocols.enums import AgentRole


def test_mas_tools_sdk_importable():
    import mas_tools_sdk  # noqa: F401


def test_sdk_documents_base_tool():
    from mas_tools_sdk import __doc__ as doc
    assert doc is not None
    assert "BaseTool" in doc


@pytest.mark.anyio
async def test_tool_client_forwards_bound_trace_id():
    """The SDK carries the current async trace into the HTTP boundary."""

    import respx
    from httpx import Response

    from mas_tools_sdk.client import ToolServiceClient

    with respx.mock(base_url="http://tool-service:8002") as mock:
        route = mock.post("/tools/web_search/run").mock(
            return_value=Response(
                200,
                json={"tool_name": "web_search", "success": True, "trace_id": "sdk-flow-123"},
            )
        )
        client = ToolServiceClient("http://tool-service:8002", secret="tool-secret")
        bind_trace_id("sdk-flow-123")
        try:
            response = await client.execute(
                tool_name="web_search",
                caller_id="worker-1",
                caller_role=AgentRole.WORKER,
            )
        finally:
            clear_trace_context()
            await client.close()

    assert response.trace_id == "sdk-flow-123"
    assert route.called
    assert route.calls[0].request.headers["X-AIAT-Trace-ID"] == "sdk-flow-123"
