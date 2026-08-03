"""Provider-neutral project/work-item tools.

Workers never receive a YouTrack/GitHub credential and never call a provider
API.  These tools speak only to the canonical orchestrator API; the
integration outbox projects the resulting state to whichever provider is
bound to the project.
"""

from __future__ import annotations

from typing import Any

from mas_core.protocols.enums import AgentRole
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup

from ._orch_client import orch_get, orch_patch, orch_post

_READ = [
    AgentRole.ORCHESTRATOR,
    AgentRole.EXECUTIVE,
    AgentRole.C_SUITE,
    AgentRole.ADMIN,
    AgentRole.WORKER,
]
_WRITE = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE, AgentRole.ADMIN]


class IssueGetTool(BaseTool):
    name = "issue.get"
    group = ToolGroup.SPRINT_ISSUE
    description = "Read one canonical work item and its comments and links."
    allowed_roles = _READ
    cache_ttl_seconds = 5

    async def execute(self, **kwargs: Any) -> Any:
        project_id = str(kwargs.get("project_id") or "")
        issue_id = str(kwargs.get("issue_id") or "")
        if not project_id or not issue_id:
            raise ValueError("project_id and issue_id are required")
        return await orch_get(f"/projects/{project_id}/issues/{issue_id}", context=kwargs.get("_aiat_context"))


class IssueUpdateTool(BaseTool):
    name = "issue.update"
    group = ToolGroup.SPRINT_ISSUE
    description = "Update canonical work-item fields with an optional revision guard."
    allowed_roles = _WRITE
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        project_id = str(kwargs.get("project_id") or "")
        issue_id = str(kwargs.get("issue_id") or "")
        if not project_id or not issue_id:
            raise ValueError("project_id and issue_id are required")
        body = {
            key: kwargs[key]
            for key in (
                "title",
                "description",
                "status",
                "priority",
                "assigned_team",
                "assigned_agent",
                "estimated_hours",
                "actual_hours",
                "story_points",
                "expected_revision",
            )
            if kwargs.get(key) is not None
        }
        return await orch_patch(
            f"/projects/{project_id}/issues/{issue_id}",
            body,
            context=kwargs.get("_aiat_context"),
            principal="operator",
        )


class IssueCommentTool(BaseTool):
    name = "issue.comment"
    group = ToolGroup.SPRINT_ISSUE
    description = "Add an attributed comment to a canonical work item."
    allowed_roles = _WRITE
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        project_id = str(kwargs.get("project_id") or "")
        issue_id = str(kwargs.get("issue_id") or "")
        body = str(kwargs.get("body") or "")
        if not project_id or not issue_id or not body:
            raise ValueError("project_id, issue_id, and body are required")
        context = kwargs.get("_aiat_context")
        if not isinstance(context, dict) or not str(context.get("caller_id") or "").strip():
            raise ValueError("signed caller context with caller_id is required")
        payload = {"body": body, "actor_id": str(context["caller_id"]).strip()}
        for key in ("run_id", "approval_id", "evidence_id", "body_blob_ref"):
            if kwargs.get(key) is not None:
                payload[key] = kwargs[key]
        return await orch_post(
            f"/projects/{project_id}/issues/{issue_id}/comments",
            payload,
            context=context,
            principal="operator",
        )


class IssueLinkTool(BaseTool):
    name = "issue.link"
    group = ToolGroup.SPRINT_ISSUE
    description = "Link a canonical work item to an external or internal object."
    allowed_roles = _WRITE
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    cache_ttl_seconds = 0
    idempotent = True

    async def execute(self, **kwargs: Any) -> Any:
        project_id = str(kwargs.get("project_id") or "")
        issue_id = str(kwargs.get("issue_id") or "")
        if not project_id or not issue_id:
            raise ValueError("project_id and issue_id are required")
        payload = {
            "link_type": str(kwargs.get("link_type") or "relates_to"),
            "target_type": str(kwargs.get("target_type") or "external_issue"),
            "target_id": str(kwargs.get("target_id") or ""),
            "metadata": kwargs.get("metadata") or {},
        }
        if not payload["target_id"]:
            raise ValueError("target_id is required")
        return await orch_post(
            f"/projects/{project_id}/issues/{issue_id}/links",
            payload,
            context=kwargs.get("_aiat_context"),
            principal="operator",
        )


class PMSyncStatusTool(BaseTool):
    name = "pm.sync.status"
    group = ToolGroup.SPRINT_ISSUE
    description = "Inspect provider-neutral PM outbox and unresolved conflicts."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE, AgentRole.ADMIN]
    cache_ttl_seconds = 5

    async def execute(self, **kwargs: Any) -> Any:
        connection_id = kwargs.get("connection_id")
        params = {"connection_id": connection_id} if connection_id else None
        context = kwargs.get("_aiat_context")
        conflicts = await orch_get("/integrations/conflicts", params=params, context=context)
        outbox = await orch_get("/integrations/outbox", params=params, context=context)
        return {
            "conflicts": conflicts,
            "outbox": outbox,
            "open_conflicts": len(conflicts) if isinstance(conflicts, list) else None,
            "pending_outbox": len(outbox) if isinstance(outbox, list) else None,
        }
