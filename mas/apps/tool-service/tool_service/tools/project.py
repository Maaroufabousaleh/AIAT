"""Workflow/Document/Review tools — real implementations that call the orchestrator-api.

All state-mutating operations go through the orchestrator-api HTTP endpoints.
The orchestrator-api is the sole writer of ``projects.state`` and manages
all persistence atomically via AgentStorage + WorkflowController.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from uuid import UUID, uuid4

import httpx
from mas_core.protocols.enums import AgentRole
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup

from ._orch_client import orch_get, orch_post

logger = logging.getLogger(__name__)
MESSAGE_ROUTER_URL = os.getenv("MESSAGE_ROUTER_URL") or os.getenv(
    "ROUTER_URL", "http://message-router:8001"
)


async def publish_message(envelope: dict[str, Any]) -> dict[str, Any]:
    """Publish a validated MAS envelope through the message-router."""
    async with httpx.AsyncClient(timeout=15, base_url=MESSAGE_ROUTER_URL) as client:
        resp = await client.post("/messages/publish", json=envelope)
        resp.raise_for_status()
        return resp.json()


# ── Project ────────────────────────────────────────────────────────────────


class ProjectCreateTool(BaseTool):
    name = "project.create"
    group = ToolGroup.WORKFLOW
    description = "Create a new project record."
    allowed_roles = [AgentRole.ORCHESTRATOR]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        name = kwargs.get("name") or kwargs.get("title") or "Untitled Project"
        body = {
            "name": name,
            "description": kwargs.get("description"),
            "human_requester": kwargs.get("human_requester"),
            "config": kwargs.get("config"),
        }
        return await orch_post("/projects", body)


class ProjectStatusTool(BaseTool):
    name = "project.status"
    group = ToolGroup.WORKFLOW
    description = "Get the current project status and state."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]
    cache_ttl_seconds = 15

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        try:
            UUID(str(project_id))
        except (TypeError, ValueError):
            return {"error": "invalid_project_id", "project_id": project_id}
        try:
            return await orch_get(f"/projects/{project_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return {"error": "project_not_found", "project_id": project_id}
            raise


class ProjectTransitionTool(BaseTool):
    name = "project.transition"
    group = ToolGroup.WORKFLOW
    description = "Transition the project to a new state via a workflow event."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        body = {
            "event": kwargs.get("event", ""),
            "actor_id": kwargs.get("actor_id", "unknown"),
            "context": kwargs.get("context"),
        }
        return await orch_post(f"/projects/{project_id}/transition", body)


class ProjectListTool(BaseTool):
    name = "project.list"
    group = ToolGroup.WORKFLOW
    description = "List projects with optional filters."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]
    cache_ttl_seconds = 15

    async def execute(self, **kwargs: Any) -> Any:
        params = {}
        if kwargs.get("state"):
            params["state"] = kwargs["state"]
        if kwargs.get("limit"):
            params["limit"] = kwargs["limit"]
        return await orch_get("/projects", params=params)


# ── Documents ──────────────────────────────────────────────────────────────


class DocumentCreateDraftTool(BaseTool):
    name = "document.create_draft"
    group = ToolGroup.DOCUMENT
    description = "Create a new document draft."
    allowed_roles = [
        AgentRole.ORCHESTRATOR,
        AgentRole.EXECUTIVE,
        AgentRole.ADMIN,
    ]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        doc_type = kwargs.get("doc_type", "PDR")
        body = {
            "team_id": "exec_ceo",
            "payload": {
                "action": "CREATE_DOCUMENT",
                "project_id": project_id,
                "doc_type": doc_type,
                "title": kwargs.get("title", ""),
                "content": kwargs.get("content", ""),
            },
        }
        return await orch_post("/tasks", body)


class DocumentSubmitTool(BaseTool):
    name = "document.submit"
    group = ToolGroup.DOCUMENT
    description = "Submit a document draft for review."
    allowed_roles = [
        AgentRole.ORCHESTRATOR,
        AgentRole.EXECUTIVE,
        AgentRole.ADMIN,
    ]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        doc_type = kwargs.get("doc_type", "PDR")
        event_map = {
            "PDR": "pdr_submitted",
            "CDR": "cdr_submitted",
            "RR": "rr_submitted",
        }
        event = event_map.get(doc_type)
        if event:
            return await orch_post(
                f"/projects/{project_id}/transition",
                {
                    "event": event,
                    "actor_id": kwargs.get("actor_id", "agent"),
                    "context": {
                        "document_id": kwargs.get("document_id"),
                        "doc_type": doc_type,
                    },
                },
            )
        return {"status": "submitted", "doc_type": doc_type}


class DocumentReviseTool(BaseTool):
    name = "document.revise"
    group = ToolGroup.DOCUMENT
    description = "Revise a document based on review feedback."
    allowed_roles = [
        AgentRole.ORCHESTRATOR,
        AgentRole.EXECUTIVE,
        AgentRole.ADMIN,
    ]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        body = {
            "team_id": kwargs.get("team_id", "office_cto"),
            "payload": {
                "action": "REVISE_DOCUMENT",
                "project_id": kwargs.get("project_id", ""),
                "document_id": kwargs.get("document_id", ""),
                "feedback": kwargs.get("feedback", ""),
            },
        }
        return await orch_post("/tasks", body)


class DocumentGetLatestTool(BaseTool):
    name = "document.get_latest"
    group = ToolGroup.DOCUMENT
    description = "Retrieve the latest version of a document."
    allowed_roles = [
        AgentRole.ORCHESTRATOR,
        AgentRole.EXECUTIVE,
        AgentRole.C_SUITE,
        AgentRole.ADMIN,
        AgentRole.WORKER,
    ]
    cache_ttl_seconds = 15

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        doc_type = kwargs.get("doc_type")
        docs = await orch_get(
            f"/projects/{project_id}/documents",
            params={"doc_type": doc_type} if doc_type else None,
        )
        if isinstance(docs, list) and docs:
            return docs[0]
        return {"error": "No documents found"}


class DocumentListTool(BaseTool):
    name = "document.list"
    group = ToolGroup.DOCUMENT
    description = "List documents, optionally filtered by project or type."
    allowed_roles = [
        AgentRole.ORCHESTRATOR,
        AgentRole.EXECUTIVE,
        AgentRole.C_SUITE,
        AgentRole.ADMIN,
        AgentRole.WORKER,
    ]
    cache_ttl_seconds = 15

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        params = {}
        if kwargs.get("doc_type"):
            params["doc_type"] = kwargs["doc_type"]
        docs = await orch_get(f"/projects/{project_id}/documents", params=params)
        return {"documents": docs, "total": len(docs) if isinstance(docs, list) else 0}


# ── Reviews ────────────────────────────────────────────────────────────────


class ReviewStartSessionTool(BaseTool):
    name = "review.start_session"
    group = ToolGroup.REVIEW
    description = "Start a multi-reviewer review session."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        # Review sessions are managed by the orchestrator
        body = {
            "team_id": kwargs.get("team_id", "exec_coo"),
            "payload": {
                "action": "START_REVIEW",
                "project_id": kwargs.get("project_id", ""),
                "document_id": kwargs.get("document_id"),
                "session_type": kwargs.get("session_type", "PEER_REVIEW"),
                "reviewer_ids": kwargs.get("reviewer_ids", []),
            },
        }
        return await orch_post("/tasks", body)


class ReviewSubmitResponseTool(BaseTool):
    name = "review.submit"
    group = ToolGroup.REVIEW
    description = "Submit a review verdict for a review session."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        body = {
            "team_id": kwargs.get("team_id", "exec_coo"),
            "payload": {
                "action": "SUBMIT_REVIEW",
                "session_id": kwargs.get("session_id", ""),
                "verdict": kwargs.get("verdict", "APPROVED"),
                "comments": kwargs.get("comments", []),
                "severity": kwargs.get("severity"),
                "reviewer_id": kwargs.get("reviewer_id", ""),
            },
        }
        return await orch_post("/tasks", body)


class ReviewSubmitVetoTool(BaseTool):
    name = "review.submit_veto"
    group = ToolGroup.REVIEW
    description = "Submit a CSO veto on a review."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        return await orch_post(
            f"/projects/{project_id}/transition",
            {
                "event": "cso_veto",
                "actor_id": kwargs.get("actor_id", "cso"),
                "context": {
                    "reason": kwargs.get("reason", "Security concern"),
                    "session_id": kwargs.get("session_id"),
                },
            },
        )


class ReviewAggregateTool(BaseTool):
    name = "review.aggregate"
    group = ToolGroup.REVIEW
    description = "Aggregate review verdicts into a final decision."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        return await orch_post(
            f"/projects/{project_id}/transition",
            {
                "event": "all_reviews_in",
                "actor_id": kwargs.get("actor_id", "coo"),
                "context": {
                    "session_id": kwargs.get("session_id"),
                    "aggregate_verdict": kwargs.get("verdict", "APPROVED"),
                },
            },
        )


# ── Approval ───────────────────────────────────────────────────────────────


class ApprovalOverrideCSOTool(BaseTool):
    name = "approval.override_cso"
    group = ToolGroup.REVIEW
    description = "CSO override: block or approve despite reviews."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        action = kwargs.get("action", "approve")
        if action == "block":
            return await orch_post(
                f"/projects/{project_id}/transition",
                {
                    "event": "cso_veto",
                    "actor_id": kwargs.get("actor_id", "cso"),
                    "context": {"reason": kwargs.get("reason", "CSO override")},
                },
            )
        else:
            return await orch_post(
                f"/projects/{project_id}/transition",
                {
                    "event": "ceo_override",
                    "actor_id": kwargs.get("actor_id", "ceo"),
                    "context": {"reason": kwargs.get("reason", "CEO override")},
                },
            )


# ── Human interface ────────────────────────────────────────────────────────


class HumanNotifyTool(BaseTool):
    name = "human.notify"
    group = ToolGroup.WORKFLOW
    description = "Send a notification to the human operator."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        message = str(kwargs.get("message") or "").strip()
        if not message:
            return {"error": "message is required"}

        project_id = str(kwargs.get("project_id") or "operator-direct")
        envelope = {
            "message_id": str(uuid4()),
            "correlation_id": str(kwargs.get("correlation_id") or uuid4()),
            "parent_id": kwargs.get("parent_id"),
            "msg_type": "RESPONSE",
            "sender_id": str(kwargs.get("sender_id") or "ceo"),
            "sender_team": "exec_ceo",
            "sender_role": AgentRole.ORCHESTRATOR.value,
            "recipient_team": "exec_ceo",
            "project_id": project_id,
            "payload": {
                "response": message,
                "source": "human.notify",
                "notification_type": kwargs.get("notification_type", "INFO"),
            },
            "ack_required": False,
        }
        result = await publish_message(envelope)
        return {
            "notified": True,
            "entry_id": result.get("entry_id"),
            "project_id": project_id,
            "message": message,
            "notification_type": envelope["payload"]["notification_type"],
        }


class HumanAwaitDecisionTool(BaseTool):
    name = "human.await_decision"
    group = ToolGroup.WORKFLOW
    description = "Check for pending human decisions on a project."
    allowed_roles = [AgentRole.ORCHESTRATOR]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        decisions = await orch_get(f"/projects/{project_id}/pending-decisions")
        if isinstance(decisions, list) and decisions:
            first_decision = decisions[0]
            return {
                "pending": True,
                "pending_count": len(decisions),
                "gate_id": first_decision.get("id"),
                "gate_type": first_decision.get("gate_type"),
                "first_decision": first_decision,
                "decisions": decisions,
            }
        return {
            "pending": False,
            "pending_count": 0,
            "decisions": [],
            "message": "No pending decisions",
        }


# ── Department task ────────────────────────────────────────────────────────


class DepartmentTaskTool(BaseTool):
    name = "department_task"
    group = ToolGroup.WORKFLOW
    description = "Dispatch a work task to a department team."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        body = {
            "team_id": kwargs.get("team", ""),
            "project_id": kwargs.get("project_id"),
            "payload": {
                "action": kwargs.get("action", "EXECUTE_TASK"),
                "description": kwargs.get("description", ""),
                "issue_id": kwargs.get("issue_id"),
                "sprint_id": kwargs.get("sprint_id"),
            },
        }
        return await orch_post("/tasks", body)
