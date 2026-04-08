"""Canonical tool manifest and compatibility aliases.

This module is the source of truth for:
- Canonical Phase 6 tool names and groups.
- Legacy aliases supported during the migration window.
"""

from __future__ import annotations

from typing import Literal

from mas_core.protocols.enums import AgentRole

from .groups import GROUP_RATE_LIMITS, ToolGroup

ToolTransport = Literal["internal", "http", "mcp", "process"]

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
    transport: ToolTransport = "internal",
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
        "transport": transport,
    }


TOOL_MANIFEST: dict[str, dict] = {}


def _register(*entries: dict) -> None:
    for e in entries:
        TOOL_MANIFEST[e["tool_name"]] = e


# --- Workflow ---
_register(
    _entry(
        name="project.create",
        group=ToolGroup.WORKFLOW,
        description="Create a new project record.",
        allowed_roles=_ORCH,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="project.status",
        group=ToolGroup.WORKFLOW,
        description="Get the current project status.",
        allowed_roles=_EXEC,
        cache_ttl=15,
    ),
    _entry(
        name="project.transition",
        group=ToolGroup.WORKFLOW,
        description="Transition a project state.",
        allowed_roles=_EXEC,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="project.list",
        group=ToolGroup.WORKFLOW,
        description="List projects with optional filters.",
        allowed_roles=_EXEC,
        cache_ttl=15,
    ),
    _entry(
        name="department_task",
        group=ToolGroup.WORKFLOW,
        description="Dispatch work to a department.",
        allowed_roles=_EXEC,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="human.notify",
        group=ToolGroup.WORKFLOW,
        description="Notify human operator.",
        allowed_roles=_EXEC,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="human.await_decision",
        group=ToolGroup.WORKFLOW,
        description="Await human decision.",
        allowed_roles=_ORCH,
        cache_ttl=0,
        idempotent=False,
    ),
)

# --- Document ---
_register(
    _entry(
        name="document.create_draft",
        group=ToolGroup.DOCUMENT,
        description="Create a document draft.",
        allowed_roles=_ADMIN,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="document.submit",
        group=ToolGroup.DOCUMENT,
        description="Submit document for review.",
        allowed_roles=_ADMIN,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="document.revise",
        group=ToolGroup.DOCUMENT,
        description="Revise an existing document.",
        allowed_roles=_ADMIN,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="document.get_latest",
        group=ToolGroup.DOCUMENT,
        description="Fetch latest document version.",
        allowed_roles=_WORKER,
        cache_ttl=15,
    ),
    _entry(
        name="document.list",
        group=ToolGroup.DOCUMENT,
        description="List documents.",
        allowed_roles=_WORKER,
        cache_ttl=15,
    ),
)

# --- Review ---
_register(
    _entry(
        name="review.start_session",
        group=ToolGroup.REVIEW,
        description="Start a review session.",
        allowed_roles=_EXEC,
        blocked_roles=[AgentRole.WORKER, AgentRole.SUB_AGENT],
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="review.submit",
        group=ToolGroup.REVIEW,
        description="Submit a review response.",
        allowed_roles=_CSUITE,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="review.submit_veto",
        group=ToolGroup.REVIEW,
        description="Submit a CSO veto.",
        allowed_roles=_CSUITE,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="review.aggregate",
        group=ToolGroup.REVIEW,
        description="Aggregate review responses.",
        allowed_roles=_EXEC,
        blocked_roles=[AgentRole.WORKER, AgentRole.SUB_AGENT],
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="approval.override_cso",
        group=ToolGroup.REVIEW,
        description="CEO override for CSO veto.",
        allowed_roles=_CSUITE,
        cache_ttl=0,
        idempotent=False,
    ),
)

