"""Sprint/Issue and KPI/utility tools — real implementations.

Sprint and issue tools call the orchestrator-api for persistence.
KPI tools compute metrics from orchestrator-api data.
"""

from __future__ import annotations

import logging
from typing import Any

from mas_core.protocols.enums import AgentRole
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup

from ._orch_client import orch_get, orch_post

logger = logging.getLogger(__name__)

_CSUITE = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE]
_ADMIN = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE, AgentRole.ADMIN]
_EXEC = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]


# ── Sprint ─────────────────────────────────────────────────────────────────


class SprintCreateTool(BaseTool):
    name = "sprint.create"
    group = ToolGroup.SPRINT_ISSUE
    description = "Create a new sprint for a project."
    allowed_roles = _CSUITE
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        body = {
            "team_id": kwargs.get("team_id", "exec_coo"),
            "project_id": kwargs.get("project_id"),
            "payload": {
                "action": "CREATE_SPRINT",
                "project_id": kwargs.get("project_id", ""),
                "sprint_number": kwargs.get("sprint_number", 1),
                "milestone": kwargs.get("milestone"),
                "goal": kwargs.get("goal"),
                "planned_story_points": kwargs.get("planned_story_points"),
                "estimated_hours": kwargs.get("estimated_hours"),
            },
        }
        return await orch_post("/tasks", body)


class SprintActivateTool(BaseTool):
    name = "sprint.activate"
    group = ToolGroup.SPRINT_ISSUE
    description = "Activate a sprint, transitioning it to IN_PROGRESS."
    allowed_roles = _CSUITE
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        body = {
            "team_id": kwargs.get("team_id", "exec_coo"),
            "payload": {
                "action": "ACTIVATE_SPRINT",
                "sprint_id": kwargs.get("sprint_id", ""),
                "project_id": kwargs.get("project_id", ""),
            },
        }
        return await orch_post("/tasks", body)


class SprintCloseTool(BaseTool):
    name = "sprint.close"
    group = ToolGroup.SPRINT_ISSUE
    description = "Close a sprint and generate the sprint report."
    allowed_roles = _CSUITE
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        body = {
            "team_id": kwargs.get("team_id", "exec_coo"),
            "payload": {
                "action": "CLOSE_SPRINT",
                "sprint_id": kwargs.get("sprint_id", ""),
                "project_id": kwargs.get("project_id", ""),
            },
        }
        return await orch_post("/tasks", body)


# ── Issues ─────────────────────────────────────────────────────────────────


class IssueCreateTool(BaseTool):
    name = "issue.create"
    group = ToolGroup.SPRINT_ISSUE
    description = "Create a new issue/work-item in a sprint."
    allowed_roles = _CSUITE
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        body = {
            "team_id": kwargs.get("team_id", "exec_coo"),
            "project_id": kwargs.get("project_id"),
            "payload": {
                "action": "CREATE_ISSUE",
                "project_id": kwargs.get("project_id", ""),
                "sprint_id": kwargs.get("sprint_id"),
                "title": kwargs.get("title", ""),
                "description": kwargs.get("description"),
                "issue_type": kwargs.get("issue_type", "TASK"),
                "priority": kwargs.get("priority", "medium"),
                "assigned_team": kwargs.get("assigned_team"),
                "estimated_hours": kwargs.get("estimated_hours"),
                "story_points": kwargs.get("story_points"),
            },
        }
        return await orch_post("/tasks", body)


class IssueDecomposeTool(BaseTool):
    name = "issue.decompose"
    group = ToolGroup.SPRINT_ISSUE
    description = "Decompose an issue into sub-tasks."
    allowed_roles = _CSUITE
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        body = {
            "team_id": kwargs.get("team_id", "exec_coo"),
            "payload": {
                "action": "DECOMPOSE_ISSUE",
                "issue_id": kwargs.get("issue_id", ""),
                "project_id": kwargs.get("project_id", ""),
                "sub_tasks": kwargs.get("sub_tasks", []),
            },
        }
        return await orch_post("/tasks", body)


class IssueUpdateStatusTool(BaseTool):
    name = "issue.update_status"
    group = ToolGroup.SPRINT_ISSUE
    description = "Update the status of an issue."
    allowed_roles = _ADMIN
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        body = {
            "team_id": kwargs.get("team_id", "exec_coo"),
            "payload": {
                "action": "UPDATE_ISSUE_STATUS",
                "issue_id": kwargs.get("issue_id", ""),
                "status": kwargs.get("status", "IN_PROGRESS"),
                "actual_hours": kwargs.get("actual_hours"),
            },
        }
        return await orch_post("/tasks", body)


# ── KPI ────────────────────────────────────────────────────────────────────


