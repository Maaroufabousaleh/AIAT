"""Tests for the deployed team-runner control-plane storage boundary."""

from __future__ import annotations

from uuid import UUID, uuid4

import httpx
import pytest


@pytest.mark.anyio
async def test_storage_client_allowlists_team_path_and_serializes_values() -> None:
    from team_runner.storage_client import ControlPlaneStorageClient

    project_id = uuid4()
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["team_header"] = request.headers.get("x-aiat-team-id")
        body = request.read()
        seen["body"] = body
        return httpx.Response(200, json={"checkpoint_id": str(uuid4())})

    client = ControlPlaneStorageClient(
        orchestrator_url="http://orchestrator-api:8000",
        api_key="worker-secret",
        team_id="office_cio",
    )
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://test",
        headers={"X-API-Key": "worker-secret", "X-AIAT-Team-ID": "office_cio"},
    )
    checkpoint_id = await client.save(
        agent_id="tech_analyst",
        team_id="office_cio",
        task_message_id="task-1",
        iteration=2,
        messages_json=[{"role": "user", "content": "hello"}],
        project_id=project_id,
        task_envelope_json={"project_id": str(project_id)},
    )
    await client.close()

    assert isinstance(checkpoint_id, UUID)
    assert seen["path"] == "/internal/team-runners/office_cio/storage"
    assert seen["team_header"] == "office_cio"
    assert f'"project_id":"{project_id}"'.encode() in seen["body"]  # type: ignore[operator]


@pytest.mark.anyio
async def test_storage_client_raises_with_control_plane_detail() -> None:
    from team_runner.storage_client import ControlPlaneStorageClient

    client = ControlPlaneStorageClient(
        orchestrator_url="http://orchestrator-api:8000",
        api_key="worker-secret",
        team_id="office_cio",
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "team identity mismatch"})

    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://test",
        headers={"X-API-Key": "worker-secret", "X-AIAT-Team-ID": "office_cio"},
    )
    try:
        with pytest.raises(RuntimeError, match="team identity mismatch"):
            await client.get_document(uuid4())
    finally:
        await client.close()


@pytest.mark.anyio
async def test_storage_health_check_requires_explicit_ok_response() -> None:
    from team_runner.storage_client import ControlPlaneStorageClient

    client = ControlPlaneStorageClient(
        orchestrator_url="http://orchestrator-api:8000",
        api_key="worker-secret",
        team_id="office_cio",
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://test",
        headers={"X-API-Key": "worker-secret", "X-AIAT-Team-ID": "office_cio"},
    )
    try:
        await client.health_check()
    finally:
        await client.close()
