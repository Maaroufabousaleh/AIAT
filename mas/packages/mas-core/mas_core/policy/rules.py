"""
Declarative policy configuration for the MAS communication and tool-access engine.

All team IDs, role-tier mappings, allowed message types, and tool patterns live here.
Only this file needs to change when the team roster or tool manifest changes.

Plan reference: org-architecture §4.1 (role matrix) + §4.2 (POLICY_RULES).
"""

from __future__ import annotations

from mas_core.protocols.enums import AgentRole, MessageType

# ---------------------------------------------------------------------------
# Team → role-tier registry
# ---------------------------------------------------------------------------

ORCHESTRATOR_TEAM: str = "exec_ceo"
EXECUTIVE_TEAM: str = "exec_coo"

C_SUITE_TEAMS: frozenset[str] = frozenset(
    {"office_cfo", "office_cio", "office_chrm", "office_cso", "office_cto"}
)
CTO_TEAM: str = "office_cto"

DEPT_TEAMS: frozenset[str] = frozenset({"dept_production", "dept_system", "dept_qa", "dept_devops"})
DEVOPS_TEAM: str = "dept_devops"

#: Maps every known team_id to the AgentRole tier that *owns* that team stream.
TEAM_TIERS: dict[str, AgentRole] = {
    ORCHESTRATOR_TEAM: AgentRole.ORCHESTRATOR,
    EXECUTIVE_TEAM: AgentRole.EXECUTIVE,
    **{t: AgentRole.C_SUITE for t in C_SUITE_TEAMS},
    **{t: AgentRole.ADMIN for t in DEPT_TEAMS},
}

# ---------------------------------------------------------------------------
# Allowed message types per role  (sets for O(1) membership tests)
# ---------------------------------------------------------------------------

#: Executive (COO) may send these types cross-team.
EXECUTIVE_MSG_TYPES: frozenset[MessageType] = frozenset(
    {
        MessageType.TASK,
        MessageType.RESULT,
        MessageType.ADMIN_TASK,
        MessageType.ADMIN_REPLY,
        MessageType.REVIEW_REQUEST,
        MessageType.REVIEW_RESPONSE,
        MessageType.DOCUMENT_SUBMIT,
        MessageType.DOCUMENT_REVISION,
        MessageType.DIRECTIVE,
        MessageType.BROADCAST,
        MessageType.SHUTDOWN,
        MessageType.SHUTDOWN_ACK,
        MessageType.ESCALATION,
        MessageType.HEARTBEAT,
        MessageType.SYSTEM_EVENT,
    }
)

#: C-Suite message types (all within own-team or for permitted cross-team types).
C_SUITE_MSG_TYPES: frozenset[MessageType] = frozenset(
    {
        MessageType.TASK,
        MessageType.RESULT,
        MessageType.QUERY,
        MessageType.RESPONSE,
        MessageType.REVIEW_REQUEST,
        MessageType.REVIEW_RESPONSE,
        MessageType.SPRINT_PLAN,
        MessageType.SPRINT_REPORT,
        MessageType.ISSUE_ASSIGN,
        MessageType.ISSUE_COMPLETE,
        MessageType.ADMIN_TASK,
        MessageType.ADMIN_REPLY,
        MessageType.ESCALATION,
        MessageType.INFRA_READY,
        MessageType.DIRECTIVE,
        MessageType.SHUTDOWN_ACK,
    }
)

#: Types that C-Suite **may** use when addressing a team outside their own.
C_SUITE_CROSS_TEAM_TYPES: frozenset[MessageType] = frozenset(
    {
        MessageType.REVIEW_REQUEST,  # Fan-out review to peers
        MessageType.REVIEW_RESPONSE,  # Reply to a cross-team review request
        MessageType.ESCALATION,  # Skip-one-level exception
        MessageType.ADMIN_REPLY,  # Reply to admin directive
        MessageType.SPRINT_PLAN,  # CTO → dept PMs
        MessageType.DIRECTIVE,  # CTO / others directing departments
        MessageType.INFRA_READY,  # DevOps notifies CTO (admin→c_suite path)
    }
)

#: Admin (Dept PM) allowed message types.
ADMIN_MSG_TYPES: frozenset[MessageType] = frozenset(
    {
        MessageType.TASK,
        MessageType.RESULT,
        MessageType.QUERY,
        MessageType.RESPONSE,
        MessageType.ADMIN_TASK,
        MessageType.DOCUMENT_SUBMIT,
        MessageType.DOCUMENT_REVISION,
        MessageType.ISSUE_ASSIGN,
        MessageType.ISSUE_COMPLETE,
        MessageType.SPRINT_REPORT,
        MessageType.ESCALATION,
        MessageType.INFRA_READY,
        MessageType.ADMIN_REPLY,
        MessageType.SHUTDOWN_ACK,
    }
)

#: Worker allowed message types.
WORKER_MSG_TYPES: frozenset[MessageType] = frozenset(
    {
        MessageType.TASK,
        MessageType.RESULT,
        MessageType.QUERY,
        MessageType.RESPONSE,
        MessageType.ISSUE_COMPLETE,
        MessageType.ESCALATION,
        MessageType.ADMIN_REPLY,
        MessageType.SHUTDOWN_ACK,
    }
)

