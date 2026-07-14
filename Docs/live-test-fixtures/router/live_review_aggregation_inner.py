"""Inner F-004 live aggregation matrix for approval/revision/reject/veto."""

from __future__ import annotations

import asyncio
import json
import os
from uuid import uuid4

import httpx
import redis.asyncio as redis

from mas_core.protocols.envelope import MessageEnvelope
from mas_core.protocols.enums import AgentRole, MessageType


REVIEWERS = ["office_cfo", "office_cio", "office_chrm", "office_cso"]


async def scenario(
    api: httpx.AsyncClient,
    router: httpx.AsyncClient,
    redis_client: redis.Redis,
    verdict: str,
    *,
    veto: bool = False,
) -> dict:
    marker = f"F-004-{verdict}-{uuid4()}"
    project = await api.post(
        "/projects",
        json={"name": marker, "human_requester": "live-F004"},
    )
    project.raise_for_status()
    project_id = project.json()["id"]
    document = await api.post(
        f"/projects/{project_id}/documents",
        json={"doc_type": "PDR", "created_by": "live-F004"},
    )
    document.raise_for_status()
    document_id = document.json()["id"]
    parent = MessageEnvelope(
        msg_type=MessageType.DOCUMENT_SUBMIT,
        sender_id="live-F004-pm",
        sender_role=AgentRole.ADMIN,
        sender_team="dept_production",
        recipient_team="exec_coo",
        project_id=project_id,
        payload={"fixture": marker, "document_id": document_id, "doc_type": "PDR"},
    )
    sent = await router.post("/messages/publish", content=parent.model_dump_json(), headers={"Content-Type": "application/json"})
    sent.raise_for_status()

    session_id = None
    request_entries: dict[str, str] = {}
    for _ in range(30):
        for team in REVIEWERS:
            for entry_id, fields in await redis_client.xrange(f"stream:{team}", "-", "+"):
                raw = fields.get("envelope", "")
                if marker not in raw:
                    continue
                data = json.loads(raw)
                if data.get("msg_type") == "REVIEW_REQUEST":
                    sid = data.get("payload", {}).get("session_id")
                    session_id = session_id or sid
                    if sid == session_id:
                        request_entries[team] = entry_id
        if len(request_entries) == len(REVIEWERS):
            break
        await asyncio.sleep(1)
    assert len(request_entries) == len(REVIEWERS), request_entries

    response_teams = ["office_cso"] if veto else REVIEWERS
    for index, team in enumerate(response_teams, start=1):
        response = MessageEnvelope(
            msg_type=MessageType.REVIEW_RESPONSE,
            sender_id=f"f004-{team}",
            sender_role=AgentRole.C_SUITE,
            sender_team=team,
            recipient_team="exec_coo",
            project_id=project_id,
            correlation_id=parent.correlation_id,
            payload={
                "fixture": marker,
                "session_id": session_id,
                "document_id": document_id,
                "reviewer_role": team.removeprefix("office_").upper(),
                "verdict": verdict,
                "veto": veto,
                "comments": [
                    {
                        "severity": "BLOCKER" if veto or verdict == "REJECTED" else ("MAJOR" if verdict == "NEEDS_REVISION" else "INFO"),
                        "body": f"{verdict} evidence {index}",
                        "veto": veto,
                    }
                ],
            },
        )
        sent = await router.post("/messages/publish", content=response.model_dump_json(), headers={"Content-Type": "application/json"})
        sent.raise_for_status()

    session = None
    for _ in range(30):
        rows = await api.get(f"/projects/{project_id}/review-sessions")
        rows.raise_for_status()
        sessions = rows.json()
        if sessions and sessions[0]["status"] != "IN_PROGRESS" and len(sessions[0]["comments"]) >= (1 if veto else 4):
            session = sessions[0]
            break
        await asyncio.sleep(1)
    assert session is not None, marker
    expected_status = "VETOED" if veto else ({"APPROVED": "COMPLETED", "NEEDS_REVISION": "NEEDS_REVISION", "REJECTED": "REJECTED"}[verdict])
    assert session["status"] == expected_status, session
    expected_comments = 1 if veto else 4
    assert len(session["comments"]) == expected_comments

    revision_entry = None
    if verdict == "NEEDS_REVISION" and not veto:
        for _ in range(10):
            for entry_id, fields in await redis_client.xrange("stream:dept_production", "-", "+"):
                if document_id in fields.get("envelope", "") and '"DOCUMENT_REVISION"' in fields.get("envelope", ""):
                    revision_entry = entry_id
                    break
            if revision_entry:
                break
            await asyncio.sleep(1)
        assert revision_entry is not None

    for team, entry_id in request_entries.items():
        await redis_client.xack(f"stream:{team}", f"group:{team}", entry_id)
        await redis_client.xdel(f"stream:{team}", entry_id)
    if revision_entry:
        await redis_client.xack("stream:dept_production", "group:dept_production", revision_entry)
        await redis_client.xdel("stream:dept_production", revision_entry)
    for entry_id, fields in await redis_client.xrange("stream:exec_coo", "-", "+"):
        if marker in fields.get("envelope", ""):
            await redis_client.xack("stream:exec_coo", "group:exec_coo", entry_id)
            await redis_client.xdel("stream:exec_coo", entry_id)
    deleted = await api.delete(f"/projects/{project_id}")
    deleted.raise_for_status()
    return {"verdict": verdict, "veto": veto, "project_id": project_id, "session_id": session["id"], "status": session["status"], "comments": len(session["comments"]), "revision_entry": revision_entry}


async def main() -> None:
    redis_client = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    async with httpx.AsyncClient(base_url="http://orchestrator-api:8000", timeout=20) as api, httpx.AsyncClient(base_url="http://127.0.0.1:8001", timeout=20) as router:
        results = [
            await scenario(api, router, redis_client, "APPROVED"),
            await scenario(api, router, redis_client, "NEEDS_REVISION"),
            await scenario(api, router, redis_client, "REJECTED"),
            await scenario(api, router, redis_client, "REJECTED", veto=True),
        ]
    await redis_client.aclose()
    print(json.dumps({"status": "PASS", "scenarios": results, "matrix_count": len(results), "cleaned": True}))


if __name__ == "__main__":
    asyncio.run(main())
