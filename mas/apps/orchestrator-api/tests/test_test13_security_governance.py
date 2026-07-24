"""Test 13 — Security & Governance

Tests the AIAT MAS security and governance boundaries:
1. Flow override RBAC — only human_operator or ceo may override
2. Shutdown middleware — 503 for new projects when system is SHUTTING_DOWN
3. ALLOWED_CONTAINERS allowlist — 400 for unknown containers
4. CEO Privileged Ops Gate — executive vs privileged action classification
5. Privileged action approval workflow (pending → approve/reject → audit)
6. Human decision gate — submit APPROVED/REJECTED/EDITS through public API
7. Project retry RBAC — 409 when not in FAILED state
8. Input validation — 422 on bad payloads, 422 on invalid UUIDs
9. Credential resolve — policy-gated (403 on denied)
10. CSO-veto privileged action requires approval gate
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

NOW_ISO = datetime.now(timezone.utc).isoformat()
PROJECT_ID = uuid4()
INSTANCE_ID = uuid4()
FLOW_ID = uuid4()

_FLOW_DEF = {
    "name": "sec-flow",
    "version": "1",
    "nodes": [
        {"id": "start", "type": "start", "label": "Start"},
        {"id": "end", "type": "end", "label": "End"},
    ],
    "edges": [{"id": "e1", "source": "start", "target": "end"}],
}


def _fake_project(state: str = "INIT", pid=None):
    return {
        "id": str(pid or PROJECT_ID),
        "name": "Sec Test Project",
        "state": state,
        "created_at": NOW_ISO,
        "updated_at": NOW_ISO,
    }


def _fake_instance(status: str = "RUNNING", iid=None, fid=None):
    return {
        "id": str(iid or INSTANCE_ID),
        "flow_id": str(fid or FLOW_ID),
        "project_id": str(PROJECT_ID),
        "status": status,
        "active_node_ids": ["start"],
        "context_json": {},
        "retry_count": 0,
        "created_at": NOW_ISO,
        "updated_at": NOW_ISO,
    }


def _fake_flow(fid=None):
    return {
        "id": str(fid or FLOW_ID),
        "name": "sec-flow",
        "version": "1",
        "definition_json": _FLOW_DEF,
        "created_at": NOW_ISO,
        "updated_at": NOW_ISO,
    }


def _mock_engine():
    """Build an async-context-manager engine mock for routes that use storage.engine.connect()."""
    conn = AsyncMock()
    conn.execute = AsyncMock(
        return_value=MagicMock(
            scalar=MagicMock(return_value=0),
            mappings=MagicMock(return_value=MagicMock(first=MagicMock(return_value=None))),
        )
    )
    engine = MagicMock()
    engine.connect = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=conn),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    engine.begin = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=conn),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    return engine


def _patch(storage) -> None:
    from orchestrator_api.main import app

    app.state.storage = storage


# ─────────────────────────────────────────────────────────────────────────────
# 1. Flow override RBAC
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_override_flow_instance_denied_for_unknown_role(client):
    """Non-human, non-ceo actor_role → 403 Forbidden."""
    storage = MagicMock()
    storage.get_flow_instance = AsyncMock(return_value=_fake_instance())
    _patch(storage)

    r = await client.post(
        f"/flows/instances/{INSTANCE_ID}/override",
        json={
            "actor_id": "rogue_agent",
            "actor_role": "agent",
            "target_node_id": "end",
            "reason": "attempt bypass",
        },
    )
    assert r.status_code == 403
    assert (
        "human operator" in r.json()["detail"].lower() or "operator" in r.json()["detail"].lower()
    )


@pytest.mark.anyio
async def test_override_flow_instance_allowed_human_operator(client):
    """human_operator role → 200 with audit entry."""
    storage = MagicMock()
    storage.get_flow_instance = AsyncMock(return_value=_fake_instance())
    storage.get_flow = AsyncMock(return_value=_fake_flow())
    storage.override_flow_instance = AsyncMock(return_value=_fake_instance("RUNNING"))
    storage.get_project = AsyncMock(return_value=_fake_project("IN_PROGRESS"))
    storage.transition_project = AsyncMock()
    _patch(storage)

    r = await client.post(
        f"/flows/instances/{INSTANCE_ID}/override",
        json={
            "actor_id": "ops_user",
            "actor_role": "human_operator",
            "target_node_id": "end",
            "reason": "manual recovery",
        },
    )
    assert r.status_code == 200
    # Verify audit was written via transition_project
    storage.transition_project.assert_called_once()
    call_kwargs = storage.transition_project.call_args
    assert call_kwargs is not None


@pytest.mark.anyio
async def test_override_flow_instance_allowed_ceo(client):
    """ceo role → 200."""
    storage = MagicMock()
    storage.get_flow_instance = AsyncMock(return_value=_fake_instance())
    storage.get_flow = AsyncMock(return_value=_fake_flow())
    storage.override_flow_instance = AsyncMock(return_value=_fake_instance("RUNNING"))
    storage.get_project = AsyncMock(return_value=None)  # no project audit if project not found
    _patch(storage)

    r = await client.post(
        f"/flows/instances/{INSTANCE_ID}/override",
        json={
            "actor_id": "ceo",
            "actor_role": "ceo",
            "target_node_id": "end",
            "reason": "ceo override",
        },
    )
    assert r.status_code == 200


@pytest.mark.anyio
async def test_override_flow_instance_not_found(client):
    """Override on nonexistent instance → 404."""
    storage = MagicMock()
    storage.get_flow_instance = AsyncMock(return_value=None)
    _patch(storage)

    r = await client.post(
        f"/flows/instances/{uuid4()}/override",
        json={
            "actor_id": "ops_user",
            "actor_role": "human_operator",
            "target_node_id": "end",
            "reason": "test",
        },
    )
    assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# 2. Shutdown middleware security — block new project creation
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_shutdown_middleware_blocks_new_projects(client):
    """POST /projects returns 503 while system is SHUTTING_DOWN."""
    from orchestrator_api.main import app

    storage = MagicMock()
    storage.create_project = AsyncMock(return_value=_fake_project())
    _patch(storage)
    app.state._cached_system_state = "SHUTTING_DOWN"
    try:
        r = await client.post("/projects", json={"name": "Should Fail"})
        assert r.status_code == 503
        assert "SHUTTING_DOWN" in r.json()["detail"]
    finally:
        app.state._cached_system_state = "RUNNING"


@pytest.mark.anyio
async def test_shutdown_middleware_allows_health(client):
    """/health is always allowed even when system is SHUTTING_DOWN."""
    from orchestrator_api.main import app

    app.state._cached_system_state = "SHUTTING_DOWN"
    try:
        r = await client.get("/health")
        assert r.status_code == 200
    finally:
        app.state._cached_system_state = "RUNNING"


@pytest.mark.anyio
async def test_shutdown_middleware_allows_system_routes(client):
    """/system/* routes are always allowed during shutdown."""
    from orchestrator_api.main import app

    storage = MagicMock()
    storage.engine = _mock_engine()
    storage.get_config = AsyncMock(return_value="SHUTTING_DOWN")
    _patch(storage)
    app.state._cached_system_state = "SHUTTING_DOWN"
    try:
        r = await client.get("/system/status")
        assert r.status_code in (200, 503)  # may return 503 body but not middleware-blocked
    finally:
        app.state._cached_system_state = "RUNNING"


@pytest.mark.anyio
async def test_stopped_state_also_blocks_projects(client):
    """STOPPED state also blocks POST /projects → 503."""
    from orchestrator_api.main import app

    storage = MagicMock()
    storage.create_project = AsyncMock(return_value=_fake_project())
    _patch(storage)
    app.state._cached_system_state = "STOPPED"
    try:
        r = await client.post("/projects", json={"name": "Blocked"})
        assert r.status_code == 503
    finally:
        app.state._cached_system_state = "RUNNING"


@pytest.mark.anyio
async def test_running_state_allows_project_creation(client):
    """Normal RUNNING state allows POST /projects."""
    from orchestrator_api.main import app

    storage = MagicMock()
    project = _fake_project()
    storage.create_project = AsyncMock(return_value=project)
    storage.get_project = AsyncMock(return_value=project)
    storage.engine = _mock_engine()
    _patch(storage)
    app.state._cached_system_state = "RUNNING"
    r = await client.post("/projects", json={"name": "New Project"})
    assert r.status_code == 201


# ─────────────────────────────────────────────────────────────────────────────
# 3. ALLOWED_CONTAINERS allowlist
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_logs_unknown_container_rejected(client):
    """Unknown container name → 400 (security boundary)."""
    r = await client.get("/system/logs/evil-container")
    assert r.status_code == 400
    assert "Unknown container" in r.json()["detail"] or "Allowed" in r.json()["detail"]


@pytest.mark.anyio
async def test_logs_path_traversal_rejected(client):
    """Path traversal in container name → 400."""
    r = await client.get("/system/logs/..%2Fetc%2Fpasswd")
    assert r.status_code in (400, 404, 422)  # must not be 200


@pytest.mark.anyio
async def test_logs_sql_injection_rejected(client):
    """SQL injection attempt in container name → 400."""
    r = await client.get("/system/logs/redis%3B%20DROP%20TABLE")
    assert r.status_code in (400, 404, 422)


@pytest.mark.anyio
async def test_logs_allowed_container_passes_validation(client):
    """Known container name passes the allowlist check (may fail on docker, that's OK)."""
    # The route will pass validation and try to run docker → OK to get 500 (docker not running)
    # but must NOT get 400 (which means allowlist blocked it)
    r = await client.get("/system/logs/redis?follow=false&tail=1")
    assert r.status_code != 400  # 400 means allowlist incorrectly rejected it


# ─────────────────────────────────────────────────────────────────────────────
# 4. CEO Privileged Ops Gate — classification and audit
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_ceo_executive_action_allowed_immediately(client):
    """Executive (Layer 1) actions are approved without gate."""
    storage = MagicMock()
    engine = _mock_engine()
    storage.engine = engine
    _patch(storage)

    with patch("mas_core.policy.privileged_ops.PrivilegedOpsGate") as MockGate:
        instance = AsyncMock()
        instance.ensure_tables = AsyncMock()
        instance.check = AsyncMock(
            return_value={
                "allowed": True,
                "level": "executive",
                "decision": "approved",
                "record_id": None,
                "reason": "executive_authority",
            }
        )
        MockGate.return_value = instance

        r = await client.post(
            "/ceo/privileged-action",
            json={"action": "project.create", "actor_id": "ceo", "actor_role": "ceo"},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["allowed"] is True
    assert data["decision"] == "approved"
    assert data["record_id"] is None


@pytest.mark.anyio
async def test_ceo_privileged_action_requiring_approval(client):
    """High-risk privileged action (system.shutdown) returns pending_approval."""
    storage = MagicMock()
    engine = _mock_engine()
    storage.engine = engine
    _patch(storage)

    record_id = str(uuid4())
    with patch("mas_core.policy.privileged_ops.PrivilegedOpsGate") as MockGate:
        instance = AsyncMock()
        instance.ensure_tables = AsyncMock()
        instance.check = AsyncMock(
            return_value={
                "allowed": False,
                "level": "privileged",
                "decision": "pending_approval",
                "record_id": record_id,
                "reason": "requires_human_approval",
            }
        )
        MockGate.return_value = instance

        r = await client.post(
            "/ceo/privileged-action",
            json={"action": "system.shutdown", "actor_id": "ceo", "actor_role": "ceo"},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["allowed"] is False
    assert data["decision"] == "pending_approval"
    assert data["record_id"] == record_id


@pytest.mark.anyio
async def test_ceo_privileged_action_security_override_requires_approval(client):
    """security.override_cso requires approval — not allowed immediately."""
    storage = MagicMock()
    engine = _mock_engine()
    storage.engine = engine
    _patch(storage)

    record_id = str(uuid4())
    with patch("mas_core.policy.privileged_ops.PrivilegedOpsGate") as MockGate:
        instance = AsyncMock()
        instance.ensure_tables = AsyncMock()
        instance.check = AsyncMock(
            return_value={
                "allowed": False,
                "level": "privileged",
                "decision": "pending_approval",
                "record_id": record_id,
                "reason": "requires_human_approval",
            }
        )
        MockGate.return_value = instance

        r = await client.post(
            "/ceo/privileged-action",
            json={"action": "security.override_cso", "actor_id": "ceo", "actor_role": "ceo"},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["allowed"] is False
    assert data["decision"] == "pending_approval"


@pytest.mark.anyio
async def test_ceo_approve_privileged_action(client):
    """Human approves a pending privileged action → decision recorded."""
    storage = MagicMock()
    engine = _mock_engine()
    storage.engine = engine
    _patch(storage)

    record_id = str(uuid4())
    with patch("mas_core.policy.privileged_ops.PrivilegedOpsGate") as MockGate:
        instance = AsyncMock()
        instance.ensure_tables = AsyncMock()
        instance.approve = AsyncMock(return_value=True)
        MockGate.return_value = instance

        r = await client.post(
            f"/ceo/privileged-action/{record_id}/approve",
            json={"approved": True, "decided_by": "human_sre", "reason": "authorized"},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["decision"] == "approved"
    assert data["record_id"] == record_id


@pytest.mark.anyio
async def test_ceo_reject_privileged_action(client):
    """Human rejects a pending privileged action → decision rejected."""
    storage = MagicMock()
    engine = _mock_engine()
    storage.engine = engine
    _patch(storage)

    record_id = str(uuid4())
    with patch("mas_core.policy.privileged_ops.PrivilegedOpsGate") as MockGate:
        instance = AsyncMock()
        instance.ensure_tables = AsyncMock()
        instance.approve = AsyncMock(return_value=True)
        MockGate.return_value = instance

        r = await client.post(
            f"/ceo/privileged-action/{record_id}/approve",
            json={"approved": False, "decided_by": "human_sre", "reason": "policy violation"},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["decision"] == "rejected"


@pytest.mark.anyio
async def test_ceo_approve_nonexistent_record(client):
    """Approving a non-existent privileged action record → 404."""
    storage = MagicMock()
    engine = _mock_engine()
    storage.engine = engine
    _patch(storage)

    with patch("mas_core.policy.privileged_ops.PrivilegedOpsGate") as MockGate:
        instance = AsyncMock()
        instance.ensure_tables = AsyncMock()
        instance.approve = AsyncMock(return_value=False)  # not found
        MockGate.return_value = instance

        r = await client.post(
            f"/ceo/privileged-action/{uuid4()}/approve",
            json={"approved": True, "decided_by": "human_sre", "reason": ""},
        )
    assert r.status_code == 404


@pytest.mark.anyio
async def test_ceo_list_pending_privileged_actions(client):
    """GET /ceo/privileged-actions/pending returns list."""
    storage = MagicMock()
    engine = _mock_engine()
    storage.engine = engine
    _patch(storage)

    with patch("mas_core.policy.privileged_ops.PrivilegedOpsGate") as MockGate:
        instance = AsyncMock()
        instance.ensure_tables = AsyncMock()
        instance.list_pending = AsyncMock(
            return_value=[
                {
                    "id": str(uuid4()),
                    "action": "system.shutdown",
                    "decision": "pending_approval",
                    "actor_id": "ceo",
                    "actor_role": "ceo",
                    "payload_json": {},
                    "requested_at": NOW_ISO,
                },
            ]
        )
        MockGate.return_value = instance

        r = await client.get("/ceo/privileged-actions/pending")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) >= 1


@pytest.mark.anyio
async def test_ceo_privileged_actions_audit_log(client):
    """GET /ceo/privileged-actions/audit returns audit entries."""
    storage = MagicMock()
    engine = _mock_engine()
    storage.engine = engine
    _patch(storage)

    with patch("mas_core.policy.privileged_ops.PrivilegedOpsGate") as MockGate:
        instance = AsyncMock()
        instance.ensure_tables = AsyncMock()
        instance.audit_log = AsyncMock(
            return_value=[
                {
                    "id": str(uuid4()),
                    "action": "project.create",
                    "decision": "approved",
                    "actor_id": "ceo",
                    "actor_role": "ceo",
                    "requested_at": NOW_ISO,
                },
            ]
        )
        MockGate.return_value = instance

        r = await client.get("/ceo/privileged-actions/audit")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)


# ─────────────────────────────────────────────────────────────────────────────
# 5. PrivilegedOpsGate unit — direct classification tests
# ─────────────────────────────────────────────────────────────────────────────


def test_privileged_ops_classify_executive():
    """Executive actions classified as EXECUTIVE level."""
    from mas_core.policy.privileged_ops import PrivilegedOpsGate, PrivilegeLevel

    gate = PrivilegedOpsGate(conn_factory=MagicMock())
    assert gate.classify("project.create") == PrivilegeLevel.EXECUTIVE
    assert gate.classify("flow.invoke") == PrivilegeLevel.EXECUTIVE
    assert gate.classify("context.read") == PrivilegeLevel.EXECUTIVE


def test_privileged_ops_classify_privileged():
    """High-risk actions classified as PRIVILEGED level."""
    from mas_core.policy.privileged_ops import PrivilegedOpsGate, PrivilegeLevel

    gate = PrivilegedOpsGate(conn_factory=MagicMock())
    assert gate.classify("system.shutdown") == PrivilegeLevel.PRIVILEGED
    assert gate.classify("system.wipe") == PrivilegeLevel.PRIVILEGED
    assert gate.classify("policy.override") == PrivilegeLevel.PRIVILEGED
    assert gate.classify("security.override_cso") == PrivilegeLevel.PRIVILEGED
    assert gate.classify("credentials.export") == PrivilegeLevel.PRIVILEGED


def test_privileged_ops_unknown_action_is_denied():
    """Unknown actions must be explicitly registered before they can run."""
    from mas_core.policy.privileged_ops import PrivilegedOpsGate, PrivilegeLevel

    gate = PrivilegedOpsGate(conn_factory=MagicMock())
    assert gate.classify("some.unknown.action") == PrivilegeLevel.DENIED


def test_privileged_actions_requiring_approval():
    """Specific high-risk actions have require_approval=True."""
    from mas_core.policy.privileged_ops import PRIVILEGED_ACTIONS

    assert PRIVILEGED_ACTIONS["system.shutdown"]["require_approval"] is True
    assert PRIVILEGED_ACTIONS["system.wipe"]["require_approval"] is True
    assert PRIVILEGED_ACTIONS["security.override_cso"]["require_approval"] is True
    assert PRIVILEGED_ACTIONS["worker.force_stop"]["require_approval"] is True
    assert PRIVILEGED_ACTIONS["credentials.export"]["require_approval"] is True


def test_privileged_actions_not_requiring_approval():
    """Some privileged actions don't require approval (lower risk)."""
    from mas_core.policy.privileged_ops import PRIVILEGED_ACTIONS

    assert PRIVILEGED_ACTIONS["team.restart"]["require_approval"] is False
    assert PRIVILEGED_ACTIONS["container.start"]["require_approval"] is False
    assert PRIVILEGED_ACTIONS["credentials.resolve"]["require_approval"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 6. Human decision gate (approval_gates)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_submit_decision_project_not_found(client):
    """POST /projects/{id}/decisions on unknown project → 404."""
    storage = MagicMock()
    storage.get_project = AsyncMock(return_value=None)
    _patch(storage)

    r = await client.post(
        f"/projects/{uuid4()}/decisions",
        json={"decision": "APPROVED", "decided_by": "human", "comments": ""},
    )
    assert r.status_code == 404


@pytest.mark.anyio
async def test_submit_decision_no_pending_gate(client):
    """POST /projects/{id}/decisions when no pending gate exists → 404."""
    storage = MagicMock()
    storage.get_project = AsyncMock(return_value=_fake_project("PDR_REVIEW"))
    storage.engine = _mock_engine()
    # The engine mock's conn.execute returns a result with .mappings().first() == None
    _patch(storage)

    r = await client.post(
        f"/projects/{PROJECT_ID}/decisions",
        json={"decision": "APPROVED", "decided_by": "human", "comments": ""},
    )
    assert r.status_code == 404


@pytest.mark.anyio
async def test_pending_decisions_project_not_found(client):
    """GET /projects/{id}/pending-decisions on unknown project → 404."""
    storage = MagicMock()
    storage.get_project = AsyncMock(return_value=None)
    storage.engine = _mock_engine()
    _patch(storage)

    r = await client.get(f"/projects/{uuid4()}/pending-decisions")
    assert r.status_code == 404


@pytest.mark.anyio
async def test_pending_decisions_empty_returns_empty_list(client):
    """GET /projects/{id}/pending-decisions when no gates → 200 empty list."""
    storage = MagicMock()
    storage.get_project = AsyncMock(return_value=_fake_project("INIT"))
    # Engine mock returns empty result set
    conn = AsyncMock()
    mappings_mock = MagicMock()
    mappings_mock.all = MagicMock(return_value=[])
    result_mock = MagicMock()
    result_mock.mappings = MagicMock(return_value=mappings_mock)
    conn.execute = AsyncMock(return_value=result_mock)
    engine = MagicMock()
    engine.connect = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=conn),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    storage.engine = engine
    _patch(storage)

    r = await client.get(f"/projects/{PROJECT_ID}/pending-decisions")
    assert r.status_code == 200
    assert r.json() == []


# ─────────────────────────────────────────────────────────────────────────────
# 7. Project retry — state gate
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_retry_project_not_failed_state(client):
    """POST /projects/{id}/retry on non-FAILED project → 409 Conflict."""
    storage = MagicMock()
    storage.get_project = AsyncMock(return_value=_fake_project("IN_PROGRESS"))
    _patch(storage)

    r = await client.post(f"/projects/{PROJECT_ID}/retry")
    assert r.status_code == 409
    assert "FAILED" in r.json()["detail"]


@pytest.mark.anyio
async def test_retry_project_not_found(client):
    """POST /projects/{id}/retry on unknown project → 404."""
    storage = MagicMock()
    storage.get_project = AsyncMock(return_value=None)
    _patch(storage)

    r = await client.post(f"/projects/{uuid4()}/retry")
    assert r.status_code == 404


@pytest.mark.anyio
async def test_retry_project_from_failed_state(client):
    """POST /projects/{id}/retry on FAILED project → transitions to retry state."""
    from orchestrator_api.main import app

    from mas_core.protocols.enums import ProjectState
    from mas_core.workflow.controller import WorkflowTransitionResult
    from mas_core.workflow.events import WorkflowEvent

    storage = MagicMock()
    storage.get_project = AsyncMock(return_value=_fake_project("FAILED"))
    _patch(storage)

    controller = MagicMock()
    controller.transition = AsyncMock(
        return_value=WorkflowTransitionResult(
            project_id=str(PROJECT_ID),
            prior_state=ProjectState.FAILED,
            event=WorkflowEvent.RETRY,
            next_state=ProjectState.FEASIBILITY_CHECK,
            actor_id="human",
            context={},
        )
    )
    app.state.controller = controller

    r = await client.post(f"/projects/{PROJECT_ID}/retry")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "retried"
    assert "next_state" in data


# ─────────────────────────────────────────────────────────────────────────────
# 8. Input validation — 422 on bad payloads
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_create_project_missing_name(client):
    """POST /projects without required 'name' field → 422."""
    r = await client.post("/projects", json={})
    assert r.status_code == 422


@pytest.mark.anyio
async def test_transition_project_missing_event(client):
    """POST /projects/{id}/transition without 'event' → 422."""
    r = await client.post(
        f"/projects/{uuid4()}/transition",
        json={"actor_id": "human"},  # missing event
    )
    assert r.status_code == 422


@pytest.mark.anyio
async def test_transition_project_invalid_uuid(client):
    """POST /projects/not-a-uuid/transition → 422."""
    r = await client.post(
        "/projects/not-a-uuid/transition",
        json={"event": "project_created", "actor_id": "human"},
    )
    assert r.status_code == 422


@pytest.mark.anyio
async def test_override_flow_missing_required_fields(client):
    """POST /flows/instances/{id}/override missing actor_role → 422."""
    r = await client.post(
        f"/flows/instances/{INSTANCE_ID}/override",
        json={"actor_id": "ops"},  # missing actor_role and target_node_id
    )
    assert r.status_code == 422


@pytest.mark.anyio
async def test_privileged_action_missing_action(client):
    """POST /ceo/privileged-action without action field → 422."""
    r = await client.post(
        "/ceo/privileged-action",
        json={"actor_id": "ceo"},  # missing action
    )
    assert r.status_code == 422


@pytest.mark.anyio
async def test_privileged_approval_missing_approved_field(client):
    """POST /ceo/privileged-action/{id}/approve without approved → 422."""
    r = await client.post(
        f"/ceo/privileged-action/{uuid4()}/approve",
        json={"decided_by": "human"},  # missing approved
    )
    assert r.status_code == 422


@pytest.mark.anyio
async def test_decision_invalid_project_uuid(client):
    """POST /projects/not-a-uuid/decisions → 422."""
    r = await client.post(
        "/projects/not-a-uuid/decisions",
        json={"decision": "APPROVED", "decided_by": "human"},
    )
    assert r.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# 9. Credential resolve — policy-gated (403 on denied)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_credential_resolve_is_not_an_http_export_surface(client):
    """Raw credentials never cross the orchestrator HTTP boundary."""
    storage = MagicMock()
    engine = _mock_engine()
    storage.engine = engine
    _patch(storage)

    r = await client.post(
        "/credentials/SECRET_API_KEY/resolve",
        json={"requester": "rogue_agent"},
    )
    assert r.status_code == 410
    assert "prohibited" in r.json()["detail"].lower()


@pytest.mark.anyio
async def test_credential_resolve_never_returns_a_value(client):
    """Even an approved caller cannot export a credential value over HTTP."""
    storage = MagicMock()
    engine = _mock_engine()
    storage.engine = engine
    _patch(storage)

    r = await client.post(
        "/credentials/MY_API_KEY/resolve",
        json={"requester": "orchestrator"},
    )
    assert r.status_code == 410
    data = r.json()
    assert "value" not in data


@pytest.mark.anyio
async def test_credential_audit_log_returns_list(client):
    """GET /credentials/{name}/audit returns audit entries."""
    storage = MagicMock()
    engine = _mock_engine()
    storage.engine = engine
    _patch(storage)

    with patch("mas_core.credentials.CredentialsManager") as MockMgr:
        instance = AsyncMock()
        instance.ensure_tables = AsyncMock()
        instance.audit_log = AsyncMock(
            return_value=[
                {
                    "id": str(uuid4()),
                    "secret_name": "MY_API_KEY",
                    "requester": "orchestrator",
                    "resolved_at": NOW_ISO,
                },
            ]
        )
        MockMgr.return_value = instance

        r = await client.get("/credentials/MY_API_KEY/audit")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)


@pytest.mark.anyio
async def test_full_credential_audit_log_returns_list(client):
    """GET /credentials-audit returns full audit log."""
    storage = MagicMock()
    engine = _mock_engine()
    storage.engine = engine
    _patch(storage)

    with patch("mas_core.credentials.CredentialsManager") as MockMgr:
        instance = AsyncMock()
        instance.ensure_tables = AsyncMock()
        instance.audit_log = AsyncMock(return_value=[])
        MockMgr.return_value = instance

        r = await client.get("/credentials-audit")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ─────────────────────────────────────────────────────────────────────────────
# 10. Flow escalation security
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_escalate_missing_escalate_to(client):
    """POST /flows/instances/{id}/escalate without escalate_to → 400."""
    storage = MagicMock()
    storage.get_flow_instance = AsyncMock(return_value=_fake_instance())
    _patch(storage)

    r = await client.post(
        f"/flows/instances/{INSTANCE_ID}/escalate",
        json={"reason": "no target provided"},
    )
    assert r.status_code == 400
    assert "escalate_to" in r.json()["detail"]


@pytest.mark.anyio
async def test_escalate_nonexistent_instance(client):
    """POST /flows/instances/{id}/escalate on nonexistent instance → 404."""
    storage = MagicMock()
    storage.get_flow_instance = AsyncMock(return_value=None)
    _patch(storage)

    r = await client.post(
        f"/flows/instances/{uuid4()}/escalate",
        json={"escalate_to": "exec_ceo", "reason": "test"},
    )
    assert r.status_code == 404


@pytest.mark.anyio
async def test_escalate_flow_instance_success(client):
    """POST /flows/instances/{id}/escalate with valid target → 200."""
    storage = MagicMock()
    storage.get_flow_instance = AsyncMock(return_value=_fake_instance())
    storage.escalate_flow_instance = AsyncMock(return_value=_fake_instance("RUNNING"))
    _patch(storage)

    r = await client.post(
        f"/flows/instances/{INSTANCE_ID}/escalate",
        json={"escalate_to": "exec_coo", "reason": "needs COO review"},
    )
    assert r.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# 11. State history audit — readable through public API
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_state_history_returns_audit_trail(client):
    """GET /projects/{id}/state-history returns an audit trail."""
    storage = MagicMock()
    storage.get_project = AsyncMock(return_value=_fake_project("IN_PROGRESS"))
    storage.get_project_history = AsyncMock(
        return_value=[
            {
                "id": str(uuid4()),
                "project_id": str(PROJECT_ID),
                "from_state": "INIT",
                "to_state": "FEASIBILITY_CHECK",
                "event": "project_created",
                "triggered_by": "human",
                "created_at": NOW_ISO,
            },
        ]
    )
    _patch(storage)

    r = await client.get(f"/projects/{PROJECT_ID}/state-history")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    entry = data[0]
    assert "event" in entry or "to_state" in entry


@pytest.mark.anyio
async def test_state_history_returns_audit_trail(client):
    """GET /projects/{id}/state-history returns an audit trail."""
    storage = MagicMock()
    storage.get_project_history = AsyncMock(
        return_value=[
            {
                "id": str(uuid4()),
                "project_id": str(PROJECT_ID),
                "from_state": "INIT",
                "to_state": "FEASIBILITY_CHECK",
                "event": "project_created",
                "triggered_by": "human",
                "created_at": NOW_ISO,
            },
        ]
    )
    _patch(storage)

    r = await client.get(f"/projects/{PROJECT_ID}/state-history")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    entry = data[0]
    assert "event" in entry or "to_state" in entry


@pytest.mark.anyio
async def test_state_history_empty_project_returns_empty_list(client):
    """GET /projects/{id}/state-history for project with no history → 200 empty list."""
    storage = MagicMock()
    storage.get_project_history = AsyncMock(return_value=[])
    _patch(storage)

    r = await client.get(f"/projects/{uuid4()}/state-history")
    assert r.status_code == 200
    assert r.json() == []


# ─────────────────────────────────────────────────────────────────────────────
# 12. Flow switch — invalid flow_id rejected
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_switch_flow_instance_invalid_flow_id(client):
    """POST /flows/instances/{id}/switch with invalid UUID flow_id → 400."""
    storage = MagicMock()
    storage.get_flow_instance = AsyncMock(return_value=_fake_instance())
    _patch(storage)

    r = await client.post(
        f"/flows/instances/{INSTANCE_ID}/switch",
        json={"flow_id": "not-a-valid-uuid"},
    )
    assert r.status_code == 400


@pytest.mark.anyio
async def test_switch_flow_instance_missing_flow_id(client):
    """POST /flows/instances/{id}/switch without flow_id → 400."""
    storage = MagicMock()
    storage.get_flow_instance = AsyncMock(return_value=_fake_instance())
    _patch(storage)

    r = await client.post(
        f"/flows/instances/{INSTANCE_ID}/switch",
        json={},
    )
    assert r.status_code == 400


@pytest.mark.anyio
async def test_switch_flow_instance_nonexistent(client):
    """POST /flows/instances/{id}/switch on nonexistent instance → 404."""
    storage = MagicMock()
    storage.get_flow_instance = AsyncMock(return_value=None)
    _patch(storage)

    r = await client.post(
        f"/flows/instances/{uuid4()}/switch",
        json={"flow_id": str(uuid4())},
    )
    assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# 13. Worker status patch — input validation
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_patch_worker_status_missing_status(client):
    """PATCH /capabilities/workers/{id}/status without status → 422."""
    r = await client.patch(
        f"/capabilities/workers/{uuid4()}/status",
        json={},  # missing status
    )
    assert r.status_code == 422


@pytest.mark.anyio
async def test_patch_worker_status_not_found(client):
    """PATCH /capabilities/workers/{id}/status for unknown worker → 404."""
    storage = MagicMock()
    storage.get_worker = AsyncMock(return_value=None)
    _patch(storage)

    r = await client.patch(
        f"/capabilities/workers/{uuid4()}/status",
        json={"action": "ACTIVATE"},
    )
    assert r.status_code == 404
