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
_KPI = list(_WORKER)


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
        # Static entries predate typed tool classes.  They intentionally carry
        # a permissive schema until the concrete implementation declares a
        # Pydantic input/output model; the live ToolRegistry replaces these
        # with generated schemas for typed tools.
        "schema_version": "1",
        "input_schema": {"type": "object", "additionalProperties": True},
        "output_schema": {},
        "schema_status": "legacy",
        "risk_tier": "standard",
        "approval_policy": "role",
        "credential_requirements": [],
        # Static entries cannot infer mutation semantics from idempotence (an
        # idempotent upsert may still mutate state).  Keep the legacy contract
        # conservative until the concrete tool declares this field.
        "side_effect": True,
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
        name="project.repository",
        group=ToolGroup.WORKFLOW,
        description="Create and manage a project Git workspace.",
        allowed_roles=[AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.ADMIN],
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
    _entry(
        name="flow.list",
        group=ToolGroup.WORKFLOW,
        description="List available orchestration flows.",
        allowed_roles=_EXEC,
        cache_ttl=15,
    ),
    _entry(
        name="flow.recommend",
        group=ToolGroup.WORKFLOW,
        description="Recommend the best active orchestration flow for a project.",
        allowed_roles=_EXEC,
        cache_ttl=15,
    ),
    _entry(
        name="flow.assign",
        group=ToolGroup.WORKFLOW,
        description="Assign or switch a project flow.",
        allowed_roles=_EXEC,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="flow.status",
        group=ToolGroup.WORKFLOW,
        description="Get the current flow instance for a project.",
        allowed_roles=_EXEC,
        cache_ttl=15,
    ),
    _entry(
        name="flow.invoke",
        group=ToolGroup.WORKFLOW,
        description="Start, pause, resume, or cancel a project flow.",
        allowed_roles=_EXEC,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="flow.advance",
        group=ToolGroup.WORKFLOW,
        description="Advance or fail an active flow node.",
        allowed_roles=_EXEC,
        cache_ttl=0,
        idempotent=False,
    ),
)

