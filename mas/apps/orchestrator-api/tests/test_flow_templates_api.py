from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest


def _research_definition(task_config: dict[str, str]) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "metadata": {"template_id": "research", "evidence_policy": "research"},
        "nodes": [
            {"id": "start", "type": "start", "label": "Start", "config": {}},
            {"id": "research", "type": "task", "label": "Research", "config": task_config},
            {"id": "end", "type": "end", "label": "Complete", "config": {}},
        ],
        "edges": [
            {"id": "start-research", "source": "start", "target": "research"},
            {"id": "research-end", "source": "research", "target": "end"},
        ],
    }


@pytest.mark.anyio
async def test_flow_template_catalog_is_public_and_sorted(client):
    response = await client.get("/flow-templates")

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "aiat.flow-template.v1"
    assert [item["template_id"] for item in body["templates"]] == sorted(
        item["template_id"] for item in body["templates"]
    )
    assert len(body["templates"]) == 6


@pytest.mark.anyio
async def test_research_template_explains_operator_worker_binding_step(client):
    response = await client.get("/flow-templates")

    assert response.status_code == 200
    research = next(
        item for item in response.json()["templates"] if item["template_id"] == "research"
    )
    assert "Bind a governed worker" in research["description"]
    task_nodes = [
        node
        for node in research["definition_json"]["nodes"]
        if node["type"] == "task"
    ]
    assert [node["id"] for node in task_nodes] == ["research", "report"]
    assert all(not node["config"].get("worker_id") for node in task_nodes)


@pytest.mark.anyio
async def test_flow_from_template_reuses_validated_create_path(client):
    from orchestrator_api.main import app

    flow_id = uuid4()
    created = {"id": flow_id, "name": "Delivery", "version": 1}
    storage = MagicMock()
    storage.create_flow = AsyncMock(return_value=created)
    app.state.storage = storage

    try:
        response = await client.post(
            "/flows/from-template",
            json={"template_id": "software_delivery", "name": "Delivery"},
        )
    finally:
        app.state.storage = None

    assert response.status_code == 201
    assert response.json()["status"] == "created_from_template"
    assert response.json()["template_id"] == "software_delivery"
    payload = storage.create_flow.await_args.kwargs
    assert payload["definition_json"]["schema_version"] == "1.0"
    assert payload["definition_json"]["metadata"]["template_id"] == "software_delivery"


@pytest.mark.anyio
async def test_unknown_flow_template_is_rejected(client):
    response = await client.post("/flows/from-template", json={"template_id": "missing"})

    assert response.status_code == 404


@pytest.mark.anyio
async def test_active_research_template_requires_bindings_but_draft_is_allowed(client):
    from orchestrator_api.main import app

    storage = MagicMock()
    storage.create_flow = AsyncMock(return_value={"id": uuid4(), "is_active": False})
    app.state.storage = storage
    try:
        active_response = await client.post(
            "/flows/from-template",
            json={"template_id": "research", "is_active": True},
        )
        draft_response = await client.post(
            "/flows/from-template",
            json={"template_id": "research", "is_active": False},
        )
    finally:
        app.state.storage = None

    assert active_response.status_code == 422
    assert active_response.json()["detail"]["code"] == "RESEARCH_TEMPLATE_BINDING_REQUIRED"
    assert active_response.json()["detail"]["missing_bindings"] == [
        {"node_id": "research", "field": "worker_id"},
        {"node_id": "report", "field": "worker_id"},
    ]
    assert draft_response.status_code == 201
    assert storage.create_flow.await_count == 1
    assert storage.create_flow.await_args.kwargs["is_active"] is False


@pytest.mark.anyio
async def test_active_research_task_requires_explicit_model_profile_binding(client):
    from orchestrator_api.main import app

    storage = MagicMock()
    storage.create_flow = AsyncMock()
    app.state.storage = storage
    try:
        response = await client.post(
            "/flows",
            json={
                "name": "Bound research",
                "is_active": True,
                "definition_json": _research_definition(
                    {
                        "worker_id": str(uuid4()),
                        "model_mode": "aiat_gateway",
                        "action": "research.collect",
                    }
                ),
            },
        )
    finally:
        app.state.storage = None

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "RESEARCH_TEMPLATE_BINDING_REQUIRED"
    assert response.json()["detail"]["missing_bindings"] == [
        {"node_id": "research", "field": "model_profile_id"}
    ]
    storage.create_flow.assert_not_awaited()


@pytest.mark.anyio
async def test_active_research_flow_uses_governed_readiness_without_dispatch(client):
    from orchestrator_api.main import app

    worker_id = uuid4()
    profile_id = "research-approved"
    storage = MagicMock()
    storage.get_worker = AsyncMock(
        return_value={
            "id": worker_id,
            "status": "ACTIVE",
            "model_mode": "aiat_gateway",
            "model_profile_id": profile_id,
            "adapter_config": {},
        }
    )
    storage.get_active_runtime_adapter = AsyncMock(
        return_value={
            "status": "active",
            "conformance_status": "passed",
            "capabilities_json": {"checkpoint_mode": "native", "capability_names": []},
        }
    )
    storage.get_model_profile = AsyncMock(
        return_value={
            "logical_profile_id": profile_id,
            "purpose": "research",
            "status": "approved",
            "versions": [
                {
                    "version": "v1",
                    "provider_id": "local-fixture",
                    "exact_model_id": "deterministic-fixture",
                    "status": "approved",
                }
            ],
        }
    )
    storage.create_flow = AsyncMock(return_value={"id": uuid4(), "is_active": True})
    app.state.storage = storage
    try:
        response = await client.post(
            "/flows",
            json={
                "name": "Ready research flow",
                "is_active": True,
                "definition_json": _research_definition(
                    {
                        "worker_id": str(worker_id),
                        "model_mode": "aiat_gateway",
                        "model_profile_id": profile_id,
                        "action": "research.collect",
                    }
                ),
            },
        )
    finally:
        app.state.storage = None

    assert response.status_code == 201
    assert response.json()["is_active"] is True
    storage.get_worker.assert_awaited_once_with(worker_id)
    storage.get_model_profile.assert_awaited_once_with(profile_id)
    storage.create_flow.assert_awaited_once()


@pytest.mark.anyio
async def test_research_flow_cannot_be_activated_by_update_or_publish(client):
    from orchestrator_api.main import app

    flow_id = uuid4()
    flow = {
        "id": flow_id,
        "name": "Research draft",
        "definition_json": _research_definition(
            {"team_id": "legacy-team", "action": "research.collect"}
        ),
        "is_active": False,
    }
    storage = MagicMock()
    storage.get_flow = AsyncMock(return_value=flow)
    storage.update_flow = AsyncMock()
    app.state.storage = storage
    try:
        update_response = await client.put(
            f"/flows/{flow_id}", json={"is_active": True}
        )
        publish_response = await client.post(f"/flows/{flow_id}/publish")
    finally:
        app.state.storage = None

    for response in (update_response, publish_response):
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "RESEARCH_TEMPLATE_BINDING_REQUIRED"
    storage.update_flow.assert_not_awaited()
