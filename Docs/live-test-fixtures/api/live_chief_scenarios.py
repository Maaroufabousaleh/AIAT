"""Live chief/PM scenarios for G-006..G-015.

The project is disposable.  The calls are intentionally made through the
tool-service with each sender's role/team so policy and implementation are
tested together.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import httpx


SECRET = os.environ["TOOL_SECRET"]
AUTH = {"Authorization": f"Bearer {SECRET}"}


async def call(
    tools: httpx.AsyncClient,
    name: str,
    *,
    agent: str,
    role: str,
    team: str,
    project_id: str | None = None,
    kwargs: dict | None = None,
) -> dict:
    response = await tools.post(
        f"/tools/{name}/run",
        headers=AUTH,
        json={
            "agent_id": agent,
            "sender_role": role,
            "sender_team": team,
            "project_id": project_id,
            "kwargs": kwargs or {},
        },
    )
    response.raise_for_status()
    return response.json()


async def main() -> None:
    async with httpx.AsyncClient(base_url="http://orchestrator-api:8000", timeout=30) as api, httpx.AsyncClient(
        base_url="http://127.0.0.1:8002", timeout=30
    ) as tools:
        created = await api.post(
            "/projects",
            json={"name": f"G-chief-scenarios-{uuid.uuid4()}", "human_requester": "live-governance"},
        )
        created.raise_for_status()
        project_id = created.json()["id"]
        try:
            sprint = await call(
                tools,
                "sprint.create",
                agent="cto",
                role="c_suite",
                team="office_cto",
                project_id=project_id,
                kwargs={
                    "project_id": project_id,
                    "sprint_number": 1,
                    "milestone": "governance",
                    "goal": "role contract",
                    "planned_story_points": 3,
                    "estimated_hours": 2,
                    "team_id": "office_cto",
                },
            )
            assert sprint["success"] is True, sprint
            sprint_id = sprint["result"]["result"]["id"]

            cfo_kpi = await call(
                tools,
                "kpi.compute",
                agent="cfo",
                role="c_suite",
                team="office_cfo",
                project_id=project_id,
                kwargs={"project_id": project_id, "sprint_id": sprint_id},
            )
            assert cfo_kpi["success"] is True, cfo_kpi
            cfo_review = await call(
                tools,
                "review.submit",
                agent="cfo",
                role="c_suite",
                team="office_cfo",
                project_id=project_id,
                kwargs={
                    "session_id": str(uuid.uuid4()),
                    "reviewer_id": "cfo",
                    "verdict": "APPROVED_WITH_COMMENTS",
                    "comments": ["Budget is within the test envelope."],
                    "severity": "INFO",
                },
            )
            assert cfo_review["success"] is True, cfo_review

            cio_search = await call(
                tools,
                "capability.search",
                agent="cio",
                role="c_suite",
                team="office_cio",
                kwargs={"name": "tech"},
            )
            assert cio_search["success"] is True and cio_search["result"]["count"] >= 0
            chrm_workers = await call(
                tools,
                "capability.list_workers",
                agent="chrm",
                role="c_suite",
                team="office_chrm",
                kwargs={"status": "ACTIVE"},
            )
            assert chrm_workers["success"] is True and chrm_workers["result"]["count"] > 0
            chrm_history = await call(
                tools,
                "kpi.query_history",
                agent="chrm",
                role="c_suite",
                team="office_chrm",
                project_id=project_id,
                kwargs={"project_id": project_id},
            )
            assert chrm_history["success"] is True

            veto = await call(
                tools,
                "review.submit_veto",
                agent="cso",
                role="c_suite",
                team="office_cso",
                project_id=project_id,
                kwargs={"project_id": project_id, "actor_id": "cso", "reason": "live security evidence"},
            )
            assert veto["success"] is True, veto
            state_after_veto = (await api.get(f"/projects/{project_id}"))
            state_after_veto.raise_for_status()
            assert state_after_veto.json()["state"] == "SECURITY_BLOCKED"

            non_cso_veto = await call(
                tools,
                "review.submit_veto",
                agent="cfo",
                role="c_suite",
                team="office_cfo",
                project_id=project_id,
                kwargs={"project_id": project_id, "actor_id": "cfo", "reason": "must be denied"},
            )
            assert non_cso_veto["success"] is False and non_cso_veto["error_code"] == "FORBIDDEN"

            override = await call(
                tools,
                "approval.override_cso",
                agent="ceo",
                role="orchestrator",
                team="exec_ceo",
                project_id=project_id,
                kwargs={"project_id": project_id, "actor_id": "ceo", "action": "approve", "reason": "live CEO decision"},
            )
            assert override["success"] is True, override

            delegation = {}
            for team in ("dept_production", "dept_system", "dept_qa", "dept_devops"):
                result = await call(
                    tools,
                    "department_task",
                    agent="ceo",
                    role="orchestrator",
                    team="exec_ceo",
                    project_id=project_id,
                    kwargs={
                        "team": team,
                        "project_id": project_id,
                        "action": "GOVERNANCE_LIVE_TASK",
                        "description": f"live delegated contract for {team}",
                    },
                )
                assert result["success"] is True and result["result"]["status"] == "published", result
                delegation[team] = result["result"]["message_id"]
        finally:
            deleted = await api.delete(f"/projects/{project_id}")
            deleted.raise_for_status()

    print(
        json.dumps(
            {
                "status": "PASS",
                "project_id": project_id,
                "sprint_id": sprint_id,
                "cfo_kpi": cfo_kpi["result"],
                "cfo_review": cfo_review["result"],
                "cio_search_count": cio_search["result"]["count"],
                "active_worker_count": chrm_workers["result"]["count"],
                "chrm_history_count": chrm_history["result"]["total"],
                "veto_state": "SECURITY_BLOCKED",
                "non_cso_veto_error": non_cso_veto["error_code"],
                "delegation_message_ids": delegation,
                "deleted": True,
            },
            default=str,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
