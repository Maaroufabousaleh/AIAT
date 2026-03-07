"""Sprint/Issue and KPI/utility tools."""

from __future__ import annotations

from typing import Any

from mas_core.protocols.enums import AgentRole
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup

_CSUITE = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE]
_ADMIN = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE, AgentRole.ADMIN]
_EXEC = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]


# ── Sprint ─────────────────────────────────────────────────────────────────

class SprintCreateTool(BaseTool):
    name = "sprint.create"
    group = ToolGroup.SPRINT_ISSUE
    description = "Create a new sprint for a team."
    allowed_roles = _CSUITE
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        return {"sprint_id": "spr-stub-001", "team": kwargs.get("team", ""), "state": "PLANNED"}


class SprintActivateTool(BaseTool):
    name = "sprint.activate"
    group = ToolGroup.SPRINT_ISSUE
    description = "Activate a sprint, transitioning it to IN_PROGRESS."
    allowed_roles = _CSUITE
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        return {"sprint_id": kwargs.get("sprint_id", ""), "state": "IN_PROGRESS"}


class SprintCloseTool(BaseTool):
    name = "sprint.close"
    group = ToolGroup.SPRINT_ISSUE
    description = "Close a sprint and generate the sprint report."
    allowed_roles = _CSUITE
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        return {"sprint_id": kwargs.get("sprint_id", ""), "state": "CLOSED", "report": {}}


# ── Issues ─────────────────────────────────────────────────────────────────

class IssueCreateTool(BaseTool):
    name = "issue.create"
    group = ToolGroup.SPRINT_ISSUE
    description = "Create a new issue/work-item in a sprint."
    allowed_roles = _CSUITE
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        return {
            "issue_id": "iss-stub-001",
            "sprint_id": kwargs.get("sprint_id", ""),
            "title": kwargs.get("title", ""),
            "status": "OPEN",
        }


class IssueDecomposeTool(BaseTool):
    name = "issue.decompose"
    group = ToolGroup.SPRINT_ISSUE
    description = "Decompose an issue into sub-tasks."
    allowed_roles = _CSUITE
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        return {
            "parent_issue_id": kwargs.get("issue_id", ""),
            "sub_tasks": [],
        }


class IssueUpdateStatusTool(BaseTool):
    name = "issue.update_status"
    group = ToolGroup.SPRINT_ISSUE
    description = "Update the status of an issue."
    allowed_roles = _ADMIN
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        return {
            "issue_id": kwargs.get("issue_id", ""),
            "old_status": "OPEN",
            "new_status": kwargs.get("status", "IN_PROGRESS"),
        }


# ── KPI ────────────────────────────────────────────────────────────────────

class KPIComputeSprintTool(BaseTool):
    name = "kpi.compute"
    group = ToolGroup.KPI_UTILITY
    description = "Compute KPI snapshot (sprint or project scope)."
    allowed_roles = _CSUITE
    cache_ttl_seconds = 30

    async def execute(self, **kwargs: Any) -> Any:
        scope = kwargs.get("scope", "sprint")
        return {"scope": scope, "project_id": kwargs.get("project_id", ""), "sprint_id": kwargs.get("sprint_id", ""), "velocity": 0, "defect_rate": 0.0}


class KPIComputeProjectTool(BaseTool):
    name = "kpi.compute_project"
    group = ToolGroup.KPI_UTILITY
    description = "Compute project-level KPIs across all sprints."
    allowed_roles = _CSUITE
    cache_ttl_seconds = 30

    async def execute(self, **kwargs: Any) -> Any:
        return {"project_id": kwargs.get("project_id", ""), "total_velocity": 0, "sprints_completed": 0}


class KPIQueryHistoryTool(BaseTool):
    name = "kpi.query_history"
    group = ToolGroup.KPI_UTILITY
    description = "Query historical KPI data with filters."
    allowed_roles = _EXEC
    cache_ttl_seconds = 30

    async def execute(self, **kwargs: Any) -> Any:
        return {"records": [], "total": 0}


class KPIUpdateAgentProfileTool(BaseTool):
    name = "kpi.update_agent_profile"
    group = ToolGroup.KPI_UTILITY
    description = "Update an agent's performance profile based on KPI data."
    allowed_roles = _CSUITE
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        return {"agent_id": kwargs.get("agent_id", ""), "updated": True}


# ── Velocity & estimation ─────────────────────────────────────────────────

class VelocityReportTool(BaseTool):
    name = "velocity.report"
    group = ToolGroup.KPI_UTILITY
    description = "Generate a velocity report for a team/sprint."
    allowed_roles = _CSUITE
    cache_ttl_seconds = 60

    async def execute(self, **kwargs: Any) -> Any:
        return {"team": kwargs.get("team", ""), "velocity_trend": []}


class EstimationAdjustTool(BaseTool):
    name = "estimation.adjust"
    group = ToolGroup.KPI_UTILITY
    description = "Adjust story-point estimation model based on actuals."
    allowed_roles = _CSUITE
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        return {"model_version": 2, "adjusted": True}
