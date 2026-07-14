"""Live F-010 profile-aware estimate comparison."""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import httpx


async def observe(api: httpx.AsyncClient, agent_id: str, **kwargs: object) -> dict:
    response = await api.post(f"/agent-profiles/{agent_id}/observations", json=kwargs)
    response.raise_for_status()
    return response.json()


async def estimate(tools: httpx.AsyncClient, agent_id: str, raw: float) -> dict:
    response = await tools.post(
        "/tools/estimation.adjust/run",
        headers={"Authorization": f"Bearer {os.environ['TOOL_SECRET']}"},
        json={
            "agent_id": f"live-f010-{uuid.uuid4()}",
            "sender_role": "c_suite",
            "sender_team": "office_cto",
            "kwargs": {"agent_id": agent_id, "raw_estimate_hours": raw},
        },
    )
    response.raise_for_status()
    body = response.json()
    assert body["success"], body
    return body["result"]


async def main() -> None:
    baseline_id = f"live-f010-baseline-{uuid.uuid4()}"
    learned_id = f"live-f010-learned-{uuid.uuid4()}"
    async with httpx.AsyncClient(base_url="http://orchestrator-api:8000", timeout=20) as api, httpx.AsyncClient(
        base_url="http://127.0.0.1:8002", timeout=20
    ) as tools:
        baseline_profile = await observe(
            api,
            baseline_id,
            team_id="dept_production",
            role="planner",
            estimated_hours=0,
            actual_hours=0,
            tasks_completed=0,
        )
        learned_profile = await observe(
            api,
            learned_id,
            team_id="dept_production",
            role="planner",
            estimated_hours=8,
            actual_hours=12,
            tasks_completed=1,
            alpha=0.5,
        )
        baseline = await estimate(tools, baseline_id, 8)
        learned = await estimate(tools, learned_id, 8)

        assert baseline["adjusted_estimate_hours"] == 8.0
        assert baseline["correction_factor"] == 1.0
        assert learned["adjusted_estimate_hours"] == 12.0
        assert learned["correction_factor"] == 1.25
        assert learned["estimation_bias"] == 2.0
        assert learned["adjusted_estimate_hours"] > baseline["adjusted_estimate_hours"]
        assert baseline_profile["correction_factor"] == "1.0000"
        assert learned_profile["correction_factor"] == "1.2500"

    print(
        json.dumps(
            {
                "status": "PASS",
                "baseline_agent_id": baseline_id,
                "learned_agent_id": learned_id,
                "raw_estimate_hours": 8,
                "baseline_adjusted_hours": baseline["adjusted_estimate_hours"],
                "learned_adjusted_hours": learned["adjusted_estimate_hours"],
                "factor": learned["correction_factor"],
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
