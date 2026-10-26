"""Exercise the real adapter and Tool Service through a deterministic model boundary.

OpenCode's HTTP API is the only fixture in this test. The adapter, signed MCP
bridge, ToolRegistry, run-scoped workspace tools, WorkerRunController and
orchestrator artifact registrar are the production implementations.
"""

from __future__ import annotations

import hashlib
import json
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from tool_service.config import Settings
from tool_service.opencode_mcp import create_opencode_mcp_app
from tool_service.registry import ToolRegistry
from tool_service.tools.opencode_workspace import (
    OpenCodeWorkspaceReadTool,
    OpenCodeWorkspaceWriteTool,
)

from mas_core.memory.blob import BlobRef
from mas_core.worker_contract import (
    AdapterContext,
    WorkerRunController,
    WorkerRunRequest,
)
from mas_core.worker_registry.runtime_adapters import (
    OpenCodeAdapter,
    OpenCodeInterfaceVerification,
)

_ORCHESTRATOR_APP = Path(__file__).resolve().parents[1].parent / "orchestrator-api"
if str(_ORCHESTRATOR_APP) not in sys.path:
    sys.path.insert(0, str(_ORCHESTRATOR_APP))
from orchestrator_api.artifact_content import make_opencode_artifact_registrar  # noqa: E402


class _EffectLedger:
    """Test repository boundary that records MCP effect admission and settlement."""

    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, str], dict[str, Any]] = {}
        self.claim_owner = "executor-fixture"
        self.attempt_count = 1

    async def validate_worker_run_claim(
        self, *, run_id: UUID, claim_owner: str | None, attempt_count: int
    ) -> bool:
        return claim_owner == self.claim_owner and attempt_count == self.attempt_count

    async def begin_worker_tool_effect(self, **values: Any) -> dict[str, Any]:
        if (
            values.get("claim_owner") != self.claim_owner
            or values.get("attempt_count") != self.attempt_count
        ):
            return {"disposition": "CLAIM_LOST"}
        key = (values["run_id"], values["idempotency_key"])
        existing = self.rows.get(key)
        if existing is not None:
            if existing["request_sha256"] != values["request_sha256"]:
                return {**existing, "disposition": "CONFLICT"}
            if existing["state"] == "COMPLETED":
                return {**existing, "disposition": "REPLAY"}
            return {**existing, "disposition": "AMBIGUOUS"}
        row = {"id": uuid4(), **values, "state": "IN_FLIGHT", "response_json": None}
        self.rows[key] = row
        return {**row, "disposition": "NEW"}

    async def complete_worker_tool_effect(
        self,
        effect_id: UUID,
        *,
        run_id: UUID,
        claim_owner: str | None,
        attempt_count: int,
        state: str,
        response_json: dict[str, Any],
    ) -> dict[str, Any] | None:
        if state == "COMPLETED" and (
            claim_owner != self.claim_owner or attempt_count != self.attempt_count
        ):
            return None
        for row in self.rows.values():
            if (
                row["id"] == effect_id
                and row["run_id"] == run_id
                and row["state"] == "IN_FLIGHT"
            ):
                row["state"] = state
                row["response_json"] = response_json
                return dict(row)
        return None


