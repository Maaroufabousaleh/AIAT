"""Live D-005 approval-gate advance/reject probe.

Exercises the built-in project workflow (rather than a simulated flow node):
``FEASIBILITY_CHECK -> FEASIBILITY_REPORT`` creates a persisted human gate,
then the API records one rejection and one approval on disposable projects.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx


async def _start_feasibility(client: httpx.AsyncClient, label: str) -> tuple[str, dict]:
    response = await client.post(
        "/projects",
        json={
            "name": f"D-005 {label} {uuid.uuid4()}",
            "description": "D-005 disposable human feasibility decision",
            "human_requester": "live-D005",
        },
    )
    response.raise_for_status()
    project = response.json()
    assert project["state"] == "FEASIBILITY_CHECK", project
    transition = await client.post(
        f"/projects/{project['id']}/transition",
        json={"event": "all_reviews_in", "actor_id": "live-D005", "context": {"fixture": "D-005"}},
    )
    transition.raise_for_status()
    assert transition.json()["next_state"] == "FEASIBILITY_REPORT"
    return project["id"], project


async def _pending_gate(client: httpx.AsyncClient, project_id: str) -> dict:
    response = await client.get(f"/projects/{project_id}/pending-decisions")
    response.raise_for_status()
    gates = response.json()
    assert len(gates) == 1, gates
    assert gates[0]["status"] == "PENDING"
    assert gates[0]["gate_type"] == "feasibility"
    return gates[0]


def _decision_from_timeline(timeline: list[dict], gate_id: str) -> dict:
    for event in timeline:
        if event.get("event_type") == "approval_gate" and event.get("details", {}).get("id") == gate_id:
            return event["details"]
    raise AssertionError(f"gate {gate_id} missing from audit timeline: {timeline}")


async def main() -> None:
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=20) as client:
        rejected_id, _ = await _start_feasibility(client, "reject")
        rejected_gate = await _pending_gate(client, rejected_id)
        rejected = await client.post(
            f"/projects/{rejected_id}/decisions",
            json={"decision": "REJECTED", "comments": "Not viable", "decided_by": "live-D005"},
        )
        rejected.raise_for_status()
        rejected_body = rejected.json()
        assert rejected_body["status"] == "transitioned", rejected_body
        assert rejected_body["next_state"] == "ARCHIVED", rejected_body
        rejected_project = await client.get(f"/projects/{rejected_id}")
        rejected_project.raise_for_status()
        assert rejected_project.json()["state"] == "ARCHIVED"
        rejected_pending = await client.get(f"/projects/{rejected_id}/pending-decisions")
        rejected_pending.raise_for_status()
        assert rejected_pending.json() == []
        rejected_timeline = await client.get(f"/projects/{rejected_id}/audit-timeline")
        rejected_timeline.raise_for_status()
        rejected_record = _decision_from_timeline(rejected_timeline.json(), str(rejected_gate["id"]))
        assert rejected_record["status"] == "REJECTED"

        approved_id, _ = await _start_feasibility(client, "approve")
        approved_gate = await _pending_gate(client, approved_id)
        approved = await client.post(
            f"/projects/{approved_id}/decisions",
            json={"decision": "APPROVED", "comments": "Feasible", "decided_by": "live-D005"},
        )
        approved.raise_for_status()
        approved_body = approved.json()
        assert approved_body["status"] == "transitioned", approved_body
        assert approved_body["next_state"] == "PDR_CREATION", approved_body
        approved_project = await client.get(f"/projects/{approved_id}")
        approved_project.raise_for_status()
        assert approved_project.json()["state"] == "PDR_CREATION"
        approved_pending = await client.get(f"/projects/{approved_id}/pending-decisions")
        approved_pending.raise_for_status()
        assert approved_pending.json() == []
        approved_timeline = await client.get(f"/projects/{approved_id}/audit-timeline")
        approved_timeline.raise_for_status()
        approved_record = _decision_from_timeline(approved_timeline.json(), str(approved_gate["id"]))
        assert approved_record["status"] == "APPROVED"

        deleted_rejected = await client.delete(f"/projects/{rejected_id}")
        deleted_rejected.raise_for_status()
        deleted_approved = await client.delete(f"/projects/{approved_id}")
        deleted_approved.raise_for_status()

    print(
        json.dumps(
            {
                "status": "PASS",
                "rejected_project": rejected_id,
                "rejected_gate": str(rejected_gate["id"]),
                "rejected_state": "ARCHIVED",
                "approved_project": approved_id,
                "approved_gate": str(approved_gate["id"]),
                "approved_state": "PDR_CREATION",
                "gates_persisted_and_decided": True,
                "deleted": True,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
