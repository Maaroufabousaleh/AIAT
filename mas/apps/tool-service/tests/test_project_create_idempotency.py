"""Project creation preserves trusted protocol idempotency across tool dispatch."""

from __future__ import annotations

from uuid import uuid4

import pytest


@pytest.mark.anyio
async def test_project_create_uses_protocol_key_and_ignores_forged_context(client, monkeypatch):
    import tool_service.tools.project as project_mod
    from tool_service.tools.project import ProjectCreateTool

    calls: list[dict | None] = []
    protocol_key = uuid4()
    forged_key = str(uuid4())

    async def fake_orch_post(path, body=None):
        calls.append(body)
        return {"id": "project-1", **(body or {})}

    monkeypatch.setattr(project_mod, "orch_post", fake_orch_post)

    response = await client.post(
        "/tools/execute",
        json={
            "agent_id": "ceo-agent",
            "sender_role": "orchestrator",
            "tool_name": "project.create",
            "idempotency_key": str(protocol_key),
            "kwargs": {
                "name": "Idempotent project",
                "idempotency_key": forged_key,
                "_aiat_context": {
                    "idempotency_key": forged_key,
                    "caller_id": "forged-caller",
                    "caller_role": "worker",
                },
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert calls[0]["idempotency_key"] == str(protocol_key)
    assert ProjectCreateTool.idempotent is False
    assert ProjectCreateTool.cache_ttl_seconds == 0


@pytest.mark.anyio
async def test_project_create_does_not_forward_kwargs_key_without_protocol_key(client, monkeypatch):
    import tool_service.tools.project as project_mod

    calls: list[dict | None] = []
    forged_key = str(uuid4())

    async def fake_orch_post(path, body=None):
        calls.append(body)
        return {"id": "project-1", **(body or {})}

    monkeypatch.setattr(project_mod, "orch_post", fake_orch_post)

    response = await client.post(
        "/tools/execute",
        json={
            "agent_id": "ceo-agent",
            "sender_role": "orchestrator",
            "tool_name": "project.create",
            "kwargs": {
                "name": "Non-forged project",
                "idempotency_key": forged_key,
                "_aiat_context": {"idempotency_key": forged_key},
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert "idempotency_key" not in calls[0]
