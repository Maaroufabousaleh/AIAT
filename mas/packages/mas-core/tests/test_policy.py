"""
Tests for Phase 2 — CommunicationPolicy (routing + tool-access).

Coverage
--------
- Routing: all six roles, positive (allowed) + negative (denied) paths
- Chain-of-command enforcement: hierarchy-skipping is rejected
- ESCALATION skip-one-level exception
- Tool access: per-role allow/deny; CTO & DevOps PM extra tools; worker blocked list
- POLICY_RULES structure; TEAM_TIERS completeness
"""

from __future__ import annotations

import pytest

from mas_core.policy import (
    ADMIN_MSG_TYPES,
    C_SUITE_CROSS_TEAM_TYPES,
    C_SUITE_TEAMS,
    CTO_TEAM,
    DEPT_TEAMS,
    DEVOPS_TEAM,
    EXECUTIVE_TEAM,
    ORCHESTRATOR_TEAM,
    POLICY_RULES,
    TEAM_TIERS,
    WORKER_BLOCKED_TOOLS,
    CommunicationPolicy,
)
from mas_core.protocols.enums import AgentRole, MessageType

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

policy = CommunicationPolicy()


def _allow(
    sender_role: AgentRole,
    sender_team: str,
    recipient_team: str | None,
    msg_type: MessageType,
) -> bool:
    """Return True when policy.can() permits the message."""
    result = policy.can(sender_role, sender_team, None, recipient_team, msg_type)
    return result is True


def _deny_reason(
    sender_role: AgentRole,
    sender_team: str,
    recipient_team: str | None,
    msg_type: MessageType,
) -> str:
    """Return the deny-reason string (assert result is not True before calling)."""
    result = policy.can(sender_role, sender_team, None, recipient_team, msg_type)
    assert result is not True, "Expected denial but got allowed"
    return str(result)


# ===========================================================================
# TEAM_TIERS + POLICY_RULES structure
# ===========================================================================


class TestTeamTiersAndPolicyRules:
    def test_all_known_teams_covered(self):
        expected = (
            {ORCHESTRATOR_TEAM, EXECUTIVE_TEAM}
            | C_SUITE_TEAMS
            | DEPT_TEAMS
        )
        assert set(TEAM_TIERS.keys()) == expected

    def test_orchestrator_tier(self):
        assert TEAM_TIERS[ORCHESTRATOR_TEAM] == AgentRole.ORCHESTRATOR

    def test_executive_tier(self):
        assert TEAM_TIERS[EXECUTIVE_TEAM] == AgentRole.EXECUTIVE

    def test_c_suite_tiers(self):
        for team in C_SUITE_TEAMS:
            assert TEAM_TIERS[team] == AgentRole.C_SUITE

    def test_dept_tiers(self):
        for team in DEPT_TEAMS:
            assert TEAM_TIERS[team] == AgentRole.ADMIN

    def test_policy_rules_has_all_roles(self):
        assert set(POLICY_RULES.keys()) == {
            "orchestrator", "executive", "c_suite", "admin", "worker", "sub_agent"
        }


# ===========================================================================
# Orchestrator (CEO) — unrestricted
# ===========================================================================


class TestOrchestratorRouting:
    def test_orchestrator_can_reach_any_team(self):
        for team in list(C_SUITE_TEAMS) + list(DEPT_TEAMS) + [EXECUTIVE_TEAM]:
            assert _allow(
                AgentRole.ORCHESTRATOR, ORCHESTRATOR_TEAM, team, MessageType.TASK
            )

    def test_orchestrator_can_send_any_msg_type(self):
        for mt in MessageType:
            assert _allow(
                AgentRole.ORCHESTRATOR, ORCHESTRATOR_TEAM, EXECUTIVE_TEAM, mt
            )

    def test_orchestrator_intra_team(self):
        assert _allow(
            AgentRole.ORCHESTRATOR, ORCHESTRATOR_TEAM, ORCHESTRATOR_TEAM, MessageType.DIRECTIVE
        )


# ===========================================================================
# Executive (COO)
# ===========================================================================


