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

        instance_resp = await orch_get(f"/projects/{project_id}/flow-instance")

        if not instance_resp or instance_resp.get("status") == 404:
            if not flow_id:
                return {"error": "No flow instance exists and no flow_id provided to create one"}
            body = {
                "flow_id": flow_id,
                "project_id": project_id,
            }
            instance_resp = await orch_post("/flows/instances", body)

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

        instance_resp = await orch_get(f"/projects/{project_id}/flow-instance")
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

        if not project_id or not flow_id:
            return {"error": "project_id and flow_id are required"}

        instance_resp = await orch_get(f"/projects/{project_id}/flow-instance")

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
            result = await orch_post("/flows/instances", body)
            return {"action": "created", "instance": result}

        instance_id = instance_resp.get("id") or instance_resp.get("instance_id")
        current_flow_id = instance_resp.get("flow_id")

        if str(current_flow_id) == str(flow_id):
            return {"action": "already_assigned", "instance": instance_resp}

        switch_body = {"flow_id": flow_id}
        result = await orch_post(f"/flows/instances/{instance_id}/switch", switch_body)
        return {"action": "switched", "instance": result}
