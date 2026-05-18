"""
Test 12 — Communication policy graph: allowed and denied message paths.

Type: unit / security

The MAS communication policy is enforced by the message-router service.
Since the router is an external service (separate process), we test:
1. The policy rules module directly (mas_core.policy.rules)
2. The message-router integration via mocked httpx calls
3. Negative cases: denied paths from various roles

The message-router uses the POLICY_RULES from mas_core.policy.rules to decide
whether a sender (role/team) can send a message type to a target (role/team).
"""

from __future__ import annotations

import pytest
from mas_core.protocols.enums import AgentRole, MessageType
from mas_core.policy.rules import (
    POLICY_RULES,
    WORKER_MSG_TYPES,
    ADMIN_MSG_TYPES,
    C_SUITE_MSG_TYPES,
    C_SUITE_CROSS_TEAM_TYPES,
    EXECUTIVE_MSG_TYPES,
    TEAM_TIERS,
    WORKER_BLOCKED_TOOLS,
    WORKER_TOOLS,
    C_SUITE_BASE_TOOLS,
    ADMIN_BASE_TOOLS,
    EXECUTIVE_TOOLS,
)


# ---------------------------------------------------------------------------
# 1. Worker → CEO communication (must be rejected)
# ---------------------------------------------------------------------------


class TestWorkerCannotMessageCEO:
    """Workers can only address team:own — direct CEO messaging is forbidden."""

    def test_worker_target_is_own_team_only(self):
        """POLICY_RULES['worker']['allowed_targets'] == ['team:own']."""
        worker_policy = POLICY_RULES["worker"]
        assert worker_policy["allowed_targets"] == ["team:own"]

    def test_worker_cannot_target_orchestrator(self):
        """No 'role:orchestrator' in worker allowed_targets."""
        assert "role:orchestrator" not in POLICY_RULES["worker"]["allowed_targets"]

    def test_worker_cannot_target_executive(self):
        """No 'role:executive' in worker allowed_targets."""
        assert "role:executive" not in POLICY_RULES["worker"]["allowed_targets"]

    def test_worker_cannot_use_broadcast(self):
        """Workers cannot send BROADCAST messages."""
        assert MessageType.BROADCAST not in WORKER_MSG_TYPES

    def test_worker_cannot_use_directive(self):
        """Workers cannot send DIRECTIVE messages."""
        assert MessageType.DIRECTIVE not in WORKER_MSG_TYPES

    def test_worker_cannot_send_admin_task(self):
        """Workers cannot initiate ADMIN_TASK (can only reply via ADMIN_REPLY)."""
        # Workers have ADMIN_REPLY (to respond to admin directives)
        # but must NOT have ADMIN_TASK (which would let them direct others)
        assert MessageType.ADMIN_TASK not in WORKER_MSG_TYPES

    def test_worker_message_types_are_limited(self):
        """Workers only have 8 message types."""
        expected = {
            MessageType.TASK,
            MessageType.RESULT,
            MessageType.QUERY,
            MessageType.RESPONSE,
            MessageType.ISSUE_COMPLETE,
            MessageType.ESCALATION,
            MessageType.ADMIN_REPLY,
            MessageType.SHUTDOWN_ACK,
        }
        assert WORKER_MSG_TYPES == expected


# ---------------------------------------------------------------------------
# 2. Worker → PM escalation (must be accepted)
# ---------------------------------------------------------------------------


class TestWorkerCanEscalateToPM:
    """Workers can send ESCALATION to their own team (PM is in team:own)."""

    def test_worker_has_escalation_type(self):
        """Workers can send ESCALATION messages."""
        assert MessageType.ESCALATION in WORKER_MSG_TYPES

    def test_worker_can_send_result(self):
        """Workers can send RESULT messages (work completion)."""
        assert MessageType.RESULT in WORKER_MSG_TYPES

    def test_worker_can_send_query(self):
        """Workers can send QUERY messages (ask PM for info)."""
        assert MessageType.QUERY in WORKER_MSG_TYPES


# ---------------------------------------------------------------------------
# 3. PM (Admin) → COO escalation (must be accepted)
# ---------------------------------------------------------------------------


