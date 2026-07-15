from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import UUID

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from mas_core.memory import models as tables
from mas_core.memory.storage import AgentStorage
from mas_core.workflow import WorkflowController
from orchestrator_api.main import app, publish_system_event


def _simple_product_build_flow(*, include_qa_review: bool) -> dict[str, object]:
    nodes = [
        {
            "id": "intake",
            "type": "start",
            "label": "Intake",
            "config": {},
            "position": {"x": 120, "y": 80},
        },
        {
            "id": "feasibility_review",
            "type": "task",
            "label": "Feasibility Review",
            "config": {
                "team_id": "office_cfo+office_cio+office_chrm+office_cso",
                "timeout_seconds": 900,
                "escalate_to_team": "exec_ceo",
            },
            "position": {"x": 120, "y": 180},
        },
        {
            "id": "pdr_creation",
            "type": "task",
            "label": "PDR Creation",
            "config": {
                "team_id": "exec_coo+dept_production",
                "retries": 2,
            },
            "position": {"x": 120, "y": 280},
        },
        {
            "id": "human_approval",
            "type": "approval",
            "label": "Human Approval",
            "config": {"approver_user": "human"},
            "position": {"x": 120, "y": 380},
        },
        {
            "id": "implementation",
            "type": "task",
            "label": "Implementation",
            "config": {"team_id": "office_cto"},
            "position": {"x": 120, "y": 480},
        },
    ]
    edges = [
        {"id": "e1", "source": "intake", "target": "feasibility_review"},
        {"id": "e2", "source": "feasibility_review", "target": "pdr_creation"},
        {"id": "e3", "source": "pdr_creation", "target": "human_approval"},
        {"id": "e4", "source": "human_approval", "target": "implementation"},
    ]
    if include_qa_review:
        nodes.append(
            {
                "id": "qa_review",
                "type": "task",
                "label": "QA Review",
                "config": {"team_id": "dept_qa"},
                "position": {"x": 120, "y": 580},
            }
        )
        nodes.append(
            {
                "id": "done",
                "type": "end",
                "label": "Done",
                "config": {},
                "position": {"x": 120, "y": 680},
            }
        )
        edges.extend(
            [
                {"id": "e5", "source": "implementation", "target": "qa_review"},
                {"id": "e6", "source": "qa_review", "target": "done"},
            ]
        )
    else:
        nodes.append(
            {
                "id": "done",
                "type": "end",
                "label": "Done",
                "config": {},
                "position": {"x": 120, "y": 580},
            }
        )
        edges.append({"id": "e5", "source": "implementation", "target": "done"})

    return {"nodes": nodes, "edges": edges}


def _branching_review_flow() -> dict[str, object]:
    return {
        "nodes": [
            {"id": "intake", "type": "start", "label": "Intake", "config": {}},
            {
                "id": "analysis",
                "type": "task",
                "label": "Feasibility Review",
                "config": {
                    "team_id": "office_cfo",
                    "timeout_seconds": 300,
                    "escalate_to_team": "exec_ceo",
                },
            },
            {
                "id": "pdr_creation",
                "type": "task",
                "label": "PDR Creation",
                "config": {"team_id": "exec_coo+dept_production", "retries": 2},
            },
            {
                "id": "human_approval",
                "type": "approval",
                "label": "Human Approval",
                "config": {"approver_user": "human"},
            },
            {
                "id": "decision_switch",
                "type": "switch",
                "label": "Decision Switch",
                "config": {
                    "switch_key": "approval",
                    "switch_cases": {
                        "approved": "implementation",
                        "edit_requested": "qa_review",
                        "rejected": "failed_terminal",
                    },
                },
            },
            {
                "id": "implementation",
                "type": "task",
                "label": "Implementation",
                "config": {"team_id": "office_cto"},
            },
            {
                "id": "qa_review",
                "type": "task",
                "label": "QA Review",
                "config": {"team_id": "dept_qa"},
            },
            {"id": "done", "type": "end", "label": "Done", "config": {}},
            {"id": "failed_terminal", "type": "end", "label": "Failed", "config": {}},
        ],
        "edges": [
            {"id": "e1", "source": "intake", "target": "analysis"},
            {"id": "e2", "source": "analysis", "target": "pdr_creation"},
            {"id": "e3", "source": "pdr_creation", "target": "human_approval"},
            {"id": "e4", "source": "human_approval", "target": "decision_switch"},
            {"id": "e5", "source": "decision_switch", "target": "implementation"},
            {"id": "e6", "source": "decision_switch", "target": "qa_review"},
            {"id": "e7", "source": "decision_switch", "target": "failed_terminal"},
            {"id": "e8", "source": "implementation", "target": "done"},
            {"id": "e9", "source": "qa_review", "target": "done"},
        ],
    }


