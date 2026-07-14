"""Inner D-004 live feasibility fan-out probe, run in router container."""

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
    project_id = str(uuid4())
    document_id = str(uuid4())
    parent = MessageEnvelope(
        msg_type=MessageType.DOCUMENT_SUBMIT,
        sender_id="live-D004-ceo",
        sender_role=AgentRole.ORCHESTRATOR,
        sender_team="exec_ceo",
        recipient_team="exec_coo",
        project_id=project_id,
        payload={"fixture": "D-004", "document_id": document_id, "doc_type": "PDR", "title": "Feasibility fan-out"},
    )
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8001") as client:
        response = await client.post("/messages/publish", content=parent.model_dump_json(), headers={"Content-Type": "application/json"})
        response.raise_for_status()

    client = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    session_id = None
    matches: dict[str, tuple[str, str]] = {}
    for _ in range(30):
        for team in REVIEWERS:
            rows = await client.xrange(f"stream:{team}", "-", "+")
            for entry_id, fields in rows:
                raw = fields.get("envelope", "")
                if '"D-004"' not in raw:
                    continue
                data = json.loads(raw)
                if data.get("msg_type") == "REVIEW_REQUEST":
                    sid = data.get("payload", {}).get("session_id")
                    session_id = session_id or sid
                    if sid == session_id:
                        matches[team] = (entry_id, data["message_id"])
        if len(matches) == len(REVIEWERS):
            break
        await asyncio.sleep(1)
    assert set(matches) == set(REVIEWERS), {"expected": REVIEWERS, "found": sorted(matches)}
    for team, (entry_id, _) in matches.items():
        await client.xack(f"stream:{team}", f"group:{team}", entry_id)
        await client.xdel(f"stream:{team}", entry_id)
    await client.aclose()
    print(json.dumps({"status": "PASS", "project_id": project_id, "document_id": document_id, "session_id": session_id, "reviewer_teams": sorted(matches), "reviewer_count": len(matches), "cleaned_entries": len(matches)}))


if __name__ == "__main__":
    asyncio.run(main())