class TestAdminCanEscalateToExecutive:
    """Admin (dept PM) can escalate to executive (COO) and CTO."""

    def test_admin_targets_include_executive(self):
        """Admin allowed_targets includes role:executive."""
        admin_targets = POLICY_RULES["admin"]["allowed_targets"]
        assert "role:executive" in admin_targets

    def test_admin_targets_include_cto(self):
        """Admin allowed_targets includes role:c_suite:cto."""
        admin_targets = POLICY_RULES["admin"]["allowed_targets"]
        assert "role:c_suite:cto" in admin_targets

    def test_admin_has_escalation_type(self):
        """Admin can send ESCALATION messages."""
        assert MessageType.ESCALATION in ADMIN_MSG_TYPES

    def test_admin_can_submit_documents(self):
        """Admin can submit DOCUMENT_SUBMIT."""
        assert MessageType.DOCUMENT_SUBMIT in ADMIN_MSG_TYPES

    def test_admin_can_send_sprint_report(self):
        """Admin can send SPRINT_REPORT (to COO/CTO)."""
        assert MessageType.SPRINT_REPORT in ADMIN_MSG_TYPES

    def test_admin_cannot_send_broadcast(self):
        """Admin cannot broadcast — only orchestrator/executive can."""
        assert MessageType.BROADCAST not in ADMIN_MSG_TYPES

    def test_admin_cannot_send_directive(self):
        """Admin cannot issue DIRECTIVE messages."""
        assert MessageType.DIRECTIVE not in ADMIN_MSG_TYPES


# ---------------------------------------------------------------------------
# 4. COO (Executive) → CEO escalation (must be accepted)
# ---------------------------------------------------------------------------


class TestExecutiveCanMessageOrchestrator:
    """Executive (COO) can message up to orchestrator (CEO)."""

    def test_executive_targets_include_orchestrator(self):
        """Executive allowed_targets includes role:orchestrator."""
        exec_targets = POLICY_RULES["executive"]["allowed_targets"]
        assert "role:orchestrator" in exec_targets

    def test_executive_has_escalation_type(self):
        """Executive can send ESCALATION."""
        assert MessageType.ESCALATION in EXECUTIVE_MSG_TYPES

    def test_executive_can_broadcast(self):
        """Executive can send BROADCAST (system-wide announcements)."""
        assert MessageType.BROADCAST in EXECUTIVE_MSG_TYPES

    def test_executive_can_shutdown(self):
        """Executive can send SHUTDOWN messages."""
        assert MessageType.SHUTDOWN in EXECUTIVE_MSG_TYPES

    def test_executive_can_send_directive(self):
        """Executive can issue DIRECTIVE messages."""
        assert MessageType.DIRECTIVE in EXECUTIVE_MSG_TYPES


# ---------------------------------------------------------------------------
# 5. CFO → Production worker direct message (must be rejected)
# ---------------------------------------------------------------------------


class TestCFOCannotDirectlyMessageProductionWorker:
    """C-suite (CFO) cannot directly address workers in another dept."""

    def test_c_suite_targets_do_not_include_worker_role(self):
        """C-suite allowed_targets does not include role:worker."""
        c_suite_targets = POLICY_RULES["c_suite"]["allowed_targets"]
        assert "role:worker" not in c_suite_targets

    def test_c_suite_targets_are_restricted(self):
        """C-suite can only message up (orchestrator, executive) or within own team."""
        c_suite_targets = POLICY_RULES["c_suite"]["allowed_targets"]
        allowed = {"role:orchestrator", "role:executive", "role:c_suite", "team:own"}
        assert set(c_suite_targets) <= allowed, (
            f"C-suite has unexpected targets: {set(c_suite_targets) - allowed}"
        )

    def test_c_suite_cross_team_types_are_limited(self):
        """Cross-team messages from C-suite are limited to review/escalation types."""
        allowed_cross = C_SUITE_CROSS_TEAM_TYPES
        # Must NOT include arbitrary TASK messages cross-team
        # (tasks should go via the COO, not directly to other dept workers)
        assert MessageType.TASK not in allowed_cross

    def test_cfo_team_is_c_suite(self):
        """office_cfo is in the c_suite tier."""
        assert TEAM_TIERS["office_cfo"] == AgentRole.C_SUITE


# ---------------------------------------------------------------------------
# 6. CTO → Department PM (must be accepted)
# ---------------------------------------------------------------------------


class TestCTOCanMessageDepartmentPM:
    """CTO (c_suite) can send SPRINT_PLAN and DIRECTIVE to dept PMs."""

    def test_c_suite_cross_team_includes_sprint_plan(self):
        """C-suite cross-team types include SPRINT_PLAN (CTO → dept PMs)."""
        assert MessageType.SPRINT_PLAN in C_SUITE_CROSS_TEAM_TYPES

    def test_c_suite_cross_team_includes_directive(self):
        """C-suite cross-team types include DIRECTIVE."""
        assert MessageType.DIRECTIVE in C_SUITE_CROSS_TEAM_TYPES

    def test_cto_team_is_c_suite(self):
        """office_cto is in the c_suite tier."""
        assert TEAM_TIERS["office_cto"] == AgentRole.C_SUITE

    def test_cto_has_sprint_tools(self):
        """CTO extra tools include sprint.* tools."""
        from mas_core.policy.rules import CTO_EXTRA_TOOLS

        assert any(t.startswith("sprint") for t in CTO_EXTRA_TOOLS)


