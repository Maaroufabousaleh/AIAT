"""Live C-014 worker-to-CEO policy bypass denial probe."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from mas_core.protocols.envelope import MessageEnvelope
from mas_core.protocols.enums import AgentRole, MessageType


def _secret() -> str:
    import os
    if os.environ.get("ROUTER_SECRET"):
        return os.environ["ROUTER_SECRET"]
    for line in (Path(__file__).parents[3] / ".env").read_text().splitlines():
        if line.startswith("AGENT_TOKEN_SECRET="):
            return line.split("=", 1)[1]
    raise RuntimeError("AGENT_TOKEN_SECRET not found")


async def main() -> None:
    envelope = MessageEnvelope(
        msg_type=MessageType.RESULT,
        sender_id="live-c014-worker",
        sender_role=AgentRole.WORKER,
        sender_team="dept_system",
        recipient_team="exec_ceo",
        project_id="live-c014",
        payload={"fixture": "C-014", "attempt": "worker-to-CEO bypass"},
    )
    auth = {"Authorization": f"Bearer live-c014:{_secret()}"}
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8001") as client:
        response = await client.post(
            "/messages/publish",
            content=envelope.model_dump_json(),
            headers={"Content-Type": "application/json"},
        )
        recent = await client.get("/streams/exec_ceo/recent?limit=500", headers=auth)
    assert response.status_code == 403
    detail = str(response.json()["detail"])
    assert "dept_system" in detail or "exec_ceo" in detail or "worker" in detail.lower()
    recent.raise_for_status()
    matches = [row for row in recent.json()["entries"] if str(envelope.message_id) in row["envelope"]]
    assert matches == []
    print(json.dumps({"status": "PASS", "message_id": str(envelope.message_id), "http_status": 403, "stream_entries": 0, "deny_reason": detail}))


if __name__ == "__main__":
    asyncio.run(main())