# --- Governed identity, mail, external-account and browser-session tools ---
# These remain in the existing low-rate utility group so the control plane does
# not create an ungoverned parallel tool-class. Every implementation delegates
# to the signed identity-service and performs a second ownership check there.
_register(
    _entry(name="identity.email.get_address", group=ToolGroup.KPI_UTILITY, description="Get the caller-owned governed mailbox address.", allowed_roles=_WORKER, cache_ttl=0, idempotent=False),
    _entry(name="mail.list", group=ToolGroup.KPI_UTILITY, description="List only caller-owned mailbox messages.", allowed_roles=_WORKER, cache_ttl=0, idempotent=False),
    _entry(name="mail.search", group=ToolGroup.KPI_UTILITY, description="Search only caller-owned mailbox messages.", allowed_roles=_WORKER, cache_ttl=0, idempotent=False),
    _entry(name="mail.read", group=ToolGroup.KPI_UTILITY, description="Read only a caller-owned mailbox message.", allowed_roles=_WORKER, cache_ttl=0, idempotent=False),
    _entry(name="mail.wait_for_verification", group=ToolGroup.KPI_UTILITY, description="Wait for a caller-owned verification message.", allowed_roles=_WORKER, cache_ttl=0, idempotent=False),
    _entry(name="mail.extract_code", group=ToolGroup.KPI_UTILITY, description="Extract a code from a caller-owned verification message.", allowed_roles=_WORKER, cache_ttl=0, idempotent=False),
    _entry(name="mail.extract_link", group=ToolGroup.KPI_UTILITY, description="Extract a link from a caller-owned verification message.", allowed_roles=_WORKER, cache_ttl=0, idempotent=False),
    _entry(name="mail.mark_processed", group=ToolGroup.KPI_UTILITY, description="Mark a caller-owned mailbox message processed.", allowed_roles=_WORKER, cache_ttl=0, idempotent=False),
    _entry(name="mail.delete", group=ToolGroup.KPI_UTILITY, description="Delete a caller-owned mailbox message.", allowed_roles=_WORKER, cache_ttl=0, idempotent=False),
    _entry(name="mail.send_request", group=ToolGroup.KPI_UTILITY, description="Request human-approved outbound mail through Stalwart.", allowed_roles=_WORKER, cache_ttl=0, idempotent=False),
    _entry(name="mail.send_approved", group=ToolGroup.KPI_UTILITY, description="Submit an approved mail request through Stalwart's queue.", allowed_roles=_WORKER, cache_ttl=0, idempotent=False),
    _entry(name="mail.get_delivery_status", group=ToolGroup.KPI_UTILITY, description="Read caller-owned outbound delivery status.", allowed_roles=_WORKER, cache_ttl=0, idempotent=False),
    _entry(name="mail.cancel_queued", group=ToolGroup.KPI_UTILITY, description="Cancel caller-owned queued outbound mail.", allowed_roles=_WORKER, cache_ttl=0, idempotent=False),
    _entry(name="identity.external.signup_request", group=ToolGroup.KPI_UTILITY, description="Request a governed external account.", allowed_roles=_WORKER, cache_ttl=0, idempotent=False),
    _entry(name="identity.external.login", group=ToolGroup.KPI_UTILITY, description="Begin external account login in an isolated local profile.", allowed_roles=_WORKER, cache_ttl=0, idempotent=False),
    _entry(name="identity.external.get_status", group=ToolGroup.KPI_UTILITY, description="Get caller-owned external account state.", allowed_roles=_WORKER, cache_ttl=0, idempotent=False),
    _entry(name="identity.external.rotate_credentials", group=ToolGroup.KPI_UTILITY, description="Request governed credential rotation without export.", allowed_roles=_WORKER, cache_ttl=0, idempotent=False),
    _entry(name="identity.external.suspend", group=ToolGroup.KPI_UTILITY, description="Suspend caller-owned external account.", allowed_roles=_WORKER, cache_ttl=0, idempotent=False),
    _entry(name="identity.external.close", group=ToolGroup.KPI_UTILITY, description="Close caller-owned external account.", allowed_roles=_WORKER, cache_ttl=0, idempotent=False),
    _entry(name="identity.session.create", group=ToolGroup.KPI_UTILITY, description="Create opaque local browser session metadata.", allowed_roles=_WORKER, cache_ttl=0, idempotent=False),
    _entry(name="identity.session.use", group=ToolGroup.KPI_UTILITY, description="Use a caller-owned opaque browser session.", allowed_roles=_WORKER, cache_ttl=0, idempotent=False),
    _entry(name="identity.session.revoke", group=ToolGroup.KPI_UTILITY, description="Revoke caller-owned browser sessions.", allowed_roles=_WORKER, cache_ttl=0, idempotent=False),
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
    _entry(
        name="document.ingest",
        group=ToolGroup.DOCUMENT,
        description="Parse documents with Docling behind the AIAT lifecycle boundary.",
        allowed_roles=_WORKER,
        cache_ttl=30,
    ),
    _entry(
        name="diagram.render",
        group=ToolGroup.DOCUMENT,
        description="Validate or render Mermaid diagrams when the Mermaid CLI is available.",
        allowed_roles=_WORKER,
        cache_ttl=30,
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
        name="sprint.list",
        group=ToolGroup.SPRINT_ISSUE,
        description="List sprints for a project.",
        allowed_roles=_ADMIN,
        cache_ttl=0,
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
    _entry(
        name="issue.list",
        group=ToolGroup.SPRINT_ISSUE,
        description="List issues for a project or sprint.",
        allowed_roles=_ADMIN,
        cache_ttl=15,
    ),
    _entry(
        name="issue.get",
        group=ToolGroup.SPRINT_ISSUE,
        description="Read one canonical work item with comments and links.",
        allowed_roles=_WORKER,
        cache_ttl=5,
    ),
    _entry(
        name="issue.update",
        group=ToolGroup.SPRINT_ISSUE,
        description="Update canonical work-item fields with optimistic concurrency.",
        allowed_roles=_ADMIN,
        blocked_roles=[AgentRole.WORKER, AgentRole.SUB_AGENT],
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="issue.comment",
        group=ToolGroup.SPRINT_ISSUE,
        description="Add an attributed comment to a canonical work item.",
        allowed_roles=_ADMIN,
        blocked_roles=[AgentRole.WORKER, AgentRole.SUB_AGENT],
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="issue.link",
        group=ToolGroup.SPRINT_ISSUE,
        description="Link a canonical work item to another object.",
        allowed_roles=_ADMIN,
        blocked_roles=[AgentRole.WORKER, AgentRole.SUB_AGENT],
        cache_ttl=0,
    ),
    _entry(
        name="pm.sync.status",
        group=ToolGroup.SPRINT_ISSUE,
        description="Inspect PM integration outbox and unresolved conflicts.",
        allowed_roles=_ADMIN,
        cache_ttl=5,
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
    _entry(
        name="iac.plan",
        group=ToolGroup.DEVOPS,
        description="Run OpenTofu/tofu plan through the DevOps adapter.",
        allowed_roles=_ADMIN,
        blocked_roles=[AgentRole.WORKER, AgentRole.SUB_AGENT],
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="scm.installation.discover",
        group=ToolGroup.DEVOPS,
        description="Discover repositories in the governed SCM installation.",
        allowed_roles=_WORKER,
        cache_ttl=15,
    ),
    _entry(
        name="scm.branch.create",
        group=ToolGroup.DEVOPS,
        description="Create a governed repository branch.",
        allowed_roles=_ADMIN,
        blocked_roles=[AgentRole.WORKER, AgentRole.SUB_AGENT],
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="scm.pull_request.create",
        group=ToolGroup.DEVOPS,
        description="Create or record a governed pull request.",
        allowed_roles=_ADMIN,
        blocked_roles=[AgentRole.WORKER, AgentRole.SUB_AGENT],
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="scm.review.comment",
        group=ToolGroup.DEVOPS,
        description="Publish a governed pull-request review comment.",
        allowed_roles=_ADMIN,
        blocked_roles=[AgentRole.WORKER, AgentRole.SUB_AGENT],
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="scm.check.publish",
        group=ToolGroup.DEVOPS,
        description="Publish a governed CI or security check result.",
        allowed_roles=_ADMIN,
        blocked_roles=[AgentRole.WORKER, AgentRole.SUB_AGENT],
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="scm.commit.evidence",
        group=ToolGroup.DEVOPS,
        description="Capture source-control commit metadata as evidence.",
        allowed_roles=_WORKER,
        cache_ttl=5,
    ),
    _entry(
        name="scm.run_credential.mint",
        group=ToolGroup.DEVOPS,
        description="Mint a short-lived, scoped source-control run credential.",
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
    _entry(
        name="mcp.invoke",
        group=ToolGroup.CAPABILITY,
        description="Invoke a configured MCP bridge endpoint.",
        allowed_roles=_WORKER,
        cache_ttl=0,
        idempotent=False,
        transport="mcp",
    ),
)

# --- KPI / Utility ---
_register(
    _entry(
        name="time_now",
        group=ToolGroup.KPI_UTILITY,
        description="Return the current date/time for the configured display timezone.",
        allowed_roles=_ALL,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="kpi.compute",
        group=ToolGroup.KPI_UTILITY,
        description="Compute KPI snapshot.",
        allowed_roles=_KPI,
        cache_ttl=30,
    ),
    _entry(
        name="kpi.compute_project",
        group=ToolGroup.KPI_UTILITY,
        description="Compute project-level KPIs across all sprints.",
        allowed_roles=_KPI,
        cache_ttl=30,
    ),
    _entry(
        name="kpi.query_history",
        group=ToolGroup.KPI_UTILITY,
        description="Query KPI history.",
        allowed_roles=_KPI,
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
        name="retrospective.generate",
        group=ToolGroup.KPI_UTILITY,
        description="Aggregate a closed sprint retrospective and persist its report artifact.",
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
        name="blob.delete",
        group=ToolGroup.KPI_UTILITY,
        description="Delete blobs from object storage.",
        allowed_roles=_WORKER,
        cache_ttl=0,
        idempotent=False,
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
        name="file.patch",
        group=ToolGroup.KPI_UTILITY,
        description="Apply a safe patch inside the workspace.",
        allowed_roles=_WORKER,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="repo.read",
        group=ToolGroup.KPI_UTILITY,
        description="Read repository files through the workspace boundary.",
        allowed_roles=_WORKER,
        cache_ttl=10,
    ),
    _entry(
        name="repo.search",
        group=ToolGroup.KPI_UTILITY,
        description="Search repository text through the workspace boundary.",
        allowed_roles=_WORKER,
        cache_ttl=10,
    ),
    _entry(
        name="command.run_safe",
        group=ToolGroup.KPI_UTILITY,
        description="Run a budgeted allowlisted command inside the workspace.",
        allowed_roles=_WORKER,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="security.scan",
        group=ToolGroup.KPI_UTILITY,
        description="Run Semgrep/SkillSpector-style static checks through a safe adapter.",
        allowed_roles=_WORKER,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="test.run",
        group=ToolGroup.KPI_UTILITY,
        description="Run pytest or Playwright through the safe command adapter.",
        allowed_roles=_WORKER,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="opencode.workspace_read",
        group=ToolGroup.KPI_UTILITY,
        description="Read one file from the run-scoped governed OpenCode workspace.",
        allowed_roles=_WORKER,
        cache_ttl=0,
    ),
    _entry(
        name="opencode.workspace_write",
        group=ToolGroup.KPI_UTILITY,
        description="Write one file to the run-scoped governed OpenCode workspace.",
        allowed_roles=_WORKER,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="opencode.workspace_pytest",
        group=ToolGroup.KPI_UTILITY,
        description="Run a bounded pytest target in the isolated governed OpenCode workspace.",
        allowed_roles=_WORKER,
        cache_ttl=0,
        idempotent=False,
    ),
    _entry(
        name="code.review",
        group=ToolGroup.KPI_UTILITY,
        description="Run a pinned external code-review adapter command when configured.",
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
    "time.now": "time_now",
    "workload_report": "kpi.query_history",
    "team_recommend": "capability.search",
    "threat_model": "security.scan",
    "compliance_check": "security.scan",
    "security_scan": "security.scan",
    "risk_assess": "security.scan",
    "semgrep": "security.scan",
    "skillspector": "security.scan",
    "docling": "document.ingest",
    "docling.parse": "document.ingest",
    "mermaid": "diagram.render",
    "pytest": "test.run",
    "playwright.test": "test.run",
    "opentofu.plan": "iac.plan",
    "tofu.plan": "iac.plan",
    "mcp.call": "mcp.invoke",
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