#: Sub-agent allowed message types (parent only).
SUB_AGENT_MSG_TYPES: frozenset[MessageType] = frozenset({MessageType.RESULT, MessageType.QUERY})

# ---------------------------------------------------------------------------
# Tool permissions — fnmatch glob patterns
# ---------------------------------------------------------------------------

#: Orchestrator has unrestricted tool access.
ORCHESTRATOR_TOOLS: tuple[str, ...] = ("*",)

EXECUTIVE_TOOLS: tuple[str, ...] = (
    "document.*",
    "review.*",
    "project.status",
    "project.transition",
    "project.list",
    "blob.*",
    "kpi.query_history",
    "department_task",
    "human.notify",
    "approval.*",
    "capability.register",
    "capability.deregister",
    "capability.search",
    "capability.list_workers",
)

C_SUITE_BASE_TOOLS: tuple[str, ...] = (
    "document.get_latest",
    "document.list",
    "blob.download",
    "blob.list",
    "web_search",
    "web_fetch",
    "review.submit",
    "review.submit_veto",
    "approval.override_cso",
    "capability.search",
    "capability.list_workers",
)

#: Extra tools available only to the CTO team (office_cto).
CTO_EXTRA_TOOLS: tuple[str, ...] = (
    "sprint.*",
    "issue.*",
    "kpi.compute",
    "kpi.query_history",
    "kpi.update_agent_profile",
    "velocity.report",
    "estimation.adjust",
    "capability.search",
    "capability.list_workers",
)

ADMIN_BASE_TOOLS: tuple[str, ...] = (
    "document.create_draft",
    "document.submit",
    "document.revise",
    "document.get_latest",
    "document.list",
    "blob.*",
    "issue.update_status",
    "capability.search",
    "capability.list_workers",
    "file_read",
    "file_write",
    "shared_memory_read",
    "shared_memory_write",
)

#: Extra tools available only to the DevOps PM team (dept_devops).
DEVOPS_PM_EXTRA_TOOLS: tuple[str, ...] = (
    "infra.*",
    "cicd.*",
    "monitoring.*",
    "secrets.*",
)

WORKER_TOOLS: tuple[str, ...] = (
    "document.get_latest",
    "document.list",
    "blob.upload",
    "blob.download",
    "blob.list",
    "web_search",
    "web_fetch",
    "file_read",
    "file_write",
    "shared_memory_read",
    "shared_memory_write",
)

#: Tools explicitly blocked for workers regardless of any allowlist.
WORKER_BLOCKED_TOOLS: tuple[str, ...] = (
    "project.*",
    "approval.*",
    "review.start_session",
    "review.aggregate",
    "sprint.create",
    "sprint.activate",
    "infra.provision",
    "cicd.configure",
    "monitoring.setup",
    "secrets.manage",
    "infra.ready_signal",
)

SUB_AGENT_TOOLS: tuple[str, ...] = (
    "blob.download",
    "web_search",
)

# ---------------------------------------------------------------------------
# POLICY_RULES — mirrors plan §4.2; used for documentation / YAML export
# ---------------------------------------------------------------------------

POLICY_RULES: dict[str, dict] = {
    "orchestrator": {
        "allowed_targets": ["*"],
        "allowed_msg_types": ["*"],
        "human_interface": True,
        "allowed_tools": list(ORCHESTRATOR_TOOLS),
    },
    "executive": {
        "allowed_targets": ["role:orchestrator", "role:c_suite", "role:admin"],
        "allowed_msg_types": [t.value for t in EXECUTIVE_MSG_TYPES],
        "allowed_tools": list(EXECUTIVE_TOOLS),
    },
    "c_suite": {
        "allowed_targets": [
            "role:orchestrator",
            "role:executive",
            "role:c_suite",
            "team:own",
        ],
        "allowed_msg_types": [t.value for t in C_SUITE_MSG_TYPES],
        "cross_team_msg_types": [t.value for t in C_SUITE_CROSS_TEAM_TYPES],
        "allowed_tools": list(C_SUITE_BASE_TOOLS),
        "cto_extra_tools": list(CTO_EXTRA_TOOLS),
    },
    "admin": {
        "allowed_targets": ["role:executive", "role:c_suite:cto", "team:own"],
        "allowed_msg_types": [t.value for t in ADMIN_MSG_TYPES],
        "allowed_tools": list(ADMIN_BASE_TOOLS),
        "devops_pm_extra_tools": list(DEVOPS_PM_EXTRA_TOOLS),
    },
    "worker": {
        "allowed_targets": ["team:own"],
        "allowed_msg_types": [t.value for t in WORKER_MSG_TYPES],
        "allowed_tools": list(WORKER_TOOLS),
        "blocked_tools": list(WORKER_BLOCKED_TOOLS),
    },
    "sub_agent": {
        "allowed_targets": ["parent:only"],
        "allowed_msg_types": [t.value for t in SUB_AGENT_MSG_TYPES],
        "allowed_tools": list(SUB_AGENT_TOOLS),
    },
}