class _RunStorage:
    def __init__(self, run_id: UUID, worker_id: UUID, project_id: UUID) -> None:
        self.row = {
            "id": run_id,
            "worker_id": worker_id,
            "project_id": project_id,
            "state": "CREATED",
            "claim_owner": "executor-fixture",
            "attempt_count": 1,
            "lease_expires_at": datetime.now(tz=UTC) + timedelta(minutes=2),
        }
        self.persisted_events: list[dict[str, Any]] = []
        self.terminal_evidence: dict[str, Any] | None = None
        self.model_resolution_snapshot_id: UUID | None = None

    async def get_worker_run(self, run_id: UUID) -> dict[str, Any] | None:
        return self.row if run_id == self.row["id"] else None

    async def validate_worker_run_claim(
        self,
        *,
        run_id: UUID,
        claim_owner: str | None,
        attempt_count: int,
    ) -> bool:
        return (
            run_id == self.row["id"]
            and claim_owner == self.row["claim_owner"]
            and attempt_count == self.row["attempt_count"]
            and self.row["state"] == "RUNNING"
        )

    async def get_model_resolution_snapshot(
        self,
        snapshot_id: UUID,
        *,
        project_id: UUID | None,
    ) -> dict[str, Any] | None:
        if (
            snapshot_id != self.model_resolution_snapshot_id
            or project_id != self.row["project_id"]
        ):
            return None
        return {"provider_id": "aiat", "exact_model_id": "aiat/local-fixture"}

    async def transition_worker_run(
        self,
        run_id: UUID,
        *,
        new_state: str,
        expected_state: str | None = None,
        claim_owner: str | None = None,
        attempt_count: int | None = None,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        negotiation: dict[str, Any] | None = None,
        replay_metadata: dict[str, Any] | None = None,
        transition_metadata: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any] | None:
        if (
            run_id != self.row["id"]
            or (expected_state is not None and expected_state != self.row["state"])
            or claim_owner != self.row["claim_owner"]
            or attempt_count != self.row["attempt_count"]
            or self.row["lease_expires_at"] <= datetime.now(tz=UTC)
        ):
            return None
        self.row["state"] = new_state
        if result is not None:
            self.row["result_json"] = result
        if error is not None:
            self.row["error_json"] = error
        if negotiation is not None:
            self.row["negotiation_json"] = negotiation
        if replay_metadata is not None:
            self.row["replay_metadata"] = replay_metadata
        if transition_metadata is not None:
            self.row["transition_metadata"] = transition_metadata
        return dict(self.row)

    async def append_worker_event(self, **event: Any) -> dict[str, Any]:
        self.persisted_events.append(event)
        return event

    async def complete_worker_run_success_claim_fenced(
        self,
        *,
        run_id: UUID,
        claim_owner: str | None,
        attempt_count: int,
        artifacts: list[dict[str, Any]],
        usage: dict[str, Any],
        result: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any] | None:
        if (
            run_id != self.row["id"]
            or self.row["state"] != "RUNNING"
            or claim_owner != self.row["claim_owner"]
            or attempt_count != self.row["attempt_count"]
            or self.row["lease_expires_at"] <= datetime.now(tz=UTC)
        ):
            return None
        self.terminal_evidence = {"artifacts": artifacts, "usage": usage}
        self.row["state"] = "SUCCEEDED"
        self.row["result_json"] = result
        return dict(self.row)


class _MemoryBlob:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.bucket = "alpha-worker-artifacts"

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def upload(
        self,
        project_id: str,
        key: str,
        content: bytes,
        *,
        content_type: str,
        bucket: str,
    ) -> BlobRef:
        object_key = f"{project_id}/{key}"
        self.objects[object_key] = content
        return BlobRef(
            bucket=bucket,
            key=object_key,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            content_type=content_type,
        )

    async def delete(self, ref: BlobRef) -> None:
        self.objects.pop(ref.key, None)


@asynccontextmanager
async def _mcp_client(bridge: Any, grant: str):
    headers = {
        "Accept": "application/json, text/event-stream",
        "X-AIAT-OpenCode-Grant": grant,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=bridge, raise_app_exceptions=False),
        base_url="http://tool-service:8002",
        headers=headers,
    ) as client:
        initialized = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "AIAT adapter integration fixture", "version": "1"},
                },
            },
        )
        assert initialized.status_code == 200, initialized.text
        client.headers["MCP-Session-Id"] = initialized.headers["mcp-session-id"]
        client.headers["MCP-Protocol-Version"] = "2025-03-26"
        await client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        yield client


