"""Exact draft Model Profile activation remains operator governed."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["approved", "active"])
async def test_model_profile_creation_rejects_non_draft_status(client, status: str) -> None:
    from orchestrator_api.main import app

    class Storage:
        get_model_profile = AsyncMock(return_value=None)
        create_model_profile = AsyncMock()
        create_model_profile_version = AsyncMock()

    storage = Storage()
    app.state.storage = storage
    headers = {"X-API-Key": "test-operator-key"}

    profile = await client.post(
        "/model-profiles",
        headers=headers,
        json={
            "profile_id": f"draft-only-{status}",
            "purpose": "test-only model profile",
            "approved_provider_ids": ["local-fixture"],
            "status": status,
        },
    )
    version = await client.post(
        f"/model-profiles/draft-only-{status}/versions",
        headers=headers,
        json={
            "version": "v1",
            "provider_id": "local-fixture",
            "exact_model_id": "fixture/no-dispatch",
            "status": status,
        },
    )

    assert profile.status_code == 422
    assert version.status_code == 422
    storage.get_model_profile.assert_not_awaited()
    storage.create_model_profile.assert_not_awaited()
    storage.create_model_profile_version.assert_not_awaited()


@pytest.mark.anyio
async def test_model_profile_activation_requires_operator_identity(client, monkeypatch) -> None:
    from orchestrator_api.main import app

    monkeypatch.setenv("AIAT_WORKER_API_KEY", "test-worker-key")

    class Storage:
        activate_model_profile_version = AsyncMock()

    storage = Storage()
    app.state.storage = storage
    version_id = uuid4()
    path = f"/model-profiles/coding/versions/{version_id}/activate"
    payload = {"reason": "Approved exact local profile version for coding"}

    service_response = await client.post(path, json=payload)
    worker_response = await client.post(
        path,
        headers={"X-API-Key": "test-worker-key"},
        json=payload,
    )

    assert service_response.status_code == 403
    assert worker_response.status_code == 403
    storage.activate_model_profile_version.assert_not_awaited()


@pytest.mark.anyio
async def test_operator_activates_exact_profile_version_with_durable_audit(client) -> None:
    from orchestrator_api.main import app

    profile_record_id = uuid4()
    version_id = uuid4()
    approval_id = uuid4()
    created_at = "2026-10-07T12:00:00+00:00"
    approval_record = {
        "id": approval_id,
        "scope_type": "model_profile_version",
        "scope_id": version_id,
        "decision": "APPROVED",
        "decided_by": "operator",
        "reason": "Reviewed provider, exact model, and limits",
        "evidence": {
            "profile_id": "coding",
            "profile_record_id": str(profile_record_id),
            "version_id": str(version_id),
            "version": "v2",
            "provider_id": "local-fixture",
            "exact_model_id": "fixture/no-dispatch",
            "version_snapshot_sha256": "a" * 64,
        },
        "created_at": created_at,
    }

    class Storage:
        activate_model_profile_version = AsyncMock(
            return_value={
                "profile": {
                    "id": profile_record_id,
                    "logical_profile_id": "coding",
                    "status": "approved",
                },
                "version": {
                    "id": version_id,
                    "version": "v2",
                    "status": "approved",
                },
                "approval_record": approval_record,
                "already_approved": False,
            }
        )

    app.state.storage = Storage()
    response = await client.post(
        f"/model-profiles/coding/versions/{version_id}/activate",
        headers={"X-API-Key": "test-operator-key"},
        json={"reason": "  Reviewed provider, exact model, and limits  "},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["profile_id"] == "coding"
    assert body["profile_status"] == "approved"
    assert body["version_id"] == str(version_id)
    assert body["version"] == "v2"
    assert body["version_status"] == "approved"
    assert body["decision"] == "APPROVED"
    assert body["already_approved"] is False
    assert body["approval_record"]["id"] == str(approval_id)
    assert body["approval_record"]["decided_by"] == "operator"
    assert body["approval_record"]["reason"] == "Reviewed provider, exact model, and limits"
    assert body["approval_record"]["created_at"] == created_at
    app.state.storage.activate_model_profile_version.assert_awaited_once_with(
        logical_profile_id="coding",
        version_id=UUID(str(version_id)),
        decided_by="operator",
        reason="Reviewed provider, exact model, and limits",
    )


@pytest.mark.anyio
async def test_profile_list_exposes_persisted_version_identity(client) -> None:
    from orchestrator_api.main import app

    version_id = uuid4()

    class Storage:
        list_model_profiles = AsyncMock(
            return_value=[
                {
                    "id": uuid4(),
                    "logical_profile_id": "coding",
                    "purpose": "Governed coding tasks",
                    "approved_provider_ids": ["local-fixture"],
                    "required_capabilities": [],
                    "fallback_profile_ids": [],
                    "status": "draft",
                    "versions": [
                        {
                            "id": version_id,
                            "version": "v1",
                            "provider_id": "local-fixture",
                            "exact_model_id": "fixture/no-dispatch",
                            "capabilities": [],
                            "constraints_json": {},
                            "provider_settings": {},
                            "status": "draft",
                        }
                    ],
                }
            ]
        )

    app.state.storage = Storage()
    response = await client.get("/model-profiles")

    assert response.status_code == 200
    assert response.json()[0]["versions"][0]["id"] == str(version_id)
