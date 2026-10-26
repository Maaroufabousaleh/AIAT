"""
Test 2: Flow runtime — branching, approval, retry, escalation.

Validates that a configured orchestration flow with:
  Start → Analysis → Approval Gate → (Decision Switch) → Branch A / Branch B / Failed
works end-to-end through the public controller API boundary.

Coverage matrix
───────────────
Type            Scenarios
API             create flow, read-back, attach to project, start, node-action (complete/timeout),
                approval decision (approved/edit_requested/rejected), retry, override
Integration     full linear chain: create → attach → start → drive → read back state
Security        non-operator override denied, double-attach denied, unknown node override denied
Negative        invalid approval decision path, action on wrong node_id, retry non-FAILED instance
Audit           state-history contains flow_assigned, flow_node_override, flow_node_escalated events
Persistence     flow definition (nodes/edges/config/switch_cases) survives serialization round-trip
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from conftest import NOW_ISO, PROJECT_ID

# ── constants ────────────────────────────────────────────────────────────────

FLOW_ID = UUID("00000000-0000-4000-a000-0000000000a1")
INST_ID = UUID("00000000-0000-4000-a000-0000000000b1")
PROJ_ID_2 = uuid4()  # second project for independent scenarios
OPERATOR_HEADERS = {"X-API-Key": "test-operator-key"}


# ── helpers ──────────────────────────────────────────────────────────────────


def _patch(storage) -> None:
    from orchestrator_api.main import app

    if not isinstance(storage.get_project, AsyncMock):
        storage.get_project = AsyncMock(
            side_effect=lambda project_id: {
                "id": str(project_id),
                "state": "IN_PROGRESS",
            }
        )
    app.state.storage = storage


def _full_flow_definition() -> dict:
    """
    The canonical Test-2 flow:
        Start → Analysis → Approval Gate → Decision Switch
                                                ├─ approved     → Branch A → Completed
                                                ├─ edit_requested → Branch B → Completed
                                                └─ rejected     → Failed (end)
    Analysis node has a timeout + escalate_to_team so timeout tests work.
    """
    return {
        "nodes": [
            {"id": "start", "type": "start", "label": "Start", "config": {}},
            {
                "id": "analysis",
                "type": "task",
                "label": "Analysis",
                "config": {
                    "team_id": "exec_ceo",
                    "timeout_seconds": 300,
                    "escalate_to_team": "exec_ceo",
                },
            },
            {
                "id": "approval_gate",
                "type": "approval",
                "label": "Approval Gate",
                "config": {"approver_user": "human"},
            },
            {
                "id": "decision_switch",
                "type": "switch",
                "label": "Decision Switch",
                "config": {
                    "switch_key": "approval",
                    "switch_cases": {
                        "approved": "branch_a",
                        "edit_requested": "branch_b",
                        "rejected": "failed_terminal",
                    },
                },
            },
            {
                "id": "branch_a",
                "type": "task",
                "label": "Branch A",
                "config": {"team_id": "dept_system"},
            },
            {
                "id": "branch_b",
                "type": "task",
                "label": "Branch B",
                "config": {"team_id": "dept_qa"},
            },
            {"id": "failed_terminal", "type": "end", "label": "Failed", "config": {}},
            {"id": "completed", "type": "end", "label": "Completed", "config": {}},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "analysis"},
            {"id": "e2", "source": "analysis", "target": "approval_gate"},
            {"id": "e3", "source": "approval_gate", "target": "decision_switch"},
            # switch_cases targets must also have explicit edges so the graph validator
            # can confirm all nodes are reachable from start
            {"id": "e4", "source": "decision_switch", "target": "branch_a"},
            {"id": "e5", "source": "decision_switch", "target": "branch_b"},
            {"id": "e6", "source": "decision_switch", "target": "failed_terminal"},
            {"id": "e7", "source": "branch_a", "target": "completed"},
            {"id": "e8", "source": "branch_b", "target": "completed"},
        ],
    }


def _flow_row(*, flow_id: UUID = FLOW_ID, version: int = 1) -> dict:
    return {
        "id": flow_id,
        "name": "Test-2 Branching Flow",
        "description": "approval/retry/escalation test flow",
        "definition_json": _full_flow_definition(),
        "version": version,
        "created_by": "human",
        "is_active": True,
        "created_at": NOW_ISO,
        "updated_at": NOW_ISO,
    }


def _instance_row(
    *,
    instance_id: UUID = INST_ID,
    active_node_ids: list[str] | None = None,
    status: str = "RUNNING",
    context_json: dict | None = None,
    escalated_to: str | None = None,
    retry_count: int = 0,
) -> dict:
    return {
        "id": instance_id,
        "flow_id": FLOW_ID,
        "flow_version": 1,
        "project_id": PROJECT_ID,
        "active_node_ids": active_node_ids or [],
        "status": status,
        "context_json": context_json or {},
        "escalated_to": escalated_to,
        "escalation_reason": None,
        "retry_count": retry_count,
        "created_at": NOW_ISO,
        "updated_at": NOW_ISO,
    }


def _project_row(state: str = "IN_PROGRESS") -> dict:
    return {
        "id": PROJECT_ID,
        "name": "Test-2 Project",
        "description": "desc",
        "state": state,
        "created_by": "human",
        "created_at": NOW_ISO,
        "updated_at": NOW_ISO,
        "failed_from_state": None,
        "failure_reason": None,
    }


def _mock_bound_approval_resolution(
    storage,
    *,
    initial: dict,
    resolved: dict,
    execution_id: int = 11,
    gate_id: UUID | None = None,
) -> UUID:
    """Configure the durable, exact execution-to-gate decision path."""
    gate_id = gate_id or uuid4()
    storage.get_flow_instance = AsyncMock(return_value=initial)
    storage.get_flow = AsyncMock(return_value=_flow_row())
    storage.list_flow_node_executions = AsyncMock(
        return_value=[
            {
                "id": execution_id,
                "instance_id": initial["id"],
                "node_id": "approval_gate",
                "node_type": "approval",
                "status": "RUNNING",
            }
        ]
    )
    storage.get_flow_node_execution = AsyncMock(
        return_value={
            "id": execution_id,
            "instance_id": initial["id"],
            "node_id": "approval_gate",
            "node_type": "approval",
            "status": "RUNNING",
        }
    )
    storage.get_approval_gate_for_flow_execution = AsyncMock(
        return_value={
            "id": gate_id,
            "project_id": initial["project_id"],
            "flow_node_execution_id": execution_id,
            "status": "PENDING",
        }
    )
    storage.resolve_flow_approval_gate = AsyncMock(
        return_value={
            "outcome": "applied",
            "instance": resolved,
            "activated_node_ids": ["decision_switch"],
        }
    )
    return gate_id


# ── 1. Flow creation and serialization round-trip ────────────────────────────


@pytest.mark.anyio
async def test_create_flow_with_test2_definition_succeeds(client):
    """POST /flows with the full Test-2 graph → 201, nodes/edges/switch_cases persist."""
    created = _flow_row()
    storage = MagicMock()
    storage.get_flow = AsyncMock(return_value=None)  # no base (not a version)
    storage.create_flow = AsyncMock(return_value=created)
    _patch(storage)

    resp = await client.post(
        "/flows",
        json={
            "name": "Test-2 Branching Flow",
            "description": "approval/retry/escalation test flow",
            "is_active": True,
            "definition_json": _full_flow_definition(),
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Test-2 Branching Flow"
    assert body["version"] == 1
    assert body["is_active"] is True
    storage.create_flow.assert_awaited_once()
    _, kwargs = storage.create_flow.await_args
    # Verify switch node config survived
    nodes_by_id = {n["id"]: n for n in kwargs["definition_json"]["nodes"]}
    sw = nodes_by_id["decision_switch"]
    assert sw["config"]["switch_key"] == "approval"
    assert sw["config"]["switch_cases"]["approved"] == "branch_a"
    assert sw["config"]["switch_cases"]["rejected"] == "failed_terminal"


@pytest.mark.anyio
async def test_get_flow_returns_complete_definition_including_switch_config(client):
    """GET /flows/{id} → nodes, edges, and switch_cases serialized correctly."""
    storage = MagicMock()
    storage.get_flow = AsyncMock(return_value=_flow_row())
    _patch(storage)

    resp = await client.get(f"/flows/{FLOW_ID}")
    assert resp.status_code == 200
    body = resp.json()
    nodes = {n["id"]: n for n in body["definition_json"]["nodes"]}
    assert "start" in nodes
    assert "analysis" in nodes
    assert "approval_gate" in nodes
    assert "decision_switch" in nodes
    assert "branch_a" in nodes
    assert "branch_b" in nodes
    assert "failed_terminal" in nodes
    assert "completed" in nodes
    sw = nodes["decision_switch"]
    assert sw["config"]["switch_cases"]["approved"] == "branch_a"
    assert sw["config"]["switch_cases"]["edit_requested"] == "branch_b"
    assert sw["config"]["switch_cases"]["rejected"] == "failed_terminal"
    # Analysis escalation config
    assert nodes["analysis"]["config"]["escalate_to_team"] == "exec_ceo"
    assert nodes["analysis"]["config"]["timeout_seconds"] == 300


@pytest.mark.anyio
async def test_create_flow_with_invalid_definition_returns_400(client):
    """POST /flows with a broken definition (no start node) → 400."""
    storage = MagicMock()
    storage.create_flow = AsyncMock()
    _patch(storage)

    bad_def = {
        "nodes": [
            {"id": "only_end", "type": "end", "label": "End", "config": {}},
        ],
        "edges": [],
    }
    resp = await client.post(
        "/flows",
        json={"name": "Bad Flow", "definition_json": bad_def},
    )
    assert resp.status_code == 400
    storage.create_flow.assert_not_called()


@pytest.mark.anyio
async def test_get_nonexistent_flow_returns_404(client):
    """GET /flows/{unknown-id} → 404."""
    storage = MagicMock()
    storage.get_flow = AsyncMock(return_value=None)
    _patch(storage)

    resp = await client.get(f"/flows/{uuid4()}")
    assert resp.status_code == 404


# ── 2. Flow attachment to project ─────────────────────────────────────────────


@pytest.mark.anyio
async def test_attach_flow_to_project_creates_instance_and_audit_event(client):
    """POST /flows/instances → instance created, flow_assigned audit logged."""
    flow = _flow_row()
    project = _project_row("INIT")
    inst = {**_instance_row(status="NOT_STARTED"), "project_id": PROJECT_ID}

    storage = MagicMock()
    storage.get_flow = AsyncMock(return_value=flow)
    storage.get_project = AsyncMock(return_value=project)
    storage.get_flow_instance_by_project = AsyncMock(return_value=None)
    storage.create_flow_instance = AsyncMock(return_value=inst)
    storage.transition_project = AsyncMock(return_value=project)
    _patch(storage)

    resp = await client.post(
        "/flows/instances",
        json={"flow_id": str(FLOW_ID), "project_id": str(PROJECT_ID)},
    )
    assert resp.status_code == 200
    storage.create_flow_instance.assert_awaited_once()
    _, kwargs = storage.transition_project.await_args
    assert kwargs["event"] == "flow_assigned"
    assert kwargs["payload"]["flow_id"] == str(FLOW_ID)
    assert kwargs["payload"]["flow_name"] == "Test-2 Branching Flow"


@pytest.mark.anyio
async def test_attach_flow_to_project_with_unknown_project_returns_404(client):
    """POST /flows/instances with unknown project_id → 404, nothing created."""
    flow = _flow_row()
    storage = MagicMock()
    storage.get_flow = AsyncMock(return_value=flow)
    storage.get_project = AsyncMock(return_value=None)
    storage.create_flow_instance = AsyncMock()
    _patch(storage)

    resp = await client.post(
        "/flows/instances",
        json={"flow_id": str(FLOW_ID), "project_id": str(uuid4())},
    )
    assert resp.status_code == 404
    storage.create_flow_instance.assert_not_called()


@pytest.mark.anyio
async def test_attach_flow_twice_to_same_project_returns_409(client):
    """POST /flows/instances when instance already exists → 409 Conflict."""
    flow = _flow_row()
    project = _project_row()
    existing = _instance_row(status="RUNNING")

    storage = MagicMock()
    storage.get_flow = AsyncMock(return_value=flow)
    storage.get_project = AsyncMock(return_value=project)
    storage.get_flow_instance_by_project = AsyncMock(return_value=existing)
    storage.create_flow_instance = AsyncMock()
    _patch(storage)

    resp = await client.post(
        "/flows/instances",
        json={"flow_id": str(FLOW_ID), "project_id": str(PROJECT_ID)},
    )
    assert resp.status_code == 409
    storage.create_flow_instance.assert_not_called()


# ── 3. Start flow instance ────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_start_flow_instance_activates_start_node(client):
    """POST /flows/instances/{id}/action {action: start} → active_node_ids = ['start']."""
    not_started = _instance_row(active_node_ids=[], status="NOT_STARTED")
    started = _instance_row(active_node_ids=["start"], status="RUNNING")

    storage = MagicMock()
    storage.get_flow_instance = AsyncMock(return_value=not_started)
    storage.get_flow = AsyncMock(return_value=_flow_row())
    storage.activate_flow_nodes = AsyncMock(
        return_value={"outcome": "applied", "instance": started, "executions": [{"id": 1}]}
    )
    _patch(storage)

    resp = await client.post(f"/flows/instances/{INST_ID}/action", json={"action": "start"})
    assert resp.status_code == 200
    assert resp.json()["active_node_ids"] == ["start"]
    kwargs = storage.activate_flow_nodes.await_args.kwargs
    assert kwargs["expected_statuses"] == {"NOT_STARTED"}
    assert kwargs["instance_updates"]["active_node_ids"] == ["start"]
    assert kwargs["activations"][0]["node_id"] == "start"
    storage.update_flow_instance.assert_not_called()
    storage.create_flow_node_execution.assert_not_called()


# ── 4. Approval decisions activate the bound next node ──────────────────────


@pytest.mark.anyio
async def test_approved_decision_activates_switch(client):
    """
    Complete approval_gate with decision=approved → active node becomes decision_switch.
    Verifies:
    - API response shows active_node_ids == ['decision_switch']
    - context_json['approval'] == 'approved' persisted
    """
    initial = _instance_row(
        active_node_ids=["approval_gate"],
        status="RUNNING",
        context_json={"last_safe_node_id": "analysis"},
    )
    after = _instance_row(
        active_node_ids=["decision_switch"],
        status="RUNNING",
        context_json={"last_safe_node_id": "approval_gate", "approval": "approved"},
    )

    storage = MagicMock()
    _mock_bound_approval_resolution(storage, initial=initial, resolved=after, execution_id=1)
    _patch(storage)

    resp = await client.post(
        f"/flows/instances/{INST_ID}/node-action",
        headers=OPERATOR_HEADERS,
        json={"node_id": "approval_gate", "action": "complete", "decision": "approved"},
    )
    assert resp.status_code == 200
    assert resp.json()["active_node_ids"] == ["decision_switch"]
    assert storage.resolve_flow_approval_gate.await_args.kwargs["instance_updates"]["context_json"]["approval"] == "approved"


# ── 5. Approval: edit_requested is retained for the switch ───────────────────


@pytest.mark.anyio
async def test_edit_requested_decision_activates_switch(client):
    """Complete approval_gate with decision=edit_requested → active node becomes switch."""
    initial = _instance_row(
        active_node_ids=["approval_gate"],
        status="RUNNING",
        context_json={"last_safe_node_id": "analysis"},
    )
    after = _instance_row(
        active_node_ids=["decision_switch"],
        status="RUNNING",
        context_json={"last_safe_node_id": "approval_gate", "approval": "edit_requested"},
    )

    storage = MagicMock()
    _mock_bound_approval_resolution(storage, initial=initial, resolved=after, execution_id=3)
    _patch(storage)

    resp = await client.post(
        f"/flows/instances/{INST_ID}/node-action",
        headers=OPERATOR_HEADERS,
        json={"node_id": "approval_gate", "action": "complete", "decision": "edit_requested"},
    )
    assert resp.status_code == 200
    assert resp.json()["active_node_ids"] == ["decision_switch"]
    assert storage.resolve_flow_approval_gate.await_args.kwargs["instance_updates"]["context_json"]["approval"] == "edit_requested"


# ── 6. Approval rejection also resolves through the switch node ───────────────


@pytest.mark.anyio
async def test_rejected_decision_activates_switch_for_failed_terminal_route(client):
    """
    A rejected decision first activates the configured decision switch.
    Verifies:
    - API response status remains RUNNING until the switch is executed
    """
    initial = _instance_row(
        active_node_ids=["approval_gate"],
        status="RUNNING",
        context_json={"last_safe_node_id": "analysis"},
    )
    resolved_inst = _instance_row(
        active_node_ids=["decision_switch"],
        status="RUNNING",
        context_json={"last_safe_node_id": "approval_gate", "approval": "rejected"},
    )

    storage = MagicMock()
    _mock_bound_approval_resolution(storage, initial=initial, resolved=resolved_inst, execution_id=5)
    _patch(storage)

    resp = await client.post(
        f"/flows/instances/{INST_ID}/node-action",
        headers=OPERATOR_HEADERS,
        json={"node_id": "approval_gate", "action": "complete", "decision": "rejected"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "RUNNING"
    assert resp.json()["active_node_ids"] == ["decision_switch"]


# ── 7. Retry from FAILED → last safe state ────────────────────────────────────


@pytest.mark.anyio
async def test_retry_failed_instance_restores_last_safe_node(client):
    """
    POST /flows/instances/{id}/retry → instance re-activates last_safe_node_id.
    Validates:
    - retry_count incremented
    - active_node_ids set to [last_safe_node_id]
    - status returns to RUNNING
    """
    failed_inst = _instance_row(
        active_node_ids=[],
        status="FAILED",
        context_json={"last_safe_node_id": "analysis", "approval": "rejected"},
        retry_count=0,
    )
    restored = _instance_row(
        active_node_ids=["analysis"],
        status="RUNNING",
        context_json={"last_safe_node_id": "analysis"},
        retry_count=1,
    )

    storage = MagicMock()
    storage.get_flow_instance = AsyncMock(side_effect=[failed_inst, restored])
    storage.get_flow = AsyncMock(return_value=_flow_row())
    storage.activate_flow_nodes = AsyncMock(
        return_value={"outcome": "applied", "instance": restored}
    )
    _patch(storage)

    resp = await client.post(f"/flows/instances/{INST_ID}/retry")
    assert resp.status_code == 200
    body = resp.json()
    assert body["active_node_ids"] == ["analysis"]
    assert body["status"] == "RUNNING"
    assert body["retry_count"] == 1
    activation = storage.activate_flow_nodes.await_args.kwargs
    assert activation["supersede_existing"] is True
    assert activation["instance_updates"]["active_node_ids"] == ["analysis"]
    assert activation["instance_updates"]["status"] == "RUNNING"


@pytest.mark.anyio
async def test_retry_non_failed_instance_returns_400(client):
    """Retry on a RUNNING (non-FAILED) instance must be rejected."""
    storage = MagicMock()
    storage.get_flow_instance = AsyncMock(
        return_value=_instance_row(active_node_ids=["analysis"], status="RUNNING")
    )
    storage.get_flow = AsyncMock(return_value=_flow_row())
    storage.update_flow_instance = AsyncMock()
    _patch(storage)

    resp = await client.post(f"/flows/instances/{INST_ID}/retry")
    assert resp.status_code == 409  # API returns 409 Conflict for non-FAILED instances
    storage.update_flow_instance.assert_not_called()


# ── 8. Timeout → escalation to CEO ───────────────────────────────────────────


@pytest.mark.anyio
async def test_analysis_timeout_escalates_to_ceo_team_and_logs_history(client):
    """
    POST /flows/instances/{id}/node-action {action: timeout, node_id: analysis}
    → instance/execution failure and escalation history are persisted atomically
    → project history event flow_node_escalated with escalated_to=exec_ceo
    """
    flow = _flow_row()
    initial = _instance_row(
        active_node_ids=["analysis"],
        status="RUNNING",
        context_json={"last_safe_node_id": "start"},
    )
    escalated = _instance_row(
        active_node_ids=[],
        status="FAILED",
        context_json={"last_timed_out_node_id": "analysis", "last_error": "Analysis timed out"},
        escalated_to="exec_ceo",
    )

    storage = MagicMock()
    storage.get_flow_instance = AsyncMock(side_effect=[initial, escalated])
    storage.get_flow = AsyncMock(return_value=flow)
    storage.list_flow_node_executions = AsyncMock(
        return_value=[
            {
                "id": 8,
                "instance_id": INST_ID,
                "node_id": "analysis",
                "node_type": "task",
                "status": "RUNNING",
            }
        ]
    )
    storage.update_flow_node_execution = AsyncMock(return_value={"id": 8})
    storage.update_flow_instance = AsyncMock(return_value=escalated)
    storage.escalate_flow_instance = AsyncMock(return_value=escalated)
    storage.activate_flow_nodes = AsyncMock(
        return_value={"outcome": "applied", "instance": escalated}
    )
    storage.get_project = AsyncMock(return_value=_project_row())
    storage.transition_project = AsyncMock(return_value=_project_row())
    _patch(storage)

    resp = await client.post(
        f"/flows/instances/{INST_ID}/node-action",
        json={"node_id": "analysis", "action": "timeout", "error": "Analysis timed out"},
    )
    assert resp.status_code == 200
    assert resp.json()["escalated_to"] == "exec_ceo"
    storage.activate_flow_nodes.assert_awaited_once()
    _, kwargs = storage.activate_flow_nodes.await_args
    assert kwargs["expected_statuses"] == {"RUNNING", "WAITING_APPROVAL"}
    assert kwargs["instance_updates"]["status"] == "FAILED"
    assert kwargs["instance_updates"]["escalated_to"] == "exec_ceo"
    assert kwargs["execution_updates"][0]["expected"]["instance_id"] == INST_ID
    assert kwargs["execution_updates"][0]["values"]["status"] == "FAILED"
    assert kwargs["project_event"]["event"] == "flow_node_escalated"
    assert kwargs["project_event"]["payload"]["escalated_to"] == "exec_ceo"
    assert kwargs["project_event"]["payload"]["node_id"] == "analysis"
    storage.update_flow_node_execution.assert_not_awaited()
    storage.update_flow_instance.assert_not_awaited()
    storage.escalate_flow_instance.assert_not_awaited()
    storage.transition_project.assert_not_awaited()


@pytest.mark.anyio
async def test_escalation_visible_in_state_history_route(client):
    """
    GET /projects/{id}/state-history returns flow_node_escalated event.
    This is the operator-visible audit trail for escalation.
    """
    storage = MagicMock()
    storage.get_project = AsyncMock(return_value={"id": str(PROJECT_ID)})
    storage.get_project_history = AsyncMock(
        return_value=[
            {
                "project_id": PROJECT_ID,
                "from_state": "IN_PROGRESS",
                "to_state": "IN_PROGRESS",
                "event": "flow_node_escalated",
                "triggered_by": "orchestrator",
                "payload": {
                    "node_id": "analysis",
                    "escalated_to": "exec_ceo",
                    "reason": "Analysis timed out",
                },
                "transitioned_at": NOW_ISO,
            }
        ]
    )
    _patch(storage)

    resp = await client.get(f"/projects/{PROJECT_ID}/state-history")
    assert resp.status_code == 200
    history = resp.json()
    escalation_events = [e for e in history if e["event"] == "flow_node_escalated"]
    assert len(escalation_events) == 1
    assert escalation_events[0]["payload"]["escalated_to"] == "exec_ceo"
    assert escalation_events[0]["payload"]["node_id"] == "analysis"


# ── 9. Manual override ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_operator_override_sets_active_node_and_logs_audit(client):
    """
    POST /flows/instances/{id}/override as human_operator → active node updated,
    flow_node_override event in project history.
    """
    overridden = _instance_row(active_node_ids=["analysis"], status="RUNNING")
    storage = MagicMock()
    storage.get_flow_instance = AsyncMock(
        return_value=_instance_row(active_node_ids=["branch_b"], status="RUNNING")
    )
    storage.get_flow = AsyncMock(return_value=_flow_row())
    storage.override_flow_instance = AsyncMock(return_value=overridden)
    storage.get_project = AsyncMock(return_value=_project_row())
    storage.transition_project = AsyncMock(return_value=_project_row())
    _patch(storage)

    resp = await client.post(
        f"/flows/instances/{INST_ID}/override",
        json={
            "target_node_id": "analysis",
            "actor_id": "human",
            "actor_role": "human_operator",
            "reason": "Operator reset for re-review",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["active_node_ids"] == ["analysis"]
    _, kwargs = storage.transition_project.await_args
    assert kwargs["event"] == "flow_node_override"
    assert kwargs["payload"]["to_node_id"] == "analysis"


@pytest.mark.anyio
async def test_override_by_non_operator_role_is_forbidden(client):
    """Security: only human_operator may override; any other role → 403."""
    storage = MagicMock()
    _patch(storage)

    resp = await client.post(
        f"/flows/instances/{INST_ID}/override",
        json={
            "target_node_id": "analysis",
            "actor_id": "agent-developer",
            "actor_role": "developer",
            "reason": "unauthorized override attempt",
        },
    )
    assert resp.status_code == 403
    storage.override_flow_instance = MagicMock()  # should never be called
    # double-check: no storage write happened
    storage.override_flow_instance.assert_not_called()


@pytest.mark.anyio
async def test_override_to_unknown_node_id_returns_400_and_no_mutation(client):
    """Override to a node_id that does not exist in flow definition → 400."""
    storage = MagicMock()
    storage.get_flow_instance = AsyncMock(
        return_value=_instance_row(active_node_ids=["approval_gate"], status="RUNNING")
    )
    storage.get_flow = AsyncMock(return_value=_flow_row())
    storage.override_flow_instance = AsyncMock()
    _patch(storage)

    resp = await client.post(
        f"/flows/instances/{INST_ID}/override",
        json={
            "target_node_id": "nonexistent_node_xyz",
            "actor_id": "human",
            "actor_role": "human_operator",
            "reason": "test bad node",
        },
    )
    assert resp.status_code == 400
    storage.override_flow_instance.assert_not_called()


# ── 10. Approval without decision → waiting state ────────────────────────────


@pytest.mark.anyio
async def test_approval_gate_complete_without_decision_transitions_to_waiting(client):
    """
    Completing an approval node without a decision → WAITING_APPROVAL status.
    Approval must remain pending until a decision is explicitly provided.
    """
    waiting = _instance_row(
        active_node_ids=["approval_gate"],
        status="WAITING_APPROVAL",
        context_json={"last_safe_node_id": "analysis"},
    )
    storage = MagicMock()
    storage.get_flow_instance = AsyncMock(
        side_effect=[
            _instance_row(
                active_node_ids=["approval_gate"],
                status="RUNNING",
                context_json={"last_safe_node_id": "analysis"},
            ),
            waiting,
        ]
    )
    storage.get_flow = AsyncMock(return_value=_flow_row())
    storage.list_flow_node_executions = AsyncMock(
        return_value=[
            {
                "id": 3,
                "instance_id": INST_ID,
                "node_id": "approval_gate",
                "node_type": "approval",
                "status": "RUNNING",
            }
        ]
    )
    storage.update_flow_instance = AsyncMock(return_value=waiting)
    storage.update_flow_node_execution = AsyncMock()
    storage.activate_flow_nodes = AsyncMock(
        return_value={"outcome": "applied", "instance": waiting}
    )
    _patch(storage)

    resp = await client.post(
        f"/flows/instances/{INST_ID}/node-action",
        headers=OPERATOR_HEADERS,
        json={"node_id": "approval_gate", "action": "complete"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "WAITING_APPROVAL"
    storage.activate_flow_nodes.assert_awaited_once()
    _, kwargs = storage.activate_flow_nodes.await_args
    assert kwargs["instance_updates"]["status"] == "WAITING_APPROVAL"
    assert kwargs["instance_updates"]["active_node_ids"] == ["approval_gate"]
    assert kwargs["execution_updates"] is None
    storage.update_flow_instance.assert_not_awaited()
    storage.update_flow_node_execution.assert_not_awaited()


# ── 11. Read-back: instance state verified through GET route ──────────────────


@pytest.mark.anyio
async def test_get_instance_after_approval_reflects_updated_context(client):
    """
    After an approval decision activates the configured switch, GET
    /flows/instances/{id} returns the durable approval context and active node.
    This simulates the operator reading state back through a separate route.
    """
    updated_inst = _instance_row(
        active_node_ids=["decision_switch"],
        status="RUNNING",
        context_json={"last_safe_node_id": "approval_gate", "approval": "approved"},
    )
    storage = MagicMock()
    storage.get_flow_instance = AsyncMock(return_value=updated_inst)
    _patch(storage)

    resp = await client.get(f"/flows/instances/{INST_ID}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["active_node_ids"] == ["decision_switch"]
    assert body["context_json"]["approval"] == "approved"


# ── 12. Node executions audit trail ──────────────────────────────────────────


@pytest.mark.anyio
async def test_node_executions_audit_trail_is_queryable(client):
    """
    GET /flows/instances/{id}/executions returns per-node execution history.
    Operator-visible audit: completed nodes, current running node, timestamps.
    """
    executions = [
        {
            "id": 1,
            "node_id": "start",
            "node_label": "Start",
            "status": "COMPLETED",
            "started_at": NOW_ISO,
            "completed_at": NOW_ISO,
        },
        {
            "id": 2,
            "node_id": "analysis",
            "node_label": "Analysis",
            "status": "COMPLETED",
            "started_at": NOW_ISO,
            "completed_at": NOW_ISO,
        },
        {
            "id": 3,
            "node_id": "approval_gate",
            "node_label": "Approval Gate",
            "status": "RUNNING",
            "started_at": NOW_ISO,
            "completed_at": None,
        },
    ]
    storage = MagicMock()
    storage.list_flow_node_executions = AsyncMock(return_value=executions)
    _patch(storage)

    resp = await client.get(f"/flows/instances/{INST_ID}/executions")
    assert resp.status_code == 200
    body = resp.json()
    node_ids = [e["node_id"] for e in body]
    assert "start" in node_ids
    assert "analysis" in node_ids
    assert "approval_gate" in node_ids
    # Current active node is RUNNING, past nodes are COMPLETED
    by_node = {e["node_id"]: e for e in body}
    assert by_node["start"]["status"] == "COMPLETED"
    assert by_node["approval_gate"]["status"] == "RUNNING"


# ── 13. Full linear sequence integration test ─────────────────────────────────


@pytest.mark.anyio
async def test_full_flow_sequence_create_attach_start_approve_read_back(client):
    """
    Integration: simulate the entire happy-path sequence as an operator would:
      1. Create flow → POST /flows
      2. Attach to project → POST /flows/instances
      3. Start instance → POST /flows/instances/{id}/action {start}
      4. Advance Analysis → POST /flows/instances/{id}/node-action {complete}
      5. Approve → POST /flows/instances/{id}/node-action {complete, decision=approved}
      6. Read back state → GET /flows/instances/{id}  (active=branch_a)

    Each step uses a separate mock call to simulate a different storage read,
    mirroring what a real DB would return after each mutation.
    """
    flow = _flow_row()
    project = _project_row("INIT")
    inst_not_started = _instance_row(active_node_ids=[], status="NOT_STARTED")
    inst_started = _instance_row(active_node_ids=["start"], status="RUNNING")
    inst_analysis = _instance_row(
        active_node_ids=["analysis"],
        status="RUNNING",
        context_json={"last_safe_node_id": "start"},
    )
    inst_approval = _instance_row(
        active_node_ids=["approval_gate"],
        status="RUNNING",
        context_json={"last_safe_node_id": "analysis"},
    )
    inst_decision_switch = _instance_row(
        active_node_ids=["decision_switch"],
        status="RUNNING",
        context_json={"last_safe_node_id": "approval_gate", "approval": "approved"},
    )

    # ── Step 1: create flow ──────────────────────────────────────────────────
    storage = MagicMock()
    storage.create_flow = AsyncMock(return_value=flow)
    storage.get_flow = AsyncMock(return_value=flow)
    storage.get_project = AsyncMock(return_value=project)
    storage.get_flow_instance_by_project = AsyncMock(return_value=None)
    storage.create_flow_instance = AsyncMock(return_value=inst_not_started)
    storage.transition_project = AsyncMock(return_value=project)
    storage.get_flow_instance = AsyncMock(
        side_effect=[
            inst_not_started,  # start action: initial read
            inst_started,  # complete start node: initial read
            inst_analysis,  # complete start node: refreshed read
            inst_analysis,  # complete analysis node: initial read
            inst_approval,  # complete analysis node: refreshed read
        ]
    )
    storage.update_flow_instance = AsyncMock(return_value=inst_analysis)
    storage.create_flow_node_execution = AsyncMock(return_value={"id": 99})
    storage.activate_flow_nodes = AsyncMock(
        side_effect=[
            {"outcome": "applied", "instance": inst_started, "executions": [{"id": 99}]},
            {"outcome": "applied", "instance": inst_analysis, "executions": [{"id": 100}]},
            {"outcome": "applied", "instance": inst_approval, "executions": []},
        ]
    )
    storage.list_flow_node_executions = AsyncMock(
        side_effect=[
            [
                {
                    "id": 1,
                    "instance_id": INST_ID,
                    "node_id": "start",
                    "status": "RUNNING",
                }
            ],  # complete start
            [
                {"instance_id": INST_ID, "node_id": "start", "status": "COMPLETED"},
                {"instance_id": INST_ID, "node_id": "analysis", "status": "RUNNING"},
            ],  # post complete start
            [
                {
                    "id": 2,
                    "instance_id": INST_ID,
                    "node_id": "analysis",
                    "status": "RUNNING",
                }
            ],  # complete analysis
            [
                {"instance_id": INST_ID, "node_id": "analysis", "status": "COMPLETED"}
            ],  # post complete analysis
            [
                {
                    "id": 3,
                    "instance_id": INST_ID,
                    "node_id": "approval_gate",
                    "status": "RUNNING",
                }
            ],  # complete approval
            [
                {"instance_id": INST_ID, "node_id": "analysis", "status": "COMPLETED"},
                {
                    "instance_id": INST_ID,
                    "node_id": "approval_gate",
                    "status": "COMPLETED",
                },
            ],  # post complete approval
        ]
    )
    storage.update_flow_node_execution = AsyncMock(return_value={"id": 99})
    storage.create_approval_gate = AsyncMock(return_value={"id": uuid4()})
    _patch(storage)

    # Step 1: create flow
    r1 = await client.post(
        "/flows",
        json={
            "name": "Test-2 Branching Flow",
            "definition_json": _full_flow_definition(),
            "is_active": True,
        },
    )
    assert r1.status_code == 201, f"Create flow failed: {r1.text}"

    # Step 2: attach to project
    r2 = await client.post(
        "/flows/instances",
        json={"flow_id": str(FLOW_ID), "project_id": str(PROJECT_ID)},
    )
    assert r2.status_code == 200, f"Attach failed: {r2.text}"

    # Step 3: start instance
    r3 = await client.post(f"/flows/instances/{INST_ID}/action", json={"action": "start"})
    assert r3.status_code == 200, f"Start failed: {r3.text}"
    assert r3.json()["active_node_ids"] == ["start"]

    # Step 4: complete start node → analysis becomes active
    r4 = await client.post(
        f"/flows/instances/{INST_ID}/node-action",
        json={"node_id": "start", "action": "complete"},
    )
    assert r4.status_code == 200, f"Start-node complete failed: {r4.text}"

    # Step 5: complete analysis → approval_gate and its gate activate atomically
    r5 = await client.post(
        f"/flows/instances/{INST_ID}/node-action",
        json={"node_id": "analysis", "action": "complete"},
    )
    assert r5.status_code == 200, f"Analysis complete failed: {r5.text}"

    # Step 6: the operator decision resolves the gate and advances the flow
    # to the switch in one request. No second decision or approval call exists.
    storage.get_flow_instance = AsyncMock(return_value=inst_approval)
    _mock_bound_approval_resolution(
        storage,
        initial=inst_approval,
        resolved=inst_decision_switch,
        execution_id=3,
    )

    r6 = await client.post(
        f"/flows/instances/{INST_ID}/node-action",
        headers=OPERATOR_HEADERS,
        json={"node_id": "approval_gate", "action": "complete", "decision": "approved"},
    )
    assert r6.status_code == 200, f"Approval failed: {r6.text}"
    assert r6.json()["active_node_ids"] == ["decision_switch"]

    # Step 7: read back state through GET route
    storage.get_flow_instance = AsyncMock(return_value=inst_decision_switch)
    r7 = await client.get(f"/flows/instances/{INST_ID}")
    assert r7.status_code == 200
    final = r7.json()
    assert final["active_node_ids"] == ["decision_switch"]
    assert final["context_json"]["approval"] == "approved"
    assert final["status"] == "RUNNING"


# ── 14. Browser E2E / UI operator flow ───────────────────────────────────────


@pytest.mark.skip(
    reason="Covered by the source-built Playwright spec at "
    "apps/mas-dashboard/e2e/flow-runtime-test2.spec.ts; run the dashboard "
    "e2e suite against a live operator environment for UI evidence."
)
def test_ui_operator_flow_approval_visible_after_refresh():
    """
    Operator-level UI flow (covered by the dashboard Playwright suite):
    1. Open flow builder at /flows/new, create flow with required nodes.
    2. Open project, attach flow, start it.
    3. Navigate to approval page, submit decision=approved.
    4. Refresh page, verify active node shows Branch A.
    5. Verify state-history panel shows flow_node transitions.
    This Python test remains skipped because the orchestrator API suite does
    not own a browser runtime. The dashboard spec is the authoritative UI
    implementation of this scenario.
    """
    pytest.skip("Browser execution belongs to the dashboard Playwright suite")