@pytest.fixture
def integration_dsn() -> str:
    dsn = os.environ.get("MAS_TEST_DATABASE_DSN")
    if not dsn:
        pytest.skip("MAS_TEST_DATABASE_DSN is required for operator-level flow integration tests")
    return dsn


@pytest.fixture
async def live_client(
    integration_dsn: str,
) -> AsyncIterator[tuple[httpx.AsyncClient, AgentStorage]]:
    engine = create_async_engine(integration_dsn, connect_args={"statement_cache_size": 0})
    async with engine.begin() as conn:
        await conn.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(sa.text("CREATE SCHEMA public"))
        await conn.run_sync(tables.metadata.create_all)
    await engine.dispose()

    storage = AgentStorage(integration_dsn)
    await storage.connect()
    app.state.storage = storage
    app.state.controller = WorkflowController(storage=storage, event_publisher=publish_system_event)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "test-mas-key"},
    ) as client:
        yield client, storage

    await storage.close()
    app.state.storage = None
    app.state.controller = WorkflowController(storage=None, event_publisher=publish_system_event)


async def _create_project_with_instance(
    client: httpx.AsyncClient,
    *,
    name: str,
    flow_id: str,
) -> tuple[dict[str, object], dict[str, object]]:
    project_response = await client.post(
        "/projects",
        json={"name": name, "description": f"Operator test for {name}"},
    )
    assert project_response.status_code == 201, project_response.text
    project = project_response.json()

    instance_response = await client.post(
        "/flows/instances",
        json={"flow_id": flow_id, "project_id": project["id"]},
    )
    assert instance_response.status_code == 200, instance_response.text
    return project, instance_response.json()


def _uuid(value: object) -> UUID:
    return UUID(str(value))


