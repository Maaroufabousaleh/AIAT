"""Inner C-015 live chain probe, run in the router container."""

from __future__ import annotations

import asyncio
import json
import os
from uuid import uuid4

import httpx
import redis.asyncio as redis

from mas_core.protocols.envelope import MessageEnvelope
from mas_core.protocols.enums import AgentRole, MessageType


async def main() -> None:
    trace = str(uuid4())
    cases = [
        ("worker_to_pm", AgentRole.WORKER, "live-worker", "dept_production", "dept_production"),
        ("pm_to_coo", AgentRole.ADMIN, "live-pm", "dept_production", "exec_coo"),
        ("coo_to_ceo", AgentRole.EXECUTIVE, "live-coo", "exec_coo", "exec_ceo"),
    ]
    entries: list[dict[str, str]] = []
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8001") as client:
        for step, role, sender_id, sender_team, recipient_team in cases:
            envelope = MessageEnvelope(
                msg_type=MessageType.ESCALATION,
                sender_id=sender_id,
                sender_role=role,
                sender_team=sender_team,
                recipient_team=recipient_team,
                project_id=trace,
                correlation_id=trace,
                payload={"fixture": "C-015", "step": step, "trace": trace},
            )
            response = await client.post(
                "/messages/publish",
                content=envelope.model_dump_json(),
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            result = response.json()
            entries.append({"step": step, "message_id": str(envelope.message_id), "entry_id": result["entry_id"], "team": recipient_team})

    redis_client = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    for item in entries:
        rows = await redis_client.xrange(f"stream:{item['team']}", "-", "+")
        matches = [entry_id for entry_id, fields in rows if item["message_id"] in fields.get("envelope", "")]
        assert matches == [item["entry_id"]]
        await redis_client.xdel(f"stream:{item['team']}", item["entry_id"])
    await redis_client.aclose()
    print(json.dumps({"status": "PASS", "trace": trace, "chain": entries, "accepted_steps": 3, "cleaned_entries": 3}))


if __name__ == "__main__":
    asyncio.run(main())
