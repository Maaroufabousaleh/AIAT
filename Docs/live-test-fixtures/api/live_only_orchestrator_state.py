"""Inner D-001 live negative probe for the forbidden direct state route."""

from __future__ import annotations

import asyncio
import json

import httpx


async def main() -> None:
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000") as client:
        projects = await client.get("/projects?limit=1")
        projects.raise_for_status()
        rows = projects.json()
        assert rows
        project_id = rows[0]["id"]
        before = rows[0]["state"]
        direct = await client.put(f"/projects/{project_id}/state", json={"state": "DONE"})
        assert direct.status_code == 404
        after = await client.get(f"/projects/{project_id}")
        after.raise_for_status()
        assert after.json()["state"] == before
    print(json.dumps({"status": "PASS", "project_id": project_id, "state": before, "direct_put_status": 404, "state_unchanged": True}))


if __name__ == "__main__":
    asyncio.run(main())
