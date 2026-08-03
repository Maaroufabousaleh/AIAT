from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.mark.anyio
async def test_gateway_health_and_metrics_are_dependency_light() -> None:
    from pm_gateway.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
        health = await client.get("/health")
        metrics = await client.get("/metrics")

    assert health.status_code == 200
    assert health.json()["service"] == "pm-gateway"
    assert metrics.status_code == 200
    assert "aiat_pm_gateway_up 1" in metrics.text


@pytest.mark.anyio
async def test_gateway_rejects_oversized_webhook_before_forwarding(monkeypatch) -> None:
    from pm_gateway import main

    monkeypatch.setattr(main.settings, "webhook_body_max_bytes", 8)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
        response = await client.post("/webhooks/00000000-0000-4000-a000-000000000001", content=b"123456789")
    assert response.status_code == 413


@pytest.mark.anyio
async def test_provider_webhook_does_not_require_internal_api_key(monkeypatch) -> None:
    from pm_gateway import main

    captured: dict[str, object] = {}

    async def fake_forward(connection_id, body, headers):
        captured.update(connection_id=connection_id, body=body, headers=headers)
        return httpx.Response(202, json={"status": "accepted"})

    monkeypatch.setattr(main, "_forward_to_orchestrator", fake_forward)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
        response = await client.post(
            "/webhooks/00000000-0000-4000-a000-000000000001",
            content=b'{"event":"issue.updated"}',
            headers={
                "X-YouTrack-Token": "provider-token",
                "X-YouTrack-Delivery": "delivery-1",
            },
        )

    assert response.status_code == 202
    assert captured["body"] == b'{"event":"issue.updated"}'
    forwarded_headers = captured["headers"]
    assert forwarded_headers["x-youtrack-token"] == "provider-token"
    assert forwarded_headers["x-youtrack-delivery"] == "delivery-1"
    assert "x-api-key" not in forwarded_headers


def test_production_gateway_requires_a_long_api_key(monkeypatch) -> None:
    from pm_gateway.config import Settings
    from pydantic import ValidationError

    monkeypatch.setenv("PM_GATEWAY_ENVIRONMENT", "production")
    monkeypatch.setenv("MAS_API_KEY", "short")
    with pytest.raises(ValidationError):
        Settings()


def test_gateway_forwards_configurable_youtrack_headers() -> None:
    from pm_gateway.main import _safe_provider_headers
    from starlette.requests import Request

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/webhooks/test",
            "headers": [(b"x-youtrack-custom-token", b"token"), (b"x-untrusted", b"drop")],
        }
    )

    headers = _safe_provider_headers(request)

    assert headers["x-youtrack-custom-token"] == "token"
    assert "x-untrusted" not in headers
