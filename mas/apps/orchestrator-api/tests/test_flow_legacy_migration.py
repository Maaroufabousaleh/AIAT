from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

FLOW_ID = UUID("00000000-0000-4000-a000-0000000000f1")
WORKER_ID = UUID("00000000-0000-4000-a000-000000000701")


def _definition() -> dict[str, object]:
    return {
        "metadata": {"owner": "operator"},
        "nodes": [
            {"id": "start", "type": "start", "label": "Start", "config": {}},
            {
                "id": "legacy-task",
                "type": "task",
                "label": "Legacy task",
                "config": {"team_id": "dept_qa", "action": "test.run"},
            },
            {"id": "end", "type": "end", "label": "End", "config": {}},
        ],
        "edges": [
            {"id": "start-task", "source": "start", "target": "legacy-task"},
            {"id": "task-end", "source": "legacy-task", "target": "end"},
        ],
    }


def _flow(*, flow_id: UUID = FLOW_ID, version: int = 1) -> dict[str, object]:
    return {
        "id": flow_id,
        "name": "Legacy flow",
        "description": "legacy",
        "definition_json": _definition(),
        "version": version,
        "created_by": "human",
        "is_active": False,
    }


def _patch_storage(storage) -> None:
    from orchestrator_api.main import app

    app.state.storage = storage


@pytest.mark.anyio
async def test_legacy_task_migration_dry_run_never_creates_a_version(client) -> None:
    storage = MagicMock()
    storage.get_flow = AsyncMock(return_value=_flow())
    storage.create_flow = AsyncMock()
    _patch_storage(storage)

    response = await client.post(
        f"/flows/{FLOW_ID}/migrate-legacy-tasks",
        json={"dry_run": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "dry_run"
    assert body["valid"] is False
    assert body["missing_worker_bindings"] == ["legacy-task"]
    assert body["definition_json"] == _definition()
    storage.create_flow.assert_not_called()


@pytest.mark.anyio
async def test_legacy_task_migration_creates_immutable_worker_bound_version(client) -> None:
    source = _flow()
    created = _flow(flow_id=uuid4(), version=2)
    storage = MagicMock()
    storage.get_flow = AsyncMock(return_value=source)
    storage.create_flow = AsyncMock(return_value=created)
    _patch_storage(storage)

    response = await client.post(
        f"/flows/{FLOW_ID}/migrate-legacy-tasks",
        json={
            "actor_id": "operator",
            "worker_bindings": {"legacy-task": str(WORKER_ID)},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "migrated"
    assert body["source_flow_id"] == str(FLOW_ID)
    assert body["migration"]["schema"] == "aiat.flow-legacy-task-migration.v1"
    assert body["migration"]["migrated_node_ids"] == ["legacy-task"]

    kwargs = storage.create_flow.await_args.kwargs
    migrated_config = kwargs["definition_json"]["nodes"][1]["config"]
    assert migrated_config == {
        "worker_id": str(WORKER_ID),
        "task_type": "test.run",
        "model_mode": "none",
    }
    assert kwargs["version"] == 2
    assert kwargs["is_active"] is False
    assert kwargs["definition_json"]["metadata"]["legacy_task_migration"]["actor_id"] == "operator"
    assert source["definition_json"] == _definition()
