from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest


def _patch_storage(storage: MagicMock) -> None:
    from orchestrator_api.main import app

    app.state.storage = storage


@pytest.mark.anyio
async def test_artifact_evidence_returns_bounded_scalar_metadata(client):
    storage = MagicMock()
    storage.get_artifact = AsyncMock(
        return_value={
            "id": 42,
            "agent_id": "requirements_writer",
            "path": "project-1/requirements.md",
            "sha256": "a" * 64,
            "size_bytes": 128,
            "created_at": datetime(2026, 8, 11, tzinfo=UTC),
            "metadata": {
                "project_id": "project-1",
                "secret": "must-not-cross-boundary",
                "license": "metadata-only",
            },
        }
    )
    _patch_storage(storage)

    response = await client.get("/artifacts/42", headers={"X-API-Key": "test-operator-key"})

    assert response.status_code == 200
    assert response.json() == {
        "agent_id": "requirements_writer",
        "created_at": "2026-08-11T00:00:00+00:00",
        "id": 42,
        "path": "project-1/requirements.md",
        "sha256": "a" * 64,
        "size_bytes": 128,
    }
    assert "metadata" not in response.text
    assert "must-not-cross-boundary" not in response.text
    storage.get_artifact.assert_awaited_once_with(42)


@pytest.mark.anyio
async def test_artifact_evidence_returns_not_found_without_leaking_storage(client):
    storage = MagicMock()
    storage.get_artifact = AsyncMock(return_value=None)
    _patch_storage(storage)

    response = await client.get("/artifacts/404", headers={"X-API-Key": "test-operator-key"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Artifact 404 not found"


@pytest.mark.anyio
async def test_usage_evidence_returns_bounded_scalar_metadata(client):
    event_id = UUID("00000000-0000-4000-a000-000000000099")
    storage = MagicMock()
    storage.get_project_usage_event = AsyncMock(
        return_value={
            "id": event_id,
            "project_id": UUID("00000000-0000-4000-a000-000000000001"),
            "company_id": UUID("00000000-0000-4000-a000-000000000002"),
            "event_type": "llm",
            "agent_id": "financial_analyst",
            "team_id": "office_cfo",
            "model": "local-model",
            "provider_id": "omniroute",
            "tool_name": None,
            "status": "success",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "cost_usd": Decimal("0.01250000"),
            "duration_ms": Decimal("42.500"),
            "trace_id": "trace-usage-1",
            "span_id": "span-usage-1",
            "occurred_at": datetime(2026, 8, 11, tzinfo=UTC),
            "pricing_snapshot": {"secret": "must-not-cross-boundary"},
            "resource_json": {"credential": "must-not-cross-boundary"},
            "details": {"prompt": "must-not-cross-boundary"},
            "idempotency_key": "private-key",
        }
    )
    _patch_storage(storage)

    response = await client.get(
        f"/usage/events/{event_id}",
        headers={"X-API-Key": "test-operator-key"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(event_id)
    assert body["event_type"] == "llm"
    assert body["cost_usd"] == "0.01250000"
    assert body["duration_ms"] == "42.500"
    for private_key in (
        "pricing_snapshot",
        "resource_json",
        "details",
        "idempotency_key",
    ):
        assert private_key not in body
    assert "must-not-cross-boundary" not in response.text
    storage.get_project_usage_event.assert_awaited_once_with(event_id)


@pytest.mark.anyio
async def test_evidence_detail_requires_operator_principal(client):
    from orchestrator_api.main import app

    previous = app.state.storage
    storage = MagicMock()
    storage.get_artifact = AsyncMock()
    storage.get_project_usage_event = AsyncMock()
    app.state.storage = storage
    try:
        artifact = await client.get("/artifacts/42", headers={"X-API-Key": "test-mas-key"})
        usage = await client.get(
            "/usage/events/00000000-0000-4000-a000-000000000099",
            headers={"X-API-Key": "test-mas-key"},
        )
    finally:
        app.state.storage = previous

    assert artifact.status_code == 403
    assert usage.status_code == 403
    storage.get_artifact.assert_not_awaited()
    storage.get_project_usage_event.assert_not_awaited()
