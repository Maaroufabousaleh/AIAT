"""Provider-neutral source-control operations routed through the orchestrator."""

from __future__ import annotations

from typing import Any

from mas_core.protocols.enums import AgentRole
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup

from ._orch_client import orch_post

_READ = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE, AgentRole.ADMIN, AgentRole.WORKER]
_WRITE = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE, AgentRole.ADMIN]


class _SCMAction(BaseTool):
    action_path: str = ""
    required: tuple[str, ...] = ()

    async def execute(self, **kwargs: Any) -> Any:
        connection_id = str(kwargs.get("connection_id") or "")
        if not connection_id:
            raise ValueError("connection_id is required")
        payload = dict(kwargs.get("payload") or {})
        for key in self.required:
            if kwargs.get(key) is not None:
                payload[key] = kwargs[key]
        missing = [key for key in self.required if not payload.get(key)]
        if missing:
            raise ValueError(f"missing required fields: {', '.join(missing)}")
        return await orch_post(
            f"/integrations/connections/{connection_id}/source-control/{self.action_path}",
            {"payload": payload},
            context=kwargs.get("_aiat_context"),
            principal="operator",
        )


class SCMInstallationDiscoverTool(BaseTool):
    name = "scm.installation.discover"
    group = ToolGroup.DEVOPS
    description = "Discover repositories in the governed source-control installation."
    allowed_roles = _READ
    cache_ttl_seconds = 15

    async def execute(self, **kwargs: Any) -> Any:
        connection_id = str(kwargs.get("connection_id") or "")
        if not connection_id:
            raise ValueError("connection_id is required")
        return await orch_post(
            f"/integrations/connections/{connection_id}/source-control/installation",
            {},
            context=kwargs.get("_aiat_context"),
        )


class SCMBranchCreateTool(_SCMAction):
    name = "scm.branch.create"
    group = ToolGroup.DEVOPS
    description = "Create a governed repository branch."
    allowed_roles = _WRITE
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    action_path = "branches"
    required = ("branch",)
    cache_ttl_seconds = 0
    idempotent = False


class SCMPullRequestCreateTool(_SCMAction):
    name = "scm.pull_request.create"
    group = ToolGroup.DEVOPS
    description = "Create or record a governed pull request."
    allowed_roles = _WRITE
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    action_path = "pull-requests"
    required = ("title", "head", "base")
    cache_ttl_seconds = 0
    idempotent = False


class SCMReviewCommentTool(_SCMAction):
    name = "scm.review.comment"
    group = ToolGroup.DEVOPS
    description = "Publish a governed pull-request review comment."
    allowed_roles = _WRITE
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    action_path = "review-comments"
    required = ("pull_request_number", "body")
    cache_ttl_seconds = 0
    idempotent = False


class SCMCheckPublishTool(_SCMAction):
    name = "scm.check.publish"
    group = ToolGroup.DEVOPS
    description = "Publish a governed CI/security check result."
    allowed_roles = _WRITE
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    action_path = "checks"
    required = ("name", "head_sha", "status")
    cache_ttl_seconds = 0
    idempotent = False


class SCMCommitEvidenceTool(_SCMAction):
    name = "scm.commit.evidence"
    group = ToolGroup.DEVOPS
    description = "Capture provider commit metadata as delivery evidence."
    allowed_roles = _READ
    action_path = "commits"
    required = ("sha",)
    cache_ttl_seconds = 5


class SCMRunCredentialTool(_SCMAction):
    name = "scm.run_credential.mint"
    group = ToolGroup.DEVOPS
    description = "Mint a short-lived, scoped source-control run credential."
    allowed_roles = _WRITE
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    action_path = "run-credentials"
    required = ("repository",)
    cache_ttl_seconds = 0
    idempotent = False
