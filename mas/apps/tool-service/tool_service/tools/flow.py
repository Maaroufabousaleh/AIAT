"""Flow orchestration tools — real implementations that call the orchestrator-api.

All state-mutating operations go through the orchestrator-api HTTP endpoints.
"""

from __future__ import annotations

import logging
from typing import Any

from mas_core.protocols.enums import AgentRole
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup

from ._orch_client import orch_get, orch_post

logger = logging.getLogger(__name__)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


# ── Flow ───────────────────────────────────────────────────────────────────


class FlowListTool(BaseTool):
    name = "flow.list"
    group = ToolGroup.WORKFLOW
    description = "List flows with optional active filter."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]
    cache_ttl_seconds = 15

    async def execute(self, **kwargs: Any) -> Any:
        params = {}
        if kwargs.get("is_active") is not None:
            params["is_active"] = kwargs["is_active"]
        return await orch_get("/flows", params=params if params else None)


class FlowRecommendTool(BaseTool):
    name = "flow.recommend"
    group = ToolGroup.WORKFLOW
    description = "Recommend the best available flow for a project based on name and description."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]
    cache_ttl_seconds = 15

    async def execute(self, **kwargs: Any) -> Any:
        project_name = str(kwargs.get("project_name") or "").strip()
        project_description = str(kwargs.get("project_description") or "").strip()
        if not project_name and not project_description:
            return {"error": "project_name or project_description is required"}

        flows = await orch_get("/flows", params={"is_active": True})
        if not isinstance(flows, list) or not flows:
            return {"error": "No active flows available"}

        haystack = f"{project_name} {project_description}".lower()
        query_tokens = {token for token in haystack.replace("-", " ").split() if token}

        def _score(flow: dict[str, Any]) -> tuple[int, int, str]:
            name = str(flow.get("name") or "")
            description = str(flow.get("description") or "")
            combined = f"{name} {description}".lower()
            flow_tokens = {token for token in combined.replace("-", " ").split() if token}
            overlap = len(query_tokens & flow_tokens)
            phrase_bonus = (
                3 if any(token in combined for token in query_tokens if len(token) > 3) else 0
            )
            keyword_bonus = 0
            if any(
                word in haystack for word in ("build", "dashboard", "software", "implement")
            ) and any(
                word in combined for word in ("build", "software", "implementation", "engineering")
            ):
                keyword_bonus += 5
            if any(word in haystack for word in ("research", "investigate", "explore")) and any(
                word in combined for word in ("research", "analysis", "discovery")
            ):
                keyword_bonus += 5
            return (overlap + phrase_bonus + keyword_bonus, int(flow.get("version") or 0), name)

        ranked = sorted(flows, key=_score, reverse=True)
        selected = ranked[0]
        return {
            "selected_flow_id": selected.get("id"),
            "selected_flow_name": selected.get("name"),
            "reason": f"Matched project intent against active flow metadata for '{project_name or project_description}'.",
            "candidates": [
                {
                    "id": flow.get("id"),
                    "name": flow.get("name"),
                    "version": flow.get("version"),
                }
                for flow in ranked[:5]
            ],
        }


class FlowInvokeTool(BaseTool):
    name = "flow.invoke"
    group = ToolGroup.WORKFLOW
    description = "Invoke a flow action (start, pause, resume, cancel) on a flow instance."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        action = kwargs.get("action", "start")
        flow_id = kwargs.get("flow_id")

        instance_resp = _as_dict(await orch_get(f"/projects/{project_id}/flow-instance"))

        if not instance_resp or instance_resp.get("status") == 404:
            if not flow_id:
                return {"error": "No flow instance exists and no flow_id provided to create one"}
            body = {
                "flow_id": flow_id,
                "project_id": project_id,
            }
            instance_resp = _as_dict(await orch_post("/flows/instances", body))

        instance_id = instance_resp.get("id") or instance_resp.get("instance_id")
        if not instance_id:
            return {"error": "Could not determine flow instance id", "response": instance_resp}

        action_body = {"action": action}
        return await orch_post(f"/flows/instances/{instance_id}/action", action_body)


class FlowStatusTool(BaseTool):
    name = "flow.status"
    group = ToolGroup.WORKFLOW
    description = "Get the flow instance status for a project."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]
    cache_ttl_seconds = 15

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        return await orch_get(f"/projects/{project_id}/flow-instance")


class FlowAdvanceTool(BaseTool):
    name = "flow.advance"
    group = ToolGroup.WORKFLOW
    description = "Advance a flow node by completing or failing it."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        node_id = kwargs.get("node_id", "")
        action = kwargs.get("action", "complete")

        instance_resp = _as_dict(await orch_get(f"/projects/{project_id}/flow-instance"))
        instance_id = instance_resp.get("id") or instance_resp.get("instance_id")
        if not instance_id:
            return {"error": "No flow instance found for project"}

        body = {
            "node_id": node_id,
            "action": action,
            "output": kwargs.get("output"),
            "approved": kwargs.get("approved"),
        }
        return await orch_post(f"/flows/instances/{instance_id}/node-action", body)


class FlowAssignTool(BaseTool):
    name = "flow.assign"
    group = ToolGroup.WORKFLOW
    description = (
        "Assign a flow definition to a project. Creates a new flow instance "
        "or switches the existing instance to the specified flow."
    )
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        flow_id = kwargs.get("flow_id", "")
        start_after_assign = bool(kwargs.get("start_after_assign", False))

        if not project_id or not flow_id:
            return {"error": "project_id and flow_id are required"}

        instance_resp = _as_dict(await orch_get(f"/projects/{project_id}/flow-instance"))

        if not instance_resp or instance_resp.get("status") == 404:
            body = {
                "flow_id": flow_id,
                "project_id": project_id,
            }
            task_id = kwargs.get("task_id")
            department_id = kwargs.get("department_id")
            if task_id:
                body["task_id"] = task_id
            if department_id:
                body["department_id"] = department_id
            result = _as_dict(await orch_post("/flows/instances", body))
            if start_after_assign and result.get("id"):
                result = await orch_post(
                    f"/flows/instances/{result['id']}/action", {"action": "start"}
                )
                return {"action": "created_and_started", "instance": result}
            return {"action": "created", "instance": result}

        instance_id = instance_resp.get("id") or instance_resp.get("instance_id")
        current_flow_id = instance_resp.get("flow_id")

        if str(current_flow_id) == str(flow_id):
            return {"action": "already_assigned", "instance": instance_resp}

        switch_body = {"flow_id": flow_id}
        result = _as_dict(await orch_post(f"/flows/instances/{instance_id}/switch", switch_body))
        if start_after_assign and result.get("id"):
            result = await orch_post(f"/flows/instances/{result['id']}/action", {"action": "start"})
            return {"action": "switched_and_started", "instance": result}
        return {"action": "switched", "instance": result}
