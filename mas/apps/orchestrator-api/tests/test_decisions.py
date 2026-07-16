"""
Tests for human-in-the-loop decision endpoints:
  GET  /projects/{id}/pending-decisions
  POST /projects/{id}/decisions
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from conftest import NOW_ISO, PROJECT_ID, _fake_project

from mas_core.workflow import (
    InvalidTransitionError,
    WorkflowEvent,
    WorkflowTransitionResult,
)
from mas_core.workflow.states import ProjectState

# ── helpers ───────────────────────────────────────────────────────────────────

GATE_ID = uuid.uuid4()


def _fake_gate() -> dict:
    return {
        "id": GATE_ID,
        "project_id": PROJECT_ID,
        "status": "PENDING",
        "created_at": NOW_ISO,
    }


def _make_storage(project=None, gate=None, pending_gates=None, decision_result=None):
    """Return a mock storage with approval_gate query support."""
    storage = MagicMock()
    storage.get_project = AsyncMock(return_value=project)
    storage.decide_approval_gate = AsyncMock(return_value=decision_result)

    # Mock the engine.connect() async context manager for raw SQL queries
    mock_conn = MagicMock()

    # For pending-decisions (list of gates)
    gates_result = MagicMock()
    gates_result.mappings.return_value.all.return_value = (
        [dict(g) for g in pending_gates] if pending_gates else []
    )

    # For decisions (single gate)
    gate_result = MagicMock()
    gate_result.mappings.return_value.first.return_value = gate

    # Track call count to differentiate list vs single queries
    call_count = [0]

    async def mock_execute(query):
        call_count[0] += 1
        if pending_gates is not None:
            return gates_result
        return gate_result

    mock_conn.execute = mock_execute

    # Async context manager for engine.connect()
    mock_connect_cm = MagicMock()
    mock_connect_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_connect_cm.__aexit__ = AsyncMock(return_value=False)
    storage.engine = MagicMock()
    storage.engine.connect = MagicMock(return_value=mock_connect_cm)

    return storage


def _make_controller(result=None, invalid=False):
    ctrl = MagicMock()
    if invalid:
        ctrl.transition = AsyncMock(
            side_effect=InvalidTransitionError(
                ProjectState.HUMAN_APPROVAL, WorkflowEvent.HUMAN_APPROVED
            )
        )
    else:
        if result is None:
            result = WorkflowTransitionResult(
                project_id=str(PROJECT_ID),
                prior_state=ProjectState.HUMAN_APPROVAL,
                event=WorkflowEvent.HUMAN_APPROVED,
                next_state=ProjectState.RR_CREATION,
                actor_id="human",
                context={},
            )
        ctrl.transition = AsyncMock(return_value=result)
    return ctrl


def _patch_state(storage, controller=None):
    """Directly set app.state attributes."""
    from orchestrator_api.main import app

    app.state.storage = storage
    if controller is not None:
        app.state.controller = controller


# ── GET /projects/{id}/pending-decisions ─────────────────────────────────────


@pytest.mark.anyio
async def test_pending_decisions_project_not_found(client):
    """GET /projects/{id}/pending-decisions returns 404 when project missing."""
    _patch_state(_make_storage(project=None))
    resp = await client.get(f"/projects/{PROJECT_ID}/pending-decisions")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_pending_decisions_no_storage_returns_503(client):
    """GET /projects/{id}/pending-decisions returns 503 when storage unavailable."""
    _patch_state(None)
    resp = await client.get(f"/projects/{PROJECT_ID}/pending-decisions")
    assert resp.status_code == 503


@pytest.mark.anyio
async def test_pending_decisions_empty(client):
    """GET /projects/{id}/pending-decisions returns empty list when no pending gates."""
    project = _fake_project("HUMAN_APPROVAL")
    _patch_state(_make_storage(project=project, pending_gates=[]))
    resp = await client.get(f"/projects/{PROJECT_ID}/pending-decisions")
    assert resp.status_code == 200
    assert resp.json() == []


# ── POST /projects/{id}/decisions — happy paths ───────────────────────────────


@pytest.mark.anyio
async def test_submit_decision_approved(client):
    """POST /projects/{id}/decisions with APPROVED triggers state transition."""
    project = _fake_project("HUMAN_APPROVAL")
    gate = _fake_gate()
    storage = _make_storage(project=project, gate=gate)
    ctrl = _make_controller()

    _patch_state(storage, ctrl)

    resp = await client.post(
        f"/projects/{PROJECT_ID}/decisions",
        json={"decision": "APPROVED", "comments": "Looks good", "decided_by": "alice"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "transitioned"
    assert "gate_id" in data
    assert "next_state" in data


@pytest.mark.anyio
async def test_submit_decision_rejects_gate_cancelled_by_racing_transition(client):
    """A late human decision cannot act on a gate already cancelled by cleanup."""
    project = _fake_project("HUMAN_APPROVAL")
    gate = _fake_gate()
    storage = _make_storage(project=project, gate=gate, decision_result=False)
    ctrl = _make_controller()
    _patch_state(storage, ctrl)

    resp = await client.post(
        f"/projects/{PROJECT_ID}/decisions",
        json={"decision": "APPROVED", "decided_by": "alice"},
    )

    assert resp.status_code == 409
    assert "no longer pending" in resp.json()["detail"]
    ctrl.transition.assert_not_awaited()


@pytest.mark.anyio
async def test_submit_decision_rejected(client):
    """POST /projects/{id}/decisions with REJECTED records decision and transitions."""
    project = _fake_project("HUMAN_APPROVAL")
    gate = _fake_gate()
    storage = _make_storage(project=project, gate=gate)

    result = WorkflowTransitionResult(
        project_id=str(PROJECT_ID),
        prior_state=ProjectState.FEASIBILITY_REPORT,
        event=WorkflowEvent.HUMAN_REJECTED,
        next_state=ProjectState.ARCHIVED,
        actor_id="alice",
        context={},
    )
    ctrl = _make_controller(result=result)

    _patch_state(storage, ctrl)

    resp = await client.post(
        f"/projects/{PROJECT_ID}/decisions",
        json={"decision": "REJECTED", "comments": "Not viable", "decided_by": "alice"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("transitioned", "decision_recorded")


@pytest.mark.anyio
async def test_submit_decision_edits(client):
    """POST /projects/{id}/decisions with EDITS records the edits."""
    project = _fake_project("HUMAN_APPROVAL")
    gate = _fake_gate()
    storage = _make_storage(project=project, gate=gate)

    result = WorkflowTransitionResult(
        project_id=str(PROJECT_ID),
        prior_state=ProjectState.HUMAN_APPROVAL,
        event=WorkflowEvent.HUMAN_EDITS,
        next_state=ProjectState.CDR_CREATION,
        actor_id="alice",
        context={},
    )
    _patch_state(storage, _make_controller(result=result))

    resp = await client.post(
        f"/projects/{PROJECT_ID}/decisions",
        json={
            "decision": "EDITS",
            "edits": {"section": "budget", "value": "50k"},
            "decided_by": "alice",
        },
    )
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_submit_decision_cancelled(client):
    """POST /projects/{id}/decisions with CANCELLED records the cancellation."""
    project = _fake_project("HUMAN_APPROVAL")
    gate = _fake_gate()
    storage = _make_storage(project=project, gate=gate)

    result = WorkflowTransitionResult(
        project_id=str(PROJECT_ID),
        prior_state=ProjectState.HUMAN_APPROVAL,
        event=WorkflowEvent.HUMAN_CANCELLED,
        next_state=ProjectState.ARCHIVED,
        actor_id="alice",
        context={},
    )
    _patch_state(storage, _make_controller(result=result))

    resp = await client.post(
        f"/projects/{PROJECT_ID}/decisions",
        json={"decision": "CANCELLED", "decided_by": "alice"},
    )
    assert resp.status_code == 200


# ── POST /projects/{id}/decisions — 404 paths ────────────────────────────────


@pytest.mark.anyio
async def test_submit_decision_project_not_found(client):
    """POST /projects/{id}/decisions returns 404 when project does not exist."""
    _patch_state(_make_storage(project=None))
    resp = await client.post(
        f"/projects/{PROJECT_ID}/decisions",
        json={"decision": "APPROVED", "decided_by": "alice"},
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_submit_decision_no_pending_gate(client):
    """POST /projects/{id}/decisions returns 404 when no pending approval gate."""
    project = _fake_project("HUMAN_APPROVAL")
    _patch_state(_make_storage(project=project, gate=None), _make_controller())
    resp = await client.post(
        f"/projects/{PROJECT_ID}/decisions",
        json={"decision": "APPROVED", "decided_by": "alice"},
    )
    assert resp.status_code == 404


# ── POST /projects/{id}/decisions — transition failure ────────────────────────


@pytest.mark.anyio
async def test_submit_decision_invalid_transition_still_records(client):
    """POST /projects/{id}/decisions records decision even if transition is invalid."""
    project = _fake_project("FEASIBILITY_CHECK")
    gate = _fake_gate()
    storage = _make_storage(project=project, gate=gate)
    _patch_state(storage, _make_controller(invalid=True))

    resp = await client.post(
        f"/projects/{PROJECT_ID}/decisions",
        json={"decision": "APPROVED", "decided_by": "alice"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "decision_recorded"


# ── POST /projects/{id}/decisions — 422 ──────────────────────────────────────


@pytest.mark.anyio
async def test_submit_decision_missing_decision_field(client):
    """POST /projects/{id}/decisions returns 422 when 'decision' field is missing."""
    resp = await client.post(
        f"/projects/{PROJECT_ID}/decisions",
        json={"decided_by": "alice"},
    )
    assert resp.status_code == 422


# ── POST /projects/{id}/decisions — 503 ──────────────────────────────────────


@pytest.mark.anyio
async def test_submit_decision_no_storage_returns_503(client):
    """POST /projects/{id}/decisions returns 503 when storage is unavailable."""
    _patch_state(None)
    resp = await client.post(
        f"/projects/{PROJECT_ID}/decisions",
        json={"decision": "APPROVED", "decided_by": "alice"},
    )
    assert resp.status_code == 503
