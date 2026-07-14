"""Live C-007 probe: repeated message UUID appends only once."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from mas_core.protocols.envelope import MessageEnvelope
from mas_core.protocols.enums import AgentRole, MessageType


def _secret() -> str:
    for line in (Path(__file__).parents[3] / ".env").read_text().splitlines():
        if line.startswith("AGENT_TOKEN_SECRET="):
            return line.split("=", 1)[1]
    raise RuntimeError("AGENT_TOKEN_SECRET not found")


async def main() -> None:
    envelope = MessageEnvelope(
        msg_type=MessageType.HEARTBEAT,
        sender_id="live-c007",
        sender_role=AgentRole.ORCHESTRATOR,
        sender_team="exec_ceo",
        recipient_team="dept_production",
        project_id=None,
        payload={"fixture": "C-007"},
    )
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8001") as client:
        first = await client.post("/messages/publish", content=envelope.model_dump_json(), headers={"Content-Type": "application/json"})
        second = await client.post("/messages/publish", content=envelope.model_dump_json(), headers={"Content-Type": "application/json"})
        recent = await client.get(
            "/streams/dept_production/recent?limit=50",
            headers={"Authorization": f"Bearer live-c007:{_secret()}"},
        )
    first.raise_for_status()
    second.raise_for_status()
    one, two = first.json(), second.json()
    assert one["deduplicated"] is False
    assert two["deduplicated"] is True
    assert two["entry_id"] == one["entry_id"]
    recent.raise_for_status()
    matches = [entry for entry in recent.json()["entries"] if str(envelope.message_id) in entry["envelope"]]
    assert len(matches) == 1
    assert matches[0]["entry_id"] == one["entry_id"]
    print(json.dumps({"status": "PASS", "message_id": str(envelope.message_id), "entry_id": one["entry_id"], "first_deduplicated": False, "second_deduplicated": True, "stream_entries": len(matches)}))


if __name__ == "__main__":
    asyncio.run(main())
