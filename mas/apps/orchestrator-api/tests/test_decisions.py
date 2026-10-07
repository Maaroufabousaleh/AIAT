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
OPERATOR_HEADERS = {"X-API-Key": "test-operator-key"}


def _fake_gate() -> dict:
    return {
        "id": GATE_ID,
        "project_id": PROJECT_ID,
        "status": "PENDING",
        "created_at": NOW_ISO,
    }


def _make_storage(
    project=None,
    gate=None,
    pending_gates=None,
    decision_gates=None,
    decision_result=None,
):
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
        if decision_gates is not None:
            filters = {}
            for criterion in getattr(query, "_where_criteria", ()):
                column = getattr(getattr(criterion, "left", None), "name", None)
                value = getattr(getattr(criterion, "right", None), "value", None)
                if column in {"id", "project_id", "status"}:
                    filters[column] = value
            candidates = [dict(row) for row in decision_gates]
            if "project_id" in filters:
                candidates = [
                    row for row in candidates if row["project_id"] == filters["project_id"]
                ]
            if "id" in filters:
                candidates = [row for row in candidates if row["id"] == filters["id"]]
            if "status" in filters:
                candidates = [
                    row for row in candidates if row["status"] == filters["status"]
                ]
            decision_result_proxy = MagicMock()
            decision_result_proxy.mappings.return_value.first.return_value = (
                candidates[0] if candidates else None
            )
            return decision_result_proxy
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


@pytest.mark.anyio
async def test_project_decision_inbox_requires_operator_identity(client):
    """The global decision inbox is not available to the shared service key."""
    storage = MagicMock()
    storage.list_pending_project_decisions = AsyncMock(return_value=[])
    _patch_state(storage)

    response = await client.get("/approvals/project-decisions")

    assert response.status_code == 403
    storage.list_pending_project_decisions.assert_not_awaited()