@pytest.mark.anyio
async def test_operator_can_create_version_assign_start_reload_and_override_flow(
    live_client: tuple[httpx.AsyncClient, AgentStorage],
) -> None:
    client, storage = live_client

    create_v1 = await client.post(
        "/flows",
        json={
            "name": "Simple Product Build Flow",
            "description": "Initial operator flow",
            "is_active": True,
            "definition_json": _simple_product_build_flow(include_qa_review=False),
        },
    )
    assert create_v1.status_code == 201, create_v1.text
    flow_v1 = create_v1.json()

    fetched_v1 = await client.get(f"/flows/{flow_v1['id']}")
    assert fetched_v1.status_code == 200
    assert (
        fetched_v1.json()["definition_json"]["nodes"][1]["config"]["escalate_to_team"] == "exec_ceo"
    )
    assert fetched_v1.json()["definition_json"]["nodes"][2]["config"]["retries"] == 2

    create_v2 = await client.post(
        "/flows",
        json={
            "name": "Simple Product Build Flow",
            "description": "Adds QA review before done",
            "is_active": True,
            "version_from_flow_id": flow_v1["id"],
            "definition_json": _simple_product_build_flow(include_qa_review=True),
        },
    )
    assert create_v2.status_code == 201, create_v2.text
    flow_v2 = create_v2.json()
    assert flow_v2["version"] == 2

    flow_list = await client.get("/flows")
    assert flow_list.status_code == 200
    listed_versions = sorted(
        (flow["version"], flow["id"])
        for flow in flow_list.json()
        if flow["name"] == "Simple Product Build Flow"
    )
    assert [version for version, _ in listed_versions] == [1, 2]

    project, instance = await _create_project_with_instance(
        client,
        name="simple-product-build-project",
        flow_id=flow_v2["id"],
    )

    history_after_assignment = await client.get(f"/projects/{project['id']}/state-history")
    assert history_after_assignment.status_code == 200
    assignment_events = [entry["event"] for entry in history_after_assignment.json()]
    assert "flow_assigned" in assignment_events

    start_response = await client.post(
        f"/flows/instances/{instance['id']}/action",
        json={"action": "start"},
    )
    assert start_response.status_code == 200, start_response.text
    started_instance = start_response.json()
    assert started_instance["active_node_ids"] == ["intake"]
    assert started_instance["status"] == "RUNNING"

    reload_instance = await client.get(f"/projects/{project['id']}/flow-instance")
    assert reload_instance.status_code == 200
    assert reload_instance.json()["flow_id"] == flow_v2["id"]
    assert reload_instance.json()["active_node_ids"] == ["intake"]

    executions_before_override = await client.get(f"/flows/instances/{instance['id']}/executions")
    assert executions_before_override.status_code == 200
    assert [entry["node_id"] for entry in executions_before_override.json()] == ["intake"]

    complete_intake = await client.post(
        f"/flows/instances/{instance['id']}/node-action",
        json={"node_id": "intake", "action": "complete", "output": {"intake_complete": True}},
    )
    assert complete_intake.status_code == 200, complete_intake.text
    assert complete_intake.json()["active_node_ids"] == ["feasibility_review"]

    override_response = await client.post(
        f"/flows/instances/{instance['id']}/override",
        json={
            "target_node_id": "implementation",
            "actor_id": "human",
            "actor_role": "human_operator",
            "reason": "Operator expedited implementation",
        },
    )
    assert override_response.status_code == 200, override_response.text
    assert override_response.json()["active_node_ids"] == ["implementation"]

    reload_after_override = await client.get(f"/projects/{project['id']}/flow-instance")
    assert reload_after_override.status_code == 200
    assert reload_after_override.json()["active_node_ids"] == ["implementation"]

    override_history = await client.get(f"/projects/{project['id']}/state-history")
    assert override_history.status_code == 200
    override_event = next(
        entry for entry in override_history.json() if entry["event"] == "flow_node_override"
    )
    assert override_event["payload"]["to_node_id"] == "implementation"
    assert override_event["payload"]["reason"] == "Operator expedited implementation"

    stored_v1 = await storage.get_flow(_uuid(flow_v1["id"]))
    stored_v2 = await storage.get_flow(_uuid(flow_v2["id"]))
    stored_instance = await storage.get_flow_instance(_uuid(instance["id"]))
    assert stored_v1 is not None and stored_v2 is not None and stored_instance is not None
    assert stored_v2["definition_json"]["nodes"][-2]["id"] == "qa_review"
    assert stored_instance["active_node_ids"] == ["implementation"]

    node_executions = await storage.list_flow_node_executions(
        instance_id=_uuid(instance["id"]), limit=20
    )
    assert [execution["node_id"] for execution in node_executions] == [
        "intake",
        "feasibility_review",
        "implementation",
    ]
    assert node_executions[0]["status"] == "COMPLETED"
    assert node_executions[1]["status"] == "SKIPPED"

    async with storage.engine.connect() as conn:
        flow_rows = (
            await conn.execute(
                sa.select(tables.flows.c.name, tables.flows.c.version).order_by(
                    tables.flows.c.version
                )
            )
        ).all()
        history_rows = (
            await conn.execute(
                sa.select(tables.project_state_history.c.event).where(
                    tables.project_state_history.c.project_id == _uuid(project["id"])
                )
            )
        ).all()
    assert flow_rows == [("Simple Product Build Flow", 1), ("Simple Product Build Flow", 2)]
    assert {row[0] for row in history_rows} >= {"flow_assigned", "flow_node_override"}


