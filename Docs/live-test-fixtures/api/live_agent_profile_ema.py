"""Live F-009 durable agent-profile EMA probe."""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import httpx


async def run_tool(
    client: httpx.AsyncClient,
    project_id: str,
    kwargs: dict,
) -> dict:
    response = await client.post(
        "/tools/kpi.update_agent_profile/run",
        headers={"Authorization": f"Bearer {os.environ['TOOL_SECRET']}"},
        json={
            "agent_id": f"live-f009-{uuid.uuid4()}",
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
    agent_id = f"live-f009-{uuid.uuid4()}"
    project_id = str(uuid.uuid4())
    async with httpx.AsyncClient(base_url="http://orchestrator-api:8000", timeout=20) as api, httpx.AsyncClient(
        base_url="http://127.0.0.1:8002", timeout=20
    ) as tools:
        first_response = await tools.post(
            "/tools/kpi.update_agent_profile/run",
            headers={"Authorization": f"Bearer {os.environ['TOOL_SECRET']}"},
            json={
                "agent_id": f"fixture-{agent_id}",
                "sender_role": "c_suite",
                "sender_team": "office_cto",
                "project_id": project_id,
                "kwargs": {
                    "agent_id": agent_id,
                    "team_id": "dept_production",
                    "role": "planner",
                    "estimated_hours": 8,
                    "actual_hours": 12,
                    "tasks_completed": 1,
                    "alpha": 0.5,
                },
            },
        )
        first_response.raise_for_status()
        first = first_response.json()
        assert first["success"], first
        first_profile = first["result"]["result"]
        assert first["result"]["action"] == "UPDATE_AGENT_PROFILE"
        assert first_profile["correction_factor"] == "1.2500"
        assert first_profile["estimation_bias"] == "2.0000"
        assert first_profile["confidence"] == "0.7500"

        second_response = await tools.post(
            "/tools/kpi.update_agent_profile/run",
            headers={"Authorization": f"Bearer {os.environ['TOOL_SECRET']}"},
            json={
                "agent_id": f"fixture-{agent_id}-2",
                "sender_role": "c_suite",
                "sender_team": "office_cto",
                "project_id": project_id,
                "kwargs": {
                    "agent_id": agent_id,
                    "estimated_hours": 8,
                    "actual_hours": 4,
                    "tasks_completed": 1,
                    "alpha": 0.5,
                },
            },
        )
        second_response.raise_for_status()
        second = second_response.json()
        assert second["success"], second
        second_profile = second["result"]["result"]
        assert second["result"]["action"] == "UPDATE_AGENT_PROFILE"
        assert second_profile["correction_factor"] == "0.8750"
        assert second_profile["estimation_bias"] == "-1.0000"
        assert second_profile["total_tasks_completed"] == 2

        persisted_response = await api.get(f"/agent-profiles/{agent_id}")
        persisted_response.raise_for_status()
        persisted = persisted_response.json()
        assert persisted["correction_factor"] == "0.8750"
        assert persisted["total_estimated_hours"] == "16.00"
        assert persisted["total_actual_hours"] == "16.00"
        assert persisted["team_id"] == "dept_production"
        assert persisted["role"] == "planner"

    print(
        json.dumps(
            {
                "status": "PASS",
                "agent_id": agent_id,
                "first_correction_factor": first_profile["correction_factor"],
                "second_correction_factor": second_profile["correction_factor"],
                "tasks_completed": persisted["total_tasks_completed"],
                "durable": True,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