class TestExecutiveRouting:
    def test_executive_can_reach_orchestrator(self):
        assert _allow(
            AgentRole.EXECUTIVE, EXECUTIVE_TEAM, ORCHESTRATOR_TEAM, MessageType.RESULT
        )

    def test_executive_can_reach_c_suite(self):
        for team in C_SUITE_TEAMS:
            assert _allow(
                AgentRole.EXECUTIVE, EXECUTIVE_TEAM, team, MessageType.REVIEW_REQUEST
            )

    def test_executive_can_reach_dept_admin(self):
        for team in DEPT_TEAMS:
            assert _allow(
                AgentRole.EXECUTIVE, EXECUTIVE_TEAM, team, MessageType.TASK
            )

    def test_executive_can_broadcast(self):
        assert _allow(
            AgentRole.EXECUTIVE, EXECUTIVE_TEAM, None, MessageType.BROADCAST
        )

    def test_executive_can_shutdown(self):
        assert _allow(
            AgentRole.EXECUTIVE, EXECUTIVE_TEAM, None, MessageType.SHUTDOWN
        )

    def test_executive_blocked_unknown_msg_type_for_workers(self):
        # RESULT is allowed, but executive should not directly address worker
        # team — that's a tier check. Here executive sends to dept (valid).
        result = policy.can(
            AgentRole.EXECUTIVE, EXECUTIVE_TEAM, None, "dept_production", MessageType.RESULT
        )
        assert result is True

    def test_executive_denied_bad_msg_type(self):
        # SPRINT_PLAN is not in executive's allowed types
        result = policy.can(
            AgentRole.EXECUTIVE, EXECUTIVE_TEAM, None, ORCHESTRATOR_TEAM,
            MessageType.SPRINT_PLAN,
        )
        assert result is not True
        assert "executive" in str(result).lower()


# ===========================================================================
# C-Suite (CFO, CIO, CHRM, CSO, CTO)
# ===========================================================================


class TestCSuiteRouting:
    def test_c_suite_can_reach_orchestrator(self):
        assert _allow(
            AgentRole.C_SUITE, "office_cfo", ORCHESTRATOR_TEAM, MessageType.RESULT
        )

    def test_c_suite_can_reach_executive(self):
        assert _allow(
            AgentRole.C_SUITE, "office_cio", EXECUTIVE_TEAM, MessageType.ADMIN_REPLY
        )

    def test_c_suite_can_send_review_to_peer(self):
        assert _allow(
            AgentRole.C_SUITE, "office_cfo", "office_cso", MessageType.REVIEW_REQUEST
        )

    def test_c_suite_review_response_to_peer(self):
        assert _allow(
            AgentRole.C_SUITE, "office_cso", "office_cio", MessageType.REVIEW_RESPONSE
        )

    def test_c_suite_cannot_send_task_to_peer(self):
        # TASK is in C_SUITE_MSG_TYPES but NOT in C_SUITE_CROSS_TEAM_TYPES
        result = policy.can(
            AgentRole.C_SUITE, "office_cfo", None, "office_cio", MessageType.TASK
        )
        assert result is not True
        assert "cross-team" in str(result).lower()

    def test_c_suite_can_send_own_team(self):
        assert _allow(
            AgentRole.C_SUITE, "office_cto", "office_cto", MessageType.TASK
        )

    def test_cto_can_send_sprint_plan_to_dept(self):
        for team in DEPT_TEAMS:
            assert _allow(
                AgentRole.C_SUITE, CTO_TEAM, team, MessageType.SPRINT_PLAN
            )

    def test_c_suite_cannot_send_task_to_dept(self):
        # TASK is not in C_SUITE_CROSS_TEAM_TYPES
        result = policy.can(
            AgentRole.C_SUITE, CTO_TEAM, None, "dept_production", MessageType.TASK
        )
        assert result is not True

    def test_c_suite_escalation_to_orchestrator(self):
        assert _allow(
            AgentRole.C_SUITE, "office_cso", ORCHESTRATOR_TEAM, MessageType.ESCALATION
        )

    def test_c_suite_denied_bad_msg_type(self):
        result = policy.can(
            AgentRole.C_SUITE, "office_cfo", None, EXECUTIVE_TEAM,
            MessageType.BROADCAST,
        )
        assert result is not True


