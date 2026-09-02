from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from conftest import PROJECT_ID, _fake_project


def _storage(project: dict | None = None) -> MagicMock:
    storage = MagicMock()
    storage.get_project = AsyncMock(return_value=project or _fake_project("IN_PROGRESS"))
    storage.list_documents = AsyncMock(return_value=[])
    storage.list_artifacts = AsyncMock(return_value=[])
    storage.get_flow_instance_by_project = AsyncMock(return_value=None)
    storage.list_approval_gates = AsyncMock(return_value=[])
    storage.list_worker_runs = AsyncMock(return_value=[])
    storage.get_project_repository_record = AsyncMock(return_value=None)
    storage.get_project_history = AsyncMock(return_value=[])
    storage.get_project_usage = AsyncMock(return_value={
        "available": True,
        "total_cost_usd": 0.12,
        "total_tokens": 500,
        "llm_calls": 2,
        "tool_calls": 3,
    })
    return storage


def _patch_state(storage: MagicMock) -> None:
    from orchestrator_api.main import app

    app.state.storage = storage


@pytest.mark.anyio
async def test_package_route_is_secret_safe_and_keeps_licence_as_notice(client):
    storage = _storage()
    storage.list_artifacts = AsyncMock(return_value=[
        {
            "id": 1,
            "kind": "security-scan",
            "path": f"{PROJECT_ID}/security.json",
            "metadata": {"project_id": str(PROJECT_ID), "license": "notice-only"},
        }
    ])
    _patch_state(storage)

    response = await client.get(f"/projects/{PROJECT_ID}/evidence/package")

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "aiat.project-evidence-package.v1"
    assert body["snapshot"] is None
    assert body["notices"] == [
        {"artifact_id": "1", "field": "license", "value": "notice-only"}
    ]
    assert body["status"] == "incomplete"


@pytest.mark.anyio
async def test_package_snapshot_requires_operator_and_is_idempotent(client):
    storage = _storage()
    storage.create_project_evidence_package = AsyncMock(
        return_value={"id": "snapshot-1", "project_id": PROJECT_ID, "status": "incomplete"}
    )
    _patch_state(storage)

    denied = await client.post(f"/projects/{PROJECT_ID}/evidence/package")
    assert denied.status_code == 403

    allowed = await client.post(
        f"/projects/{PROJECT_ID}/evidence/package",
        headers={"X-API-Key": "test-operator-key"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["snapshot"]["id"] == "snapshot-1"
    storage.create_project_evidence_package.assert_awaited_once()


@pytest.mark.anyio
async def test_project_policy_route_persists_only_after_operator_validation(client):
    project = _fake_project("IN_PROGRESS")
    project["config"] = {}
    storage = _storage(project)
    storage.update_project_config = AsyncMock(
        return_value={**project, "config": {
            "evidence_policy": {
                "policy_id": "software_delivery",
                "version": "1.0",
                "requirements": {},
            }
        }}
    )
    _patch_state(storage)

    denied = await client.put(
        f"/projects/{PROJECT_ID}/evidence-policy",
        json={"policy_id": "software_delivery"},
    )
    assert denied.status_code == 403

    allowed = await client.put(
        f"/projects/{PROJECT_ID}/evidence-policy",
        headers={"X-API-Key": "test-operator-key"},
        json={"policy_id": "software_delivery"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["evidence_policy"]["policy_id"] == "software_delivery"
    storage.update_project_config.assert_awaited_once()


@pytest.mark.anyio
async def test_invalid_milestone_scope_is_a_client_error(client):
    storage = _storage()
    _patch_state(storage)

    response = await client.put(
        f"/projects/{PROJECT_ID}/evidence-policy",
        headers={"X-API-Key": "test-operator-key"},
        json={"policy_id": "operations", "scope": "milestone"},
    )

    assert response.status_code == 422
