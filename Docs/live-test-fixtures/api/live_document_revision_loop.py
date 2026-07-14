"""Live D-006 document/version and human-edit loop probe."""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx


async def transition(client: httpx.AsyncClient, project_id: str, event: str, expected: str) -> dict:
    response = await client.post(
        f"/projects/{project_id}/transition",
        json={"event": event, "actor_id": "live-D006", "context": {"fixture": "D-006"}},
    )
    response.raise_for_status()
    body = response.json()
    assert body["next_state"] == expected, body
    return body


async def create_doc(client: httpx.AsyncClient, project_id: str, doc_type: str, key: str) -> dict:
    response = await client.post(
        f"/projects/{project_id}/documents",
        json={
            "doc_type": doc_type,
            "created_by": "live-D006",
            "blob_bucket": "aiat-artifacts",
            "blob_key": key,
            "blob_sha256": uuid.uuid4().hex,
        },
    )
    response.raise_for_status()
    return response.json()


async def set_status(client: httpx.AsyncClient, project_id: str, doc_id: str, status: str) -> dict:
    response = await client.patch(
        f"/projects/{project_id}/documents/{doc_id}/status",
        json={"status": status},
    )
    response.raise_for_status()
    return response.json()


async def main() -> None:
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=20) as client:
        created = await client.post(
            "/projects",
            json={"name": f"D-006 live {uuid.uuid4()}", "human_requester": "live-D006"},
        )
        created.raise_for_status()
        project_id = created.json()["id"]

        await transition(client, project_id, "all_reviews_in", "FEASIBILITY_REPORT")
        gates = await client.get(f"/projects/{project_id}/pending-decisions")
        gates.raise_for_status()
        assert len(gates.json()) == 1
        approved = await client.post(
            f"/projects/{project_id}/decisions",
            json={"decision": "APPROVED", "decided_by": "live-D006"},
        )
        approved.raise_for_status()
        assert approved.json()["next_state"] == "PDR_CREATION"

        pdr_v1 = await create_doc(client, project_id, "PDR", f"{project_id}/pdr-v1.md")
        assert pdr_v1["version"] == 1 and pdr_v1["status"] == "DRAFT"
        pdr_review = await set_status(client, project_id, pdr_v1["id"], "IN_REVIEW")
        assert pdr_review["status"] == "IN_REVIEW"
        await transition(client, project_id, "pdr_submitted", "PDR_REVIEW")
        pdr_approved = await set_status(client, project_id, pdr_v1["id"], "APPROVED")
        assert pdr_approved["status"] == "APPROVED"
        await transition(client, project_id, "all_reviews_in", "CDR_CREATION")

        cdr_v1 = await create_doc(client, project_id, "CDR", f"{project_id}/cdr-v1.md")
        assert cdr_v1["version"] == 1
        await set_status(client, project_id, cdr_v1["id"], "IN_REVIEW")
        await transition(client, project_id, "cdr_submitted", "CDR_REVIEW")
        await set_status(client, project_id, cdr_v1["id"], "NEEDS_REVISION")
        await transition(client, project_id, "cdr_presented", "HUMAN_APPROVAL")

        edit = await client.post(
            f"/projects/{project_id}/decisions",
            json={
                "decision": "EDITS",
                "comments": "Clarify the data-flow boundary",
                "edits": {"section": "data-flow", "requested": True},
                "decided_by": "live-D006",
            },
        )
        edit.raise_for_status()
        assert edit.json()["next_state"] == "CDR_CREATION"

        cdr_v2 = await client.post(
            f"/projects/{project_id}/documents/{cdr_v1['id']}/revisions",
            json={
                "created_by": "live-D006",
                "blob_bucket": "aiat-artifacts",
                "blob_key": f"{project_id}/cdr-v2.md",
                "blob_sha256": uuid.uuid4().hex,
            },
        )
        cdr_v2.raise_for_status()
        cdr_v2 = cdr_v2.json()
        assert cdr_v2["version"] == 2 and cdr_v2["status"] == "DRAFT"

        versions = await client.get(f"/projects/{project_id}/documents", params={"doc_type": "CDR"})
        versions.raise_for_status()
        cdr_versions = versions.json()
        assert [row["version"] for row in cdr_versions] == [2, 1], cdr_versions
        assert cdr_versions[1]["status"] == "SUPERSEDED"

        await set_status(client, project_id, cdr_v2["id"], "IN_REVIEW")
        await transition(client, project_id, "cdr_submitted", "CDR_REVIEW")
        await set_status(client, project_id, cdr_v2["id"], "APPROVED")
        await transition(client, project_id, "cdr_presented", "HUMAN_APPROVAL")
        final_approval = await client.post(
            f"/projects/{project_id}/decisions",
            json={"decision": "APPROVED", "decided_by": "live-D006"},
        )
        final_approval.raise_for_status()
        assert final_approval.json()["next_state"] == "RR_CREATION"

        documents = await client.get(f"/projects/{project_id}/documents")
        documents.raise_for_status()
        rows = documents.json()
        assert {(row["doc_type"], row["version"]) for row in rows} == {
            ("PDR", 1),
            ("CDR", 1),
            ("CDR", 2),
        }
        deleted = await client.delete(f"/projects/{project_id}")
        deleted.raise_for_status()

    print(
        json.dumps(
            {
                "status": "PASS",
                "project_id": project_id,
                "pdr_version": 1,
                "cdr_versions": [1, 2],
                "edit_state": "CDR_CREATION",
                "final_state": "RR_CREATION",
                "superseded_v1": True,
                "deleted": True,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