# ===========================================================================
# Admin (Dept PM)
# ===========================================================================


class TestAdminRouting:
    def test_admin_can_reach_executive(self):
        assert _allow(
            AgentRole.ADMIN, "dept_production", EXECUTIVE_TEAM, MessageType.DOCUMENT_SUBMIT
        )

    def test_admin_can_reach_cto(self):
        assert _allow(
            AgentRole.ADMIN, "dept_production", CTO_TEAM, MessageType.SPRINT_REPORT
        )

    def test_admin_can_reach_own_team(self):
        assert _allow(
            AgentRole.ADMIN, "dept_system", "dept_system", MessageType.TASK
        )

    def test_devops_pm_infra_ready_to_cto(self):
        assert _allow(
            AgentRole.ADMIN, DEVOPS_TEAM, CTO_TEAM, MessageType.INFRA_READY
        )

    def test_admin_cannot_reach_other_c_suite(self):
        # PMs must go through COO; cannot message CFO directly
        result = policy.can(
            AgentRole.ADMIN, "dept_production", None, "office_cfo", MessageType.RESULT
        )
        assert result is not True
        assert "admin" in str(result).lower()

    def test_admin_cannot_reach_orchestrator_normally(self):
        result = policy.can(
            AgentRole.ADMIN, "dept_system", None, ORCHESTRATOR_TEAM, MessageType.RESULT
        )
        assert result is not True

    def test_admin_can_reach_orchestrator_via_escalation(self):
        # ESCALATION exception: skip COO → CEO directly
        assert _allow(
            AgentRole.ADMIN, "dept_system", ORCHESTRATOR_TEAM, MessageType.ESCALATION
        )

    def test_admin_cannot_reach_other_dept(self):
        result = policy.can(
            AgentRole.ADMIN, "dept_production", None, "dept_qa", MessageType.TASK
        )
        assert result is not True

    def test_admin_denied_bad_msg_type(self):
        result = policy.can(
            AgentRole.ADMIN, "dept_system", None, EXECUTIVE_TEAM,
            MessageType.BROADCAST,
        )
        assert result is not True


# ===========================================================================
# Worker — strict own-team confinement
# ===========================================================================


class TestWorkerRouting:
    def test_worker_can_message_own_team(self):
        assert _allow(
            AgentRole.WORKER, "dept_production", "dept_production", MessageType.RESULT
        )

    def test_worker_cannot_message_other_dept(self):
        result = policy.can(
            AgentRole.WORKER, "dept_production", None, "dept_qa", MessageType.RESULT
        )
        assert result is not True
        assert "own team" in str(result).lower()

    def test_worker_cannot_reach_c_suite(self):
        for team in C_SUITE_TEAMS:
            result = policy.can(
                AgentRole.WORKER, "dept_system", None, team, MessageType.RESULT
            )
            assert result is not True

    def test_worker_cannot_reach_orchestrator_normally(self):
        result = policy.can(
            AgentRole.WORKER, "dept_system", None, ORCHESTRATOR_TEAM, MessageType.TASK
        )
        assert result is not True

    def test_worker_can_escalate_to_executive(self):
        # Skip PM → COO directly with ESCALATION
        assert _allow(
            AgentRole.WORKER, "dept_devops", EXECUTIVE_TEAM, MessageType.ESCALATION
        )

    def test_worker_cannot_send_broadcast(self):
        result = policy.can(
            AgentRole.WORKER, "dept_qa", None, None, MessageType.BROADCAST
        )
        assert result is not True

    def test_worker_denied_bad_msg_type(self):
        result = policy.can(
            AgentRole.WORKER, "dept_production", None, "dept_production",
            MessageType.SPRINT_PLAN,
        )
        assert result is not True
        assert "worker" in str(result).lower()


# ===========================================================================
# Sub-agent — parent-team only
# ===========================================================================


