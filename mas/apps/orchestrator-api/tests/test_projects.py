"""
Tests for Projects CRUD endpoints:
  POST /projects
  GET  /projects
  GET  /projects/{id}
  DELETE /projects/{id}
  POST /projects/{id}/retry
  POST /projects/{id}/archive
  GET  /projects/{id}/allowed-transitions
  GET  /projects/{id}/state-history
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from conftest import PROJECT_ID, _fake_project

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_storage(project=None, projects=None):
    """Build a mock storage that looks real enough for the endpoints."""
    storage = MagicMock()
    storage.create_project = AsyncMock(return_value=_fake_project("INIT"))
    storage.get_project = AsyncMock(return_value=project)
    storage.delete_project = AsyncMock(return_value=project is not None)
    storage.list_projects = AsyncMock(return_value=projects if projects is not None else [])
    storage.get_project_history = AsyncMock(return_value=[])
    return storage


def _make_controller(result=None):
    """Build a mock WorkflowController."""
    from mas_core.workflow import WorkflowEvent, WorkflowTransitionResult
    from mas_core.workflow.states import ProjectState

    if result is None:
        result = WorkflowTransitionResult(
            project_id=str(PROJECT_ID),
            prior_state=ProjectState.INIT,
            event=WorkflowEvent.PROJECT_CREATED,
            next_state=ProjectState.FEASIBILITY_CHECK,
            actor_id="human",
            context={},
        )
    ctrl = MagicMock()
    ctrl.transition = AsyncMock(return_value=result)
    return ctrl


def _patch_state(storage, controller=None):
    """Directly set app.state attributes (bypasses monkeypatch limitation)."""
    from orchestrator_api.main import app

    app.state.storage = storage
    if controller is not None:
        app.state.controller = controller


# ── POST /projects ─────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_create_project_happy_path(client):
    """POST /projects creates a project and returns 201."""
    storage = _make_storage()
    ctrl = _make_controller()
    _patch_state(storage, ctrl)

    with patch("httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(post=AsyncMock(return_value=MagicMock(status_code=201)))
        )
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = await client.post(
            "/projects",
            json={"name": "My Project", "description": "test", "human_requester": "alice"},
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Test Project"
    assert data["state"] == "INIT"
    storage.create_project.assert_awaited_once()


@pytest.mark.anyio
async def test_create_project_without_storage(client):
    """POST /projects returns 503 when storage is unavailable."""
    _patch_state(None)
    resp = await client.post("/projects", json={"name": "X"})
    assert resp.status_code == 503


@pytest.mark.anyio
async def test_create_project_validation_error(client):
    """POST /projects returns 422 when name is missing."""
    resp = await client.post("/projects", json={})
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_create_project_minimal_payload(client):
    """POST /projects succeeds with only name (description/requester optional)."""
    storage = _make_storage()
    ctrl = _make_controller()
    _patch_state(storage, ctrl)

    with patch("httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(post=AsyncMock(return_value=MagicMock(status_code=201)))
        )
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = await client.post("/projects", json={"name": "Minimal"})

    assert resp.status_code == 201


# ── GET /projects ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_list_projects_empty(client):
    """GET /projects returns empty list when no projects exist."""
    _patch_state(_make_storage(projects=[]))
    resp = await client.get("/projects")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_list_projects_returns_all(client):
    """GET /projects returns all stored projects."""
    projects = [_fake_project("INIT"), _fake_project("FEASIBILITY_CHECK")]
    _patch_state(_make_storage(projects=projects))
    resp = await client.get("/projects")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.anyio
async def test_list_projects_with_state_filter(client):
    """GET /projects?state=INIT forwards state filter to storage."""
    storage = _make_storage(projects=[_fake_project("INIT")])
    _patch_state(storage)
    resp = await client.get("/projects?state=INIT")
    assert resp.status_code == 200
    storage.list_projects.assert_awaited_once_with(state="INIT", limit=100, offset=0)


@pytest.mark.anyio
async def test_list_projects_pagination(client):
    """GET /projects?limit=10&offset=5 passes pagination params."""
    storage = _make_storage(projects=[])
    _patch_state(storage)
    resp = await client.get("/projects?limit=10&offset=5")
    assert resp.status_code == 200
    storage.list_projects.assert_awaited_once_with(state=None, limit=10, offset=5)


@pytest.mark.anyio
async def test_list_projects_invalid_limit(client):
    """GET /projects returns 422 for limit < 1."""
    resp = await client.get("/projects?limit=0")
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_list_projects_503_no_storage(client):
    """GET /projects returns 503 when storage is unavailable."""
    _patch_state(None)
    resp = await client.get("/projects")
    assert resp.status_code == 503


# ── GET /projects/{id} ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_project_found(client):
    """GET /projects/{id} returns 200 with project data."""
    project = _fake_project("IN_PROGRESS")
    _patch_state(_make_storage(project=project))
    resp = await client.get(f"/projects/{PROJECT_ID}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "IN_PROGRESS"


@pytest.mark.anyio
async def test_get_project_not_found(client):
    """GET /projects/{id} returns 404 when project does not exist."""
    _patch_state(_make_storage(project=None))
    resp = await client.get(f"/projects/{PROJECT_ID}")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_get_project_invalid_uuid(client):
    """GET /projects/{id} returns 422 for non-UUID path param."""
    resp = await client.get("/projects/not-a-uuid")
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_get_project_503_no_storage(client):
    """GET /projects/{id} returns 503 when storage is unavailable."""
    _patch_state(None)
    resp = await client.get(f"/projects/{PROJECT_ID}")
    assert resp.status_code == 503


@pytest.mark.anyio
async def test_list_project_issues_reads_persisted_records(client):
    sprint_id = "00000000-0000-4000-a000-000000000010"
    storage = _make_storage(project=_fake_project("IN_PROGRESS"))
    storage.list_issues = AsyncMock(
        return_value=[
            {
                "id": "00000000-0000-4000-a000-000000000099",
                "project_id": PROJECT_ID,
                "sprint_id": sprint_id,
                "status": "backlog",
                "title": "Fix boundary",
                "assigned_team": "dept_qa",
            }
        ]
    )
    _patch_state(storage)

    resp = await client.get(
        f"/projects/{PROJECT_ID}/issues?sprint_id={sprint_id}"
        "&status=backlog&assigned_team=dept_qa"
    )

    assert resp.status_code == 200
    assert [issue["title"] for issue in resp.json()] == ["Fix boundary"]
    storage.list_issues.assert_awaited_once_with(
        project_id=PROJECT_ID,
        sprint_id=UUID(sprint_id),
        status="backlog",
        assigned_team="dept_qa",
    )


# ── GET /projects/{id}/allowed-transitions ────────────────────────────────────


@pytest.mark.anyio
async def test_allowed_transitions_known_state(client):
    """GET /projects/{id}/allowed-transitions returns valid events."""
    project = _fake_project("INIT")
    _patch_state(_make_storage(project=project))
    resp = await client.get(f"/projects/{PROJECT_ID}/allowed-transitions")
    assert resp.status_code == 200
    data = resp.json()
    assert "state" in data
    assert "allowed_events" in data
    # INIT only allows project_created
    assert "project_created" in data["allowed_events"]


@pytest.mark.anyio
async def test_allowed_transitions_not_found(client):
    """GET /projects/{id}/allowed-transitions returns 404 when project missing."""
    _patch_state(_make_storage(project=None))
    resp = await client.get(f"/projects/{PROJECT_ID}/allowed-transitions")
    assert resp.status_code == 404


# ── GET /projects/{id}/state-history ─────────────────────────────────────────


@pytest.mark.anyio
async def test_state_history_empty(client):
    """GET /projects/{id}/state-history returns empty list when no history."""
    storage = _make_storage()
    storage.get_project_history = AsyncMock(return_value=[])
    _patch_state(storage)
    resp = await client.get(f"/projects/{PROJECT_ID}/state-history")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_state_history_503_no_storage(client):
    """GET /projects/{id}/state-history returns 503 when storage is unavailable."""
    _patch_state(None)
    resp = await client.get(f"/projects/{PROJECT_ID}/state-history")
    assert resp.status_code == 503


# ── POST /projects/{id}/retry ─────────────────────────────────────────────────


@pytest.mark.anyio
async def test_retry_failed_project(client):
    """POST /projects/{id}/retry succeeds for a FAILED project."""
    from mas_core.workflow import WorkflowEvent, WorkflowTransitionResult
    from mas_core.workflow.states import ProjectState

    project = _fake_project("FAILED")
    project["failed_from_state"] = "IN_PROGRESS"
    storage = _make_storage(project=project)

    result = WorkflowTransitionResult(
        project_id=str(PROJECT_ID),
        prior_state=ProjectState.FAILED,
        event=WorkflowEvent.RETRY,
        next_state=ProjectState.IN_PROGRESS,
        actor_id="human",
        context={"last_safe_state": "IN_PROGRESS"},
    )
    ctrl = _make_controller(result=result)
    _patch_state(storage, ctrl)

    resp = await client.post(f"/projects/{PROJECT_ID}/retry")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "retried"


@pytest.mark.anyio
async def test_retry_failed_project_recreates_human_checkpoint_gate(client):
    """Retrying into a human checkpoint restores its pending approval gate."""
    from mas_core.workflow import WorkflowEvent, WorkflowTransitionResult
    from mas_core.workflow.states import ProjectState

    project = _fake_project("FAILED")
    project["failed_from_state"] = "FEASIBILITY_REPORT"
    storage = _make_storage(project=project)
    storage.list_approval_gates = AsyncMock(return_value=[])
    storage.create_approval_gate = AsyncMock()

    result = WorkflowTransitionResult(
        project_id=str(PROJECT_ID),
        prior_state=ProjectState.FAILED,
        event=WorkflowEvent.RETRY,
        next_state=ProjectState.FEASIBILITY_REPORT,
        actor_id="human",
        context={"last_safe_state": "FEASIBILITY_REPORT"},
    )
    _patch_state(storage, _make_controller(result=result))

    resp = await client.post(f"/projects/{PROJECT_ID}/retry")

    assert resp.status_code == 200
    storage.list_approval_gates.assert_awaited_once_with(
        project_id=PROJECT_ID,
        status="PENDING",
        limit=100,
    )
    storage.create_approval_gate.assert_awaited_once_with(
        project_id=PROJECT_ID,
        gate_type="feasibility",
    )


@pytest.mark.anyio
async def test_retry_non_failed_project_returns_409(client):
    """POST /projects/{id}/retry returns 409 if project is not in FAILED state."""
    project = _fake_project("IN_PROGRESS")
    _patch_state(_make_storage(project=project), _make_controller())
    resp = await client.post(f"/projects/{PROJECT_ID}/retry")
    assert resp.status_code == 409


@pytest.mark.anyio
async def test_retry_not_found_returns_404(client):
    """POST /projects/{id}/retry returns 404 when project does not exist."""
    _patch_state(_make_storage(project=None))
    resp = await client.post(f"/projects/{PROJECT_ID}/retry")
    assert resp.status_code == 404


# ── POST /projects/{id}/archive ───────────────────────────────────────────────


@pytest.mark.anyio
async def test_archive_completed_project(client):
    """POST /projects/{id}/archive succeeds for a COMPLETED project."""
    from mas_core.workflow import WorkflowEvent, WorkflowTransitionResult
    from mas_core.workflow.states import ProjectState

    project = _fake_project("COMPLETED")
    storage = _make_storage(project=project)

    result = WorkflowTransitionResult(
        project_id=str(PROJECT_ID),
        prior_state=ProjectState.COMPLETED,
        event=WorkflowEvent.ARCHIVE_REQUESTED,
        next_state=ProjectState.ARCHIVED,
        actor_id="human",
        context={},
    )
    ctrl = _make_controller(result=result)
    _patch_state(storage, ctrl)

    with patch("httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(post=AsyncMock(return_value=MagicMock(status_code=200)))
        )
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = await client.post(f"/projects/{PROJECT_ID}/archive")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "archived"


@pytest.mark.anyio
async def test_archive_not_found_returns_404(client):
    """POST /projects/{id}/archive returns 404 when project does not exist."""
    _patch_state(_make_storage(project=None))
    resp = await client.post(f"/projects/{PROJECT_ID}/archive")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_archive_archived_project_is_idempotent(client):
    """POST /projects/{id}/archive succeeds when project is already archived."""
    storage = _make_storage(project=_fake_project("ARCHIVED"))
    ctrl = _make_controller()
    _patch_state(storage, ctrl)

    resp = await client.post(f"/projects/{PROJECT_ID}/archive")

    assert resp.status_code == 200
    assert resp.json() == {"status": "archived", "next_state": "ARCHIVED"}
    ctrl.transition.assert_not_awaited()


@pytest.mark.anyio
async def test_delete_project_happy_path(client):
    """DELETE /projects/{id} permanently deletes a project."""
    storage = _make_storage(project=_fake_project("ARCHIVED"))
    _patch_state(storage)

    resp = await client.delete(f"/projects/{PROJECT_ID}")

    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"
    storage.delete_project.assert_awaited_once_with(PROJECT_ID)


@pytest.mark.anyio
async def test_delete_project_not_found_returns_404(client):
    """DELETE /projects/{id} returns 404 when project does not exist."""
    storage = _make_storage(project=None)
    storage.delete_project = AsyncMock(return_value=False)
    _patch_state(storage)

    resp = await client.delete(f"/projects/{PROJECT_ID}")

    assert resp.status_code == 404
