"""
Tests for workflow transition endpoints:
  POST /projects/{id}/transition
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from conftest import PROJECT_ID, _fake_project

from mas_core.workflow import (
    InvalidTransitionError,
    WorkflowEvent,
    WorkflowTransitionResult,
)
from mas_core.workflow.states import ProjectState

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_storage(project=None):
    storage = MagicMock()
    storage.get_project = AsyncMock(return_value=project)
    return storage


def _make_controller_ok(prior: ProjectState, event: WorkflowEvent, next_s: ProjectState):
    result = WorkflowTransitionResult(
        project_id=str(PROJECT_ID),
        prior_state=prior,
        event=event,
        next_state=next_s,
        actor_id="test_actor",
        context={},
    )
    ctrl = MagicMock()
    ctrl.transition = AsyncMock(return_value=result)
    return ctrl


def _make_controller_invalid():
    ctrl = MagicMock()
    ctrl.transition = AsyncMock(
        side_effect=InvalidTransitionError(ProjectState.INIT, WorkflowEvent.CDR_SUBMITTED)
    )
    return ctrl


def _make_controller_stale():
    ctrl = MagicMock()
    ctrl.transition = AsyncMock(side_effect=ValueError("State changed concurrently — retry"))
    return ctrl


def _patch_state(storage, controller=None):
    """Directly set app.state attributes (bypasses monkeypatch limitation)."""
    from orchestrator_api.main import app

    app.state.storage = storage
    if controller is not None:
        app.state.controller = controller


# ── POST /projects/{id}/transition — happy path ───────────────────────────────


@pytest.mark.anyio
async def test_transition_valid_event(client):
    """POST /projects/{id}/transition with valid event returns transition result."""
    project = _fake_project("INIT")
    storage = _make_storage(project=project)
    ctrl = _make_controller_ok(
        ProjectState.INIT, WorkflowEvent.PROJECT_CREATED, ProjectState.FEASIBILITY_CHECK
    )
    _patch_state(storage, ctrl)

    resp = await client.post(
        f"/projects/{PROJECT_ID}/transition",
        json={"event": "project_created", "actor_id": "test_actor", "context": {}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["prior_state"] == "INIT"
    assert data["event"] == "project_created"
    assert data["next_state"] == "FEASIBILITY_CHECK"
    assert data["actor_id"] == "test_actor"
    assert data["project_id"] == str(PROJECT_ID)


@pytest.mark.anyio
async def test_transition_with_context(client):
    """POST /projects/{id}/transition passes context through."""
    project = _fake_project("PDR_CREATION")
    storage = _make_storage(project=project)
    ctrl = _make_controller_ok(
        ProjectState.PDR_CREATION, WorkflowEvent.PDR_SUBMITTED, ProjectState.PDR_REVIEW
    )
    _patch_state(storage, ctrl)

    resp = await client.post(
        f"/projects/{PROJECT_ID}/transition",
        json={
            "event": "pdr_submitted",
            "actor_id": "cto",
            "context": {"doc_id": "abc-123"},
        },
    )
    assert resp.status_code == 200


# ── POST /projects/{id}/transition — 404 ─────────────────────────────────────


@pytest.mark.anyio
async def test_transition_project_not_found(client):
    """POST /projects/{id}/transition returns 404 when project does not exist."""
    storage = _make_storage(project=None)
    _patch_state(storage, MagicMock())

    resp = await client.post(
        f"/projects/{PROJECT_ID}/transition",
        json={"event": "project_created", "actor_id": "human"},
    )
    assert resp.status_code == 404


# ── POST /projects/{id}/transition — 400 unknown event ───────────────────────


@pytest.mark.anyio
async def test_transition_unknown_event(client):
    """POST /projects/{id}/transition returns 400 for an unknown workflow event."""
    project = _fake_project("INIT")
    storage = _make_storage(project=project)
    _patch_state(storage, MagicMock())

    resp = await client.post(
        f"/projects/{PROJECT_ID}/transition",
        json={"event": "not_a_real_event", "actor_id": "actor"},
    )
    assert resp.status_code == 400
    assert "Unknown workflow event" in resp.json()["detail"]


# ── POST /projects/{id}/transition — 409 invalid transition ──────────────────


@pytest.mark.anyio
async def test_transition_invalid_transition_returns_409(client):
    """POST /projects/{id}/transition returns 409 for invalid state+event combo."""
    project = _fake_project("INIT")
    storage = _make_storage(project=project)
    ctrl = _make_controller_invalid()
    _patch_state(storage, ctrl)

    resp = await client.post(
        f"/projects/{PROJECT_ID}/transition",
        json={"event": "cdr_submitted", "actor_id": "actor"},
    )
    assert resp.status_code == 409
    assert "Invalid transition" in resp.json()["detail"]


@pytest.mark.anyio
async def test_transition_stale_state_returns_409(client):
    """POST /projects/{id}/transition returns 409 on CAS guard failure."""
    project = _fake_project("INIT")
    storage = _make_storage(project=project)
    ctrl = _make_controller_stale()
    _patch_state(storage, ctrl)

    resp = await client.post(
        f"/projects/{PROJECT_ID}/transition",
        json={"event": "project_created", "actor_id": "actor"},
    )
    assert resp.status_code == 409
    assert "Stale state conflict" in resp.json()["detail"]


# ── POST /projects/{id}/transition — 422 validation ──────────────────────────


@pytest.mark.anyio
async def test_transition_missing_event_field(client):
    """POST /projects/{id}/transition returns 422 when 'event' field is missing."""
    resp = await client.post(
        f"/projects/{PROJECT_ID}/transition",
        json={"actor_id": "human"},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_transition_missing_actor_id(client):
    """POST /projects/{id}/transition returns 422 when 'actor_id' field is missing."""
    resp = await client.post(
        f"/projects/{PROJECT_ID}/transition",
        json={"event": "project_created"},
    )
    assert resp.status_code == 422


# ── POST /projects/{id}/transition — 503 ─────────────────────────────────────


@pytest.mark.anyio
async def test_transition_no_storage_returns_503(client):
    """POST /projects/{id}/transition returns 503 when storage is unavailable."""
    _patch_state(None)
    resp = await client.post(
        f"/projects/{PROJECT_ID}/transition",
        json={"event": "project_created", "actor_id": "human"},
    )
    assert resp.status_code == 503


# ── Invalid project_id format ─────────────────────────────────────────────────


@pytest.mark.anyio
async def test_transition_invalid_uuid_returns_422(client):
    """POST /projects/{id}/transition returns 422 for non-UUID path param."""
    resp = await client.post(
        "/projects/not-a-uuid/transition",
        json={"event": "project_created", "actor_id": "human"},
    )
    assert resp.status_code == 422
