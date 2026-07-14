"""Inner D-002 live 14-state project lifecycle probe."""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx


EVENTS = [
    ("all_reviews_in", "FEASIBILITY_REPORT"),
    ("human_approved", "PDR_CREATION"),
    ("pdr_submitted", "PDR_REVIEW"),
    ("all_reviews_in", "CDR_CREATION"),
    ("cdr_submitted", "CDR_REVIEW"),
    ("cdr_presented", "HUMAN_APPROVAL"),
    ("human_approved", "RR_CREATION"),
    ("rr_submitted", "SPRINT_PLANNING"),
    ("sprints_created", "INFRA_PROVISIONING"),
    ("infra_ready", "IN_PROGRESS"),
    ("all_sprints_done", "RETROSPECTIVE"),
    ("retrospective_done", "KPI_PERSISTENCE"),
    ("kpi_saved", "COMPLETED"),
    ("archive_requested", "ARCHIVED"),
]


async def main() -> None:
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=20) as client:
        created = await client.post(
            "/projects",
            json={"name": f"C-002 live {uuid.uuid4()}", "description": "D-002 disposable full workflow", "human_requester": "live-D002"},
        )
        created.raise_for_status()
        project = created.json()
        project_id = project["id"]
        assert project["state"] == "FEASIBILITY_CHECK"
        states = ["INIT", "FEASIBILITY_CHECK"]
        for index, (event, expected) in enumerate(EVENTS, start=1):
            response = await client.post(
                f"/projects/{project_id}/transition",
                json={"event": event, "actor_id": "live-D002", "context": {"fixture": "D-002", "step": index}},
            )
            response.raise_for_status()
            body = response.json()
            assert body["next_state"] == expected, body
            states.append(expected)
        history = await client.get(f"/projects/{project_id}/state-history?limit=100")
        history.raise_for_status()
        rows = history.json()
        assert len(rows) >= len(EVENTS) + 1
        assert rows[0]["to_state"] == "ARCHIVED"
        assert any(row["to_state"] == "COMPLETED" for row in rows)
        deleted = await client.delete(f"/projects/{project_id}")
        deleted.raise_for_status()
    print(json.dumps({"status": "PASS", "project_id": project_id, "transition_count": len(EVENTS) + 1, "final_state": "ARCHIVED", "states": states, "history_rows_before_cleanup": len(rows), "deleted": True}))


if __name__ == "__main__":
    asyncio.run(main())
