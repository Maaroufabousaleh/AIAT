"""Tool manifest — the canonical registry of every tool available in the MAS.

Each entry maps a tool name to its metadata (group, description, roles, limits).
The tool-service uses this to build the GET /tools endpoint and to validate
incoming ToolRequests before dispatch.

This module is the "single source of truth" for tool definitions.  Adding a
new tool means:
1. Adding an entry here.
2. Creating a ``BaseTool`` subclass in ``tool_service/tools/``.
"""

from __future__ import annotations

from mas_core.protocols.enums import AgentRole

from .groups import GROUP_RATE_LIMITS, ToolGroup

# Shorthand aliases for readability
_ALL = list(AgentRole)
_ORCH = [AgentRole.ORCHESTRATOR]
_EXEC = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]
_CSUITE = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE]
_ADMIN = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE, AgentRole.ADMIN]
_WORKER = [
    AgentRole.ORCHESTRATOR,
    AgentRole.EXECUTIVE,
    AgentRole.C_SUITE,
    AgentRole.ADMIN,
    AgentRole.WORKER,
]


def _entry(
    *,
    name: str,
    group: ToolGroup,
    description: str,
    allowed_roles: list[AgentRole],
    blocked_roles: list[AgentRole] | None = None,
    cache_ttl: int = 30,
    idempotent: bool = True,
) -> dict:
    return {
        "tool_name": name,
        "tool_group": group.value,
        "description": description,
        "allowed_roles": allowed_roles,
        "blocked_roles": blocked_roles or [],
        "rate_limit_calls_per_min": GROUP_RATE_LIMITS[group],
        "cache_ttl_seconds": cache_ttl,
        "idempotent": idempotent,
    }


# ---------------------------------------------------------------------------
# TOOL_MANIFEST — dict[str, dict]  keyed by tool_name
# ---------------------------------------------------------------------------

TOOL_MANIFEST: dict[str, dict] = {}

def _register(*entries: dict) -> None:
    for e in entries:
        TOOL_MANIFEST[e["tool_name"]] = e


# ── GROUP_WEB ──────────────────────────────────────────────────────────────
_register(
    _entry(
        name="web_search",
        group=ToolGroup.WEB,
        description="Search the web via a search API and return top results.",
        allowed_roles=_WORKER,  # C-Suite, Admin, Worker all have web_search
        cache_ttl=60,
    ),
    _entry(
        name="web_fetch",
        group=ToolGroup.WEB,
        description="Fetch the contents of a URL and return text/HTML.",
        allowed_roles=_WORKER,
        cache_ttl=60,
    ),
)

# ── GROUP_FILE ─────────────────────────────────────────────────────────────
_register(
    _entry(
        name="file_read",
        group=ToolGroup.FILE,
        description="Read a file from the project workspace.",
        allowed_roles=_WORKER,
        cache_ttl=10,
    ),
    _entry(
        name="file_write",
        group=ToolGroup.FILE,
        description="Write content to a file in the project workspace.",
        allowed_roles=_WORKER,
        cache_ttl=0,
        idempotent=False,
    ),
)

# ── GROUP_MEMORY ───────────────────────────────────────────────────────────
_register(
    _entry(
        name="shared_memory_read",
        group=ToolGroup.MEMORY,
        description="Read a value from the shared agent memory store.",
        allowed_roles=_WORKER,
        cache_ttl=5,
    ),
    _entry(
        name="shared_memory_write",
        group=ToolGroup.MEMORY,
        description="Write a value to the shared agent memory store.",
        allowed_roles=_WORKER,
        cache_ttl=0,
        idempotent=False,
    ),
)

# ── GROUP_PROJECT ──────────────────────────────────────────────────────────
_register(
    _entry(
        name="project.create",
        group=ToolGroup.PROJECT,
        description="Create a new project record.",
        allowed_roles=_ORCH,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="project.status",
        group=ToolGroup.PROJECT,
        description="Get the current project status and state.",
        allowed_roles=_EXEC,
        cache_ttl=15,
    ),
    _entry(
        name="project.transition",
        group=ToolGroup.PROJECT,
        description="Transition the project to a new state.",
        allowed_roles=_EXEC,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="document.create_draft",
        group=ToolGroup.PROJECT,
        description="Create a new document draft.",
        allowed_roles=_ADMIN,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="document.submit",
        group=ToolGroup.PROJECT,
        description="Submit a document draft for review.",
        allowed_roles=_ADMIN,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="document.revise",
        group=ToolGroup.PROJECT,
        description="Revise a document based on review feedback.",
        allowed_roles=_ADMIN,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="document.get_latest",
        group=ToolGroup.PROJECT,
        description="Retrieve the latest version of a document.",
        allowed_roles=_WORKER,
        cache_ttl=15,
    ),
    _entry(
        name="document.list",
        group=ToolGroup.PROJECT,
        description="List documents, optionally filtered by project or type.",
        allowed_roles=_WORKER,
        cache_ttl=15,
    ),
    _entry(
        name="review.start_session",
        group=ToolGroup.PROJECT,
        description="Start a multi-reviewer review session.",
        allowed_roles=_EXEC,
        blocked_roles=[AgentRole.WORKER, AgentRole.SUB_AGENT],
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="review.submit_response",
        group=ToolGroup.PROJECT,
        description="Submit a review verdict for a review session.",
        allowed_roles=_CSUITE,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="review.submit_veto",
        group=ToolGroup.PROJECT,
        description="Submit a CSO veto on a review.",
        allowed_roles=[AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE],
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="review.aggregate",
        group=ToolGroup.PROJECT,
        description="Aggregate review verdicts into a final decision.",
        allowed_roles=_EXEC,
        blocked_roles=[AgentRole.WORKER, AgentRole.SUB_AGENT],
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="approval.override_cso",
        group=ToolGroup.PROJECT,
        description="CSO override: block or approve despite reviews.",
        allowed_roles=[AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE],
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="human.notify",
        group=ToolGroup.PROJECT,
        description="Send a notification to the human operator.",
        allowed_roles=_EXEC,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="human.await_decision",
        group=ToolGroup.PROJECT,
        description="Block until the human operator makes a decision.",
        allowed_roles=_ORCH,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="department_task",
        group=ToolGroup.PROJECT,
        description="Dispatch a work task to a department team.",
        allowed_roles=_EXEC,
        cache_ttl=0,
        idempotent=False,
    ),
)