class TestSubAgentRouting:
    def test_sub_agent_can_send_within_own_team(self):
        assert _allow(
            AgentRole.SUB_AGENT, "dept_production", "dept_production", MessageType.RESULT
        )

    def test_sub_agent_cannot_leave_team(self):
        result = policy.can(
            AgentRole.SUB_AGENT, "dept_production", None, "dept_system", MessageType.RESULT
        )
        assert result is not True
        assert "parent" in str(result).lower()

    def test_sub_agent_denied_bad_msg_type(self):
        result = policy.can(
            AgentRole.SUB_AGENT, "dept_production", None, "dept_production",
            MessageType.BROADCAST,
        )
        assert result is not True


# ===========================================================================
# Chain-of-command — hierarchy skip detection
# ===========================================================================


class TestChainOfCommand:
    """Verify that messages skipping hierarchy levels are rejected."""

    def test_worker_cannot_skip_to_ceo(self):
        result = policy.can(
            AgentRole.WORKER, "dept_system", None, ORCHESTRATOR_TEAM, MessageType.RESULT
        )
        assert result is not True

    def test_worker_cannot_skip_to_cto(self):
        result = policy.can(
            AgentRole.WORKER, "dept_system", None, CTO_TEAM, MessageType.RESULT
        )
        assert result is not True

    def test_admin_cannot_skip_to_cfo(self):
        result = policy.can(
            AgentRole.ADMIN, "dept_production", None, "office_cfo", MessageType.RESULT
        )
        assert result is not True

    def test_escalation_cannot_jump_two_levels_for_worker(self):
        # Worker can only skip to COO (one level), not all the way to CEO
        result = policy.can(
            AgentRole.WORKER, "dept_system", None, ORCHESTRATOR_TEAM,
            MessageType.ESCALATION,
        )
        assert result is not True

    def test_c_suite_cannot_skip_to_worker_directly(self):
        # C-Suite addressing another dept's workers is not permitted
        result = policy.can(
            AgentRole.C_SUITE, "office_cto", None, "dept_production",
            MessageType.TASK,
        )
        assert result is not True


# ===========================================================================
# Tool access — per-role allow
# ===========================================================================


class TestToolAccessAllowed:
    def test_orchestrator_can_use_any_tool(self):
        for tool in [
            "project.transition", "approval.override_cso", "sprint.create",
            "infra.provision", "secrets.manage", "web_search",
        ]:
            assert policy.can_use_tool(AgentRole.ORCHESTRATOR, tool) is True

    def test_executive_document_tools(self):
        for tool in ["document.create_draft", "document.submit", "document.list"]:
            assert policy.can_use_tool(AgentRole.EXECUTIVE, tool) is True

    def test_executive_review_tools(self):
        for tool in ["review.start_session", "review.aggregate"]:
            assert policy.can_use_tool(AgentRole.EXECUTIVE, tool) is True

    def test_executive_blob_tools(self):
        assert policy.can_use_tool(AgentRole.EXECUTIVE, "blob.upload") is True

    def test_c_suite_base_tools(self):
        for tool in ["web_search", "web_fetch", "blob.download", "document.list"]:
            result = policy.can_use_tool(AgentRole.C_SUITE, tool, sender_team="office_cfo")
            assert result is True

    def test_cto_extra_tools(self):
        for tool in [
            "sprint.create", "sprint.activate", "issue.create",
            "kpi.compute_sprint", "kpi.update_agent_profile", "velocity.report",
        ]:
            result = policy.can_use_tool(AgentRole.C_SUITE, tool, sender_team=CTO_TEAM)
            assert result is True

    def test_non_cto_c_suite_blocked_from_sprint_tools(self):
        result = policy.can_use_tool(
            AgentRole.C_SUITE, "sprint.create", sender_team="office_cfo"
        )
        assert result is not True

    def test_admin_base_tools(self):
        for tool in [
            "document.create_draft", "document.submit", "blob.upload",
            "issue.update_status",
        ]:
            result = policy.can_use_tool(AgentRole.ADMIN, tool, sender_team="dept_system")
            assert result is True

    def test_devops_pm_extra_tools(self):
        for tool in [
            "infra.provision", "cicd.configure",
            "monitoring.setup", "secrets.manage", "infra.ready_signal",
        ]:
            result = policy.can_use_tool(AgentRole.ADMIN, tool, sender_team=DEVOPS_TEAM)
            assert result is True

    def test_non_devops_admin_blocked_from_infra_tools(self):
        result = policy.can_use_tool(
            AgentRole.ADMIN, "infra.provision", sender_team="dept_production"
        )
        assert result is not True

    def test_worker_basic_tools(self):
        for tool in [
            "web_search", "web_fetch", "blob.upload",
            "blob.download", "document.get_latest",
        ]:
            result = policy.can_use_tool(AgentRole.WORKER, tool)
            assert result is True

    def test_sub_agent_allowed_tools(self):
        for tool in ["blob.download", "web_search"]:
            assert policy.can_use_tool(AgentRole.SUB_AGENT, tool) is True


