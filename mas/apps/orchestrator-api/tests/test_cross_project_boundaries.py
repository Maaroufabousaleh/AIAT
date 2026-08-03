"""Regression tests for project ownership at persistence boundaries."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from conftest import PROJECT_ID


def _patch(storage) -> None:
    from orchestrator_api.main import app

    app.state.storage = storage


def _project_storage() -> MagicMock:
    storage = MagicMock()
    storage.get_project = AsyncMock(return_value={"id": PROJECT_ID})
    return storage


@pytest.mark.anyio
async def test_artifact_metadata_cannot_override_route_project(client):
    storage = _project_storage()
    storage.create_artifact = AsyncMock(
        return_value={"id": 7, "metadata": {"project_id": str(PROJECT_ID)}}
    )
    _patch(storage)

    other_project = uuid4()
    response = await client.post(
        f"/projects/{PROJECT_ID}/artifacts",
        json={
            "path": "documents/report.json",
            "metadata": {"project_id": str(other_project), "kind": "report"},
        },
    )

    assert response.status_code == 201
    metadata = storage.create_artifact.await_args.kwargs["metadata"]
    assert metadata["project_id"] == str(PROJECT_ID)
    assert metadata["kind"] == "report"


@pytest.mark.anyio
async def test_kpi_snapshot_rejects_foreign_sprint(client):
    storage = _project_storage()
    foreign_sprint = {"id": uuid4(), "project_id": uuid4()}
    storage.get_sprint = AsyncMock(return_value=foreign_sprint)
    storage.save_kpi_snapshot = AsyncMock()
    _patch(storage)

    response = await client.post(
        f"/projects/{PROJECT_ID}/kpi",
        json={"scope": "project", "sprint_id": str(foreign_sprint["id"])},
    )

    assert response.status_code == 404
    storage.save_kpi_snapshot.assert_not_awaited()


@pytest.mark.anyio
async def test_issue_creation_rejects_foreign_sprint(client):
    storage = _project_storage()
    foreign_sprint = {"id": uuid4(), "project_id": uuid4()}
    storage.get_sprint = AsyncMock(return_value=foreign_sprint)
    storage.create_issue = AsyncMock()
    _patch(storage)

    response = await client.post(
        "/tasks",
        json={
            "project_id": str(PROJECT_ID),
            "payload": {
                "action": "CREATE_ISSUE",
                "sprint_id": str(foreign_sprint["id"]),
                "title": "must stay in project",
            },
        },
        headers={"X-API-Key": "test-operator-key"},
    )

    assert response.status_code == 404
    storage.create_issue.assert_not_awaited()
