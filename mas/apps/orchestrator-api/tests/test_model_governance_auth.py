"""Model-governance mutations require the authenticated operator identity."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest


@pytest.mark.anyio
async def test_service_and_worker_cannot_mutate_model_governance(client, monkeypatch) -> None:
    from orchestrator_api.main import app

    monkeypatch.setenv("AIAT_WORKER_API_KEY", "test-worker-key")

    class Storage:
        get_model_profile = AsyncMock()
        get_model_override_request = AsyncMock()
        get_project = AsyncMock()

    storage = Storage()
    app.state.storage = storage
    profile = {
        "profile_id": "local-fixture",
        "purpose": "bounded local tests",
        "approved_provider_ids": ["local-fixture"],
    }
    version = {
        "version": "v1",
        "provider_id": "local-fixture",
        "exact_model_id": "fixture/no-dispatch",
        "status": "draft",
        "local": True,
    }
    override_id = UUID("10000000-0000-4000-8000-000000000001")
    decision = {
        "decision": "APPROVED",
        "decided_by": "spoofed-operator",
        "reason": "not an authenticated operator",
    }

    service_calls = [
        await client.post("/model-profiles", json=profile),
        await client.post(
            "/model-profiles/local-fixture/versions",
            json=version,
        ),
        await client.post(
            "/model-overrides",
            json={
                "project_id": "00000000-0000-4000-a000-000000000001",
                "requested_by": "spoofed-operator",
                "requested_profile_id": "local-fixture",
                "reason": "test request",
            },
        ),
        await client.post(f"/model-overrides/{override_id}/decision", json=decision),
    ]
    worker_calls = [
        await client.post(
            "/model-profiles",
            headers={"X-API-Key": "test-worker-key"},
            json=profile,
        ),
        await client.post(
            "/model-profiles/local-fixture/versions",
            headers={"X-API-Key": "test-worker-key"},
            json=version,
        ),
        await client.post(
            f"/model-overrides/{override_id}/decision",
            headers={"X-API-Key": "test-worker-key"},
            json=decision,
        ),
    ]

    assert [response.status_code for response in service_calls] == [403, 403, 403, 403]
    assert [response.status_code for response in worker_calls] == [403, 403, 403]
    storage.get_model_profile.assert_not_awaited()
    storage.get_model_override_request.assert_not_awaited()
    storage.get_project.assert_not_awaited()


@pytest.mark.anyio
async def test_operator_can_create_draft_model_profile_and_version(client) -> None:
    from orchestrator_api.main import app

    profile_row = {
        "id": uuid4(),
        "profile_id": "local-fixture",
        "purpose": "bounded local tests",
        "approved_provider_ids": ["local-fixture"],
    }

    class Storage:
        get_model_profile = AsyncMock(side_effect=[None, profile_row])
        create_model_profile = AsyncMock()
        create_model_profile_version = AsyncMock()

    storage = Storage()
    app.state.storage = storage
    headers = {"X-API-Key": "test-operator-key"}

    created = await client.post(
        "/model-profiles",
        headers=headers,
        json={
            "profile_id": "local-fixture",
            "purpose": "bounded local tests",
            "approved_provider_ids": ["local-fixture"],
            "status": "draft",
        },
    )
    versioned = await client.post(
        "/model-profiles/local-fixture/versions",
        headers=headers,
        json={
            "version": "v1",
            "provider_id": "local-fixture",
            "exact_model_id": "fixture/no-dispatch",
            "status": "draft",
            "local": True,
        },
    )

    assert created.status_code == 201
    assert created.json()["status"] == "draft"
    assert versioned.status_code == 201
    assert versioned.json()["exact_model_id"] == "fixture/no-dispatch"
    storage.create_model_profile.assert_awaited_once()
    storage.create_model_profile_version.assert_awaited_once()


@pytest.mark.anyio
async def test_model_override_decision_uses_authenticated_operator_for_audit(client) -> None:
    from orchestrator_api.main import app

    override_id = UUID("10000000-0000-4000-8000-000000000002")
    updated = {
        "id": override_id,
        "status": "APPROVED",
        "decided_by": "operator",
    }

    class Storage:
        get_model_override_request = AsyncMock(
            return_value={"id": override_id, "status": "PENDING"}
        )
        create_approval_record = AsyncMock(return_value={"id": "approval-1"})
        update_model_override_request = AsyncMock(return_value=updated)

    storage = Storage()
    app.state.storage = storage
    response = await client.post(
        f"/model-overrides/{override_id}/decision",
        headers={"X-API-Key": "test-operator-key"},
        json={
            "decision": "APPROVED",
            "decided_by": "spoofed-operator",
            "reason": "reviewed by operator",
        },
    )

    assert response.status_code == 200
    assert storage.create_approval_record.await_args.kwargs["decided_by"] == "operator"
    assert storage.update_model_override_request.await_args.kwargs["decided_by"] == "operator"
