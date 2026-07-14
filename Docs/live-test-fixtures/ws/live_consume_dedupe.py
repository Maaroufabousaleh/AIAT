"""Live C-008 probe: duplicate stream entries produce one observable delivery."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import websockets

from mas_core.protocols.envelope import MessageEnvelope
from mas_core.protocols.enums import AgentRole, MessageType


CONTAINER = "mas-message-router-1"
TEAM = "dept_production"
CONSUMER = "live-c008"


def _secret() -> str:
    for line in (Path(__file__).parents[3] / ".env").read_text().splitlines():
        if line.startswith("AGENT_TOKEN_SECRET="):
            return line.split("=", 1)[1]
    raise RuntimeError("AGENT_TOKEN_SECRET not found")


def _redis_python(source: str, stdin: str = "") -> str:
    result = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "python", "-c", source],
        input=stdin,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def _inject_twice(envelope_json: str) -> list[str]:
    source = """
import asyncio, json, os, sys
import redis.asyncio as redis
async def main():
    client = redis.from_url(os.environ['REDIS_URL'], decode_responses=True)
    payload = sys.stdin.read()
    ids = [await client.xadd('stream:dept_production', {'envelope': payload}) for _ in range(2)]
    await client.aclose()
    print(json.dumps(ids))
asyncio.run(main())
"""
    return json.loads(_redis_python(source, envelope_json))


def _pending_for_consumer() -> list[dict[str, object]]:
    source = """
import asyncio, json, os
import redis.asyncio as redis
async def main():
    client = redis.from_url(os.environ['REDIS_URL'], decode_responses=True)
    rows = await client.xpending_range('stream:dept_production', 'group:dept_production', '-', '+', 20, consumername='live-c008')
    await client.aclose()
    print(json.dumps(rows))
asyncio.run(main())
"""
    return json.loads(_redis_python(source))


async def _connect():
    headers = {"Authorization": f"Bearer {CONSUMER}:{_secret()}"}
    url = f"ws://127.0.0.1:8001/ws/subscribe/{TEAM}"
    try:
        return await websockets.connect(url, additional_headers=headers)
    except TypeError:
        return await websockets.connect(url, extra_headers=headers)


async def main() -> None:
    envelope = MessageEnvelope(
        msg_type=MessageType.HEARTBEAT,
        sender_id=CONSUMER,
        sender_role=AgentRole.ORCHESTRATOR,
        sender_team="exec_ceo",
        recipient_team=TEAM,
        project_id=None,
        payload={"fixture": "C-008"},
    )
    ws = await _connect()
    injected = _inject_twice(envelope.model_dump_json())
    frame = json.loads(await asyncio.wait_for(ws.recv(), 5))
    assert frame["type"] == "MESSAGE"
    assert frame["envelope"]["message_id"] == str(envelope.message_id)
    await ws.send(json.dumps({"type": "ACK", "entry_id": frame["entry_id"], "message_id": str(envelope.message_id)}))
    try:
        duplicate = json.loads(await asyncio.wait_for(ws.recv(), 1))
        assert duplicate.get("envelope", {}).get("message_id") != str(envelope.message_id)
    except TimeoutError:
        pass
    await asyncio.sleep(0.25)
    await ws.close()
    pending = _pending_for_consumer()
    assert not any(row["message_id"] in injected for row in pending)
    print(json.dumps({"status": "PASS", "message_id": str(envelope.message_id), "injected_entries": injected, "observable_deliveries": 1, "pending_duplicates": 0}))


if __name__ == "__main__":
    asyncio.run(main())
