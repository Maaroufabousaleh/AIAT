"""Inner D-003 live atomic invalid-transition probe."""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx


async def main() -> None:
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=20) as client:
        created = await client.post("/projects", json={"name": f"D-003 live {uuid.uuid4()}", "human_requester": "live-D003"})
        created.raise_for_status()
        project_id = created.json()["id"]
        before = await client.get(f"/projects/{project_id}")
        history_before = await client.get(f"/projects/{project_id}/state-history")
        before.raise_for_status()
        history_before.raise_for_status()
        invalid = await client.post(
            f"/projects/{project_id}/transition",
            json={"event": "pdr_submitted", "actor_id": "live-D003"},
        )
        assert invalid.status_code == 409, invalid.text
        after = await client.get(f"/projects/{project_id}")
        history_after = await client.get(f"/projects/{project_id}/state-history")
        after.raise_for_status()
        history_after.raise_for_status()
        assert after.json()["state"] == before.json()["state"] == "FEASIBILITY_CHECK"
        assert len(history_after.json()) == len(history_before.json())
        deleted = await client.delete(f"/projects/{project_id}")
        deleted.raise_for_status()
    print(json.dumps({"status": "PASS", "project_id": project_id, "invalid_status": 409, "state": "FEASIBILITY_CHECK", "history_unchanged": True, "deleted": True}))


if __name__ == "__main__":
    asyncio.run(main())