# --- Sprint / Issue ---
_register(
    _entry(
        name="sprint.create",
        group=ToolGroup.SPRINT_ISSUE,
        description="Create sprint.",
        allowed_roles=_CSUITE,
        blocked_roles=[AgentRole.WORKER, AgentRole.SUB_AGENT],
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="sprint.activate",
        group=ToolGroup.SPRINT_ISSUE,
        description="Activate sprint.",
        allowed_roles=_CSUITE,
        blocked_roles=[AgentRole.WORKER, AgentRole.SUB_AGENT],
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="sprint.close",
        group=ToolGroup.SPRINT_ISSUE,
        description="Close sprint.",
        allowed_roles=_CSUITE,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="issue.create",
        group=ToolGroup.SPRINT_ISSUE,
        description="Create issue.",
        allowed_roles=_CSUITE,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="issue.decompose",
        group=ToolGroup.SPRINT_ISSUE,
        description="Decompose issue.",
        allowed_roles=_CSUITE,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="issue.update_status",
        group=ToolGroup.SPRINT_ISSUE,
        description="Update issue status.",
        allowed_roles=_ADMIN,
        cache_ttl=0,
        idempotent=False,
    ),
)

# --- DevOps ---
_register(
    _entry(
        name="infra.provision",
        group=ToolGroup.DEVOPS,
        description="Provision infrastructure resources.",
        allowed_roles=_ADMIN,
        blocked_roles=[AgentRole.WORKER, AgentRole.SUB_AGENT],
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="cicd.configure",
        group=ToolGroup.DEVOPS,
        description="Configure CI/CD.",
        allowed_roles=_ADMIN,
        blocked_roles=[AgentRole.WORKER, AgentRole.SUB_AGENT],
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="monitoring.setup",
        group=ToolGroup.DEVOPS,
        description="Setup monitoring.",
        allowed_roles=_ADMIN,
        blocked_roles=[AgentRole.WORKER, AgentRole.SUB_AGENT],
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="secrets.manage",
        group=ToolGroup.DEVOPS,
        description="Manage secrets.",
        allowed_roles=_ADMIN,
        blocked_roles=[AgentRole.WORKER, AgentRole.SUB_AGENT],
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="infra.ready_signal",
        group=ToolGroup.DEVOPS,
        description="Signal infra readiness.",
        allowed_roles=_ADMIN,
        blocked_roles=[AgentRole.WORKER, AgentRole.SUB_AGENT],
        cache_ttl=0,
        idempotent=False,
    ),
)

# --- Capability ---
_register(
    _entry(
        name="capability.search",
        group=ToolGroup.CAPABILITY,
        description="Search for workers by capability.",
        allowed_roles=_ADMIN,
        cache_ttl=15,
    ),
    _entry(
        name="capability.list_workers",
        group=ToolGroup.CAPABILITY,
        description="List registered workers and capabilities.",
        allowed_roles=_ADMIN,
        cache_ttl=15,
    ),
    _entry(
        name="capability.register",
        group=ToolGroup.CAPABILITY,
        description="Register worker capabilities.",
        allowed_roles=_EXEC,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="capability.deregister",
        group=ToolGroup.CAPABILITY,
        description="Deregister worker capabilities.",
        allowed_roles=_EXEC,
        cache_ttl=0,
        idempotent=False,
    ),
)