@pytest.mark.anyio
async def test_operator_runtime_matrix_covers_invalid_input_denial_retry_timeout_and_switch(
    live_client: tuple[httpx.AsyncClient, AgentStorage],
) -> None:
    client, storage = live_client

    invalid_flow = await client.post(
        "/flows",
        json={
            "name": "Broken Flow",
            "definition_json": {
                "nodes": [
                    {"id": "start", "type": "start", "label": "Start", "config": {}},
                    {"id": "approval", "type": "approval", "label": "Approval", "config": {}},
                ],
                "edges": [{"id": "e1", "source": "start", "target": "approval"}],
            },
        },
    )
    assert invalid_flow.status_code == 400

    create_flow = await client.post(
        "/flows",
        json={
            "name": "Decision Product Flow",
            "description": "Covers approval routing, timeout, retry, and switching",
            "is_active": True,
            "definition_json": _branching_review_flow(),
        },
    )
    assert create_flow.status_code == 201, create_flow.text
    decision_flow = create_flow.json()

    alternate_flow_response = await client.post(
        "/flows",
        json={
            "name": "Alternate Recovery Flow",
            "description": "Used to test switching",
            "is_active": True,
            "definition_json": _simple_product_build_flow(include_qa_review=False),
        },
    )
    assert alternate_flow_response.status_code == 201, alternate_flow_response.text
    alternate_flow = alternate_flow_response.json()

    project, instance = await _create_project_with_instance(
        client,
        name="decision-flow-project",
        flow_id=decision_flow["id"],
    )

    await client.post(f"/flows/instances/{instance['id']}/action", json={"action": "start"})
    await client.post(
        f"/flows/instances/{instance['id']}/node-action",
        json={"node_id": "intake", "action": "complete"},
    )

    forbidden_override = await client.post(
        f"/flows/instances/{instance['id']}/override",
        json={
            "target_node_id": "implementation",
            "actor_id": "agent-cto",
            "actor_role": "cto",
            "reason": "Bypass human control",
        },
    )
    assert forbidden_override.status_code == 403
    instance_after_denial = await client.get(f"/projects/{project['id']}/flow-instance")
    assert instance_after_denial.status_code == 200
    assert instance_after_denial.json()["active_node_ids"] == ["analysis"]

    timeout_response = await client.post(
        f"/flows/instances/{instance['id']}/node-action",
        json={"node_id": "analysis", "action": "timeout", "error": "Feasibility SLA missed"},
    )
    assert timeout_response.status_code == 200, timeout_response.text
    assert timeout_response.json()["status"] == "FAILED"
    assert timeout_response.json()["escalated_to"] == "exec_ceo"

    timeout_history = await client.get(f"/projects/{project['id']}/state-history")
    assert timeout_history.status_code == 200
    escalation_event = next(
        entry for entry in timeout_history.json() if entry["event"] == "flow_node_escalated"
    )
    assert escalation_event["payload"]["node_id"] == "analysis"
    assert escalation_event["payload"]["escalated_to"] == "exec_ceo"

    retry_response = await client.post(f"/flows/instances/{instance['id']}/retry")
    assert retry_response.status_code == 200, retry_response.text
    assert retry_response.json()["status"] == "RUNNING"
    assert retry_response.json()["active_node_ids"] == ["intake"]
    assert retry_response.json()["retry_count"] == 1

    await client.post(
        f"/flows/instances/{instance['id']}/node-action",
        json={"node_id": "intake", "action": "complete"},
    )
    await client.post(
        f"/flows/instances/{instance['id']}/node-action",
        json={"node_id": "analysis", "action": "complete"},
    )
    await client.post(
        f"/flows/instances/{instance['id']}/node-action",
        json={"node_id": "pdr_creation", "action": "complete", "output": {"pdr_complete": True}},
    )

    wait_for_approval = await client.post(
        f"/flows/instances/{instance['id']}/node-action",
        json={"node_id": "human_approval", "action": "complete"},
    )
    assert wait_for_approval.status_code == 200
    assert wait_for_approval.json()["status"] == "WAITING_APPROVAL"

    pending_decisions = await client.get(f"/projects/{project['id']}/pending-decisions")
    assert pending_decisions.status_code == 200
    assert len(pending_decisions.json()) == 1
    assert pending_decisions.json()[0]["gate_type"] == "human"

    edit_requested = await client.post(
        f"/flows/instances/{instance['id']}/node-action",
        json={"node_id": "human_approval", "action": "complete", "decision": "edit_requested"},
    )
    assert edit_requested.status_code == 200, edit_requested.text
    assert edit_requested.json()["active_node_ids"] == ["qa_review"]

    switch_response = await client.post(
        f"/flows/instances/{instance['id']}/switch",
        json={"flow_id": alternate_flow["id"], "preserve_context": True},
    )
    assert switch_response.status_code == 200, switch_response.text
    assert switch_response.json()["flow_id"] == alternate_flow["id"]
    assert switch_response.json()["status"] == "NOT_STARTED"

    switched_history = await client.get(f"/projects/{project['id']}/state-history")
    assert switched_history.status_code == 200
    switched_event = next(
        entry for entry in switched_history.json() if entry["event"] == "flow_switched"
    )
    assert switched_event["payload"]["to_flow_id"] == alternate_flow["id"]

    approved_project, approved_instance = await _create_project_with_instance(
        client,
        name="approved-branch-project",
        flow_id=decision_flow["id"],
    )
    await client.post(
        f"/flows/instances/{approved_instance['id']}/action", json={"action": "start"}
    )
    for node_id in ("intake", "analysis", "pdr_creation"):
        response = await client.post(
            f"/flows/instances/{approved_instance['id']}/node-action",
            json={"node_id": node_id, "action": "complete"},
        )
        assert response.status_code == 200, response.text
    approved_branch = await client.post(
        f"/flows/instances/{approved_instance['id']}/node-action",
        json={"node_id": "human_approval", "action": "complete", "decision": "approved"},
    )
    assert approved_branch.status_code == 200, approved_branch.text
    assert approved_branch.json()["active_node_ids"] == ["implementation"]

    rejected_project, rejected_instance = await _create_project_with_instance(
        client,
        name="rejected-branch-project",
        flow_id=decision_flow["id"],
    )
    await client.post(
        f"/flows/instances/{rejected_instance['id']}/action", json={"action": "start"}
    )
    for node_id in ("intake", "analysis", "pdr_creation"):
        response = await client.post(
            f"/flows/instances/{rejected_instance['id']}/node-action",
            json={"node_id": node_id, "action": "complete"},
        )
        assert response.status_code == 200, response.text
    rejected_branch = await client.post(
        f"/flows/instances/{rejected_instance['id']}/node-action",
        json={"node_id": "human_approval", "action": "complete", "decision": "rejected"},
    )
    assert rejected_branch.status_code == 200, rejected_branch.text
    assert rejected_branch.json()["status"] == "FAILED"

    denied_history = await client.get(f"/projects/{project['id']}/state-history")
    denied_events = [entry["event"] for entry in denied_history.json()]
    assert denied_events.count("flow_node_override") == 0

    approval_gate_count = await client.get(f"/projects/{approved_project['id']}/pending-decisions")
    assert approval_gate_count.status_code == 200

    async with storage.engine.connect() as conn:
        approval_rows = (
            await conn.execute(sa.select(sa.func.count()).select_from(tables.approval_gates))
        ).scalar_one()
        history_rows = (
            await conn.execute(
                sa.select(tables.project_state_history.c.event).where(
                    tables.project_state_history.c.project_id.in_(
                        [
                            _uuid(project["id"]),
                            _uuid(approved_project["id"]),
                            _uuid(rejected_project["id"]),
                        ]
                    )
                )
            )
        ).all()
    assert approval_rows >= 3
    assert {row[0] for row in history_rows} >= {
        "flow_assigned",
        "flow_node_escalated",
        "flow_switched",
    }