@pytest.mark.anyio
async def test_project_decision_inbox_returns_bounded_context_and_cursor(client):
    from datetime import UTC, datetime

    created_at = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
    gate_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    rows = [
        {
            "id": gate_id,
            "project_id": PROJECT_ID,
            "project_name": "Alpha project",
            "project_state": "HUMAN_APPROVAL",
            "company_id": uuid.UUID("00000000-0000-4000-a000-000000000099"),
            "gate_type": "human_approval",
            "status": "PENDING",
            "created_at": created_at,
        }
        for gate_id in gate_ids
    ]
    storage = MagicMock()
    storage.list_pending_project_decisions = AsyncMock(return_value=rows)
    _patch_state(storage)

    response = await client.get(
        "/approvals/project-decisions?limit=2",
        headers={"X-API-Key": "test-operator-key"},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    assert body["items"][0] == {
        "id": str(gate_ids[0]),
        "project_id": str(PROJECT_ID),
        "project_name": "Alpha project",
        "project_state": "HUMAN_APPROVAL",
        "company_id": "00000000-0000-4000-a000-000000000099",
        "gate_type": "human_approval",
        "status": "PENDING",
        "created_at": created_at.isoformat().replace("+00:00", "Z"),
    }
    assert body["next_cursor"]
    storage.list_pending_project_decisions.assert_awaited_once_with(
        limit=3,
        cursor=None,
    )
    assert "human_input" not in body["items"][0]
    assert "justification" not in body["items"][0]

    storage.list_pending_project_decisions.reset_mock()
    storage.list_pending_project_decisions.return_value = [rows[2]]
    next_response = await client.get(
        f"/approvals/project-decisions?limit=2&cursor={body['next_cursor']}",
        headers={"X-API-Key": "test-operator-key"},
    )
    assert next_response.status_code == 200
    assert next_response.json()["next_cursor"] is None
    storage.list_pending_project_decisions.assert_awaited_once_with(
        limit=3,
        cursor=(created_at, gate_ids[1]),
    )


@pytest.mark.anyio
async def test_project_decision_inbox_validates_cursor_and_page_size(client):
    storage = MagicMock()
    storage.list_pending_project_decisions = AsyncMock(return_value=[])
    _patch_state(storage)
    headers = {"X-API-Key": "test-operator-key"}

    invalid_cursor = await client.get(
        "/approvals/project-decisions?cursor=not-a-cursor", headers=headers
    )
    oversized_page = await client.get(
        "/approvals/project-decisions?limit=101", headers=headers
    )

    assert invalid_cursor.status_code == 400
    assert oversized_page.status_code == 422
    storage.list_pending_project_decisions.assert_not_awaited()


# ── POST /projects/{id}/decisions — happy paths ───────────────────────────────


@pytest.mark.anyio
async def test_submit_decision_requires_operator_before_storage(client):
    """The shared service key cannot record human approval decisions."""
    storage = _make_storage(project=_fake_project("HUMAN_APPROVAL"), gate=_fake_gate())
    controller = _make_controller()
    _patch_state(storage, controller)

    response = await client.post(
        f"/projects/{PROJECT_ID}/decisions",
        json={"decision": "APPROVED", "decided_by": "operator"},
    )

    assert response.status_code == 403
    storage.get_project.assert_not_awaited()
    storage.decide_approval_gate.assert_not_awaited()
    controller.transition.assert_not_awaited()


@pytest.mark.anyio
async def test_submit_decision_rejects_unsupported_value_before_storage(client):
    """Unsupported decision values fail validation before durable mutation."""
    storage = _make_storage(project=_fake_project("HUMAN_APPROVAL"), gate=_fake_gate())
    controller = _make_controller()
    _patch_state(storage, controller)

    response = await client.post(
        f"/projects/{PROJECT_ID}/decisions",
        json={"decision": "ARCHIVE_PROJECT", "decided_by": "forged"},
        headers=OPERATOR_HEADERS,
    )

    assert response.status_code == 422
    storage.get_project.assert_not_awaited()
    storage.decide_approval_gate.assert_not_awaited()
    controller.transition.assert_not_awaited()


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
        json={"decision": " approved ", "comments": "Looks good", "decided_by": "alice"},
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "transitioned"
    assert "gate_id" in data
    assert "next_state" in data
    storage.decide_approval_gate.assert_awaited_once()
    assert storage.decide_approval_gate.await_args.kwargs["status"] == "APPROVED"
    assert storage.decide_approval_gate.await_args.kwargs["decided_by"] == "operator"
    assert ctrl.transition.await_args.kwargs["actor_id"] == "operator"


@pytest.mark.anyio
async def test_submit_decision_changes_only_the_requested_gate(client):
    """An explicit gate ID targets that gate even when a newer gate is pending."""
    project = _fake_project("HUMAN_APPROVAL")
    requested_gate = {
        "id": uuid.uuid4(),
        "project_id": PROJECT_ID,
        "status": "PENDING",
        "created_at": NOW_ISO,
    }
    newer_gate = {
        "id": uuid.uuid4(),
        "project_id": PROJECT_ID,
        "status": "PENDING",
        "created_at": "2026-01-02T00:00:00+00:00",
    }
    gates = [newer_gate, requested_gate]
    storage = _make_storage(project=project, decision_gates=gates)

    async def decide_gate(gate_id, *, status, **_kwargs):
        selected = next(row for row in gates if row["id"] == gate_id)
        if selected["status"] != "PENDING":
            return False
        selected["status"] = status
        return True

    storage.decide_approval_gate = AsyncMock(side_effect=decide_gate)
    controller = _make_controller()
    _patch_state(storage, controller)

    response = await client.post(
        f"/projects/{PROJECT_ID}/decisions",
        json={
            "decision": "APPROVED",
            "gate_id": str(requested_gate["id"]),
            "decided_by": "alice",
        },
        headers=OPERATOR_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["gate_id"] == str(requested_gate["id"])
    assert requested_gate["status"] == "APPROVED"
    assert newer_gate["status"] == "PENDING"
    storage.decide_approval_gate.assert_awaited_once()
    assert storage.decide_approval_gate.await_args.args[0] == requested_gate["id"]
    controller.transition.assert_awaited_once()


@pytest.mark.anyio
async def test_submit_decision_with_foreign_gate_id_fails_without_fallback(client):
    """A gate belonging to another project cannot select this project's gate."""
    project = _fake_project("HUMAN_APPROVAL")
    pending_gate = _fake_gate()
    foreign_gate = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "status": "PENDING",
        "created_at": NOW_ISO,
    }
    storage = _make_storage(
        project=project,
        decision_gates=[foreign_gate, pending_gate],
    )
    controller = _make_controller()
    _patch_state(storage, controller)

    response = await client.post(
        f"/projects/{PROJECT_ID}/decisions",
        json={
            "decision": "APPROVED",
            "gate_id": str(foreign_gate["id"]),
            "decided_by": "alice",
        },
        headers=OPERATOR_HEADERS,
    )

    assert response.status_code == 404
    assert "not found for this project" in response.json()["detail"]
    storage.decide_approval_gate.assert_not_awaited()
    controller.transition.assert_not_awaited()


@pytest.mark.anyio
async def test_submit_decision_with_stale_gate_id_fails_without_fallback(client):
    """A resolved requested gate returns conflict instead of deciding another gate."""
    project = _fake_project("HUMAN_APPROVAL")
    stale_gate = {
        "id": uuid.uuid4(),
        "project_id": PROJECT_ID,
        "status": "APPROVED",
        "created_at": NOW_ISO,
    }
    other_pending_gate = {
        "id": uuid.uuid4(),
        "project_id": PROJECT_ID,
        "status": "PENDING",
        "created_at": "2026-01-02T00:00:00+00:00",
    }
    storage = _make_storage(
        project=project,
        decision_gates=[other_pending_gate, stale_gate],
    )
    controller = _make_controller()
    _patch_state(storage, controller)

    response = await client.post(
        f"/projects/{PROJECT_ID}/decisions",
        json={
            "decision": "APPROVED",
            "gate_id": str(stale_gate["id"]),
            "decided_by": "alice",
        },
        headers=OPERATOR_HEADERS,
    )

    assert response.status_code == 409
    assert "no longer pending" in response.json()["detail"]
    storage.decide_approval_gate.assert_not_awaited()
    controller.transition.assert_not_awaited()


@pytest.mark.anyio
async def test_submit_decision_without_gate_id_keeps_newest_pending_compatibility(client):
    """Legacy callers without gate_id retain the prior newest-pending behavior."""
    project = _fake_project("HUMAN_APPROVAL")
    newest_gate = {
        "id": uuid.uuid4(),
        "project_id": PROJECT_ID,
        "status": "PENDING",
        "created_at": "2026-01-02T00:00:00+00:00",
    }
    older_gate = {
        "id": uuid.uuid4(),
        "project_id": PROJECT_ID,
        "status": "PENDING",
        "created_at": NOW_ISO,
    }
    storage = _make_storage(
        project=project,
        decision_gates=[newest_gate, older_gate],
    )
    _patch_state(storage, _make_controller())

    response = await client.post(
        f"/projects/{PROJECT_ID}/decisions",
        json={"decision": "APPROVED", "decided_by": "alice"},
        headers=OPERATOR_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["gate_id"] == str(newest_gate["id"])
    storage.decide_approval_gate.assert_awaited_once()
    assert storage.decide_approval_gate.await_args.args[0] == newest_gate["id"]


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
        headers=OPERATOR_HEADERS,
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
        headers=OPERATOR_HEADERS,
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
        headers=OPERATOR_HEADERS,
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
        headers=OPERATOR_HEADERS,
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
        headers=OPERATOR_HEADERS,
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
        headers=OPERATOR_HEADERS,
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
        headers=OPERATOR_HEADERS,
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
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 503
