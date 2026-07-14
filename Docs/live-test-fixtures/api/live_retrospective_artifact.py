"""Live F-011 sprint retrospective aggregation and artifact proof."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid

import httpx


async def tool(
    client: httpx.AsyncClient,
    name: str,
    project_id: str,
    kwargs: dict,
) -> dict:
    response = await client.post(
        f"/tools/{name}/run",
        headers={"Authorization": f"Bearer {os.environ['TOOL_SECRET']}"},
        json={
            "agent_id": f"live-f011-{uuid.uuid4()}",
            "sender_role": "c_suite",
            "sender_team": "office_cto",
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
            json={"name": f"F-011 retrospective {uuid.uuid4()}", "human_requester": "live-F011"},
        )
        created.raise_for_status()
        project_id = created.json()["id"]
        sprint_result = await tool(
            tools,
            "sprint.create",
            project_id,
            {
                "project_id": project_id,
                "sprint_number": 1,
                "milestone": "MVP",
                "goal": "Prove retrospective artifact persistence",
                "planned_story_points": 5,
                "estimated_hours": 10,
                "team_id": "office_cto",
            },
        )
        sprint = sprint_result["result"]
        sprint_id = sprint["id"]
        await tool(
            tools,
            "sprint.activate",
            project_id,
            {"project_id": project_id, "sprint_id": sprint_id, "team_id": "office_cto"},
        )
        issue = await tool(
            tools,
            "issue.create",
            project_id,
            {
                "project_id": project_id,
                "sprint_id": sprint_id,
                "title": "Retrospective fixture issue",
                "issue_type": "TASK",
                "assigned_team": "dept_production",
                "story_points": 5,
                "estimated_hours": 10,
            },
        )
        issue_id = issue["result"]["id"]
        updated = await tool(
            tools,
            "issue.update_status",
            project_id,
            {
                "project_id": project_id,
                "issue_id": issue_id,
                "status": "DONE",
                "actual_hours": 8,
            },
        )
        assert updated["result"]["status"] == "DONE"
        closed = await tool(
            tools,
            "sprint.close",
            project_id,
            {"project_id": project_id, "sprint_id": sprint_id, "team_id": "office_cto"},
        )
        assert closed["result"]["status"] == "CLOSED"
        kpi = await tool(
            tools,
            "kpi.compute",
            project_id,
            {"project_id": project_id, "sprint_id": sprint_id},
        )
        retro = await tool(
            tools,
            "retrospective.generate",
            project_id,
            {"project_id": project_id, "sprint_id": sprint_id, "agent_id": "exec_coo"},
        )
        assert retro["report"]["metrics"]["completed_story_points"] == 5
        assert retro["report"]["metrics"]["task_completion_rate"] == 1.0
        assert retro["report"]["metrics"]["completed_issue_count"] == 1
        assert retro["blob"]["key"].endswith("retrospectives/sprint_1.json")
        assert retro["artifact"]["metadata"]["project_id"] == project_id
        assert retro["artifact"]["sha256"] == retro["blob"]["sha256"]

        artifacts = await api.get(f"/projects/{project_id}/artifacts")
        artifacts.raise_for_status()
        assert len(artifacts.json()) == 1
        artifact_id = artifacts.json()[0]["id"]
        downloaded = await tool(
            tools,
            "blob.download",
            project_id,
            {"project_id": project_id, "key": "retrospectives/sprint_1.json"},
        )
        report_bytes = downloaded["content"].encode("utf-8")
        report = json.loads(downloaded["content"])
        assert report["sprint_id"] == sprint_id
        assert hashlib.sha256(report_bytes).hexdigest() == retro["blob"]["sha256"]

        deleted_blob = await tool(
            tools,
            "blob.delete",
            project_id,
            {"project_id": project_id, "key": "retrospectives/sprint_1.json"},
        )
        assert deleted_blob["deleted"] is True
        deleted_artifact = await api.delete(f"/projects/{project_id}/artifacts/{artifact_id}")
        deleted_artifact.raise_for_status()
        deleted_project = await api.delete(f"/projects/{project_id}")
        deleted_project.raise_for_status()

    print(
        json.dumps(
            {
                "status": "PASS",
                "project_id": project_id,
                "sprint_id": sprint_id,
                "kpi_snapshot_id": kpi["snapshot_id"],
                "artifact_id": artifact_id,
                "artifact_key": retro["blob"]["key"],
                "sha256": retro["blob"]["sha256"],
                "deleted": True,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
