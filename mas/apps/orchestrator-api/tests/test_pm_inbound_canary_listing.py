from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest


@pytest.mark.anyio
async def test_operator_can_list_bounded_inbound_canary_summaries(client):
    from orchestrator_api.main import app

    plan_id = uuid4()
    connection_id = uuid4()
    binding_id = uuid4()
    project_id = uuid4()
    created_at = datetime(2026, 10, 8, tzinfo=UTC)
    storage = MagicMock()
    storage.list_pm_inbound_canary_plans = AsyncMock(return_value=[
        {
            "id": plan_id,
            "connection_id": connection_id,
            "binding_id": binding_id,
            "project_id": project_id,
            "external_issue_id": "YT-17",
            "status": "PLANNED",
            "current_priority": "medium",
            "target_priority": "high",
            "created_at": created_at,
            "expires_at": created_at,
            "digest": "must not be included in the summary",
            "operations": [{"operation": "private detail omitted from the summary"}],
        }
    ])
    app.state.storage = storage

    response = await client.get(
        f"/integrations/inbound-canaries?connection_id={connection_id}&limit=25",
        headers={"X-API-Key": "test-operator-key"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["limit"] == 25
    assert payload["has_more"] is False
    assert payload["plans"] == [
        {
            "plan_id": str(plan_id),
            "connection_id": str(connection_id),
            "binding_id": str(binding_id),
            "project_id": str(project_id),
            "external_issue_id": "YT-17",
            "status": "PLANNED",
            "current_priority": "medium",
            "target_priority": "high",
            "created_at": created_at.isoformat(),
            "expires_at": created_at.isoformat(),
        }
    ]
    assert "digest" not in payload["plans"][0]
    assert "operations" not in payload["plans"][0]
    storage.list_pm_inbound_canary_plans.assert_awaited_once_with(
        connection_id=connection_id,
        limit=26,
    )


@pytest.mark.anyio
async def test_inbound_canary_list_requires_operator_authority(client):
    from orchestrator_api.main import app

    storage = MagicMock()
    storage.list_pm_inbound_canary_plans = AsyncMock(return_value=[])
    app.state.storage = storage

    response = await client.get("/integrations/inbound-canaries")

    assert response.status_code == 403
    storage.list_pm_inbound_canary_plans.assert_not_awaited()


@pytest.mark.anyio
async def test_inbound_canary_list_marks_results_beyond_the_requested_limit(client):
    from orchestrator_api.main import app

    storage = MagicMock()
    storage.list_pm_inbound_canary_plans = AsyncMock(return_value=[{"id": uuid4()}, {"id": uuid4()}])
    app.state.storage = storage

    response = await client.get(
        "/integrations/inbound-canaries?limit=1",
        headers={"X-API-Key": "test-operator-key"},
    )

    assert response.status_code == 200
    assert response.json()["limit"] == 1
    assert response.json()["has_more"] is True
    assert len(response.json()["plans"]) == 1
    storage.list_pm_inbound_canary_plans.assert_awaited_once_with(connection_id=None, limit=2)


@pytest.mark.anyio
async def test_inbound_canary_list_caps_requested_limit(client):
    from orchestrator_api.main import app

    storage = MagicMock()
    storage.list_pm_inbound_canary_plans = AsyncMock(return_value=[])
    app.state.storage = storage

    response = await client.get(
        "/integrations/inbound-canaries?limit=101",
        headers={"X-API-Key": "test-operator-key"},
    )

    assert response.status_code == 422
    storage.list_pm_inbound_canary_plans.assert_not_awaited()
