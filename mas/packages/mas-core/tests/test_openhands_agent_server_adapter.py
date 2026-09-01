"""Contract tests for the inactive OpenHands Agent Server candidate."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest

import mas_core.worker_registry.openhands_agent_server_adapter as adapter_module
from mas_core.worker_contract import (
    AdapterContext,
    ModelProfileReference,
    WorkerAdapter,
    WorkerCancellation,
    WorkerPause,
    WorkerResume,
    WorkerRunRequest,
)
from mas_core.worker_contract.openhands_bridge import (
    issue_openhands_tool_grant,
    verify_openhands_tool_grant,
)
from mas_core.worker_contract.openhands_model import OPENHANDS_WIRE_MODEL_ID
from mas_core.worker_registry.openhands_agent_server_adapter import (
    OPENHANDS_MCP_BRIDGE_URL,
    OpenHandsAgentServerAdapter,
    OpenHandsCertificationAuthorization,
    OpenHandsInterfaceVerification,
    issue_openhands_certification_authorization,
)
from mas_core.worker_registry.runtime_adapters import adapter_for_transport

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
            "conversation_message": "/api/conversations/{conversation_id}/events",
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
            exact_model_id="omniroute-coding",
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
            "openhands_model_id": "omniroute-coding",
            "openhands_certification_controller": "aiat-github-actions",
            "openhands_certification_controller_run_id": "32594885180",
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
    run = request(workspace=tmp_path / "workspace")
    grant = issue_openhands_tool_grant(
        "tool-secret-test",
        worker_id="coding-worker-openhands-candidate",
        run_id=run.run_id,
        project_id=run.project_id,
        tool_names=run.tool_grants,
    )

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
                            "headers": {"X-AIAT-OpenHands-Grant": grant},
                        }
                    }
                },
            )
        if request.method == "DELETE" and request.url.path == "/api/settings/mcp/aiat-openhands-test-run":
            # A valid Agent Server implementation may return an empty
            # successful response for run-scoped MCP deletion.
            return httpx.Response(204)
        raise AssertionError(request)

    adapter = make_adapter(tmp_path, handler, preconfigured=True)
    await adapter._configure_tool_bridge(run)
    assert adapter._mcp_by_run[run.run_id] == "aiat-openhands-test-run"
    assert not any(call.startswith("POST /api/settings/mcp/") for call in calls)
    await adapter._cleanup_tool_bridge(run.run_id)
    assert "DELETE /api/settings/mcp/aiat-openhands-test-run" in calls
    await adapter.close()


@pytest.mark.asyncio
async def test_preconfigured_run_scoped_bridge_reads_v143_nested_settings_envelope(tmp_path: Path) -> None:
    run = request(workspace=tmp_path / "workspace")
    grant = issue_openhands_tool_grant(
        "tool-secret-test",
        worker_id="coding-worker-openhands-candidate",
        run_id=run.run_id,
        project_id=run.project_id,
        tool_names=run.tool_grants,
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/settings":
            return httpx.Response(
                200,
                json={
                    "agent_settings": {
                        "mcp_config": {
                            "aiat-openhands-test-run": {
                                "url": OPENHANDS_MCP_BRIDGE_URL,
                                "transport": "streamable-http",
                                "enabled": True,
                                "headers": {"X-AIAT-OpenHands-Grant": grant},
                            }
                        }
                    }
                },
            )
        if request.method == "DELETE" and request.url.path == "/api/settings/mcp/aiat-openhands-test-run":
            return httpx.Response(204)
        raise AssertionError(request)

    adapter = make_adapter(tmp_path, handler, preconfigured=True)
    await adapter._configure_tool_bridge(run)
    assert adapter._mcp_by_run[run.run_id] == "aiat-openhands-test-run"
    await adapter._cleanup_tool_bridge(run.run_id)
    await adapter.close()


@pytest.mark.asyncio
async def test_certification_rotates_pinned_server_redacted_grant_readback(tmp_path: Path) -> None:
    """v1.43 masks MCP headers, so certification must rotate its own grant."""

    calls: list[str] = []
    current_grant = "**********"
    run = request(workspace=tmp_path / "workspace")

    async def handler(http_request: httpx.Request) -> httpx.Response:
        nonlocal current_grant
        calls.append(f"{http_request.method} {http_request.url.path}")
        if http_request.method == "GET" and http_request.url.path == "/api/settings":
            config = {}
            if current_grant is not None:
                config = {
                    "aiat-openhands-test-run": {
                        "url": OPENHANDS_MCP_BRIDGE_URL,
                        "transport": "streamable-http",
                        "enabled": True,
                        # Pinned Agent Server v1.43.0 response redaction.
                        "headers": {"X-AIAT-OpenHands-Grant": current_grant},
                    }
                }
            return httpx.Response(200, json={"mcp_config": config})
        if http_request.method == "DELETE" and http_request.url.path == "/api/settings/mcp/aiat-openhands-test-run":
            current_grant = None
            return httpx.Response(204)
        if http_request.method == "POST" and http_request.url.path == "/api/settings/mcp/aiat-openhands-test-run":
            current_grant = json.loads(http_request.content.decode())["headers"]["X-AIAT-OpenHands-Grant"]
            verified = verify_openhands_tool_grant(current_grant, "tool-secret-test")
            assert verified.worker_id == "coding-worker-openhands-candidate"
            assert verified.run_id == run.run_id
            assert verified.tool_names == frozenset(run.tool_grants)
            return httpx.Response(201, json={})
        raise AssertionError(f"unexpected network call: {http_request.method} {http_request.url}")

    pending = verification(approved=False)
    authorization = issue_openhands_certification_authorization(
        pending,
        controller="aiat-github-actions",
        controller_run_id="32594885180",
        sandbox_profile="gvisor",
        sandbox_runtime="runsc",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = AdapterContext(
        workspace_path=str(workspace),
        secrets={"openhands_session_api_key": "session-secret-test", "tool_secret": "tool-secret-test"},
        metadata={
            "openhands_agent_profile_id": PROFILE_ID,
            "openhands_mcp_profile_ref": "aiat-mcp-profile-test",
            "openhands_mcp_settings_key": "aiat-openhands-test-run",
            "openhands_mcp_preconfigured": True,
            "openhands_mcp_bridge_url": OPENHANDS_MCP_BRIDGE_URL,
            "openhands_image_digest": IMAGE_DIGEST,
            "openhands_model_id": "omniroute-coding",
            "openhands_certification_controller": "aiat-github-actions",
            "openhands_certification_controller_run_id": "32594885180",
            "openhands_certification_sandbox_profile": "gvisor",
            "openhands_certification_sandbox_runtime": "runsc",
            "openhands_defer_mcp_cleanup": True,
        },
    )
    adapter = OpenHandsAgentServerAdapter.for_certification(
        pending,
        authorization=authorization,
        base_url="http://openhands.test",
        worker_id="coding-worker-openhands-candidate",
        client=httpx.AsyncClient(
            base_url="http://openhands.test",
            transport=httpx.MockTransport(handler),
        ),
        context=context,
    )
    await adapter._configure_tool_bridge(run)
    assert adapter._mcp_by_run[run.run_id] == "aiat-openhands-test-run"
    assert "POST /api/settings/mcp/aiat-openhands-test-run" in calls
    # Switch only cleanup behavior after the trusted rotation; the final
    # workflow cleanup owns deletion of the shared profile-bound key.
    context.metadata["openhands_defer_mcp_cleanup"] = False
    await adapter._cleanup_tool_bridge(run.run_id)
    assert "DELETE /api/settings/mcp/aiat-openhands-test-run" in calls
    await adapter.close()


@pytest.mark.asyncio
async def test_production_preconfigured_redacted_grant_fails_closed(tmp_path: Path) -> None:
    async def handler(http_request: httpx.Request) -> httpx.Response:
        if http_request.method == "GET" and http_request.url.path == "/api/settings":
            return httpx.Response(
                200,
                json={
                    "mcp_config": {
                        "aiat-openhands-test-run": {
                            "url": OPENHANDS_MCP_BRIDGE_URL,
                            "transport": "streamable-http",
                            "enabled": True,
                            "headers": {"X-AIAT-OpenHands-Grant": "**********"},
                        }
                    }
                },
            )
        raise AssertionError(f"unexpected network call: {http_request.method} {http_request.url}")

    adapter = make_adapter(tmp_path, handler, preconfigured=True)
    run = request(workspace=tmp_path / "workspace")
    with pytest.raises(RuntimeError, match="readback is redacted"):
        await adapter._configure_tool_bridge(run)
    await adapter.close()


@pytest.mark.asyncio
async def test_preconfigured_run_scoped_bridge_rejects_grant_bound_to_another_run(tmp_path: Path) -> None:
    other_run = request(workspace=tmp_path / "workspace")
    grant = issue_openhands_tool_grant(
        "tool-secret-test",
        worker_id="coding-worker-openhands-candidate",
        run_id=other_run.run_id,
        project_id=other_run.project_id,
        tool_names=other_run.tool_grants,
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/settings":
            return httpx.Response(
                200,
                json={
                    "mcp_config": {
                        "aiat-openhands-test-run": {
                            "url": OPENHANDS_MCP_BRIDGE_URL,
                            "transport": "streamable-http",
                            "enabled": True,
                            "headers": {"X-AIAT-OpenHands-Grant": grant},
                        }
                    }
                },
            )
        raise AssertionError(f"unexpected network call: {request.method} {request.url}")

    adapter = make_adapter(tmp_path, handler, preconfigured=True)
    run = request(workspace=tmp_path / "workspace")
    with pytest.raises(RuntimeError, match="not bound to this run"):
        await adapter._configure_tool_bridge(run)
    await adapter.close()


@pytest.mark.asyncio
async def test_certification_lifecycle_rotates_preconfigured_grant_per_run(tmp_path: Path) -> None:
    calls: list[str] = []
    original_run = request(workspace=tmp_path / "workspace")
    next_run = request(workspace=tmp_path / "workspace", run_id=uuid4())
    original_grant = issue_openhands_tool_grant(
        "tool-secret-test",
        worker_id="coding-worker-openhands-candidate",
        run_id=original_run.run_id,
        project_id=original_run.project_id,
        tool_names=original_run.tool_grants,
    )
    current_grant: str | None = original_grant

    async def handler(http_request: httpx.Request) -> httpx.Response:
        nonlocal current_grant
        calls.append(f"{http_request.method} {http_request.url.path}")
        if http_request.method == "GET" and http_request.url.path == "/api/settings":
            config = {}
            if current_grant is not None:
                config = {
                    "aiat-openhands-test-run": {
                        "url": OPENHANDS_MCP_BRIDGE_URL,
                        "transport": "streamable-http",
                        "enabled": True,
                        "headers": {"X-AIAT-OpenHands-Grant": current_grant},
                    }
                }
            return httpx.Response(
                200,
                json={"mcp_config": config},
            )
        if http_request.method == "DELETE" and http_request.url.path == "/api/settings/mcp/aiat-openhands-test-run":
            current_grant = None
            return httpx.Response(204)
        if http_request.method == "POST" and http_request.url.path == "/api/settings/mcp/aiat-openhands-test-run":
            current_grant = json.loads(http_request.content.decode())["headers"]["X-AIAT-OpenHands-Grant"]
            return httpx.Response(201, json={})
        raise AssertionError(f"unexpected network call: {http_request.method} {http_request.url}")

    pending = verification(approved=False)
    authorization = issue_openhands_certification_authorization(
        pending,
        controller="aiat-github-actions",
        controller_run_id="32594885180",
        sandbox_profile="gvisor",
        sandbox_runtime="runsc",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = OpenHandsAgentServerAdapter.for_certification(
        pending,
        authorization=authorization,
        base_url="http://openhands.test",
        worker_id="coding-worker-openhands-candidate",
        client=httpx.AsyncClient(
            base_url="http://openhands.test",
            transport=httpx.MockTransport(handler),
        ),
        context=AdapterContext(
            workspace_path=str(workspace),
            secrets={"openhands_session_api_key": "session-secret-test", "tool_secret": "tool-secret-test"},
            metadata={
                "openhands_agent_profile_id": PROFILE_ID,
                "openhands_mcp_profile_ref": "aiat-mcp-profile-test",
                "openhands_mcp_settings_key": "aiat-openhands-test-run",
                "openhands_mcp_preconfigured": True,
                "openhands_mcp_bridge_url": OPENHANDS_MCP_BRIDGE_URL,
                "openhands_image_digest": IMAGE_DIGEST,
                "openhands_model_id": "omniroute-coding",
                "openhands_certification_controller": "aiat-github-actions",
                "openhands_certification_controller_run_id": "32594885180",
                "openhands_certification_sandbox_profile": "gvisor",
                "openhands_certification_sandbox_runtime": "runsc",
                "openhands_defer_mcp_cleanup": True,
            },
        ),
    )
    await adapter._configure_tool_bridge(original_run)
    await adapter._configure_tool_bridge(next_run)
    assert adapter._mcp_by_run[original_run.run_id] == "aiat-openhands-test-run"
    assert adapter._mcp_by_run[next_run.run_id] == "aiat-openhands-test-run"
    assert "DELETE /api/settings/mcp/aiat-openhands-test-run" in calls
    assert "POST /api/settings/mcp/aiat-openhands-test-run" in calls
    await adapter.close()


def test_mcp_settings_config_merges_empty_direct_and_nested_maps() -> None:
    config = OpenHandsAgentServerAdapter._mcp_settings_config(
        {
            "mcp_config": {},
            "agent_settings": {"mcp_config": {"aiat-openhands-test-run": {}}},
        }
    )
    assert config == {"aiat-openhands-test-run": {}}


@pytest.mark.asyncio
async def test_tool_bridge_rejects_grants_outside_bounded_coding_surface(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected network call: {request.method} {request.url}")

    adapter = make_adapter(tmp_path, handler)
    run = request(workspace=tmp_path / "workspace")
    run = run.model_copy(update={"tool_grants": [*run.tool_grants, "aiat.github.write"]})
    with pytest.raises(RuntimeError, match="bounded coding surface"):
        await adapter._configure_tool_bridge(run)
    await adapter.close()


@pytest.mark.asyncio
async def test_lifecycle_wave_defers_bridge_delete_until_trusted_final_cleanup(tmp_path: Path) -> None:
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.method == "DELETE" and request.url.path == "/api/settings/mcp/aiat-openhands-test-run":
            return httpx.Response(200, json={})
        raise AssertionError(request)

    pending = verification(approved=False)
    authorization = issue_openhands_certification_authorization(
        pending,
        controller="aiat-github-actions",
        controller_run_id="32594885180",
        sandbox_profile="gvisor",
        sandbox_runtime="runsc",
    )
    client = httpx.AsyncClient(
        base_url="http://openhands.test",
        transport=httpx.MockTransport(handler),
    )
    adapter = OpenHandsAgentServerAdapter.for_certification(
        pending,
        authorization=authorization,
        base_url="http://openhands.test",
        worker_id="coding-worker-openhands-candidate",
        context=_certification_context(tmp_path),
        client=client,
    )
    run_id = uuid4()
    adapter._mcp_by_run[run_id] = "aiat-openhands-test-run"
    adapter.context.metadata["openhands_defer_mcp_cleanup"] = True

    await adapter._cleanup_tool_bridge(run_id)
    assert calls == []
    assert adapter._mcp_by_run[run_id] == "aiat-openhands-test-run"

    adapter.context.metadata["openhands_defer_mcp_cleanup"] = False
    await adapter._cleanup_tool_bridge(run_id)
    assert calls == ["DELETE /api/settings/mcp/aiat-openhands-test-run"]
    assert run_id not in adapter._mcp_by_run
    await adapter.close()


def test_pending_report_cannot_construct_executable_adapter() -> None:
    with pytest.raises(ValueError, match="approved interface verification"):
        OpenHandsAgentServerAdapter(
            verification(approved=False),
            base_url="http://openhands.test",
            worker_id="candidate",
            context=AdapterContext(secrets={"openhands_session_api_key": "secret"}),
        )


def _certification_context(tmp_path: Path) -> AdapterContext:
    workspace = tmp_path / "certification-workspace"
    workspace.mkdir(exist_ok=True)
    return AdapterContext(
        workspace_path=str(workspace),
        secrets={"openhands_session_api_key": "session-secret-test", "tool_secret": "tool-secret-test"},
        metadata={
            "openhands_agent_profile_id": PROFILE_ID,
            "openhands_mcp_profile_ref": "aiat-mcp-profile-test",
            "openhands_mcp_settings_key": "aiat-openhands-test-run",
            "openhands_mcp_bridge_url": OPENHANDS_MCP_BRIDGE_URL,
            "openhands_image_digest": IMAGE_DIGEST,
            "openhands_model_id": "omniroute-coding",
            "openhands_certification_controller": "aiat-github-actions",
            "openhands_certification_controller_run_id": "32594885180",
            "openhands_public_skills_disabled": True,
            "openhands_plugins_disabled": True,
            "openhands_subagents_disabled": True,
            "openhands_browser_disabled": True,
            "openhands_direct_credentials_disabled": True,
        },
    )


def test_pending_candidate_can_run_only_with_exact_gvisor_certification_authorization(tmp_path: Path) -> None:
    pending = verification(approved=False)
    authorization = issue_openhands_certification_authorization(
        pending,
        controller="aiat-github-actions",
        controller_run_id="32594885180",
        sandbox_profile="gvisor",
        sandbox_runtime="runsc",
    )
    adapter = OpenHandsAgentServerAdapter.for_certification(
        pending,
        authorization=authorization,
        base_url="http://openhands.test",
        worker_id="coding-worker-openhands-candidate",
        context=_certification_context(tmp_path),
    )
    assert adapter.certification_mode is True
    assert adapter.activation_eligible is False
    assert adapter.context.metadata["openhands_certification_mode"] is True


def test_certification_authorization_cannot_be_spoofed_or_rebound_to_other_pins(tmp_path: Path) -> None:
    pending = verification(approved=False)
    authorization = issue_openhands_certification_authorization(
        pending,
        controller="aiat-github-actions",
        controller_run_id="32594885180",
        sandbox_profile="gvisor",
        sandbox_runtime="runsc",
    )
    rebound = replace(authorization, candidate_commit="0" * 40)
    with pytest.raises(ValueError, match="invalid or does not match"):
        OpenHandsAgentServerAdapter.for_certification(
            pending,
            authorization=rebound,
            base_url="http://openhands.test",
            worker_id="candidate",
            context=_certification_context(tmp_path),
        )


def test_certification_authorization_is_bound_to_candidate_worker_identity(tmp_path: Path) -> None:
    pending = verification(approved=False)
    authorization = issue_openhands_certification_authorization(
        pending,
        controller="aiat-github-actions",
        controller_run_id="32594885180",
        sandbox_profile="gvisor",
        sandbox_runtime="runsc",
    )
    with pytest.raises(ValueError, match="invalid or does not match"):
        OpenHandsAgentServerAdapter.for_certification(
            pending,
            authorization=authorization,
            base_url="http://openhands.test",
            worker_id="another-worker",
            context=_certification_context(tmp_path),
        )

    fake = OpenHandsCertificationAuthorization(
        candidate_commit=pending.commit_sha,
        image_digest=pending.image_digest,
        sandbox_profile="gvisor",
        sandbox_runtime="runsc",
        controller="aiat-github-actions",
        controller_run_id="32594885180",
        _authority=object(),
    )
    with pytest.raises(ValueError, match="invalid or does not match"):
        OpenHandsAgentServerAdapter.for_certification(
            pending,
            authorization=fake,
            base_url="http://openhands.test",
            worker_id="candidate",
            context=_certification_context(tmp_path),
        )


def test_certification_authorization_is_factory_only_single_use_and_time_bounded(tmp_path: Path) -> None:
    pending = verification(approved=False)
    authorization = issue_openhands_certification_authorization(
        pending,
        controller="aiat-github-actions",
        controller_run_id="32594885180",
        sandbox_profile="gvisor",
        sandbox_runtime="runsc",
    )
    with pytest.raises(ValueError, match="trusted certification factory"):
        OpenHandsAgentServerAdapter(
            pending,
            base_url="http://openhands.test",
            worker_id="candidate",
            context=_certification_context(tmp_path),
            certification_authorization=authorization,
        )

    adapter = OpenHandsAgentServerAdapter.for_certification(
        pending,
        authorization=authorization,
        base_url="http://openhands.test",
        worker_id="coding-worker-openhands-candidate",
        context=_certification_context(tmp_path),
    )
    assert adapter.certification_mode is True
    with pytest.raises(ValueError, match="already been used"):
        OpenHandsAgentServerAdapter.for_certification(
            pending,
            authorization=authorization,
            base_url="http://openhands.test",
            worker_id="candidate-replay",
            context=_certification_context(tmp_path),
        )

    expired = issue_openhands_certification_authorization(
        pending,
        controller="aiat-github-actions",
        controller_run_id="32594885181",
        sandbox_profile="gvisor",
        sandbox_runtime="runsc",
    )
    expired = replace(expired, issued_at=time.time() - 901, expires_at=time.time() - 1)
    with pytest.raises(ValueError, match="invalid or does not match"):
        OpenHandsAgentServerAdapter.for_certification(
            pending,
            authorization=expired,
            base_url="http://openhands.test",
            worker_id="candidate-expired",
            context=_certification_context(tmp_path),
        )


def test_invalid_factory_context_does_not_burn_certification_authorization(tmp_path: Path) -> None:
    pending = verification(approved=False)
    authorization = issue_openhands_certification_authorization(
        pending,
        controller="aiat-github-actions",
        controller_run_id="32594885185",
        sandbox_profile="gvisor",
        sandbox_runtime="runsc",
    )
    invalid_context = _certification_context(tmp_path)
    invalid_context.metadata["openhands_certification_controller_run_id"] = "32594885185"
    invalid_context.secrets.pop("openhands_session_api_key")
    with pytest.raises(ValueError, match="session API key"):
        OpenHandsAgentServerAdapter.for_certification(
            pending,
            authorization=authorization,
            base_url="http://openhands.test",
            worker_id="coding-worker-openhands-candidate",
            context=invalid_context,
        )

    valid_context = _certification_context(tmp_path)
    valid_context.metadata["openhands_certification_controller_run_id"] = "32594885185"
    adapter = OpenHandsAgentServerAdapter.for_certification(
        pending,
        authorization=authorization,
        base_url="http://openhands.test",
        worker_id="coding-worker-openhands-candidate",
        context=valid_context,
    )
    assert adapter.certification_mode is True


def test_certification_authorization_requires_gvisor_and_runsc() -> None:
    pending = verification(approved=False)
    with pytest.raises(ValueError, match="gVisor"):
        issue_openhands_certification_authorization(
            pending,
            controller="aiat-github-actions",
            controller_run_id="32594885180",
            sandbox_profile="runc",
            sandbox_runtime="runsc",
        )
    with pytest.raises(ValueError, match="runsc"):
        issue_openhands_certification_authorization(
            pending,
            controller="aiat-github-actions",
            controller_run_id="32594885180",
            sandbox_profile="gvisor",
            sandbox_runtime="runc",
        )


def test_certification_authorization_requires_a_bounded_controller_run_id() -> None:
    pending = verification(approved=False)
    with pytest.raises(ValueError, match="bounded numeric"):
        issue_openhands_certification_authorization(
            pending,
            controller="aiat-github-actions",
            controller_run_id="not-a-github-run",
            sandbox_profile="gvisor",
            sandbox_runtime="runsc",
        )


def test_certification_authorization_requires_the_trusted_controller() -> None:
    pending = verification(approved=False)
    with pytest.raises(ValueError, match="trusted controller"):
        issue_openhands_certification_authorization(
            pending,
            controller="untrusted-caller",
            controller_run_id="32594885182",
            sandbox_profile="gvisor",
            sandbox_runtime="runsc",
        )


def test_certification_authorization_requires_controller_attestation(tmp_path: Path) -> None:
    pending = verification(approved=False)
    authorization = issue_openhands_certification_authorization(
        pending,
        controller="aiat-github-actions",
        controller_run_id="32594885182",
        sandbox_profile="gvisor",
        sandbox_runtime="runsc",
    )
    context = _certification_context(tmp_path)
    context.metadata["openhands_certification_controller"] = "untrusted-caller"
    context.metadata["openhands_certification_controller_run_id"] = "32594885182"
    with pytest.raises(ValueError, match="controller attestation"):
        OpenHandsAgentServerAdapter.for_certification(
            pending,
            authorization=authorization,
            base_url="http://openhands.test",
            worker_id="coding-worker-openhands-candidate",
            context=context,
        )


def test_certification_authorization_requires_matching_controller_run_id(tmp_path: Path) -> None:
    pending = verification(approved=False)
    authorization = issue_openhands_certification_authorization(
        pending,
        controller="aiat-github-actions",
        controller_run_id="32594885183",
        sandbox_profile="gvisor",
        sandbox_runtime="runsc",
    )
    context = _certification_context(tmp_path)
    context.metadata["openhands_certification_controller_run_id"] = "32594885184"
    with pytest.raises(ValueError, match="controller attestation"):
        OpenHandsAgentServerAdapter.for_certification(
            pending,
            authorization=authorization,
            base_url="http://openhands.test",
            worker_id="coding-worker-openhands-candidate",
            context=context,
        )


def test_metadata_cannot_claim_certification_mode(tmp_path: Path) -> None:
    context = _certification_context(tmp_path)
    context.metadata["openhands_certification_mode"] = True
    with pytest.raises(ValueError, match="requires AIAT certification authorization"):
        OpenHandsAgentServerAdapter(
            verification(),
            base_url="http://openhands.test",
            worker_id="candidate",
            context=context,
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
    assert loaded.endpoint("conversation_message", conversation_id="abc") == "/api/conversations/abc/events"
    assert loaded.endpoint("conversation_run", conversation_id="abc") == "/api/conversations/abc/run"
    assert loaded.approved is False

    report["image"]["digest"] = "latest"
    with pytest.raises(ValueError, match="image digest"):
        OpenHandsInterfaceVerification.from_report(report)

    report["image"]["digest"] = IMAGE_DIGEST
    report["approval_status"] = "APPROVED"
    report["approved"] = True
    with pytest.raises(ValueError, match="approval record ID"):
        OpenHandsInterfaceVerification.from_report(report)


def test_transport_factory_rejects_inline_openhands_approval_claim(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="inline approval claims are not trusted"):
        adapter_for_transport(
            "openhands_agent_server",
            worker_id="coding-worker-openhands-candidate",
            config={
                "base_url": "http://openhands.test",
                "interface_verification": {
                    "approval_status": "APPROVED",
                    "approved": True,
                    "pin": {
                        "repository": "https://github.com/OpenHands/software-agent-sdk.git",
                        "release": "v1.43.0",
                        "commit_sha": COMMIT,
                    },
                    "image": {"ref": "ghcr.io/openhands/agent-server:1.43.0-python", "digest": IMAGE_DIGEST},
                },
            },
            context=AdapterContext(
                workspace_path=str(tmp_path),
                secrets={"openhands_session_api_key": "session-secret-test"},
            ),
        )


def test_transport_factory_rejects_noncanonical_openhands_report_ref(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="canonical candidate provenance directory"):
        adapter_for_transport(
            "openhands_agent_server",
            worker_id="coding-worker-openhands-candidate",
            config={
                "base_url": "http://openhands.test",
                "interface_verification_ref": "mas/docs/provenance/security_scan_evidence.yaml",
            },
            context=AdapterContext(
                workspace_path=str(tmp_path),
                secrets={"openhands_session_api_key": "session-secret-test"},
            ),
        )


@pytest.mark.asyncio
async def test_transport_factory_resolves_repository_relative_interface_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(Path(__file__).resolve().parents[4])
    captured: dict[str, object] = {}

    def load_report(report: object) -> OpenHandsInterfaceVerification:
        captured["report"] = report
        return verification(approved=True)

    monkeypatch.setattr(OpenHandsInterfaceVerification, "from_report", staticmethod(load_report))
    adapter = adapter_for_transport(
        "openhands_agent_server",
        worker_id="coding-worker-openhands-candidate",
        config={
            "base_url": "http://openhands.test",
            "interface_verification_ref": "mas/docs/provenance/openhands-candidate/2026-08-22-v1.43.0/interface-verification.json",
        },
        context=AdapterContext(
            workspace_path=str(tmp_path),
            secrets={"openhands_session_api_key": "session-secret-test"},
        ),
    )
    assert isinstance(adapter, OpenHandsAgentServerAdapter)
    assert Path(str(captured["report"])).is_file()
    assert adapter.verification.commit_sha == COMMIT
    await adapter.close()


@pytest.mark.asyncio
async def test_transport_factory_resolves_source_ref_from_api_image_evidence_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report_path = tmp_path / "docs/provenance/openhands-candidate/2026-08-22-v1.43.0/interface-verification.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("AIAT_REPOSITORY_ROOT", str(tmp_path))
    captured: dict[str, object] = {}

    def load_report(report: object) -> OpenHandsInterfaceVerification:
        captured["report"] = report
        return verification(approved=True)

    monkeypatch.setattr(OpenHandsInterfaceVerification, "from_report", staticmethod(load_report))
    adapter = adapter_for_transport(
        "openhands_agent_server",
        worker_id="coding-worker-openhands-candidate",
        config={
            "base_url": "http://openhands.test",
            "interface_verification_ref": "mas/docs/provenance/openhands-candidate/2026-08-22-v1.43.0/interface-verification.json",
        },
        context=AdapterContext(
            workspace_path=str(tmp_path),
            secrets={"openhands_session_api_key": "session-secret-test"},
        ),
    )
    assert Path(str(captured["report"])) == report_path
    await adapter.close()


@pytest.mark.asyncio
async def test_transport_factory_accepts_absolute_source_report_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report_path = tmp_path / "mas/docs/provenance/openhands-candidate/2026-08-22-v1.43.0/interface-verification.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("AIAT_REPOSITORY_ROOT", str(tmp_path))
    captured: dict[str, object] = {}

    def load_report(report: object) -> OpenHandsInterfaceVerification:
        captured["report"] = report
        return verification(approved=True)

    monkeypatch.setattr(OpenHandsInterfaceVerification, "from_report", staticmethod(load_report))
    adapter = adapter_for_transport(
        "openhands_agent_server",
        worker_id="coding-worker-openhands-candidate",
        config={"base_url": "http://openhands.test", "interface_verification_ref": str(report_path)},
        context=AdapterContext(
            workspace_path=str(tmp_path),
            secrets={"openhands_session_api_key": "session-secret-test"},
        ),
    )
    assert Path(str(captured["report"])) == report_path
    await adapter.close()


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
async def test_readiness_accepts_pinned_build_git_sha_field(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/ready":
            return httpx.Response(200, json={"status": "ready"})
        if request.url.path == "/server_info":
            return httpx.Response(200, json={"versions": {"openhands-agent-server": "1.43.0"}, "build_git_sha": COMMIT})
        raise AssertionError(request.url)

    adapter = make_adapter(tmp_path, handler)
    result = await adapter.readiness(request=request(workspace=tmp_path / "workspace"))
    assert result.ready is True
    assert result.checks["build_pinned"] is True
    await adapter.close()


@pytest.mark.asyncio
async def test_readiness_rejects_caller_selected_model(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/ready":
            return httpx.Response(200, json={"status": "ready"})
        if request.url.path == "/server_info":
            return httpx.Response(200, json={"versions": {"openhands-agent-server": "1.43.0"}, "build_sha": COMMIT})
        raise AssertionError(request.url)

    adapter = make_adapter(tmp_path, handler)
    malicious = request(workspace=tmp_path / "workspace").model_copy(
        update={
            "resolved_model_profile": ModelProfileReference(
                profile_id="attacker-profile",
                exact_model_id="attacker-model",
            )
        }
    )
    result = await adapter.readiness(request=malicious)
    assert result.ready is False
    assert result.checks["governed_model_id_pinned"] is True
    assert result.checks["exact_model_bound"] is False
    assert any("governed model snapshot" in blocker for blocker in result.blockers)
    await adapter.close()


@pytest.mark.asyncio
async def test_start_payload_and_message_contain_only_controlled_inputs(tmp_path: Path) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    adapter = make_adapter(tmp_path, handler)
    payload = adapter._start_payload(request(workspace=tmp_path / "workspace"))
    serialized = str(payload)
    assert payload["agent_profile_id"] == PROFILE_ID
    assert payload["workspace"]["working_dir"] == str((tmp_path / "workspace").resolve())
    assert payload["max_iterations"] == 4
    assert "initial_message" not in payload
    message = adapter._message_payload(request(workspace=tmp_path / "workspace"))
    assert message == {
        "role": "user",
        "content": [{"type": "text", "text": "Add the smallest possible test."}],
        "run": False,
    }
    assert "session-secret-test" not in serialized
    assert "api_key" not in serialized
    await adapter.close()


@pytest.mark.asyncio
async def test_start_payload_uses_v143_alphanumeric_conversation_tag_keys(tmp_path: Path) -> None:
    """The pinned SDK rejects underscores in ConversationTags with HTTP 422."""

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    adapter = make_adapter(tmp_path, handler)
    payload = adapter._start_payload(request(workspace=tmp_path / "workspace"))
    assert set(payload["tags"]) == {"aiatworkerid", "aiatrunid", "aiatidempotencykey"}
    assert all(key.isalnum() and key.islower() for key in payload["tags"])
    await adapter.close()


@pytest.mark.asyncio
async def test_conversation_create_422_is_retained_as_scalar_contract_diagnostic(tmp_path: Path) -> None:
    run = request(workspace=tmp_path / "workspace")

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/api/settings/mcp/aiat-openhands-test-run":
            return httpx.Response(201, json={})
        if request.method == "POST" and request.url.path == "/api/conversations":
            return httpx.Response(422, json={"detail": [{"loc": ["body", "tags"], "type": "value_error"}]})
        raise AssertionError(request)

    adapter = make_adapter(tmp_path, handler)
    with pytest.raises(httpx.HTTPStatusError):
        await adapter._create_conversation(run)
    diagnostics = adapter._diagnostics(run.run_id)
    assert diagnostics["conversation_create_status"] == "FAILED"
    assert diagnostics["conversation_create_http_status"] == 422
    assert diagnostics["conversation_id_present"] is False
    assert diagnostics["model_error_class"] == "CONVERSATION_CREATE_HTTP_422"
    assert diagnostics["request_errors"] == ["CONVERSATION_CREATE_HTTP_422"]
    await adapter.close()


@pytest.mark.asyncio
async def test_conversation_create_500_retains_sanitized_upstream_exception_and_shape(tmp_path: Path) -> None:
    run = request(workspace=tmp_path / "workspace")

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/api/settings/mcp/aiat-openhands-test-run":
            return httpx.Response(201, json={})
        if request.method == "POST" and request.url.path == "/api/conversations":
            return httpx.Response(
                500,
                json={
                    "exception": "ToolDefinition 'TerminalTool' is not registered",
                    "error_id": "request-correlation-value",
                },
            )
        raise AssertionError(request)

    adapter = make_adapter(tmp_path, handler)
    with pytest.raises(httpx.HTTPStatusError):
        await adapter._create_conversation(run)
    diagnostics = adapter._diagnostics(run.run_id)
    assert diagnostics["conversation_create_http_status"] == 500
    assert diagnostics["conversation_create_request_schema_valid"] is True
    assert diagnostics["conversation_create_request_shape_sha256"]
    assert diagnostics["conversation_create_profile_id_present"] is True
    assert diagnostics["conversation_create_workspace_path_present"] is True
    assert diagnostics["conversation_create_exception_class"] == "KeyError"
    assert diagnostics["conversation_create_exception_message_sanitized"] == (
        "ToolDefinition 'TerminalTool' is not registered"
    )
    assert diagnostics["conversation_create_failure_stage"] == "agent_initialization"
    assert diagnostics["conversation_create_error_fingerprint"]
    assert "request-correlation-value" not in json.dumps(diagnostics)
    await adapter.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("server_model", "expected_status"),
    [(OPENHANDS_WIRE_MODEL_ID, "PASS"), ("omniroute-coding", "FAILED")],
)
async def test_conversation_create_requires_v143_wire_model_prefix(
    tmp_path: Path,
    server_model: str,
    expected_status: str,
) -> None:
    """The logical alias is retained by AIAT, but v1.43 readback must be provider-qualified."""

    conversation_id = str(uuid4())
    run = request(workspace=tmp_path / "workspace")

    async def handler(http_request: httpx.Request) -> httpx.Response:
        if http_request.method == "POST" and http_request.url.path == "/api/settings/mcp/aiat-openhands-test-run":
            return httpx.Response(201, json={})
        if http_request.method == "POST" and http_request.url.path == "/api/conversations":
            return httpx.Response(201, json={"id": conversation_id})
        if http_request.method == "GET" and http_request.url.path == f"/api/conversations/{conversation_id}":
            return httpx.Response(200, json={"execution_status": "idle", "agent": {"llm": {"model": server_model}}})
        raise AssertionError(http_request)

    adapter = make_adapter(tmp_path, handler)
    if expected_status == "PASS":
        assert await adapter._create_conversation(run) == conversation_id
    else:
        with pytest.raises(RuntimeError, match="governed v1.43 wire model"):
            await adapter._create_conversation(run)
    diagnostics = adapter._diagnostics(run.run_id)
    assert diagnostics["model_resolution_status"] == expected_status
    assert diagnostics["model_resolution_logical_model_id"] == "omniroute-coding"
    assert diagnostics["model_resolution_wire_model_id"] == server_model
    await adapter.close()


@pytest.mark.asyncio
async def test_task_cannot_override_model_gateway_profile_or_budget(tmp_path: Path) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    adapter = make_adapter(tmp_path, handler)
    malicious = request(workspace=tmp_path / "workspace").model_copy(
        update={
            "task_input": {
                "prompt": "safe task",
                "model": "attacker-model",
                "provider": "attacker-provider",
                "base_url": "http://attacker.invalid",
                "api_key": "attacker-secret",
                "agent_profile_id": "attacker-profile",
                "workspace": "/operator",
                "mcp_servers": ["attacker-mcp"],
                "tools": ["github.write"],
                "credentials": {"provider": "attacker"},
            },
            "extensions": {"max_iterations": 999999},
        }
    )
    payload = adapter._start_payload(malicious)
    serialized = json.dumps(payload, sort_keys=True)
    assert payload["agent_profile_id"] == PROFILE_ID
    assert payload["max_iterations"] == 4
    assert payload["workspace"]["working_dir"] == str((tmp_path / "workspace").resolve())
    assert "attacker-model" not in serialized
    assert "attacker-provider" not in serialized
    assert "attacker.invalid" not in serialized
    assert "attacker-secret" not in serialized
    assert "attacker-profile" not in serialized
    over_budget = malicious.model_copy(update={"budget": {"max_iterations": 999999}})
    with pytest.raises(ValueError, match="exceeds the governed candidate budget"):
        adapter._start_payload(over_budget)
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
async def test_execute_maps_conversation_result_and_scalar_usage(tmp_path: Path, monkeypatch) -> None:
    # This fixture intentionally exercises the pinned SDK-compatible REST
    # fallback.  Production keeps the bounded v1.43 fallback enabled; the
    # test avoids waiting 30 seconds for a synthetic server that emits no WS
    # terminal snapshot.
    monkeypatch.setattr(adapter_module, "_TERMINAL_REST_FALLBACK_SECONDS", 0.0)
    conversation_id = str(uuid4())
    status_reads = 0
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal status_reads
        calls.append(f"{request.method} {request.url.path}")
        if request.method == "POST" and request.url.path == "/api/settings/mcp/aiat-openhands-test-run":
            payload = json.loads(request.content)
            assert payload["url"] == OPENHANDS_MCP_BRIDGE_URL
            assert "X-AIAT-OpenHands-Grant" in payload["headers"]
            return httpx.Response(201, json={})
        if request.method == "DELETE" and request.url.path == "/api/settings/mcp/aiat-openhands-test-run":
            return httpx.Response(200, json={})
        if request.method == "POST" and request.url.path == "/api/conversations":
            payload = json.loads(request.content)
            assert "initial_message" not in payload
            return httpx.Response(201, json={"id": conversation_id})
        if request.method == "GET" and request.url.path == f"/api/conversations/{conversation_id}":
            status_reads += 1
            if status_reads == 1:
                return httpx.Response(
                    200,
                    json={"execution_status": "idle", "agent": {"llm": {"model": OPENHANDS_WIRE_MODEL_ID}}},
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
        if request.method == "POST" and request.url.path == f"/api/conversations/{conversation_id}/events":
            payload = json.loads(request.content)
            assert payload["role"] == "user"
            assert payload["run"] is False
            return httpx.Response(200, json={"success": True})
        if request.method == "POST" and request.url.path == f"/api/conversations/{conversation_id}/run":
            if f"POST /api/conversations/{conversation_id}/events" not in calls:
                # v1.43 would return this conflict when the create payload
                # accidentally contained initial_message and the adapter then
                # attempted an explicit /run.
                return httpx.Response(409, json={"detail": "conversation already running"})
            return httpx.Response(200, json={"success": True})
        if request.method == "GET" and request.url.path == f"/api/conversations/{conversation_id}/agent_final_response":
            return httpx.Response(200, json={"response": "implemented"})
        if request.method == "GET" and request.url.path == "/api/git/changes":
            return httpx.Response(200, json=[])
        raise AssertionError(request.url)

    adapter = make_adapter(tmp_path, handler)
    run_request = request(workspace=tmp_path / "workspace")
    result = await adapter._execute(run_request)
    assert result.success is True
    assert result.output == "implemented"
    assert result.usage.total_tokens == 12
    assert result.usage.cost_usd == 0.12
    assert result.replay_metadata["openhands_conversation_id"] == conversation_id
    assert calls.count(f"POST /api/conversations/{conversation_id}/events") == 1
    assert calls.count(f"POST /api/conversations/{conversation_id}/run") == 1
    assert calls.index(f"POST /api/conversations/{conversation_id}/events") < calls.index(
        f"POST /api/conversations/{conversation_id}/run"
    )
    diagnostics = adapter._diagnostics(run_request.run_id)
    assert diagnostics["conversation_message_status"] == "PASS"
    assert diagnostics["conversation_message_http_status"] == 200
    assert diagnostics["conversation_message_endpoint"] == f"/api/conversations/{conversation_id}/events"
    assert diagnostics["run_start_status"] == "PASS"
    assert diagnostics["rest_terminal_fallback_used"] is True
    assert diagnostics["terminal_state_source"] == "rest_fallback"
    assert not adapter._event_tasks
    await adapter.close()


@pytest.mark.asyncio
async def test_execute_timeout_interrupts_remote_and_returns_terminal_timeout(tmp_path: Path) -> None:
    conversation_id = str(uuid4())
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.method == "POST" and request.url.path == "/api/settings/mcp/aiat-openhands-test-run":
            return httpx.Response(201, json={})
        if request.method == "DELETE" and request.url.path == "/api/settings/mcp/aiat-openhands-test-run":
            return httpx.Response(200, json={})
        if request.method == "POST" and request.url.path == "/api/conversations":
            assert "initial_message" not in json.loads(request.content)
            return httpx.Response(201, json={"id": conversation_id})
        if request.method == "GET" and request.url.path == f"/api/conversations/{conversation_id}":
            if calls.count(f"GET /api/conversations/{conversation_id}") == 1:
                return httpx.Response(
                    200,
                    json={"execution_status": "idle", "agent": {"llm": {"model": OPENHANDS_WIRE_MODEL_ID}}},
                )
            return httpx.Response(200, json={"execution_status": "running"})
        if request.method == "POST" and request.url.path == f"/api/conversations/{conversation_id}/events":
            payload = json.loads(request.content)
            assert payload["run"] is False
            return httpx.Response(200, json={"success": True})
        if request.method == "POST" and request.url.path == f"/api/conversations/{conversation_id}/run":
            return httpx.Response(200, json={"success": True})
        if request.method == "POST" and request.url.path == f"/api/conversations/{conversation_id}/interrupt":
            return httpx.Response(200, json={"success": True})
        if request.method == "DELETE" and request.url.path == f"/api/conversations/{conversation_id}":
            return httpx.Response(200, json={})
        raise AssertionError(request.url)

    adapter = make_adapter(tmp_path, handler)
    adapter.context.metadata["openhands_cleanup_conversations"] = True
    run_request = request(workspace=tmp_path / "workspace").model_copy(update={"timeout_seconds": 0.01})
    result = await adapter._execute(run_request)
    assert result.success is False
    assert result.error is not None
    assert result.error.code == "TIMEOUT"
    assert result.error.category == "timeout"
    assert result.error.terminal is True
    assert f"POST /api/conversations/{conversation_id}/interrupt" in calls
    assert f"DELETE /api/conversations/{conversation_id}" in calls
    await adapter.close()


@pytest.mark.asyncio
async def test_v143_state_update_terminal_mapping_ignores_initial_and_field_finished(tmp_path: Path) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    adapter = make_adapter(tmp_path, handler)
    run = request(workspace=tmp_path / "workspace")

    # The WebSocket subscription sends an initial snapshot before /run.  It
    # must never complete the newly requested execution.
    await adapter._emit_runtime_event(
        run,
        {
            "id": "initial-state",
            "kind": "ConversationStateUpdateEvent",
            "key": "full_state",
            "value": {"execution_status": "idle"},
        },
    )
    assert adapter._diagnostics(run.run_id)["terminal_state_observed"] is False
    # The advisory per-field FINISHED hint must not replace the effective
    # running state used by lifecycle polling.
    assert adapter.observed_execution_status(run.run_id) == "idle"

    adapter._run_started.add(run.run_id)
    # v1.43 treats a per-field FINISHED update as advisory; only the
    # post-run full-state snapshot is a successful terminal signal.
    await adapter._emit_runtime_event(
        run,
        {
            "id": "field-finished",
            "kind": "ConversationStateUpdateEvent",
            "key": "execution_status",
            "value": "finished",
        },
    )
    assert adapter._diagnostics(run.run_id)["terminal_state_observed"] is False

    await adapter._emit_runtime_event(
        run,
        {
            "id": "full-finished",
            "kind": "ConversationStateUpdateEvent",
            "key": "full_state",
            "value": {"execution_status": "finished"},
        },
    )
    diagnostics = adapter._diagnostics(run.run_id)
    assert diagnostics["terminal_state_observed"] is True
    assert diagnostics["terminal_state_value"] == "finished"
    assert diagnostics["terminal_state_source"] == "websocket"
    assert diagnostics["terminal_event_type"] == "ConversationStateUpdateEvent"
    await adapter.close()


@pytest.mark.asyncio
async def test_v143_rest_finished_is_advisory_until_execute_fallback(tmp_path: Path) -> None:
    conversation_id = str(uuid4())

    async def handler(http_request: httpx.Request) -> httpx.Response:
        if http_request.method == "GET" and http_request.url.path == f"/api/conversations/{conversation_id}":
            return httpx.Response(200, json={"execution_status": "finished"})
        raise AssertionError(http_request)

    adapter = make_adapter(tmp_path, handler)
    run = request(workspace=tmp_path / "workspace")
    info = await adapter._conversation(conversation_id, diagnostic_run_id=run.run_id)
    assert info["execution_status"] == "finished"
    diagnostics = adapter._diagnostics(run.run_id)
    assert diagnostics["terminal_state_observed"] is False
    assert diagnostics["terminal_state_source"] is None
    await adapter.close()


@pytest.mark.asyncio
async def test_v143_error_field_is_immediate_terminal_and_event_ids_are_counted(tmp_path: Path) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    adapter = make_adapter(tmp_path, handler)
    run = request(workspace=tmp_path / "workspace")
    adapter._run_started.add(run.run_id)
    event = {
        "id": "same-event",
        "kind": "ConversationStateUpdateEvent",
        "key": "execution_status",
        "value": "error",
    }
    await adapter._emit_runtime_event(run, event)
    await adapter._emit_runtime_event(run, event)
    diagnostics = adapter._diagnostics(run.run_id)
    assert diagnostics["terminal_state_value"] == "error"
    assert diagnostics["terminal_state_source"] == "websocket"
    assert diagnostics["duplicate_event_count"] == 1
    assert diagnostics["event_id_count"] == 2
    assert diagnostics["model_error_observed"] is False
    assert diagnostics["model_error_count"] == 0
    assert all("value" not in item for item in diagnostics["event_tail"])
    await adapter.close()


@pytest.mark.asyncio
async def test_conversation_error_is_not_assumed_to_be_model_error(tmp_path: Path) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    adapter = make_adapter(tmp_path, handler)
    run = request(workspace=tmp_path / "workspace")
    adapter._run_started.add(run.run_id)
    await adapter._emit_runtime_event(
        run,
        {
            "id": "internal-error",
            "kind": "ConversationErrorEvent",
            "code": "KeyError",
            "detail": "internal runtime failure",
        },
    )
    diagnostics = adapter._diagnostics(run.run_id)
    assert diagnostics["event_error_observed"] is True
    assert diagnostics["model_error_observed"] is False
    assert diagnostics["model_error_count"] == 0
    await adapter.close()


@pytest.mark.asyncio
async def test_execute_uses_authoritative_websocket_terminal_state_when_rest_lags(tmp_path: Path) -> None:
    conversation_id = str(uuid4())
    calls: list[str] = []

    async def handler(http_request: httpx.Request) -> httpx.Response:
        calls.append(f"{http_request.method} {http_request.url.path}")
        if http_request.method == "POST" and http_request.url.path == "/api/settings/mcp/aiat-openhands-test-run":
            return httpx.Response(201, json={})
        if http_request.method == "DELETE" and http_request.url.path == "/api/settings/mcp/aiat-openhands-test-run":
            return httpx.Response(204)
        if http_request.method == "POST" and http_request.url.path == "/api/conversations":
            return httpx.Response(201, json={"id": conversation_id})
        if http_request.method == "GET" and http_request.url.path == f"/api/conversations/{conversation_id}":
            return httpx.Response(
                200,
                json={
                    "execution_status": "running",
                    "agent": {"llm": {"model": OPENHANDS_WIRE_MODEL_ID}},
                    "metrics": {"accumulated_token_usage": {"prompt_tokens": 2, "completion_tokens": 3}},
                },
            )
        if http_request.method == "POST" and http_request.url.path == f"/api/conversations/{conversation_id}/events":
            return httpx.Response(200, json={"success": True})
        if http_request.method == "POST" and http_request.url.path == f"/api/conversations/{conversation_id}/run":
            return httpx.Response(200, json={"success": True})
        if http_request.method == "GET" and http_request.url.path == f"/api/conversations/{conversation_id}/agent_final_response":
            return httpx.Response(200, json={"response": "done"})
        if http_request.method == "GET" and http_request.url.path == "/api/git/changes":
            return httpx.Response(200, json=[])
        raise AssertionError(http_request)

    adapter = make_adapter(tmp_path, handler)

    async def emit_terminal(request: WorkerRunRequest, _: str) -> None:
        while request.run_id not in adapter._run_started:
            await asyncio.sleep(0)
        await adapter._emit_runtime_event(
            request,
            {
                "id": "post-run-full-state",
                "kind": "ConversationStateUpdateEvent",
                "key": "full_state",
                "value": {"execution_status": "finished"},
            },
        )

    adapter._consume_events = emit_terminal  # type: ignore[method-assign]
    run = request(workspace=tmp_path / "workspace")
    result = await adapter._execute(run)
    assert result.success is True
    assert result.output == "done"
    diagnostics = adapter._diagnostics(run.run_id)
    assert diagnostics["terminal_state_source"] == "websocket"
    assert diagnostics["terminal_state_value"] == "finished"
    assert diagnostics["last_status_poll_value"] == "running"
    assert diagnostics["rest_terminal_fallback_used"] is False
    assert calls.count(f"POST /api/conversations/{conversation_id}/run") == 1
    await adapter.close()


@pytest.mark.asyncio
async def test_finished_without_final_response_remains_fail_closed(tmp_path: Path) -> None:
    conversation_id = str(uuid4())

    async def handler(http_request: httpx.Request) -> httpx.Response:
        if http_request.method == "GET" and http_request.url.path == f"/api/conversations/{conversation_id}/agent_final_response":
            return httpx.Response(200, json={"response": ""})
        raise AssertionError(http_request)

    adapter = make_adapter(tmp_path, handler)
    run = request(workspace=tmp_path / "workspace")
    result = await adapter._terminal_result(run, conversation_id, "finished", {}, time.monotonic())
    assert result.success is False
    assert result.error is not None
    assert result.error.code == "OPENHANDS_FINAL_RESPONSE_MISSING"
    diagnostics = adapter._diagnostics(run.run_id)
    assert diagnostics["execution_failure_class"] == "FAILED_FINAL_RESPONSE"
    assert diagnostics["final_response_endpoint_called"] is True
    assert diagnostics["final_response_response_class"] == "empty"
    await adapter.close()
