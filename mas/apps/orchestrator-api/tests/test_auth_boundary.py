"""Regression coverage for the control-plane authentication boundary."""

from __future__ import annotations

import httpx
import pytest


@pytest.mark.asyncio
async def test_non_health_endpoints_require_an_api_key() -> None:
    from orchestrator_api.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/metrics")).status_code == 401
        assert (await client.get("/metrics", headers={"X-API-Key": "test-mas-key"})).status_code == 200