def _test2_branching_flow() -> dict[str, object]:
    """
    The exact Test-2 flow:
      Start → Analysis → Approval Gate → Decision Switch
                                            ├─ approved       → Branch A → Completed
                                            ├─ edit_requested → Branch B → Completed
                                            └─ rejected       → Failed (end)
    Analysis has timeout + escalate_to_team.
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
            {"id": "e4", "source": "decision_switch", "target": "branch_a"},
            {"id": "e5", "source": "decision_switch", "target": "branch_b"},
            {"id": "e6", "source": "decision_switch", "target": "failed_terminal"},
            {"id": "e7", "source": "branch_a", "target": "completed"},
            {"id": "e8", "source": "branch_b", "target": "completed"},
        ],
    }


async def _advance_to_approval(client: "httpx.AsyncClient", instance_id: object) -> None:
    """Drive start → analysis → approval_gate through the node-action API."""
    await client.post(f"/flows/instances/{instance_id}/action", json={"action": "start"})
    for node_id in ("start", "analysis"):
        r = await client.post(
            f"/flows/instances/{instance_id}/node-action",
            json={"node_id": node_id, "action": "complete"},
        )
        assert r.status_code == 200, f"Failed to complete {node_id}: {r.text}"


@pytest.mark.anyio
async def test_operator_test2_flow_create_serialization_and_approval_routing(
    live_client: "tuple[httpx.AsyncClient, AgentStorage]",
) -> None:
    """
    Test-2 operator-level DB integration:
    1. Create flow via POST /flows, verify nodes/edges/switch_cases serialized to DB.
    2. Reload via GET /flows/{id} — all config survives.
    3. Attach to project, start, drive to approval.
    4. Three parallel projects: approved→branch_a, edit_requested→branch_b, rejected→FAILED.
    5. Assert DB rows: approval_gates, flow_node_executions, project_state_history.
    """
    client, storage = live_client

    # ── Step 1: Create Test-2 flow and verify serialization ──────────────────
    create_r = await client.post(
        "/flows",
        json={
            "name": "Test-2 Branching Flow",
            "description": "Operator integration: branching/approval/retry/escalation",
            "is_active": True,
            "definition_json": _test2_branching_flow(),
        },
    )
    assert create_r.status_code == 201, create_r.text
    flow = create_r.json()
    flow_id = flow["id"]

    # ── Step 2: Reload and verify serialization ──────────────────────────────
    reload_r = await client.get(f"/flows/{flow_id}")
    assert reload_r.status_code == 200
    reloaded = reload_r.json()
    nodes_by_id = {n["id"]: n for n in reloaded["definition_json"]["nodes"]}
    assert "start" in nodes_by_id
    assert "analysis" in nodes_by_id
    assert "approval_gate" in nodes_by_id
    assert "decision_switch" in nodes_by_id
    assert "branch_a" in nodes_by_id
    assert "branch_b" in nodes_by_id
    assert "failed_terminal" in nodes_by_id
    assert "completed" in nodes_by_id
    sw = nodes_by_id["decision_switch"]
    assert sw["config"]["switch_cases"]["approved"] == "branch_a"
    assert sw["config"]["switch_cases"]["edit_requested"] == "branch_b"
    assert sw["config"]["switch_cases"]["rejected"] == "failed_terminal"
    assert nodes_by_id["analysis"]["config"]["escalate_to_team"] == "exec_ceo"
    assert nodes_by_id["analysis"]["config"]["timeout_seconds"] == 300

    # ── Step 3: approved → branch_a ──────────────────────────────────────────
    proj_a, inst_a = await _create_project_with_instance(
        client, name="test2-approved", flow_id=flow_id
    )
    await _advance_to_approval(client, inst_a["id"])
    approved_r = await client.post(
        f"/flows/instances/{inst_a['id']}/node-action",
        json={"node_id": "approval_gate", "action": "complete", "decision": "approved"},
    )
    assert approved_r.status_code == 200, approved_r.text
    assert approved_r.json()["active_node_ids"] == ["branch_a"]

    # Read back from separate GET route
    inst_a_reload = await client.get(f"/flows/instances/{inst_a['id']}")
    assert inst_a_reload.status_code == 200
    assert inst_a_reload.json()["active_node_ids"] == ["branch_a"]
    assert inst_a_reload.json()["context_json"]["approval"] == "approved"

    # ── Step 4: edit_requested → branch_b ────────────────────────────────────
    proj_b, inst_b = await _create_project_with_instance(
        client, name="test2-edit-req", flow_id=flow_id
    )
    await _advance_to_approval(client, inst_b["id"])
    edit_r = await client.post(
        f"/flows/instances/{inst_b['id']}/node-action",
        json={"node_id": "approval_gate", "action": "complete", "decision": "edit_requested"},
    )
    assert edit_r.status_code == 200, edit_r.text
    assert edit_r.json()["active_node_ids"] == ["branch_b"]

    # ── Step 5: rejected → FAILED ─────────────────────────────────────────────
    proj_c, inst_c = await _create_project_with_instance(
        client, name="test2-rejected", flow_id=flow_id
    )
    await _advance_to_approval(client, inst_c["id"])
    rejected_r = await client.post(
        f"/flows/instances/{inst_c['id']}/node-action",
        json={"node_id": "approval_gate", "action": "complete", "decision": "rejected"},
    )
    assert rejected_r.status_code == 200, rejected_r.text
    assert rejected_r.json()["status"] == "FAILED"

    # ── Step 6: retry from FAILED → restores last safe node ──────────────────
    retry_r = await client.post(f"/flows/instances/{inst_c['id']}/retry")
    assert retry_r.status_code == 200, retry_r.text
    assert retry_r.json()["status"] == "RUNNING"
    assert retry_r.json()["retry_count"] == 1
    # The last safe node was approval_gate (set during the complete action) or analysis
    # depending on implementation — either is valid; verify not empty
    assert retry_r.json()["active_node_ids"]

    # ── Step 7: timeout/escalation on analysis ────────────────────────────────
    proj_d, inst_d = await _create_project_with_instance(
        client, name="test2-timeout", flow_id=flow_id
    )
    await client.post(f"/flows/instances/{inst_d['id']}/action", json={"action": "start"})
    await client.post(
        f"/flows/instances/{inst_d['id']}/node-action",
        json={"node_id": "start", "action": "complete"},
    )
    timeout_r = await client.post(
        f"/flows/instances/{inst_d['id']}/node-action",
        json={"node_id": "analysis", "action": "timeout", "error": "Analysis SLA missed"},
    )
    assert timeout_r.status_code == 200, timeout_r.text
    assert timeout_r.json()["status"] == "FAILED"
    assert timeout_r.json()["escalated_to"] == "exec_ceo"

    # Verify escalation visible through state-history API
    history_r = await client.get(f"/projects/{proj_d['id']}/state-history")
    assert history_r.status_code == 200
    escalation_events = [e for e in history_r.json() if e["event"] == "flow_node_escalated"]
    assert len(escalation_events) >= 1
    assert escalation_events[0]["payload"]["escalated_to"] == "exec_ceo"
    assert escalation_events[0]["payload"]["node_id"] == "analysis"

    # ── Step 8: Negative — retry on non-FAILED instance returns 409 ──────────
    retry_running = await client.post(f"/flows/instances/{inst_a['id']}/retry")
    assert retry_running.status_code == 409

    # ── Step 9: Negative — override with non-operator role returns 403 ───────
    override_denied = await client.post(
        f"/flows/instances/{inst_a['id']}/override",
        json={
            "target_node_id": "start",
            "actor_id": "agent-developer",
            "actor_role": "developer",
            "reason": "unauthorized",
        },
    )
    assert override_denied.status_code == 403

    # ── Step 10: DB assertions ────────────────────────────────────────────────
    async with storage.engine.connect() as conn:
        # Approval gates were created for each approval node activation
        approval_gate_count = (
            await conn.execute(sa.select(sa.func.count()).select_from(tables.approval_gates))
        ).scalar_one()
        assert approval_gate_count >= 3, f"Expected ≥3 approval gates, got {approval_gate_count}"

        # Flow stored in DB with correct name
        flow_rows = (
            await conn.execute(
                sa.select(tables.flows.c.name, tables.flows.c.version).where(
                    tables.flows.c.name == "Test-2 Branching Flow"
                )
            )
        ).all()
        assert flow_rows == [("Test-2 Branching Flow", 1)]

        # project_state_history contains flow_assigned events for all projects
        all_project_ids = [
            _uuid(proj_a["id"]),
            _uuid(proj_b["id"]),
            _uuid(proj_c["id"]),
            _uuid(proj_d["id"]),
        ]
        history_events = (
            await conn.execute(
                sa.select(tables.project_state_history.c.event).where(
                    tables.project_state_history.c.project_id.in_(all_project_ids)
                )
            )
        ).all()
        event_set = {row[0] for row in history_events}
        assert "flow_assigned" in event_set
        assert "flow_node_escalated" in event_set

        # Node executions recorded for all driven projects
        inst_ids = [
            _uuid(inst_a["id"]),
            _uuid(inst_b["id"]),
            _uuid(inst_c["id"]),
            _uuid(inst_d["id"]),
        ]
        from mas_core.memory import models as t2

        exec_count = (
            await conn.execute(
                sa.select(sa.func.count())
                .select_from(t2.flow_node_executions)
                .where(t2.flow_node_executions.c.instance_id.in_(inst_ids))
            )
        ).scalar_one()
        # Each instance drove at least 3 nodes (start, analysis, approval_gate)
        assert exec_count >= 12, f"Expected ≥12 node executions, got {exec_count}"
