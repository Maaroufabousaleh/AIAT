"""Inner F-003 probe: durable review session, deadlines, and comments."""

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


async def main() -> None:
    marker = f"F-003-{uuid4()}"
    async with httpx.AsyncClient(base_url="http://orchestrator-api:8000", timeout=20) as api:
        project_response = await api.post(
            "/projects",
            json={"name": marker, "description": "review persistence", "human_requester": "live-F003"},
        )
        project_response.raise_for_status()
        project_id = project_response.json()["id"]
        document_response = await api.post(
            f"/projects/{project_id}/documents",
            json={"doc_type": "PDR", "created_by": "live-F003"},
        )
        document_response.raise_for_status()
        document_id = document_response.json()["id"]

    parent = MessageEnvelope(
        msg_type=MessageType.DOCUMENT_SUBMIT,
        sender_id="live-F003-ceo",
        sender_role=AgentRole.ORCHESTRATOR,
        sender_team="exec_ceo",
        recipient_team="exec_coo",
        project_id=project_id,
        payload={
            "fixture": marker,
            "document_id": document_id,
            "doc_type": "PDR",
            "title": "Review persistence",
        },
    )
    redis_client = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8001", timeout=20) as router:
        published = await router.post(
            "/messages/publish",
            content=parent.model_dump_json(),
            headers={"Content-Type": "application/json"},
        )
        published.raise_for_status()

    session_id = None
    request_entries: dict[str, str] = {}
    for _ in range(30):
        for team in REVIEWERS:
            rows = await redis_client.xrange(f"stream:{team}", "-", "+")
            for entry_id, fields in rows:
                raw = fields.get("envelope", "")
                if marker not in raw:
                    continue
                data = json.loads(raw)
                if data.get("msg_type") != "REVIEW_REQUEST":
                    continue
                sid = data.get("payload", {}).get("session_id")
                session_id = session_id or sid
                if sid == session_id:
                    request_entries[team] = entry_id
        if len(request_entries) == len(REVIEWERS):
            break
        await asyncio.sleep(1)
    assert set(request_entries) == set(REVIEWERS), request_entries

    # The reviewer containers are stopped by the outer probe; inject four
    # deterministic responses into the real COO stream to exercise fan-in and
    # durable comment writes without an LLM-generated result.
    for index, team in enumerate(REVIEWERS, start=1):
        response = MessageEnvelope(
            msg_type=MessageType.REVIEW_RESPONSE,
            sender_id=f"live-{team}",
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
                "verdict": "APPROVED",
                "veto": False,
                "comments": [
                    {"severity": "INFO", "body": f"{team} durable comment {index}"}
                ],
            },
        )
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8001", timeout=20) as router:
            sent = await router.post(
                "/messages/publish",
                content=response.model_dump_json(),
                headers={"Content-Type": "application/json"},
            )
            sent.raise_for_status()

    sessions = []
    async with httpx.AsyncClient(base_url="http://orchestrator-api:8000", timeout=20) as api:
        for _ in range(30):
            result = await api.get(f"/projects/{project_id}/review-sessions")
            result.raise_for_status()
            sessions = result.json()
            if sessions and sessions[0]["status"] == "COMPLETED" and len(sessions[0]["comments"]) == 4:
                break
            await asyncio.sleep(1)
        assert len(sessions) == 1, sessions
        session = sessions[0]
        assert session["id"] == session_id
        assert set(session["reviewer_ids"]) == set(REVIEWERS)
        assert session["review_timeout_seconds"] >= 1
        assert session["timeout_count"] == 0
        assert len(session["comments"]) == 4
        assert {comment["reviewer_id"] for comment in session["comments"]} == {
            f"live-{team}" for team in REVIEWERS
        }

        # Remove exact request entries and the response entries after durable
        # inspection; project deletion below removes Postgres session/comments.
        for team, entry_id in request_entries.items():
            await redis_client.xack(f"stream:{team}", f"group:{team}", entry_id)
            await redis_client.xdel(f"stream:{team}", entry_id)
        coo_rows = await redis_client.xrange("stream:exec_coo", "-", "+")
        for entry_id, fields in coo_rows:
            if marker in fields.get("envelope", ""):
                await redis_client.xack("stream:exec_coo", "group:exec_coo", entry_id)
                await redis_client.xdel("stream:exec_coo", entry_id)
        deleted = await api.delete(f"/projects/{project_id}")
        deleted.raise_for_status()

    await redis_client.aclose()
    print(
        json.dumps(
            {
                "status": "PASS",
                "project_id": project_id,
                "document_id": document_id,
                "session_id": session_id,
                "reviewer_count": len(session["reviewer_ids"]),
                "comment_count": len(session["comments"]),
                "deadline_seconds": session["review_timeout_seconds"],
                "status_after_fanin": session["status"],
                "cleaned": True,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
