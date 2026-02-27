"""PROJECT group tools — project, document, review, approval, human, department_task."""

from __future__ import annotations

from typing import Any

from mas_core.protocols.enums import AgentRole
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup


# ── Project ────────────────────────────────────────────────────────────────

class ProjectCreateTool(BaseTool):
    name = "project.create"
    group = ToolGroup.PROJECT
    description = "Create a new project record."
    allowed_roles = [AgentRole.ORCHESTRATOR]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        return {"project_id": "proj-stub-001", "name": kwargs.get("name", ""), "state": "SUBMITTED"}


class ProjectStatusTool(BaseTool):
    name = "project.status"
    group = ToolGroup.PROJECT
    description = "Get the current project status and state."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]
    cache_ttl_seconds = 15

    async def execute(self, **kwargs: Any) -> Any:
        return {"project_id": kwargs.get("project_id", ""), "state": "IN_PROGRESS", "progress_pct": 0}


class ProjectTransitionTool(BaseTool):
    name = "project.transition"
    group = ToolGroup.PROJECT
    description = "Transition the project to a new state."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        return {"project_id": kwargs.get("project_id", ""), "new_state": kwargs.get("target_state", ""), "ok": True}


# ── Documents ──────────────────────────────────────────────────────────────

class DocumentCreateDraftTool(BaseTool):
    name = "document.create_draft"
    group = ToolGroup.PROJECT
    description = "Create a new document draft."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE, AgentRole.ADMIN]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        return {"document_id": "doc-stub-001", "title": kwargs.get("title", ""), "state": "DRAFT"}


class DocumentSubmitTool(BaseTool):
    name = "document.submit"
    group = ToolGroup.PROJECT
    description = "Submit a document draft for review."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE, AgentRole.ADMIN]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        return {"document_id": kwargs.get("document_id", ""), "state": "SUBMITTED"}


class DocumentReviseTool(BaseTool):
    name = "document.revise"
    group = ToolGroup.PROJECT
    description = "Revise a document based on review feedback."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE, AgentRole.ADMIN]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        return {"document_id": kwargs.get("document_id", ""), "version": 2, "state": "DRAFT"}


class DocumentGetLatestTool(BaseTool):
    name = "document.get_latest"
    group = ToolGroup.PROJECT
    description = "Retrieve the latest version of a document."
    allowed_roles = [
        AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE,
        AgentRole.C_SUITE, AgentRole.ADMIN, AgentRole.WORKER,
    ]
    cache_ttl_seconds = 15

    async def execute(self, **kwargs: Any) -> Any:
        return {"document_id": kwargs.get("document_id", ""), "version": 1, "content": "[stub]"}


class DocumentListTool(BaseTool):
    name = "document.list"
    group = ToolGroup.PROJECT
    description = "List documents, optionally filtered by project or type."
    allowed_roles = [
        AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE,
        AgentRole.C_SUITE, AgentRole.ADMIN, AgentRole.WORKER,
    ]
    cache_ttl_seconds = 15

    async def execute(self, **kwargs: Any) -> Any:
        return {"documents": [], "total": 0}


# ── Reviews ────────────────────────────────────────────────────────────────

class ReviewStartSessionTool(BaseTool):
    name = "review.start_session"
    group = ToolGroup.PROJECT
    description = "Start a multi-reviewer review session."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        return {"session_id": "rev-stub-001", "state": "OPEN"}


class ReviewSubmitResponseTool(BaseTool):
    name = "review.submit_response"
    group = ToolGroup.PROJECT
    description = "Submit a review verdict for a review session."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        return {"session_id": kwargs.get("session_id", ""), "verdict": kwargs.get("verdict", ""), "accepted": True}


class ReviewSubmitVetoTool(BaseTool):
    name = "review.submit_veto"
    group = ToolGroup.PROJECT
    description = "Submit a CSO veto on a review."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        return {"session_id": kwargs.get("session_id", ""), "vetoed": True}


class ReviewAggregateTool(BaseTool):
    name = "review.aggregate"
    group = ToolGroup.PROJECT
    description = "Aggregate review verdicts into a final decision."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        return {"session_id": kwargs.get("session_id", ""), "final_verdict": "APPROVED"}


# ── Approval ───────────────────────────────────────────────────────────────

class ApprovalOverrideCSOTool(BaseTool):
    name = "approval.override_cso"
    group = ToolGroup.PROJECT
    description = "CSO override: block or approve despite reviews."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        return {"override": kwargs.get("action", "approve"), "accepted": True}


# ── Human interface ────────────────────────────────────────────────────────

class HumanNotifyTool(BaseTool):
    name = "human.notify"
    group = ToolGroup.PROJECT
    description = "Send a notification to the human operator."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        return {"notified": True, "message": kwargs.get("message", "")}


class HumanAwaitDecisionTool(BaseTool):
    name = "human.await_decision"
    group = ToolGroup.PROJECT
    description = "Block until the human operator makes a decision."
    allowed_roles = [AgentRole.ORCHESTRATOR]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        return {"decision": "approved", "awaited": True}


# ── Department task ────────────────────────────────────────────────────────

class DepartmentTaskTool(BaseTool):
    name = "department_task"
    group = ToolGroup.PROJECT
    description = "Dispatch a work task to a department team."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        return {
            "task_id": "task-stub-001",
            "team": kwargs.get("team", ""),
            "description": kwargs.get("description", ""),
            "dispatched": True,
        }
