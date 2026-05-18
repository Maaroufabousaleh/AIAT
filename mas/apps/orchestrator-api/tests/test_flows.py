from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from conftest import PROJECT_ID


FLOW_ID = UUID("00000000-0000-4000-a000-0000000000f1")
FLOW_INSTANCE_ID = UUID("00000000-0000-4000-a000-0000000000f2")


def _patch_state(storage) -> None:
    from orchestrator_api.main import app

    app.state.storage = storage


def _flow_definition() -> dict[str, object]:
    return {
        "nodes": [
            {"id": "intake", "type": "start", "label": "Intake", "config": {}},
            {
                "id": "feasibility_review",
                "type": "task",
                "label": "Feasibility Review",
                "config": {"team_id": "cfo-cio-chrm-cso"},
            },
            {
                "id": "human_approval",
                "type": "approval",
                "label": "Human Approval",
                "config": {"approver_user": "human"},
            },
            {
                "id": "implementation",
                "type": "task",
                "label": "Implementation",
                "config": {"team_id": "cto", "retries": 1},
            },
            {"id": "done", "type": "end", "label": "Done", "config": {}},
        ],
        "edges": [
            {"id": "e1", "source": "intake", "target": "feasibility_review"},
            {"id": "e2", "source": "feasibility_review", "target": "human_approval"},
            {"id": "e3", "source": "human_approval", "target": "implementation"},
            {"id": "e4", "source": "implementation", "target": "done"},
        ],
    }


