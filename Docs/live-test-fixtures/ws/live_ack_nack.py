"""Live C-005 probe: NACK preserves a PEL entry; reconnect + ACK clears it."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx
import websockets

from mas_core.protocols.envelope import MessageEnvelope
from mas_core.protocols.enums import AgentRole, MessageType


def _secret() -> str:
    for line in (Path(__file__).parents[3] / ".env").read_text().splitlines():
        if line.startswith("AGENT_TOKEN_SECRET="):
            return line.split("=", 1)[1]
    raise RuntimeError("AGENT_TOKEN_SECRET not found")


async def _connect(secret: str):
    headers = {"Authorization": f"Bearer live-c005:{secret}"}
    try:
        return await websockets.connect(
            "ws://127.0.0.1:8001/ws/subscribe/dept_production", additional_headers=headers
        )
    except TypeError:
        return await websockets.connect(
            "ws://127.0.0.1:8001/ws/subscribe/dept_production", extra_headers=headers
        )


async def main() -> None:
    secret = _secret()
    envelope = MessageEnvelope(
        msg_type=MessageType.TASK,
        sender_id="live-c005",
        sender_role=AgentRole.ORCHESTRATOR,
        sender_team="exec_ceo",
        recipient_team="dept_production",
        project_id="live-c005",
        payload={"fixture": "C-005"},
    )
    ws = await _connect(secret)
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://127.0.0.1:8001/messages/publish",
            content=envelope.model_dump_json(),
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
    first = json.loads(await asyncio.wait_for(ws.recv(), 5))
    assert first["type"] == "MESSAGE"
    assert first["envelope"]["message_id"] == str(envelope.message_id)
    await ws.send(json.dumps({"type": "NACK", "entry_id": first["entry_id"], "reason": "live retry"}))
    await ws.close()

    ws = await _connect(secret)
    replay = json.loads(await asyncio.wait_for(ws.recv(), 5))
    assert replay["type"] == "MESSAGE"
    assert replay["entry_id"] == first["entry_id"]
    await ws.send(json.dumps({"type": "ACK", "entry_id": replay["entry_id"], "message_id": str(envelope.message_id)}))
    await asyncio.sleep(0.25)
    await ws.close()

    ws = await _connect(secret)
    try:
        post_ack = json.loads(await asyncio.wait_for(ws.recv(), 1))
        assert post_ack.get("entry_id") != replay["entry_id"], "ACKed entry was delivered again"
    except TimeoutError:
        pass
    finally:
        await ws.close()
    print(json.dumps({"status": "PASS", "entry_id": replay["entry_id"], "nack_replayed": True, "ack_cleared": True}))


if __name__ == "__main__":
    asyncio.run(main())