# --- KPI / Utility ---
_register(
    _entry(
        name="kpi.compute",
        group=ToolGroup.KPI_UTILITY,
        description="Compute KPI snapshot.",
        allowed_roles=_CSUITE,
        cache_ttl=30,
    ),
    _entry(
        name="kpi.query_history",
        group=ToolGroup.KPI_UTILITY,
        description="Query KPI history.",
        allowed_roles=_EXEC,
        cache_ttl=30,
    ),
    _entry(
        name="kpi.update_agent_profile",
        group=ToolGroup.KPI_UTILITY,
        description="Update agent profile from KPI data.",
        allowed_roles=_CSUITE,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="velocity.report",
        group=ToolGroup.KPI_UTILITY,
        description="Generate velocity report.",
        allowed_roles=_CSUITE,
        cache_ttl=60,
    ),
    _entry(
        name="estimation.adjust",
        group=ToolGroup.KPI_UTILITY,
        description="Adjust estimation strategy.",
        allowed_roles=_CSUITE,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="blob.upload",
        group=ToolGroup.KPI_UTILITY,
        description="Upload blob to object storage.",
        allowed_roles=_WORKER,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="blob.download",
        group=ToolGroup.KPI_UTILITY,
        description="Download blob from object storage.",
        allowed_roles=_ALL,
        cache_ttl=30,
    ),
    _entry(
        name="blob.list",
        group=ToolGroup.KPI_UTILITY,
        description="List blobs from object storage.",
        allowed_roles=_WORKER,
        cache_ttl=15,
    ),
    _entry(
        name="web_search",
        group=ToolGroup.KPI_UTILITY,
        description="Search the web.",
        allowed_roles=_WORKER,
        cache_ttl=60,
    ),
    _entry(
        name="web_fetch",
        group=ToolGroup.KPI_UTILITY,
        description="Fetch URL content.",
        allowed_roles=_WORKER,
        cache_ttl=60,
    ),
    _entry(
        name="file_read",
        group=ToolGroup.KPI_UTILITY,
        description="Read a workspace file.",
        allowed_roles=_WORKER,
        cache_ttl=10,
    ),
    _entry(
        name="file_write",
        group=ToolGroup.KPI_UTILITY,
        description="Write a workspace file.",
        allowed_roles=_WORKER,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="shared_memory_read",
        group=ToolGroup.KPI_UTILITY,
        description="Read shared memory.",
        allowed_roles=_WORKER,
        cache_ttl=5,
    ),
    _entry(
        name="shared_memory_write",
        group=ToolGroup.KPI_UTILITY,
        description="Write shared memory.",
        allowed_roles=_WORKER,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="browser_navigate",
        group=ToolGroup.KPI_UTILITY,
        description="Navigate to a URL in browser.",
        allowed_roles=_WORKER,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="browser_click",
        group=ToolGroup.KPI_UTILITY,
        description="Click element by selector.",
        allowed_roles=_WORKER,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="browser_type",
        group=ToolGroup.KPI_UTILITY,
        description="Type text into input.",
        allowed_roles=_WORKER,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="browser_screenshot",
        group=ToolGroup.KPI_UTILITY,
        description="Take page screenshot.",
        allowed_roles=_WORKER,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="browser_evaluate",
        group=ToolGroup.KPI_UTILITY,
        description="Execute JavaScript.",
        allowed_roles=_WORKER,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="browser_close",
        group=ToolGroup.KPI_UTILITY,
        description="Close browser session.",
        allowed_roles=_WORKER,
        cache_ttl=0,
        idempotent=False,
    ),
)


# Compatibility aliases (legacy_name -> canonical_name)
TOOL_ALIASES: dict[str, str] = {
    # legacy document/review names
    "document_create": "document.create_draft",
    "document_get": "document.get_latest",
    "review_aggregate": "review.aggregate",
    "review.submit_response": "review.submit",
    # legacy KPI split names
    "kpi.compute_sprint": "kpi.compute",
    "kpi.compute_project": "kpi.compute",
    # legacy org-specific helper names
    "cost_estimate": "kpi.compute",
    "budget_check": "kpi.query_history",
    "roi_calculate": "kpi.compute",
    "market_research": "web_search",
    "tech_stack_analyze": "capability.search",
    "integration_check": "capability.search",
    "code_analyze": "capability.search",
    "capacity_check": "capability.list_workers",
    "agent_registry_query": "capability.list_workers",
    "workload_report": "kpi.query_history",
    "team_recommend": "capability.search",
    "threat_model": "review.submit",
    "compliance_check": "review.submit",
    "security_scan": "review.submit",
    "risk_assess": "review.submit",
}


def resolve_tool_name(tool_name: str) -> str | None:
    """Return canonical name for a tool (or None when unknown)."""
    if tool_name in TOOL_MANIFEST:
        return tool_name
    return TOOL_ALIASES.get(tool_name)


def tool_exists(tool_name: str) -> bool:
    return resolve_tool_name(tool_name) is not None


def all_manifest_entries(*, include_aliases: bool = True) -> list[dict]:
    """Return manifest entries, optionally including deprecated alias records."""
    entries = [dict(v) for _, v in sorted(TOOL_MANIFEST.items())]
    if not include_aliases:
        return entries

    for alias, canonical in sorted(TOOL_ALIASES.items()):
        base = TOOL_MANIFEST[canonical]
        alias_entry = dict(base)
        alias_entry["tool_name"] = alias
        alias_entry["deprecated_alias_of"] = canonical
        entries.append(alias_entry)
    return entries