def _runtime_flow_definition() -> dict[str, object]:
    return {
        "nodes": [
            {"id": "start", "type": "start", "label": "Start", "config": {}},
            {
                "id": "analysis",
                "type": "task",
                "label": "Analysis",
                "config": {
                    "team_id": "office_cfo",
                    "timeout_seconds": 60,
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
            {"id": "completed_terminal", "type": "end", "label": "Completed", "config": {}},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "analysis"},
            {"id": "e2", "source": "analysis", "target": "approval_gate"},
            {"id": "e3", "source": "approval_gate", "target": "decision_switch"},
            {"id": "e4", "source": "branch_a", "target": "completed_terminal"},
            {"id": "e5", "source": "branch_b", "target": "completed_terminal"},
        ],
    }


def _flow(
    *, flow_id: UUID = FLOW_ID, version: int = 1, name: str = "Simple Product Build Flow"
) -> dict[str, object]:
    return {
        "id": flow_id,
        "name": name,
        "description": "Custom workflow",
        "definition_json": _flow_definition(),
        "version": version,
        "created_by": "human",
        "is_active": True,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }


def _instance(
    *, active_node_ids: list[str] | None = None, status: str = "RUNNING"
) -> dict[str, object]:
    return {
        "id": FLOW_INSTANCE_ID,
        "flow_id": FLOW_ID,
        "flow_version": 1,
        "project_id": PROJECT_ID,
        "active_node_ids": active_node_ids or ["feasibility_review"],
        "status": status,
        "context_json": {},
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }


@pytest.mark.anyio
async def test_create_flow_version_from_existing_flow(client):
    base_flow = _flow(version=1)
    created_flow = _flow(flow_id=uuid4(), version=2)

    storage = MagicMock()
    storage.get_flow = AsyncMock(return_value=base_flow)
    storage.create_flow = AsyncMock(return_value=created_flow)
    _patch_state(storage)

    response = await client.post(
        "/flows",
        json={
            "name": "Simple Product Build Flow",
            "description": "v2",
            "is_active": True,
            "version_from_flow_id": str(FLOW_ID),
            "definition_json": _flow_definition(),
        },
    )

    assert response.status_code == 201
    assert response.json()["version"] == 2
    storage.get_flow.assert_awaited_once_with(FLOW_ID)
    _, kwargs = storage.create_flow.await_args
    assert kwargs["version"] == 2
    assert kwargs["definition_json"]["metadata"]["version_group_id"] == str(FLOW_ID)
    assert kwargs["definition_json"]["metadata"]["source_flow_id"] == str(FLOW_ID)


@pytest.mark.anyio
async def test_create_flow_version_from_missing_base_returns_404(client):
    storage = MagicMock()
    storage.get_flow = AsyncMock(return_value=None)
    storage.create_flow = AsyncMock()
    _patch_state(storage)

    response = await client.post(
        "/flows",
        json={
            "name": "Simple Product Build Flow",
            "version_from_flow_id": str(FLOW_ID),
            "definition_json": _flow_definition(),
        },
    )

    assert response.status_code == 404
    storage.create_flow.assert_not_called()


@pytest.mark.anyio
async def test_create_flow_instance_records_assignment_audit(client):
    flow = _flow()
    project = {"id": PROJECT_ID, "state": "INIT"}
    created_instance = {
        **_instance(active_node_ids=[], status="NOT_STARTED"),
        "project_id": PROJECT_ID,
    }

    storage = MagicMock()
    storage.get_flow = AsyncMock(return_value=flow)
    storage.get_project = AsyncMock(return_value=project)
    storage.get_flow_instance_by_project = AsyncMock(return_value=None)
    storage.create_flow_instance = AsyncMock(return_value=created_instance)
    storage.transition_project = AsyncMock(return_value=project)
    _patch_state(storage)

    response = await client.post(
        "/flows/instances",
        json={"flow_id": str(FLOW_ID), "project_id": str(PROJECT_ID)},
    )

    assert response.status_code == 200
    storage.transition_project.assert_awaited_once()
    _, kwargs = storage.transition_project.await_args
    assert kwargs["event"] == "flow_assigned"
    assert kwargs["payload"]["flow_name"] == "Simple Product Build Flow"


@pytest.mark.anyio
async def test_start_flow_instance_uses_custom_flow_definition(client):
    storage = MagicMock()
    storage.get_flow_instance = AsyncMock(
        side_effect=[
            _instance(active_node_ids=[], status="NOT_STARTED"),
            _instance(active_node_ids=["intake"], status="RUNNING"),
        ]
    )
    storage.get_flow = AsyncMock(return_value=_flow())
    storage.update_flow_instance = AsyncMock(
        return_value=_instance(active_node_ids=["intake"], status="RUNNING")
    )
    storage.create_flow_node_execution = AsyncMock(return_value={"id": 1})
    _patch_state(storage)

    response = await client.post(
        f"/flows/instances/{FLOW_INSTANCE_ID}/action",
        json={"action": "start"},
    )

    assert response.status_code == 200
    assert response.json()["active_node_ids"] == ["intake"]
    storage.update_flow_instance.assert_awaited_once()
    _, kwargs = storage.create_flow_node_execution.await_args
    assert kwargs["node_id"] == "intake"
    assert kwargs["node_label"] == "Intake"


@pytest.mark.anyio
async def test_override_flow_instance_logs_project_history(client):
    updated_instance = _instance(active_node_ids=["implementation"], status="RUNNING")

    storage = MagicMock()
    storage.get_flow_instance = AsyncMock(return_value=_instance())
    storage.get_flow = AsyncMock(return_value=_flow())
    storage.override_flow_instance = AsyncMock(return_value=updated_instance)
    storage.get_project = AsyncMock(return_value={"id": PROJECT_ID, "state": "IN_PROGRESS"})
    storage.transition_project = AsyncMock(return_value={"id": PROJECT_ID, "state": "IN_PROGRESS"})
    _patch_state(storage)

    response = await client.post(
        f"/flows/instances/{FLOW_INSTANCE_ID}/override",
        json={
            "target_node_id": "implementation",
            "actor_id": "human",
            "actor_role": "human_operator",
            "reason": "Manual expedite",
        },
    )

    assert response.status_code == 200
    assert response.json()["active_node_ids"] == ["implementation"]
    storage.override_flow_instance.assert_awaited_once()
    storage.transition_project.assert_awaited_once()
    _, kwargs = storage.transition_project.await_args
    assert kwargs["event"] == "flow_node_override"
    assert kwargs["payload"]["to_node_id"] == "implementation"


@pytest.mark.anyio
async def test_switch_flow_instance_records_audit_event(client):
    switched_instance = {**_instance(active_node_ids=[], status="NOT_STARTED"), "flow_id": uuid4()}
    new_flow_id = uuid4()

    storage = MagicMock()
    storage.get_flow_instance = AsyncMock(return_value=_instance())
    storage.get_flow = AsyncMock(
        return_value=_flow(flow_id=new_flow_id, name="Research Flow", version=3)
    )
    storage.switch_flow_instance = AsyncMock(return_value=switched_instance)
    storage.get_project = AsyncMock(return_value={"id": PROJECT_ID, "state": "IN_PROGRESS"})
    storage.transition_project = AsyncMock(return_value={"id": PROJECT_ID, "state": "IN_PROGRESS"})
    _patch_state(storage)

    response = await client.post(
        f"/flows/instances/{FLOW_INSTANCE_ID}/switch",
        json={"flow_id": str(new_flow_id), "preserve_context": True},
    )

    assert response.status_code == 200
    storage.transition_project.assert_awaited_once()
    _, kwargs = storage.transition_project.await_args
    assert kwargs["event"] == "flow_switched"
    assert kwargs["payload"]["to_flow_id"] == str(new_flow_id)
    assert kwargs["payload"]["to_flow_name"] == "Research Flow"


@pytest.mark.anyio
async def test_override_flow_instance_rejects_non_human_operator(client):
    storage = MagicMock()
    _patch_state(storage)

    response = await client.post(
        f"/flows/instances/{FLOW_INSTANCE_ID}/override",
        json={
            "target_node_id": "implementation",
            "actor_id": "agent-1",
            "actor_role": "cto",
        },
    )

    assert response.status_code == 403


@pytest.mark.anyio
async def test_override_flow_instance_rejects_unknown_node(client):
    storage = MagicMock()
    storage.get_flow_instance = AsyncMock(return_value=_instance())
    storage.get_flow = AsyncMock(return_value=_flow())
    storage.override_flow_instance = AsyncMock()
    _patch_state(storage)

    response = await client.post(
        f"/flows/instances/{FLOW_INSTANCE_ID}/override",
        json={
            "target_node_id": "qa_review",
            "actor_id": "human",
            "actor_role": "human_operator",
        },
    )

    assert response.status_code == 400
    storage.override_flow_instance.assert_not_called()


@pytest.mark.anyio
async def test_state_history_returns_override_audit_payload(client):
    storage = MagicMock()
    storage.get_project_history = AsyncMock(
        return_value=[
            {
                "project_id": PROJECT_ID,
                "from_state": "IN_PROGRESS",
                "to_state": "IN_PROGRESS",
                "event": "flow_node_override",
                "triggered_by": "human",
                "payload": {"to_node_id": "implementation", "reason": "Manual expedite"},
                "transitioned_at": "2026-01-01T00:00:00+00:00",
            }
        ]
    )
    _patch_state(storage)

    response = await client.get(f"/projects/{PROJECT_ID}/state-history")

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["event"] == "flow_node_override"
    assert payload[0]["payload"]["to_node_id"] == "implementation"


@pytest.mark.anyio
async def test_complete_analysis_activates_approval_gate_and_audit_record(client):
    runtime_flow = {
        **_flow(),
        "definition_json": _runtime_flow_definition(),
    }
    initial_instance = {
        **_instance(active_node_ids=["analysis"], status="RUNNING"),
        "context_json": {},
    }
    updated_instance = {
        **_instance(active_node_ids=["approval_gate"], status="RUNNING"),
        "context_json": {"last_safe_node_id": "analysis"},
    }

    storage = MagicMock()
    storage.get_flow_instance = AsyncMock(side_effect=[initial_instance, updated_instance])
    storage.get_flow = AsyncMock(return_value=runtime_flow)
    storage.list_flow_node_executions = AsyncMock(
        side_effect=[
            [{"id": 10, "node_id": "analysis", "status": "RUNNING"}],
            [{"node_id": "analysis", "status": "COMPLETED"}],
        ]
    )
    storage.update_flow_node_execution = AsyncMock(return_value={"id": 10})
    storage.create_flow_node_execution = AsyncMock(return_value={"id": 11})
    storage.create_approval_gate = AsyncMock(return_value={"id": uuid4()})
    storage.update_flow_instance = AsyncMock(return_value=updated_instance)
    _patch_state(storage)

    response = await client.post(
        f"/flows/instances/{FLOW_INSTANCE_ID}/node-action",
        json={"node_id": "analysis", "action": "complete"},
    )

    assert response.status_code == 200
    assert response.json()["active_node_ids"] == ["approval_gate"]
    storage.create_approval_gate.assert_awaited_once()
    _, kwargs = storage.create_flow_node_execution.await_args
    assert kwargs["node_id"] == "approval_gate"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("decision", "expected_node"),
    [("approved", "branch_a"), ("edit_requested", "branch_b")],
)
async def test_approval_decision_routes_to_expected_branch(client, decision, expected_node):
    runtime_flow = {
        **_flow(),
        "definition_json": _runtime_flow_definition(),
    }
    initial_instance = {
        **_instance(active_node_ids=["approval_gate"], status="RUNNING"),
        "context_json": {"last_safe_node_id": "analysis"},
    }
    updated_instance = {
        **_instance(active_node_ids=[expected_node], status="RUNNING"),
        "context_json": {
            "last_safe_node_id": "approval_gate",
            "approval": decision,
            "last_approval_decision": decision,
        },
    }

    storage = MagicMock()
    storage.get_flow_instance = AsyncMock(side_effect=[initial_instance, updated_instance])
    storage.get_flow = AsyncMock(return_value=runtime_flow)
    storage.list_flow_node_executions = AsyncMock(
        side_effect=[
            [{"id": 20, "node_id": "approval_gate", "status": "RUNNING"}],
            [
                {"node_id": "analysis", "status": "COMPLETED"},
                {"node_id": "approval_gate", "status": "COMPLETED"},
            ],
        ]
    )
    storage.update_flow_node_execution = AsyncMock(return_value={"id": 20})
    storage.create_flow_node_execution = AsyncMock(return_value={"id": 21})
    storage.update_flow_instance = AsyncMock(return_value=updated_instance)
    _patch_state(storage)

    response = await client.post(
        f"/flows/instances/{FLOW_INSTANCE_ID}/node-action",
        json={"node_id": "approval_gate", "action": "complete", "decision": decision},
    )

    assert response.status_code == 200
    assert response.json()["active_node_ids"] == [expected_node]
    _, kwargs = storage.update_flow_instance.await_args
    assert kwargs["context_json"]["approval"] == decision


