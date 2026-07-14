"""Live F-005/F-006 planning-tool probe."""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import httpx


async def tool(client: httpx.AsyncClient, name: str, role: str, team: str, project_id: str, kwargs: dict) -> dict:
    response = await client.post(
        f"/tools/{name}/run",
        headers={"Authorization": f"Bearer {os.environ['TOOL_SECRET']}"},
        json={
            "agent_id": f"live-{role}",
            "sender_role": role,
            "sender_team": team,
            "project_id": project_id,
            "kwargs": kwargs,
        },
    )
    response.raise_for_status()
    body = response.json()
    assert body["success"], body
    return body["result"]


async def main() -> None:
    async with httpx.AsyncClient(base_url="http://orchestrator-api:8000", timeout=20) as api, httpx.AsyncClient(
        base_url="http://127.0.0.1:8002", timeout=20
    ) as tools:
        created = await api.post(
            "/projects",
            json={"name": f"F-005/F-006 live {uuid.uuid4()}", "human_requester": "live-F005"},
        )
        created.raise_for_status()
        project_id = created.json()["id"]

        sprint_result = await tool(
            tools,
            "sprint.create",
            "c_suite",
            "office_cto",
            project_id,
            {
                "project_id": project_id,
                "sprint_number": 1,
                "milestone": "MVP",
                "goal": "Deliver governed planning slice",
                "planned_story_points": 8,
                "estimated_hours": 16,
                "team_id": "office_cto",
            },
        )
        sprint = sprint_result["result"]
        sprint_id = sprint["id"]
        assert sprint_result["action"] == "CREATE_SPRINT" and sprint["status"] == "PLANNED"

        activated_result = await tool(
            tools,
            "sprint.activate",
            "c_suite",
            "office_cto",
            project_id,
            {"project_id": project_id, "sprint_id": sprint_id, "team_id": "office_cto"},
        )
        assert activated_result["result"]["status"] == "IN_PROGRESS"

        issue_result = await tool(
            tools,
            "issue.create",
            "c_suite",
            "office_cto",
            project_id,
            {
                "project_id": project_id,
                "sprint_id": sprint_id,
                "title": "Implement bounded planning adapter",
                "description": "Parent issue for decomposition",
                "issue_type": "FEATURE",
                "priority": "high",
                "assigned_team": "dept_production",
                "story_points": 5,
                "estimated_hours": 10,
            },
        )
        parent = issue_result["result"]
        parent_id = parent["id"]
        assert parent["parent_issue_id"] is None

        decomposed = await tool(
            tools,
            "issue.decompose",
            "c_suite",
            "office_cto",
            project_id,
            {
                "project_id": project_id,
                "issue_id": parent_id,
                "sub_tasks": [
                    {"title": "Write requirements", "story_points": 2},
                    {"title": "Add integration tests", "story_points": 3},
                ],
            },
        )
        children = decomposed["result"]["children"]
        assert len(children) == 2 and all(child["parent_issue_id"] == parent_id for child in children)

        updated = await tool(
            tools,
            "issue.update_status",
            "admin",
            "dept_production",
            project_id,
            {"project_id": project_id, "issue_id": children[0]["id"], "status": "DONE", "actual_hours": 3},
        )
        assert updated["result"]["status"] == "DONE"

        listed = await tool(
            tools,
            "issue.list",
            "admin",
            "dept_production",
            project_id,
            {"project_id": project_id, "sprint_id": sprint_id},
        )
        assert listed["total"] == 3
        assert any(row["parent_issue_id"] == parent_id for row in listed["issues"])

        closed_result = await tool(
            tools,
            "sprint.close",
            "c_suite",
            "office_cto",
            project_id,
            {"project_id": project_id, "sprint_id": sprint_id, "team_id": "office_cto"},
        )
        assert closed_result["result"]["status"] == "CLOSED"
        sprint_kpi = await tool(
            tools,
            "kpi.compute",
            "c_suite",
            "office_cto",
            project_id,
            {"project_id": project_id, "sprint_id": sprint_id},
        )
        assert sprint_kpi["snapshot_id"] and sprint_kpi["velocity"] == 2
        assert sprint_kpi["task_completion_rate"] == 0.25
        project_kpi = await tool(
            tools,
            "kpi.compute_project",
            "c_suite",
            "office_cto",
            project_id,
            {"project_id": project_id},
        )
        assert project_kpi["snapshot_id"] and project_kpi["total_velocity"] == 2
        velocity = await tool(
            tools,
            "velocity.report",
            "c_suite",
            "office_cto",
            project_id,
            {"project_id": project_id},
        )
        assert velocity["project_id"] == project_id
        assert velocity["velocity_trend"][0]["status"] == "CLOSED"
        sprints = await api.get(f"/projects/{project_id}/sprints")
        sprints.raise_for_status()
        assert sprints.json()[0]["status"] == "CLOSED"
        snapshots = await api.get(f"/projects/{project_id}/kpi")
        snapshots.raise_for_status()
        assert len(snapshots.json()) == 2
        deleted = await api.delete(f"/projects/{project_id}")
        deleted.raise_for_status()

    print(
        json.dumps(
            {
                "status": "PASS",
                "project_id": project_id,
                "sprint_id": sprint_id,
                "issue_count": 3,
                "children": len(children),
                "updated_status": "DONE",
                "closed_status": "CLOSED",
                "velocity_rows": len(velocity["velocity_trend"]),
                "kpi_snapshots": len(snapshots.json()),
                "deleted": True,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
