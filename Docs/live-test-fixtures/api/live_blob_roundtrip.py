"""Live F-002 object-storage reference and integrity probe."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid

import httpx


async def call_tool(client: httpx.AsyncClient, tool: str, project_id: str, kwargs: dict) -> dict:
    response = await client.post(
        f"/tools/{tool}/run",
        headers={"Authorization": f"Bearer {os.environ['TOOL_SECRET']}"},
        json={
            "agent_id": "live-F002",
            "sender_role": "admin",
            "sender_team": "dept_production",
            "project_id": project_id,
            "kwargs": kwargs,
        },
    )
    response.raise_for_status()
    body = response.json()
    assert body["success"], body
    return body["result"]


async def main() -> None:
    content = "AIAT F-002 blob integrity fixture\n" + uuid.uuid4().hex
    expected_sha256 = hashlib.sha256(content.encode()).hexdigest()
    async with httpx.AsyncClient(base_url="http://orchestrator-api:8000", timeout=20) as api, httpx.AsyncClient(
        base_url="http://127.0.0.1:8002", timeout=20
    ) as tools:
        created = await api.post(
            "/projects",
            json={"name": f"F-002 live {uuid.uuid4()}", "human_requester": "live-F002"},
        )
        created.raise_for_status()
        project_id = created.json()["id"]
        key = "f002/integrity-fixture.txt"

        uploaded = await call_tool(
            tools,
            "blob.upload",
            project_id,
            {"project_id": project_id, "key": key, "content": content, "content_type": "text/plain"},
        )
        assert uploaded["uploaded"] is True
        assert uploaded["sha256"] == expected_sha256, uploaded

        document = await api.post(
            f"/projects/{project_id}/documents",
            json={
                "doc_type": "PDR",
                "created_by": "live-F002",
                "blob_bucket": uploaded["bucket"],
                "blob_key": uploaded["key"],
                "blob_sha256": uploaded["sha256"],
            },
        )
        document.raise_for_status()
        document = document.json()
        assert document["blob_bucket"] == uploaded["bucket"]
        assert document["blob_key"] == uploaded["key"]
        assert document["blob_sha256"] == expected_sha256

        downloaded = await call_tool(
            tools,
            "blob.download",
            project_id,
            {"project_id": project_id, "key": key},
        )
        assert downloaded["content"] == content
        assert downloaded["size_bytes"] == len(content.encode())

        listed = await call_tool(
            tools,
            "blob.list",
            project_id,
            {"project_id": project_id, "prefix": "f002/"},
        )
        assert listed["count"] >= 1
        assert any(item.get("key", "").endswith(key) for item in listed["objects"]), listed

        deleted_blob = await call_tool(
            tools,
            "blob.delete",
            project_id,
            {"project_id": project_id, "key": key},
        )
        assert deleted_blob["deleted"] is True
        deleted_project = await api.delete(f"/projects/{project_id}")
        deleted_project.raise_for_status()

    print(
        json.dumps(
            {
                "status": "PASS",
                "project_id": project_id,
                "bucket": uploaded["bucket"],
                "key": uploaded["key"],
                "sha256_verified": True,
                "download_verified": True,
                "listed": True,
                "deleted": True,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