@pytest.mark.anyio
async def test_approval_rejection_routes_to_failed_terminal(client):
    runtime_flow = {
        **_flow(),
        "definition_json": _runtime_flow_definition(),
    }
    failed_instance = {
        **_instance(active_node_ids=[], status="FAILED"),
        "context_json": {"last_safe_node_id": "approval_gate", "approval": "rejected"},
    }

    storage = MagicMock()
    storage.get_flow_instance = AsyncMock(
        side_effect=[
            {
                **_instance(active_node_ids=["approval_gate"], status="RUNNING"),
                "context_json": {"last_safe_node_id": "analysis"},
            },
            failed_instance,
        ]
    )
    storage.get_flow = AsyncMock(return_value=runtime_flow)
    storage.list_flow_node_executions = AsyncMock(
        side_effect=[
            [{"id": 30, "node_id": "approval_gate", "status": "RUNNING"}],
            [
                {"node_id": "analysis", "status": "COMPLETED"},
                {"node_id": "approval_gate", "status": "COMPLETED"},
            ],
            [{"id": 31, "node_id": "failed_terminal", "status": "RUNNING"}],
        ]
    )
    storage.update_flow_node_execution = AsyncMock(return_value={"id": 30})
    storage.create_flow_node_execution = AsyncMock(return_value={"id": 31})
    storage.update_flow_instance = AsyncMock(return_value=failed_instance)
    _patch_state(storage)

    response = await client.post(
        f"/flows/instances/{FLOW_INSTANCE_ID}/node-action",
        json={"node_id": "approval_gate", "action": "complete", "decision": "rejected"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "FAILED"


@pytest.mark.anyio
async def test_approval_complete_without_decision_waits_for_approval(client):
    runtime_flow = {
        **_flow(),
        "definition_json": _runtime_flow_definition(),
    }
    waiting_instance = {
        **_instance(active_node_ids=["approval_gate"], status="WAITING_APPROVAL"),
        "context_json": {"last_safe_node_id": "analysis"},
    }

    storage = MagicMock()
    storage.get_flow_instance = AsyncMock(
        side_effect=[
            {
                **_instance(active_node_ids=["approval_gate"], status="RUNNING"),
                "context_json": {"last_safe_node_id": "analysis"},
            },
            waiting_instance,
        ]
    )
    storage.get_flow = AsyncMock(return_value=runtime_flow)
    storage.update_flow_instance = AsyncMock(return_value=waiting_instance)
    _patch_state(storage)

    response = await client.post(
        f"/flows/instances/{FLOW_INSTANCE_ID}/node-action",
        json={"node_id": "approval_gate", "action": "complete"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "WAITING_APPROVAL"


@pytest.mark.anyio
async def test_retry_failed_flow_restores_last_safe_node(client):
    runtime_flow = {
        **_flow(),
        "definition_json": _runtime_flow_definition(),
    }
    failed_instance = {
        **_instance(active_node_ids=[], status="FAILED"),
        "retry_count": 0,
        "context_json": {"last_safe_node_id": "analysis", "approval": "rejected"},
    }
    restored_instance = {
        **_instance(active_node_ids=["analysis"], status="RUNNING"),
        "retry_count": 1,
        "context_json": {"last_safe_node_id": "analysis", "approval": "rejected"},
    }

    storage = MagicMock()
    storage.get_flow_instance = AsyncMock(side_effect=[failed_instance, restored_instance])
    storage.get_flow = AsyncMock(return_value=runtime_flow)
    storage.clear_flow_node_executions = AsyncMock(return_value=None)
    storage.update_flow_instance = AsyncMock(return_value=restored_instance)
    storage.create_flow_node_execution = AsyncMock(return_value={"id": 40})
    storage.create_approval_gate = AsyncMock(return_value={"id": uuid4()})
    _patch_state(storage)

    response = await client.post(f"/flows/instances/{FLOW_INSTANCE_ID}/retry")

    assert response.status_code == 200
    assert response.json()["active_node_ids"] == ["analysis"]
    _, kwargs = storage.update_flow_instance.await_args
    assert kwargs["status"] == "RUNNING"
    assert kwargs["active_node_ids"] == ["analysis"]


@pytest.mark.anyio
async def test_timeout_escalates_to_ceo_and_logs_history(client):
    runtime_flow = {
        **_flow(),
        "definition_json": _runtime_flow_definition(),
    }
    escalated_instance = {
        **_instance(active_node_ids=[], status="FAILED"),
        "escalated_to": "exec_ceo",
        "escalation_reason": "Analysis timed out",
        "context_json": {"last_timed_out_node_id": "analysis", "last_error": "Analysis timed out"},
    }

    storage = MagicMock()
    storage.get_flow_instance = AsyncMock(
        side_effect=[
            {
                **_instance(active_node_ids=["analysis"], status="RUNNING"),
                "context_json": {"last_safe_node_id": "start"},
            },
            escalated_instance,
        ]
    )
    storage.get_flow = AsyncMock(return_value=runtime_flow)
    storage.list_flow_node_executions = AsyncMock(
        return_value=[{"id": 50, "node_id": "analysis", "status": "RUNNING"}]
    )
    storage.update_flow_node_execution = AsyncMock(return_value={"id": 50})
    storage.update_flow_instance = AsyncMock(return_value=escalated_instance)
    storage.escalate_flow_instance = AsyncMock(return_value=escalated_instance)
    storage.get_project = AsyncMock(return_value={"id": PROJECT_ID, "state": "IN_PROGRESS"})
    storage.transition_project = AsyncMock(return_value={"id": PROJECT_ID, "state": "IN_PROGRESS"})
    _patch_state(storage)

    response = await client.post(
        f"/flows/instances/{FLOW_INSTANCE_ID}/node-action",
        json={"node_id": "analysis", "action": "timeout", "error": "Analysis timed out"},
    )

    assert response.status_code == 200
    assert response.json()["escalated_to"] == "exec_ceo"
    storage.escalate_flow_instance.assert_awaited_once_with(
        FLOW_INSTANCE_ID, "exec_ceo", "Analysis timed out"
    )
    _, kwargs = storage.transition_project.await_args
    assert kwargs["event"] == "flow_node_escalated"
    assert kwargs["payload"]["escalated_to"] == "exec_ceo"