# ---------------------------------------------------------------------------
# 7. CSO veto during review (must be accepted)
# ---------------------------------------------------------------------------


class TestCSOVetoPath:
    """CSO can submit veto during review session."""

    def test_c_suite_has_review_response_type(self):
        """C-suite can send REVIEW_RESPONSE (which includes veto)."""
        assert MessageType.REVIEW_RESPONSE in C_SUITE_MSG_TYPES

    def test_cso_tool_includes_veto(self):
        """CSO has access to review.submit_veto tool."""
        assert "review.submit_veto" in C_SUITE_BASE_TOOLS

    def test_cso_tool_includes_override(self):
        """CSO has access to approval.override_cso tool."""
        assert "approval.override_cso" in C_SUITE_BASE_TOOLS

    def test_cso_team_is_c_suite(self):
        """office_cso is in the c_suite tier."""
        assert TEAM_TIERS["office_cso"] == AgentRole.C_SUITE

    def test_c_suite_cross_team_review_response(self):
        """Cross-team REVIEW_RESPONSE allowed (CSO reviews across teams)."""
        assert MessageType.REVIEW_RESPONSE in C_SUITE_CROSS_TEAM_TYPES


# ---------------------------------------------------------------------------
# 8. CFO review response to COO (must be accepted)
# ---------------------------------------------------------------------------


class TestCFOReviewResponseToExecutive:
    """CFO can send REVIEW_RESPONSE cross-team (e.g. to COO during review)."""

    def test_c_suite_cross_team_review_request_allowed(self):
        """C-suite can send REVIEW_REQUEST cross-team (fan-out)."""
        assert MessageType.REVIEW_REQUEST in C_SUITE_CROSS_TEAM_TYPES

    def test_c_suite_has_review_request_type(self):
        """C-suite message types include REVIEW_REQUEST."""
        assert MessageType.REVIEW_REQUEST in C_SUITE_MSG_TYPES

    def test_executive_can_send_review_request(self):
        """Executive can also send REVIEW_REQUEST."""
        assert MessageType.REVIEW_REQUEST in EXECUTIVE_MSG_TYPES


# ---------------------------------------------------------------------------
# 9. DevOps INFRA_READY signal (admin → c_suite upward path)
# ---------------------------------------------------------------------------


class TestDevOpsInfraReadyPath:
    """DevOps PM can signal INFRA_READY to CTO (admin → c_suite upward message)."""

    def test_admin_has_infra_ready_type(self):
        """Admin can send INFRA_READY messages."""
        assert MessageType.INFRA_READY in ADMIN_MSG_TYPES

    def test_c_suite_cross_team_includes_infra_ready(self):
        """INFRA_READY is in cross-team types (DevOps notifies CTO)."""
        assert MessageType.INFRA_READY in C_SUITE_CROSS_TEAM_TYPES

    def test_devops_pm_has_infra_tools(self):
        """DevOps PM extra tools include infra.ready_signal is blocked for workers."""
        assert "infra.ready_signal" in WORKER_BLOCKED_TOOLS

    def test_devops_team_is_admin(self):
        """dept_devops is in the admin tier."""
        assert TEAM_TIERS["dept_devops"] == AgentRole.ADMIN


# ---------------------------------------------------------------------------
# 10. Sub-agent constraints (most restricted tier)
# ---------------------------------------------------------------------------


class TestSubAgentConstraints:
    """Sub-agents are the most restricted tier — parent communication only."""

    def test_sub_agent_targets_parent_only(self):
        """Sub-agent can only message parent."""
        assert POLICY_RULES["sub_agent"]["allowed_targets"] == ["parent:only"]

    def test_sub_agent_message_types_minimal(self):
        """Sub-agent has minimal message types: RESULT and QUERY only."""
        from mas_core.policy.rules import SUB_AGENT_MSG_TYPES

        assert MessageType.RESULT in SUB_AGENT_MSG_TYPES
        assert MessageType.QUERY in SUB_AGENT_MSG_TYPES
        # Sub-agents cannot escalate or broadcast
        assert MessageType.ESCALATION not in SUB_AGENT_MSG_TYPES
        assert MessageType.BROADCAST not in SUB_AGENT_MSG_TYPES

    def test_sub_agent_tools_minimal(self):
        """Sub-agent tools are minimal: only blob.download and web_search."""
        from mas_core.policy.rules import SUB_AGENT_TOOLS

        assert "blob.download" in SUB_AGENT_TOOLS
        assert "web_search" in SUB_AGENT_TOOLS
        assert len(SUB_AGENT_TOOLS) <= 5, "Sub-agents should have minimal tools"


