from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

FLOW_ID = UUID("00000000-0000-4000-a000-0000000000d1")
SECOND_FLOW_ID = UUID("00000000-0000-4000-a000-0000000000d2")


def _definition(*, include_end: bool = True) -> dict[str, object]:
    nodes: list[dict[str, object]] = [{"id": "start", "type": "start", "config": {}}]
    edges: list[dict[str, object]] = []
    if include_end:
        nodes.append({"id": "end", "type": "end", "config": {}})
        edges.append({"id": "start-end", "source": "start", "target": "end"})
    return {"schema_version": "1.0", "nodes": nodes, "edges": edges}


def _flow(flow_id: UUID, definition: dict[str, object]) -> dict[str, object]:
    return {"id": flow_id, "version": 1, "definition_json": definition, "is_active": False}


@pytest.mark.anyio
async def test_flow_export_and_diff_return_stable_hashes(client):
    first = _flow(FLOW_ID, _definition())
    second_definition = {
        **_definition(),
        "nodes": [*_definition()["nodes"], {"id": "review", "type": "approval", "config": {}}],
    }
    second = _flow(SECOND_FLOW_ID, second_definition)
    storage = MagicMock()
    storage.get_flow = AsyncMock(side_effect=[first, first, second])
    from orchestrator_api.main import app

    app.state.storage = storage
    try:
        export_response = await client.get(f"/flows/{FLOW_ID}/export")
        diff_response = await client.post(
            "/flows/diff",
            json={"from_flow_id": str(FLOW_ID), "to_flow_id": str(SECOND_FLOW_ID)},
        )
    finally:
        app.state.storage = None

    assert export_response.status_code == 200
    assert export_response.json()["format"] == "aiat.flow-export.v1"
    assert len(export_response.json()["definition_sha256"]) == 64
    assert diff_response.status_code == 200
    assert diff_response.json()["changes"]["nodes"]["added"][0]["id"] == "review"


@pytest.mark.anyio
async def test_flow_import_reuses_validated_version_path(client):
    created = _flow(uuid4(), _definition())
    storage = MagicMock()
    storage.create_flow = AsyncMock(return_value=created)
    from orchestrator_api.main import app

    app.state.storage = storage
    try:
        response = await client.post(
            "/flows/import",
            json={"name": "Imported flow", "definition_json": _definition()},
        )
    finally:
        app.state.storage = None

    assert response.status_code == 201
    assert response.json()["status"] == "imported"
    assert storage.create_flow.await_args.kwargs["definition_json"]["schema_version"] == "1.0"


@pytest.mark.anyio
async def test_flow_publish_and_deprecate_toggle_selection_without_deleting_history(client):
    flow = _flow(FLOW_ID, _definition())
    published = {**flow, "is_active": True}
    deprecated = {**flow, "is_active": False}
    storage = MagicMock()
    storage.get_flow = AsyncMock(side_effect=[flow, flow])
    storage.update_flow = AsyncMock(side_effect=[published, deprecated])
    from orchestrator_api.main import app

    app.state.storage = storage
    try:
        publish_response = await client.post(f"/flows/{FLOW_ID}/publish")
        deprecate_response = await client.post(f"/flows/{FLOW_ID}/deprecate")
    finally:
        app.state.storage = None

    assert publish_response.status_code == 200
    assert deprecate_response.status_code == 200
    assert storage.update_flow.await_args_list[0].kwargs == {"is_active": True}
    assert storage.update_flow.await_args_list[1].kwargs == {"is_active": False}
