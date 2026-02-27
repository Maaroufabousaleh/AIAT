"""
Root conftest for mas-tools-sdk tests.
"""
import pytest
import respx
import httpx


@pytest.fixture
def mock_tool_service():
    """HTTPX mock for tool-service — no real HTTP in unit tests."""
    with respx.mock(base_url="http://tool-service:8002") as mock:
        mock.get("/health").respond(200, json={"status": "ok"})
        mock.get("/tools").respond(200, json={"tools": []})
        yield mock