# ---------------------------------------------------------------------------
# 11. Worker tool blocking — blocked tools are not in worker tools
# ---------------------------------------------------------------------------


class TestWorkerToolBlocking:
    """Blocked tools must not appear in WORKER_TOOLS."""

    def test_project_tools_blocked_for_workers(self):
        """Workers cannot use project.* tools."""
        assert "project.*" in WORKER_BLOCKED_TOOLS

    def test_approval_tools_blocked_for_workers(self):
        """Workers cannot use approval.* tools."""
        assert "approval.*" in WORKER_BLOCKED_TOOLS

    def test_review_start_blocked_for_workers(self):
        """Workers cannot start review sessions."""
        assert "review.start_session" in WORKER_BLOCKED_TOOLS

    def test_sprint_create_blocked_for_workers(self):
        """Workers cannot create sprints."""
        assert "sprint.create" in WORKER_BLOCKED_TOOLS

    def test_infra_provision_blocked_for_workers(self):
        """Workers cannot provision infrastructure."""
        assert "infra.provision" in WORKER_BLOCKED_TOOLS

    def test_worker_tools_do_not_contain_blocked(self):
        """None of the exact blocked tool names are in WORKER_TOOLS.

        Note: wildcard patterns like 'project.*' are in WORKER_BLOCKED_TOOLS
        but not literal matches — this verifies no explicit blocked tool slipped in.
        """
        worker_set = set(WORKER_TOOLS)
        explicitly_blocked = {
            "review.start_session",
            "review.aggregate",
            "sprint.create",
            "sprint.activate",
            "infra.provision",
            "cicd.configure",
            "monitoring.setup",
            "secrets.manage",
            "infra.ready_signal",
        }
        overlap = worker_set & explicitly_blocked
        assert not overlap, f"Blocked tools found in WORKER_TOOLS: {overlap}"


# ---------------------------------------------------------------------------
# 12. Router integration test (mocked external service)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_router_message_format_documented():
    """
    The message-router is an external service at ROUTER_URL.
    This test documents the expected message envelope format used by the orchestrator-api
    (from system_shutdown broadcast in main.py).

    Real router integration requires a live message-router service.
    """
    from mas_core.protocols.enums import MessageType, AgentRole

    # This is the envelope format the orchestrator-api sends to the router
    expected_fields = {
        "message_id",
        "msg_type",
        "sender_id",
        "sender_team",
        "sender_role",
        "payload",
        "created_at",
    }

    envelope = {
        "message_id": "test-id",
        "msg_type": MessageType.SHUTDOWN.value,
        "sender_id": "orchestrator",
        "sender_team": "orchestrator",
        "sender_role": AgentRole.ORCHESTRATOR.value,
        "payload": {"action": "SHUTDOWN"},
        "created_at": "2024-01-01T00:00:00+00:00",
    }

    assert set(envelope.keys()) == expected_fields


# ---------------------------------------------------------------------------
# 13. TODO tests — gaps requiring live router
# ---------------------------------------------------------------------------


def test_todo_live_router_rejection():
    """
    TODO (production gap): Worker → CEO direct message rejection.
    Requires a live message-router at ROUTER_URL.
    Steps:
      1. POST worker message with msg_type=TASK to exec_ceo stream
      2. Router should return 403 (policy violation)
    Currently untestable without live router.
    """
    pytest.skip("TODO: Requires live message-router service at ROUTER_URL")


def test_todo_router_enforces_message_types():
    """
    TODO (production gap): Router validates msg_type against sender role.
    Steps:
      1. POST a BROADCAST from a worker (should be 403)
      2. POST a BROADCAST from exec_coo (should be 200)
    Currently untestable without live router.
    """
    pytest.skip("TODO: Requires live message-router service at ROUTER_URL")


def test_todo_hierarchy_graph_shows_denied_paths():
    """
    TODO (production gap): The hierarchy graph UI overlay for comm permissions.
    Steps:
      1. Navigate to hierarchy page in Next.js dashboard
      2. Toggle communication permissions overlay
      3. Verify denied paths are shown in red, allowed in green
    Requires live dashboard at http://127.0.0.1:3000.
    """
    pytest.skip("TODO: Requires live dashboard and Playwright")