async def _call_mcp_tool(
    client: httpx.AsyncClient,
    *,
    request_id: int,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    response = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {
                "name": "aiat_tool",
                "arguments": {"tool_name": tool_name, "arguments": arguments},
            },
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    return json.loads(
        next(item["text"] for item in result["content"] if item.get("type") == "text")
    )


def _verification(schema: dict[str, Any]) -> OpenCodeInterfaceVerification:
    endpoints = {
        "health": "/global/health",
        "openapi": "/doc",
        "project_current": "/project/current",
        "session_list": "/session",
        "session_create": "/session",
        "session_get": "/session/{sessionID}",
        "session_delete": "/session/{sessionID}",
        "session_status": "/session/status",
        "messages": "/session/{sessionID}/message",
        "prompt_async": "/session/{sessionID}/prompt_async",
        "events": "/global/event",
        "abort": "/session/{sessionID}/abort",
        "diff": "/session/{sessionID}/diff",
        "permission_reply": "/session/{sessionID}/permissions/{permissionID}",
        "mcp_add": "/mcp",
        "mcp_status": "/mcp",
    }
    digest = hashlib.sha256(
        json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return OpenCodeInterfaceVerification(
        release="1.17.13",
        commit_sha="F8C45BAE73A8F1E2088023FDD34DC2FE0A7F93F505F073E0703E4E1A19AFE8FF",
        report_version="test",
        approved=True,
        openapi_sha256=digest,
        config_schema_sha256="b" * 64,
        endpoints=endpoints,
        evidence={"approval_record_id": "test", "fixture_refs": ["adapter-tool-loop"]},
    )


@pytest.mark.asyncio
async def test_opencode_adapter_tool_service_effects_and_artifacts_integrate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A deterministic OpenCode boundary invokes real governed workspace tools."""
    run_id = uuid4()
    project_id = uuid4()
    worker_uuid = uuid4()
    worker_id = str(worker_uuid)
    tool_secret = "local-test-opencode-tool-secret"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("OPENCODE_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("MINIO_ENDPOINT", "http://object-store.fixture")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "fixture-access")
    monkeypatch.setenv("MINIO_SECRET_KEY", "fixture-secret")
    monkeypatch.setenv("MINIO_BUCKET", "alpha-worker-artifacts")

    settings = Settings(tool_secret=tool_secret)
    registry = ToolRegistry(settings)
    registry.register_all([OpenCodeWorkspaceReadTool(), OpenCodeWorkspaceWriteTool()])
    effects = _EffectLedger()
    parent = SimpleNamespace(
        state=SimpleNamespace(settings=settings, registry=registry, worker_tool_effects=effects)
    )
    bridge = create_opencode_mcp_app(parent)
    bridge_lifespan = bridge.aiat_lifespan()

    schema = {
        "openapi": "3.1.0",
        "paths": {"/global/health": {"get": {"operationId": "global.health"}}},
    }
    mcp_name: str | None = None
    mcp_grant: str | None = None
    local_provider_dispatches = 0
    session_id = "ses_alpha_tool_loop"
    contents = {
        "src/expenses.py": (
            "from collections import defaultdict\n\n"
            "def totals_by_category(rows):\n"
            "    totals = defaultdict(float)\n"
            "    for row in rows:\n"
            "        totals[row['category']] += float(row['amount'])\n"
            "    return dict(totals)\n"
        ),
        "tests/test_expenses.py": (
            "from src.expenses import totals_by_category\n\n"
            "def test_totals_by_category():\n"
            "    assert totals_by_category([{'category': 'food', 'amount': '2.50'}]) == {'food': 2.5}\n"
        ),
        "README.md": "# Expense totals\n\nA small CSV expense totals example.\n",
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal mcp_name, mcp_grant, local_provider_dispatches
        path = request.url.path
        if request.method == "GET" and path == "/global/health":
            return httpx.Response(200, json={"healthy": True}, request=request)
        if request.method == "GET" and path == "/doc":
            return httpx.Response(200, json=schema, request=request)
        if request.method == "POST" and path == "/mcp":
            config = json.loads(request.content)
            mcp_name = str(config["name"])
            assert config["config"]["url"] == "http://tool-service:8002/opencode/mcp"
            mcp_grant = config["config"]["headers"]["X-AIAT-OpenCode-Grant"]
            return httpx.Response(200, json={mcp_name: {"status": "connected"}}, request=request)
        if request.method == "GET" and path == "/mcp":
            return httpx.Response(
                200, json={str(mcp_name): {"status": "connected"}}, request=request
            )
        if request.method == "GET" and path == "/session":
            return httpx.Response(200, json=[], request=request)
        if request.method == "POST" and path == "/session":
            payload = json.loads(request.content)
            assert payload["permission"][-1]["permission"] == f"{mcp_name}_aiat_tool"
            assert payload["model"] == {
                "providerID": "local",
                "id": "fixture-model",
                "variant": "1",
            }
            return httpx.Response(200, json={"id": session_id}, request=request)
        if request.method == "GET" and path == "/global/event":
            return httpx.Response(
                200, text="", headers={"content-type": "text/event-stream"}, request=request
            )
        if request.method == "POST" and path == f"/session/{session_id}/prompt_async":
            local_provider_dispatches += 1
            assert json.loads(request.content)["model"] == {
                "providerID": "local",
                "modelID": "fixture-model",
            }
            assert mcp_grant is not None
            async with _mcp_client(bridge, mcp_grant) as client:
                for request_id, (file_path, content) in enumerate(contents.items(), start=1):
                    result = await _call_mcp_tool(
                        client,
                        request_id=request_id,
                        tool_name="opencode.workspace_write",
                        arguments={"path": file_path, "content": content},
                    )
                    assert result["success"] is True, result
                read_back = await _call_mcp_tool(
                    client,
                    request_id=4,
                    tool_name="opencode.workspace_read",
                    arguments={"path": "README.md"},
                )
                assert read_back["success"] is True
                assert read_back["result"]["content"] == contents["README.md"]
            return httpx.Response(204, request=request)
        if request.method == "GET" and path == f"/session/{session_id}/message":
            return httpx.Response(
                200,
                json=[
                    {
                        "info": {
                            "role": "assistant",
                            "tokens": {"input": 12, "output": 8, "total": 20},
                        },
                        "parts": [
                            {
                                "type": "text",
                                "text": "Created and verified the expense application.",
                            }
                        ],
                    }
                ],
                request=request,
            )
        if request.method == "GET" and path == "/session/status":
            return httpx.Response(200, json={session_id: {"type": "idle"}}, request=request)
        if request.method == "DELETE" and path == f"/session/{session_id}":
            return httpx.Response(204, request=request)
        raise AssertionError(f"unexpected OpenCode request: {request.method} {path}")

    objects: dict[str, bytes] = {}
    run_storage = _RunStorage(run_id, worker_uuid, project_id)
    registrar = make_opencode_artifact_registrar(
        run_storage,
        worker_id=worker_uuid,
        workspace_root=workspace,
        blob_client_factory=lambda *_args, **_kwargs: _MemoryBlob(objects),
    )
    verification = _verification(schema)
    client = httpx.AsyncClient(
        base_url="http://opencode.fixture", transport=httpx.MockTransport(handler)
    )
    adapter = OpenCodeAdapter(
        verification,
        base_url="http://opencode.fixture",
        worker_id=worker_id,
        model_mapper=lambda _model_id: "local/fixture-model",
        client=client,
        context=AdapterContext(
            secrets={"opencode_password": "fixture-password", "tool_secret": tool_secret},
            workspace_path=str(workspace),
            artifact_registrar=registrar,
            metadata={"opencode_cleanup_sessions": True},
        ),
    )
    request = WorkerRunRequest(
        run_id=run_id,
        idempotency_key=f"alpha-opencode-tool-loop:{run_id}",
        worker_id=worker_id,
        project_id=project_id,
        task_type="coding",
        task_input={"prompt": "Create the expense application through the AIAT workspace tools."},
        tool_grants=["opencode.workspace_read", "opencode.workspace_write"],
        resolved_model_profile={
            "profile_id": "local-coding-fixture",
            "version": "1",
            "exact_model_id": "aiat/local-fixture",
            "resolution_snapshot_id": uuid4(),
        },
        timeout_seconds=10,
    )
    run_storage.model_resolution_snapshot_id = (
        request.resolved_model_profile.resolution_snapshot_id
    )
    request._execution_claim_fence = {
        "claim_owner": "executor-fixture",
        "attempt_count": 1,
    }

    controller = WorkerRunController(storage=run_storage)

    try:
        async with bridge_lifespan:
            outcome = await controller.execute(request, adapter)
    finally:
        await adapter.close()
        await client.aclose()

    assert outcome.state == "SUCCEEDED", run_storage.row.get("error_json")
    assert outcome.result is not None and outcome.result.success is True
    assert outcome.result.output == "Created and verified the expense application."
    assert {artifact.uri for artifact in outcome.result.artifacts} == set(contents)
    assert local_provider_dispatches == 1  # deterministic boundary fixture; no external provider
    assert len(registry.get_audit_log(worker_id=worker_id)) == 4
    assert len(effects.rows) == 4
    assert all(row["state"] == "COMPLETED" for row in effects.rows.values())
    assert run_storage.row["state"] == "SUCCEEDED"
    assert run_storage.terminal_evidence is not None
    assert len(run_storage.terminal_evidence["artifacts"]) == len(contents)
    assert run_storage.terminal_evidence["usage"]["total_tokens"] == 20
    assert objects == {
        f"{project_id}/worker-runs/{run_id}/attempt-1/{name}": content.encode()
        for name, content in contents.items()
    }
    assert not adapter._active_tasks