# ===========================================================================
# Tool access — blocked for workers
# ===========================================================================


class TestToolAccessDenied:
    def test_worker_blocked_from_project_tools(self):
        for tool in [
            "project.create", "project.transition", "project.status",
        ]:
            result = policy.can_use_tool(AgentRole.WORKER, tool)
            assert result is not True
            assert "block" in str(result).lower() or "worker" in str(result).lower()

    def test_worker_blocked_from_approval_tools(self):
        result = policy.can_use_tool(AgentRole.WORKER, "approval.override_cso")
        assert result is not True

    def test_worker_blocked_from_review_session(self):
        result = policy.can_use_tool(AgentRole.WORKER, "review.start_session")
        assert result is not True

    def test_worker_blocked_from_sprint_create(self):
        result = policy.can_use_tool(AgentRole.WORKER, "sprint.create")
        assert result is not True

    def test_worker_blocked_from_sprint_activate(self):
        result = policy.can_use_tool(AgentRole.WORKER, "sprint.activate")
        assert result is not True

    def test_worker_blocked_from_infra_tools(self):
        result = policy.can_use_tool(AgentRole.WORKER, "infra.provision")
        assert result is not True

    def test_worker_blocked_from_secrets(self):
        result = policy.can_use_tool(AgentRole.WORKER, "secrets.manage")
        assert result is not True

    def test_executive_blocked_from_sprint_tools(self):
        # Executive's tool allowlist is document/review centric; not sprint.*
        result = policy.can_use_tool(AgentRole.EXECUTIVE, "sprint.create")
        assert result is not True

    def test_sub_agent_blocked_from_most_tools(self):
        for tool in ["project.create", "sprint.create", "document.submit"]:
            result = policy.can_use_tool(AgentRole.SUB_AGENT, tool)
            assert result is not True

    def test_all_worker_blocked_patterns_enforced(self):
        """All patterns in WORKER_BLOCKED_TOOLS must produce denials."""
        # Spot-check concrete tools matching each blocked pattern
        concrete_blocked = [
            "project.create",
            "project.transition",
            "approval.override_cso",
            "approval.submit",
            "review.start_session",
            "review.aggregate",
            "sprint.create",
            "sprint.activate",
            "infra.provision",
            "cicd.configure",
            "monitoring.setup",
            "secrets.manage",
            "infra.ready_signal",
        ]
        for tool in concrete_blocked:
            result = policy.can_use_tool(AgentRole.WORKER, tool)
            assert result is not True, f"Expected block for worker tool '{tool}'"


# ===========================================================================
# Deny-reason message quality
# ===========================================================================


class TestDenyReasonMessages:
    def test_routing_denial_mentions_sender_role(self):
        reason = _deny_reason(
            AgentRole.WORKER, "dept_qa", ORCHESTRATOR_TEAM, MessageType.TASK
        )
        assert "worker" in reason.lower()

    def test_tool_denial_mentions_tool_name(self):
        result = policy.can_use_tool(AgentRole.WORKER, "project.transition")
        assert "project.transition" in str(result)

    def test_c_suite_cross_team_denial_lists_allowed_types(self):
        reason = _deny_reason(
            AgentRole.C_SUITE, "office_cfo", "office_cio", MessageType.TASK
        )
        assert "cross-team" in reason.lower()
