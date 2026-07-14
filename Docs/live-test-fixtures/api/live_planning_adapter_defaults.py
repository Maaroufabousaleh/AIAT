"""Live F-012 default planning-adapter/catalog check."""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import httpx


async def main() -> None:
    async with httpx.AsyncClient(base_url="http://orchestrator-api:8000", timeout=20) as api, httpx.AsyncClient(
        base_url="http://127.0.0.1:8002", timeout=20
    ) as tools:
        manifest_response = await tools.get("/tools")
        manifest_response.raise_for_status()
        manifest = manifest_response.json()["tools"]
        names = {str(entry.get("tool_name")) for entry in manifest}
        assert {"sprint.create", "sprint.activate", "issue.create", "issue.list"} <= names
        restricted = sorted(
            name for name in names if any(token in name.lower() for token in ("plane", "openproject"))
        )
        assert restricted == []

        workers_response = await api.get("/capabilities/workers")
        workers_response.raise_for_status()
        workers = workers_response.json()
        planning_workers = {
            worker["name"]: worker
            for worker in workers
            if worker.get("name") in {"planner", "sprint_planner"}
        }
        assert set(planning_workers) == {"planner", "sprint_planner"}
        assert all(
            worker.get("adapter_config", {}).get("default_planning_adapter") == "ccpm"
            for worker in planning_workers.values()
        )
        planner = planning_workers["planner"]
        assert planner.get("adapter_config", {}).get("optional_issue_adapter") == "github_issues"

        created = await api.post(
            "/projects",
            json={"name": f"F-012 planning adapter {uuid.uuid4()}", "human_requester": "live-F012"},
        )
        created.raise_for_status()
        project_id = created.json()["id"]
        sprint_response = await tools.post(
            "/tools/sprint.create/run",
            headers={"Authorization": f"Bearer {os.environ['TOOL_SECRET']}"},
            json={
                "agent_id": f"live-f012-{uuid.uuid4()}",
                "sender_role": "c_suite",
                "sender_team": "office_cto",
                "project_id": project_id,
                "kwargs": {
                    "project_id": project_id,
                    "sprint_number": 1,
                    "milestone": "adapter-check",
                    "goal": "Use default ccpm planning adapter",
                    "planned_story_points": 1,
                    "estimated_hours": 1,
                    "team_id": "office_cto",
                },
            },
        )
        sprint_response.raise_for_status()
        sprint_body = sprint_response.json()
        assert sprint_body["success"], sprint_body
        sprint = sprint_body["result"]["result"]
        assert sprint["status"] == "PLANNED"
        sprints = await api.get(f"/projects/{project_id}/sprints")
        sprints.raise_for_status()
        assert len(sprints.json()) == 1
        deleted = await api.delete(f"/projects/{project_id}")
        deleted.raise_for_status()

    print(
        json.dumps(
            {
                "status": "PASS",
                "project_id": project_id,
                "sprint_id": sprint["id"],
                "default_adapter": "ccpm",
                "optional_issue_adapter": "github_issues",
                "restricted_tools": restricted,
                "deleted": True,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
