"""Smoke tests for tool-service."""
import pytest


@pytest.mark.anyio
async def test_health_endpoint(client):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "tools_registered" in data
    assert data["tools_registered"] > 0


@pytest.mark.anyio
async def test_tools_endpoint_returns_list(client):
    response = await client.get("/tools")
    assert response.status_code == 200
    data = response.json()
    assert "tools" in data
    assert isinstance(data["tools"], list)
    assert data["count"] > 0

