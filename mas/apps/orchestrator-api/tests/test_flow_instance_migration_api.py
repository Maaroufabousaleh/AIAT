from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

INSTANCE_ID = UUID("00000000-0000-4000-a000-0000000000e1")
SOURCE_FLOW_ID = UUID("00000000-0000-4000-a000-0000000000e2")
TARGET_FLOW_ID = UUID("00000000-0000-4000-a000-0000000000e3")
PROJECT_ID = UUID("00000000-0000-4000-a000-0000000000e4")


def _definition(task_id: str = "task", *, schema_version: str = "1.0") -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "nodes": [
            {"id": "start", "type": "start", "config": {}},
            {"id": task_id, "type": "task", "config": {"team_id": "dept_system"}},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [
            {"id": "start-task", "source": "start", "target": task_id},
            {"id": "task-end", "source": task_id, "target": "end"},
        ],
    }


def _flow(flow_id: UUID, version: int, definition: dict[str, object]) -> dict[str, object]:
    return {"id": flow_id, "version": version, "definition_json": definition}


def _instance(*, status: str = "RUNNING", active_node_ids: list[str] | None = None) -> dict[str, object]:
    return {
        "id": INSTANCE_ID,
        "project_id": PROJECT_ID,
        "flow_id": SOURCE_FLOW_ID,
        "flow_version": 1,
        "status": status,
        "active_node_ids": active_node_ids or ["task"],
        "context_json": {"request": "keep"},
    }


def _storage(instance: dict[str, object], target_definition: dict[str, object]) -> MagicMock:
    storage = MagicMock()
    storage.get_flow_instance = AsyncMock(return_value=instance)
    storage.get_flow = AsyncMock(
        side_effect=[
            _flow(SOURCE_FLOW_ID, 1, _definition()),
            _flow(TARGET_FLOW_ID, 2, target_definition),
        ]
    )
    storage.migrate_flow_instance = AsyncMock(
        return_value={**instance, "flow_id": TARGET_FLOW_ID, "flow_version": 2}
    )
    storage.get_project = AsyncMock(return_value={"id": PROJECT_ID, "state": "IN_PROGRESS"})
    storage.transition_project = AsyncMock(return_value=None)
    return storage


@pytest.mark.anyio
async def test_compatible_migration_preserves_active_node_and_records_transition(client):
    storage = _storage(_instance(), _definition())
    from orchestrator_api.main import app

    app.state.storage = storage
    try:
        response = await client.post(
            f"/flows/instances/{INSTANCE_ID}/migrate",
            json={"flow_id": str(TARGET_FLOW_ID), "actor_id": "operator"},
        )
    finally:
        app.state.storage = None

    assert response.status_code == 200
    assert storage.migrate_flow_instance.await_args.kwargs["active_node_ids"] == ["task"]
    assert storage.migrate_flow_instance.await_args.kwargs["migration_record"]["actor_id"] == "operator"
    assert storage.transition_project.await_args.kwargs["event"] == "flow_migrated"


@pytest.mark.anyio
async def test_migration_rejects_missing_active_node_without_mutation(client):
    target = _definition()
    target["nodes"] = [node for node in target["nodes"] if node["id"] != "task"]
    target["edges"] = []
    storage = _storage(_instance(), target)
    from orchestrator_api.main import app

    app.state.storage = storage
    try:
        response = await client.post(
            f"/flows/instances/{INSTANCE_ID}/migrate",
            json={"flow_id": str(TARGET_FLOW_ID)},
        )
    finally:
        app.state.storage = None

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "FLOW_MIGRATION_INCOMPATIBLE"
    storage.migrate_flow_instance.assert_not_awaited()


@pytest.mark.anyio
async def test_graph_rewrite_requires_explicit_mapping_and_is_audited(client):
    target = _definition("review")
    storage = _storage(_instance(), target)
    from orchestrator_api.main import app

    app.state.storage = storage
    try:
        missing_mapping = await client.post(
            f"/flows/instances/{INSTANCE_ID}/migrate",
            json={"flow_id": str(TARGET_FLOW_ID), "allow_graph_rewrite": True},
        )
        storage.get_flow.reset_mock()
        storage.get_flow.side_effect = [
            _flow(SOURCE_FLOW_ID, 1, _definition()),
            _flow(TARGET_FLOW_ID, 2, target),
        ]
        mapped = await client.post(
            f"/flows/instances/{INSTANCE_ID}/migrate",
            json={
                "flow_id": str(TARGET_FLOW_ID),
                "allow_graph_rewrite": True,
                "active_node_mapping": {"task": "review"},
            },
        )
    finally:
        app.state.storage = None

    assert missing_mapping.status_code == 400
    assert mapped.status_code == 200
    kwargs = storage.migrate_flow_instance.await_args.kwargs
    assert kwargs["active_node_ids"] == ["review"]
    assert kwargs["migration_record"]["graph_rewrite"] is True


@pytest.mark.anyio
async def test_terminal_instance_cannot_be_migrated(client):
    storage = _storage(_instance(status="COMPLETED"), _definition())
    from orchestrator_api.main import app

    app.state.storage = storage
    try:
        response = await client.post(
            f"/flows/instances/{INSTANCE_ID}/migrate",
            json={"flow_id": str(TARGET_FLOW_ID)},
        )
    finally:
        app.state.storage = None

    assert response.status_code == 409
    storage.migrate_flow_instance.assert_not_awaited()