# ── GROUP_SPRINT_KPI ───────────────────────────────────────────────────────
_register(
    _entry(
        name="sprint.create",
        group=ToolGroup.SPRINT_KPI,
        description="Create a new sprint for a team.",
        allowed_roles=_CSUITE,
        blocked_roles=[AgentRole.WORKER, AgentRole.SUB_AGENT],
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="sprint.activate",
        group=ToolGroup.SPRINT_KPI,
        description="Activate a sprint, transitioning it to IN_PROGRESS.",
        allowed_roles=_CSUITE,
        blocked_roles=[AgentRole.WORKER, AgentRole.SUB_AGENT],
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="sprint.close",
        group=ToolGroup.SPRINT_KPI,
        description="Close a sprint and generate the sprint report.",
        allowed_roles=_CSUITE,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="issue.create",
        group=ToolGroup.SPRINT_KPI,
        description="Create a new issue/work-item in a sprint.",
        allowed_roles=_CSUITE,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="issue.decompose",
        group=ToolGroup.SPRINT_KPI,
        description="Decompose an issue into sub-tasks.",
        allowed_roles=_CSUITE,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="issue.update_status",
        group=ToolGroup.SPRINT_KPI,
        description="Update the status of an issue.",
        allowed_roles=_ADMIN,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="kpi.compute_sprint",
        group=ToolGroup.SPRINT_KPI,
        description="Compute KPIs for a sprint (velocity, defect rate, etc.).",
        allowed_roles=_CSUITE,
        cache_ttl=30,
    ),
    _entry(
        name="kpi.compute_project",
        group=ToolGroup.SPRINT_KPI,
        description="Compute project-level KPIs across all sprints.",
        allowed_roles=_CSUITE,
        cache_ttl=30,
    ),
    _entry(
        name="kpi.query_history",
        group=ToolGroup.SPRINT_KPI,
        description="Query historical KPI data with filters.",
        allowed_roles=_EXEC,
        cache_ttl=30,
    ),
    _entry(
        name="kpi.update_agent_profile",
        group=ToolGroup.SPRINT_KPI,
        description="Update an agent's performance profile based on KPI data.",
        allowed_roles=_CSUITE,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="velocity.report",
        group=ToolGroup.SPRINT_KPI,
        description="Generate a velocity report for a team/sprint.",
        allowed_roles=_CSUITE,
        cache_ttl=60,
    ),
    _entry(
        name="estimation.adjust",
        group=ToolGroup.SPRINT_KPI,
        description="Adjust story-point estimation model based on actuals.",
        allowed_roles=_CSUITE,
        cache_ttl=0,
        idempotent=False,
    ),
)

# ── GROUP_INFRA ────────────────────────────────────────────────────────────
_register(
    _entry(
        name="infra.provision",
        group=ToolGroup.INFRA,
        description="Provision infrastructure resources.",
        allowed_roles=_ADMIN,
        blocked_roles=[AgentRole.WORKER, AgentRole.SUB_AGENT],
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="cicd.configure",
        group=ToolGroup.INFRA,
        description="Configure CI/CD pipeline settings.",
        allowed_roles=_ADMIN,
        blocked_roles=[AgentRole.WORKER, AgentRole.SUB_AGENT],
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="monitoring.setup",
        group=ToolGroup.INFRA,
        description="Set up monitoring and alerting rules.",
        allowed_roles=_ADMIN,
        blocked_roles=[AgentRole.WORKER, AgentRole.SUB_AGENT],
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="secrets.manage",
        group=ToolGroup.INFRA,
        description="Manage secrets (create, rotate, revoke).",
        allowed_roles=_ADMIN,
        blocked_roles=[AgentRole.WORKER, AgentRole.SUB_AGENT],
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="infra.ready_signal",
        group=ToolGroup.INFRA,
        description="Signal that infrastructure is ready for deployment.",
        allowed_roles=_ADMIN,
        blocked_roles=[AgentRole.WORKER, AgentRole.SUB_AGENT],
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="blob.upload",
        group=ToolGroup.INFRA,
        description="Upload a file to MinIO blob storage.",
        allowed_roles=_WORKER,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="blob.download",
        group=ToolGroup.INFRA,
        description="Download a file from MinIO blob storage.",
        allowed_roles=_ALL,  # Even sub-agents can download
        cache_ttl=30,
    ),
    _entry(
        name="blob.list",
        group=ToolGroup.INFRA,
        description="List objects in a MinIO bucket with optional prefix.",
        allowed_roles=_WORKER,
        cache_ttl=15,
    ),
)