class KPIComputeSprintTool(BaseTool):
    name = "kpi.compute"
    group = ToolGroup.KPI_UTILITY
    description = "Compute KPI snapshot for a sprint."
    allowed_roles = _CSUITE
    cache_ttl_seconds = 30

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        sprints = await orch_get(f"/projects/{project_id}/sprints")
        if not isinstance(sprints, list) or not sprints:
            return {"scope": "sprint", "project_id": project_id, "message": "No sprints found"}

        sprint_id = kwargs.get("sprint_id")
        sprint = None
        if sprint_id:
            sprint = next((s for s in sprints if s.get("id") == sprint_id), None)
        else:
            sprint = sprints[-1]

        if sprint is None:
            return {"scope": "sprint", "error": "Sprint not found"}

        planned_points = sprint.get("planned_story_points") or 0
        completed_points = sprint.get("completed_story_points") or 0
        estimated_hours = float(sprint.get("estimated_hours") or 0)
        actual_hours = float(sprint.get("actual_hours") or 0)

        velocity = completed_points
        estimation_accuracy = 0.0
        if estimated_hours > 0:
            estimation_accuracy = (
                min(1.0, actual_hours / estimated_hours) if actual_hours > 0 else 0.0
            )
        task_completion_rate = completed_points / planned_points if planned_points > 0 else 0.0

        return {
            "scope": "sprint",
            "project_id": project_id,
            "sprint_id": sprint.get("id"),
            "velocity": velocity,
            "estimation_accuracy": round(estimation_accuracy, 4),
            "task_completion_rate": round(task_completion_rate, 4),
            "planned_points": planned_points,
            "completed_points": completed_points,
        }


class KPIComputeProjectTool(BaseTool):
    name = "kpi.compute_project"
    group = ToolGroup.KPI_UTILITY
    description = "Compute project-level KPIs across all sprints."
    allowed_roles = _CSUITE
    cache_ttl_seconds = 30

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        sprints = await orch_get(f"/projects/{project_id}/sprints")
        if not isinstance(sprints, list):
            sprints = []

        total_velocity = sum(s.get("completed_story_points") or 0 for s in sprints)
        total_planned = sum(s.get("planned_story_points") or 0 for s in sprints)
        total_estimated = sum(float(s.get("estimated_hours") or 0) for s in sprints)
        total_actual = sum(float(s.get("actual_hours") or 0) for s in sprints)
        completed_sprints = sum(1 for s in sprints if s.get("status") in ("CLOSED", "COMPLETED"))

        budget_adherence = 0.0
        if total_estimated > 0:
            budget_adherence = min(1.0, total_estimated / total_actual) if total_actual > 0 else 1.0

        return {
            "project_id": project_id,
            "total_velocity": total_velocity,
            "sprints_completed": completed_sprints,
            "total_sprints": len(sprints),
            "total_planned_points": total_planned,
            "total_estimated_hours": round(total_estimated, 2),
            "total_actual_hours": round(total_actual, 2),
            "budget_adherence": round(budget_adherence, 4),
        }


class KPIQueryHistoryTool(BaseTool):
    name = "kpi.query_history"
    group = ToolGroup.KPI_UTILITY
    description = "Query historical KPI data with filters."
    allowed_roles = _EXEC
    cache_ttl_seconds = 30

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        # Get project state history as a proxy for KPI history
        history = await orch_get(
            f"/projects/{project_id}/state-history",
            params={"limit": kwargs.get("limit", 100)},
        )
        return {"records": history, "total": len(history) if isinstance(history, list) else 0}


class KPIUpdateAgentProfileTool(BaseTool):
    name = "kpi.update_agent_profile"
    group = ToolGroup.KPI_UTILITY
    description = "Update an agent's performance profile based on KPI data."
    allowed_roles = _CSUITE
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        # Agent profile updates are sent as tasks to the orchestrator
        body = {
            "team_id": "office_cfo",
            "payload": {
                "action": "UPDATE_AGENT_PROFILE",
                "agent_id": kwargs.get("agent_id", ""),
                "correction_factor": kwargs.get("correction_factor"),
                "estimation_bias": kwargs.get("estimation_bias"),
                "total_tasks_completed": kwargs.get("total_tasks_completed"),
            },
        }
        return await orch_post("/tasks", body)


# ── Velocity & estimation ─────────────────────────────────────────────────


class VelocityReportTool(BaseTool):
    name = "velocity.report"
    group = ToolGroup.KPI_UTILITY
    description = "Generate a velocity report for a project."
    allowed_roles = _CSUITE
    cache_ttl_seconds = 60

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        sprints = await orch_get(f"/projects/{project_id}/sprints")
        if not isinstance(sprints, list):
            sprints = []

        velocity_trend = [
            {
                "sprint_number": s.get("sprint_number"),
                "completed_points": s.get("completed_story_points") or 0,
                "planned_points": s.get("planned_story_points") or 0,
                "status": s.get("status"),
            }
            for s in sprints
        ]

        avg_velocity = 0.0
        completed = [v["completed_points"] for v in velocity_trend if v["completed_points"] > 0]
        if completed:
            avg_velocity = sum(completed) / len(completed)

        return {
            "project_id": project_id,
            "velocity_trend": velocity_trend,
            "average_velocity": round(avg_velocity, 1),
            "total_sprints": len(sprints),
        }


class EstimationAdjustTool(BaseTool):
    name = "estimation.adjust"
    group = ToolGroup.KPI_UTILITY
    description = "Adjust story-point estimation model based on actuals."
    allowed_roles = _CSUITE
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        body = {
            "team_id": "office_cfo",
            "payload": {
                "action": "ADJUST_ESTIMATION",
                "project_id": kwargs.get("project_id", ""),
                "agent_id": kwargs.get("agent_id"),
                "adjustment_factor": kwargs.get("adjustment_factor", 1.0),
            },
        }
        return await orch_post("/tasks", body)
