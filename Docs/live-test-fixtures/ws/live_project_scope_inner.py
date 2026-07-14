"""Inner C-016 project/team-scoped WebSocket probe."""

from __future__ import annotations

import asyncio
import json
import os
from uuid import uuid4

import httpx
import redis.asyncio as redis
import websockets

from mas_core.protocols.envelope import MessageEnvelope
from mas_core.protocols.enums import AgentRole, MessageType


async def main() -> None:
    project_a = str(uuid4())
    project_b = str(uuid4())
    secret = os.environ["ROUTER_SECRET"]
    headers = {"Authorization": f"Bearer live-c016:{secret}"}
    ws = await websockets.connect(
        f"ws://127.0.0.1:8001/ws/subscribe/dept_production?project_id={project_a}",
        additional_headers=headers,
    )
    envelopes = []
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8001") as client:
        for project_id, label in ((project_a, "in-scope"), (project_b, "cross-project")):
            envelope = MessageEnvelope(
                msg_type=MessageType.RESULT,
                sender_id="live-c016-publisher",
                sender_role=AgentRole.ORCHESTRATOR,
                sender_team="exec_ceo",
                recipient_team="dept_production",
                project_id=project_id,
                payload={"fixture": "C-016", "label": label},
            )
            response = await client.post(
                "/messages/publish",
                content=envelope.model_dump_json(),
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            envelopes.append((envelope, response.json()["entry_id"]))

    received = json.loads(await asyncio.wait_for(ws.recv(), 5))
    assert received["type"] == "MESSAGE"
    assert received["envelope"]["project_id"] == project_a
    await ws.send(json.dumps({"type": "ACK", "entry_id": received["entry_id"], "message_id": received["envelope"]["message_id"]}))
    try:
        extra = json.loads(await asyncio.wait_for(ws.recv(), 1))
        assert extra.get("envelope", {}).get("project_id") != project_b
    except TimeoutError:
        pass
    await ws.close()

    client = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    # The scoped consumer transfers out-of-scope entries to a deterministic
    # project consumer rather than leaving them owned by the wrong subscriber.
    pending = await client.xpending_range(
        "stream:dept_production",
        "group:dept_production",
        "-",
        "+",
        20,
        consumername=f"project-scope:{project_b}",
    )
    assert any(row["message_id"] == envelopes[1][1] for row in pending)
    for _, entry_id in envelopes:
        await client.xack("stream:dept_production", "group:dept_production", entry_id)
        await client.xdel("stream:dept_production", entry_id)
    await client.aclose()
    print(json.dumps({"status": "PASS", "team": "dept_production", "project_scope": project_a, "cross_project": project_b, "received_project": project_a, "cross_project_frames": 0, "cross_project_transferred_to": f"project-scope:{project_b}", "cleaned_entries": 2}))


if __name__ == "__main__":
    asyncio.run(main())
