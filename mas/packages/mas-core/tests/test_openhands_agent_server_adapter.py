"""Contract tests for the inactive OpenHands Agent Server candidate."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import httpx
import pytest

from mas_core.worker_contract import (
    AdapterContext,
    ModelProfileReference,
    WorkerAdapter,
    WorkerCancellation,
    WorkerPause,
    WorkerResume,
    WorkerRunRequest,
)
from mas_core.worker_registry.openhands_agent_server_adapter import (
    OPENHANDS_MCP_BRIDGE_URL,
    OpenHandsAgentServerAdapter,
    OpenHandsInterfaceVerification,
)

if TYPE_CHECKING:
    from pathlib import Path


COMMIT = "4c1237f391fe394e9f67505fe3a0bd2d81f84188"
IMAGE_DIGEST = "sha256:36f847d1dfbbbdce90052437b06a3c6e76b8a54683228182eaf73085f03fcd97"
PROFILE_ID = "5e8f2b8a-9d9c-4a7f-9c82-14d8ccf9dd31"


def verification(*, approved: bool = True) -> OpenHandsInterfaceVerification:
    return OpenHandsInterfaceVerification(
        report_id="openhands-v1.43.0-pending",
        release="v1.43.0",
        commit_sha=COMMIT,
        repository="https://github.com/OpenHands/software-agent-sdk.git",
        image_ref="ghcr.io/openhands/agent-server:1.43.0-python",
        image_digest=IMAGE_DIGEST,
        image_platform_digest="sha256:c826bcfa6455267d8f99fe277d97d00806bc0f90bf263b94268cab29fa7be529",
        endpoints={
            "health": "/health",
            "readiness": "/ready",
            "server_info": "/server_info",
            "conversation_create": "/api/conversations",
            "conversation_get": "/api/conversations/{conversation_id}",
            "conversation_run": "/api/conversations/{conversation_id}/run",
            "conversation_pause": "/api/conversations/{conversation_id}/pause",
            "conversation_interrupt": "/api/conversations/{conversation_id}/interrupt",
            "conversation_delete": "/api/conversations/{conversation_id}",
            "agent_final_response": "/api/conversations/{conversation_id}/agent_final_response",
            "git_changes": "/api/git/changes",
            "file_download": "/api/file/download",
            "settings_mcp": "/api/settings/mcp/{settings_key}",
            "events_socket": "/sockets/events/{conversation_id}",
        },
        approved=approved,
        approval_record_id="operator-review-not-yet-issued" if approved else None,
    )


def request(*, workspace: Path, run_id: UUID | None = None) -> WorkerRunRequest:
    return WorkerRunRequest(
        run_id=run_id or uuid4(),
        idempotency_key="candidate-test-001",
        worker_id="coding-worker-openhands-candidate",
        task_type="coding",
        task_input={"prompt": "Add the smallest possible test."},
        resolved_model_profile=ModelProfileReference(
            profile_id="gateway-test",
            exact_model_id="openai/gpt-test",
        ),
        timeout_seconds=30,
        budget={"max_iterations": 4},
        tool_grants=["aiat.repository.read", "aiat.repository.write", "aiat.tests.execute"],
        extensions={"workspace_for_test_only": str(workspace)},
    )


def make_adapter(
    tmp_path: Path,
    handler,
    *,
    registrar=None,
    preconfigured: bool = False,
) -> OpenHandsAgentServerAdapter:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = httpx.AsyncClient(
        base_url="http://openhands.test",
        transport=httpx.MockTransport(handler),
    )
    context = AdapterContext(
        workspace_path=str(workspace),
        secrets={"openhands_session_api_key": "session-secret-test", "tool_secret": "tool-secret-test"},
        metadata={
            "openhands_agent_profile_id": PROFILE_ID,
            "openhands_mcp_profile_ref": "aiat-mcp-profile-test",
            "openhands_mcp_settings_key": "aiat-openhands-test-run",
            "openhands_mcp_preconfigured": preconfigured,
            "openhands_mcp_bridge_url": OPENHANDS_MCP_BRIDGE_URL,
            "openhands_image_digest": IMAGE_DIGEST,
            "openhands_public_skills_disabled": True,
            "openhands_plugins_disabled": True,
            "openhands_subagents_disabled": True,
            "openhands_browser_disabled": True,
            "openhands_direct_credentials_disabled": True,
        },
        artifact_registrar=registrar,
    )
    return OpenHandsAgentServerAdapter(
        verification(),
        base_url="http://openhands.test",
        worker_id="coding-worker-openhands-candidate",
        client=client,
        context=context,
    )


@pytest.mark.asyncio
async def test_preconfigured_run_scoped_bridge_is_read_back_without_recreating_it(tmp_path: Path) -> None:
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.method == "GET" and request.url.path == "/api/settings":
            return httpx.Response(
                200,
                json={
                    "mcp_config": {
                        "aiat-openhands-test-run": {
                            "url": OPENHANDS_MCP_BRIDGE_URL,
                            "transport": "streamable-http",
                            "enabled": True,
                            "headers": {"X-AIAT-OpenHands-Grant": "REDACTED"},
                        }
                    }
                },
            )
        if request.method == "DELETE" and request.url.path == "/api/settings/mcp/aiat-openhands-test-run":
            return httpx.Response(200, json={})
        raise AssertionError(request)

    adapter = make_adapter(tmp_path, handler, preconfigured=True)
    run = request(workspace=tmp_path / "workspace")
    await adapter._configure_tool_bridge(run)
    assert adapter._mcp_by_run[run.run_id] == "aiat-openhands-test-run"
    assert not any(call.startswith("POST /api/settings/mcp/") for call in calls)
    await adapter._cleanup_tool_bridge(run.run_id)
    assert "DELETE /api/settings/mcp/aiat-openhands-test-run" in calls
    await adapter.close()


def test_pending_report_cannot_construct_executable_adapter() -> None:
    with pytest.raises(ValueError, match="approved interface verification"):
        OpenHandsAgentServerAdapter(
            verification(approved=False),
            base_url="http://openhands.test",
            worker_id="candidate",
            context=AdapterContext(secrets={"openhands_session_api_key": "secret"}),
        )


@pytest.mark.asyncio
async def test_candidate_implements_the_same_worker_adapter_protocol(tmp_path: Path) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    adapter = make_adapter(tmp_path, handler)
    assert isinstance(adapter, WorkerAdapter)
    await adapter.close()


def test_report_loader_requires_full_provenance_and_pinned_digest() -> None:
    report = {
        "report_id": "candidate",
        "approval_status": "PENDING",
        "pin": {
            "repository": "https://github.com/OpenHands/software-agent-sdk.git",
            "release": "v1.43.0",
            "commit_sha": COMMIT,
        },
        "image": {"ref": "ghcr.io/openhands/agent-server:1.43.0-python", "digest": IMAGE_DIGEST},
    }
    loaded = OpenHandsInterfaceVerification.from_report(report)
    assert loaded.release == "v1.43.0"
    assert loaded.commit_sha == COMMIT
    assert loaded.endpoint("conversation_run", conversation_id="abc") == "/api/conversations/abc/run"
    assert loaded.approved is False

    report["image"]["digest"] = "latest"
    with pytest.raises(ValueError, match="image digest"):
        OpenHandsInterfaceVerification.from_report(report)


@pytest.mark.asyncio
async def test_readiness_checks_server_and_governed_profile(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/ready":
            return httpx.Response(200, json={"status": "ready"})
        if request.url.path == "/server_info":
            return httpx.Response(200, json={"versions": {"openhands-agent-server": "1.43.0"}, "build_sha": COMMIT})
        raise AssertionError(request.url)

    adapter = make_adapter(tmp_path, handler)
    result = await adapter.readiness(request=request(workspace=tmp_path / "workspace"))
    assert result.ready is True
    assert result.checks["authenticated_health"] is True
    assert result.checks["aiat_tool_bridge_bound"] is True
    assert result.checks["aiat_tool_bridge_url_pinned"] is True
    assert result.checks["openhands_public_skills_disabled"] is True
    await adapter.close()


@pytest.mark.asyncio
async def test_start_payload_contains_only_controlled_profile_workspace_and_prompt(tmp_path: Path) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    adapter = make_adapter(tmp_path, handler)
    payload = adapter._start_payload(request(workspace=tmp_path / "workspace"))
    serialized = str(payload)
    assert payload["agent_profile_id"] == PROFILE_ID
    assert payload["workspace"]["working_dir"] == str((tmp_path / "workspace").resolve())
    assert payload["max_iterations"] == 4
    assert "session-secret-test" not in serialized
    assert "api_key" not in serialized
    await adapter.close()


@pytest.mark.asyncio
async def test_pause_interrupt_and_resume_use_exact_governed_routes(tmp_path: Path) -> None:
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json={"success": True})

    adapter = make_adapter(tmp_path, handler)
    run_id = uuid4()
    conversation_id = str(uuid4())
    adapter._conversation_by_run[run_id] = conversation_id
    await adapter.pause(WorkerPause(run_id=run_id, reason="operator pause", requested_by="operator"))
    await adapter.resume(WorkerResume(run_id=run_id, requested_by="operator"))
    await adapter.cancel(WorkerCancellation(run_id=run_id, reason="stop now", requested_by="operator", force=True))
    assert f"POST /api/conversations/{conversation_id}/pause" in calls
    assert f"POST /api/conversations/{conversation_id}/run" in calls
    assert f"POST /api/conversations/{conversation_id}/interrupt" in calls
    await adapter.close()


@pytest.mark.asyncio
async def test_artifacts_hash_remote_files_and_reject_path_escape(tmp_path: Path) -> None:
    recorded: list[object] = []
    payload = b"safe changed file\n"

    async def registrar(artifact: object) -> None:
        recorded.append(artifact)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/git/changes":
            return httpx.Response(200, json=[
                {"status": "UPDATED", "path": "src/changed.py"},
                {"status": "UPDATED", "path": "../outside.txt"},
            ])
        if request.url.path == "/api/file/download":
            assert request.url.params["path"].endswith("/src/changed.py")
            return httpx.Response(200, content=payload)
        raise AssertionError(request.url)

    adapter = make_adapter(tmp_path, handler, registrar=registrar)
    artifacts = await adapter._artifacts(str(uuid4()))
    assert len(artifacts) == 1
    assert artifacts[0].name == "src/changed.py"
    assert artifacts[0].sha256 == hashlib.sha256(payload).hexdigest()
    assert len(recorded) == 1
    await adapter.close()


@pytest.mark.asyncio
async def test_execute_maps_conversation_result_and_scalar_usage(tmp_path: Path) -> None:
    conversation_id = str(uuid4())
    status_reads = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal status_reads
        if request.method == "POST" and request.url.path == "/api/settings/mcp/aiat-openhands-test-run":
            payload = json.loads(request.content)
            assert payload["url"] == OPENHANDS_MCP_BRIDGE_URL
            assert "X-AIAT-OpenHands-Grant" in payload["headers"]
            return httpx.Response(201, json={})
        if request.method == "DELETE" and request.url.path == "/api/settings/mcp/aiat-openhands-test-run":
            return httpx.Response(200, json={})
        if request.method == "POST" and request.url.path == "/api/conversations":
            return httpx.Response(201, json={"id": conversation_id})
        if request.method == "GET" and request.url.path == f"/api/conversations/{conversation_id}":
            status_reads += 1
            if status_reads == 1:
                return httpx.Response(
                    200,
                    json={"execution_status": "idle", "agent": {"llm": {"model": "openai/gpt-test"}}},
                )
            return httpx.Response(
                200,
                json={
                    "execution_status": "finished",
                    "metrics": {
                        "accumulated_cost": 0.12,
                        "accumulated_token_usage": {"prompt_tokens": 5, "completion_tokens": 7},
                    },
                },
            )
        if request.method == "POST" and request.url.path == f"/api/conversations/{conversation_id}/run":
            return httpx.Response(200, json={"success": True})
        if request.method == "GET" and request.url.path == f"/api/conversations/{conversation_id}/agent_final_response":
            return httpx.Response(200, json={"response": "implemented"})
        if request.method == "GET" and request.url.path == "/api/git/changes":
            return httpx.Response(200, json=[])
        raise AssertionError(request.url)

    adapter = make_adapter(tmp_path, handler)
    result = await adapter._execute(request(workspace=tmp_path / "workspace"))
    assert result.success is True
    assert result.output == "implemented"
    assert result.usage.total_tokens == 12
    assert result.usage.cost_usd == 0.12
    assert result.replay_metadata["openhands_conversation_id"] == conversation_id
    assert not adapter._event_tasks
    await adapter.close()
