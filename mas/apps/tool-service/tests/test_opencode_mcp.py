"""Durable idempotency for the signed OpenCode MCP bridge."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from tool_service.opencode_mcp import create_opencode_mcp_app

from mas_core.protocols.tool import ToolResponse
from mas_core.worker_contract.opencode_bridge import issue_opencode_tool_grant


class _EffectLedger:
    """Durable-store test double at the SQL repository boundary."""

    def __init__(self, *, preexisting_state: str | None = None) -> None:
        self.rows: dict[tuple[UUID, str], dict[str, Any]] = {}
        self.preexisting_state = preexisting_state

    async def begin_worker_tool_effect(self, **values: Any) -> dict[str, Any]:
        key = (values["run_id"], values["idempotency_key"])
        row = self.rows.get(key)
        if row is None:
            row = {
                "id": uuid4(),
                **values,
                "state": self.preexisting_state or "IN_FLIGHT",
                "response_json": None,
            }
            self.rows[key] = row
            disposition = "AMBIGUOUS" if self.preexisting_state else "NEW"
        elif row["request_sha256"] != values["request_sha256"]:
            disposition = "CONFLICT"
        elif row["state"] == "COMPLETED" and row["response_json"] is not None:
            disposition = "REPLAY"
        else:
            disposition = "AMBIGUOUS"
        return {**row, "disposition": disposition}

    async def complete_worker_tool_effect(
        self,
        effect_id: UUID,
        *,
        state: str,
        response_json: dict[str, Any],
    ) -> dict[str, Any] | None:
        for row in self.rows.values():
            if row["id"] == effect_id and row["state"] == "IN_FLIGHT":
                row["state"] = state
                row["response_json"] = response_json
                return dict(row)
        return None


class _Registry:
    def __init__(self, ledger: _EffectLedger | None = None) -> None:
        self.requests = []
        self.ledger = ledger
        self.raise_on_execute = False

    async def execute(self, request):  # noqa: ANN001
        if self.ledger is not None:
            key = (request.worker_run_id, str(request.idempotency_key))
            assert self.ledger.rows[key]["state"] == "IN_FLIGHT"
        self.requests.append(request)
        if self.raise_on_execute:
            raise RuntimeError("sanitized test dispatch failure")
        return ToolResponse(
            tool_name=request.tool_name,
            worker_run_id=request.worker_run_id,
            idempotency_key=request.idempotency_key,
            success=True,
            result={"call": len(self.requests), "path": request.tool_kwargs.get("path")},
        )


@asynccontextmanager
async def _open_mcp_client(parent: Any, grant: str):
    bridge = create_opencode_mcp_app(parent)
    headers = {
        "Accept": "application/json, text/event-stream",
        "X-AIAT-OpenCode-Grant": grant,
    }
    async with bridge.aiat_lifespan(), httpx.AsyncClient(
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
                    "clientInfo": {"name": "AIAT Level A test", "version": "1"},
                },
            },
        )
        assert initialized.status_code == 200, initialized.text
        session_id = initialized.headers.get("mcp-session-id")
        assert session_id
        client.headers["MCP-Session-Id"] = session_id
        client.headers["MCP-Protocol-Version"] = "2025-03-26"
        await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        yield client


async def _call_tool(
    client: httpx.AsyncClient,
    *,
    request_id: int | str,
    tool_name: str = "repository.read",
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {
                "name": "aiat_tool",
                "arguments": {"tool_name": tool_name, "arguments": arguments or {}},
            },
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    for content in result.get("content", []):
        if content.get("type") == "text":
            return json.loads(content["text"])
    raise AssertionError(f"MCP tool result had no structured response: {result}")


def _parent(
    *, with_ledger: bool = True, preexisting_state: str | None = None
) -> tuple[Any, _Registry, _EffectLedger | None, UUID, str]:
    ledger = _EffectLedger(preexisting_state=preexisting_state) if with_ledger else None
    registry = _Registry(ledger)
    run_id = uuid4()
    state = SimpleNamespace(
        settings=SimpleNamespace(tool_secret="test-bridge-secret"),
        registry=registry,
        worker_tool_effects=ledger,
    )
    parent = SimpleNamespace(state=state)
    token = issue_opencode_tool_grant(
        "test-bridge-secret",
        worker_id="test-worker",
        run_id=run_id,
        project_id=uuid4(),
        tool_names=["repository.read", "opencode.workspace_read"],
    )
    return parent, registry, ledger, run_id, token


@pytest.mark.asyncio
async def test_new_signed_mcp_tool_call_is_journaled_before_dispatch() -> None:
    parent, registry, ledger, run_id, token = _parent()
    assert ledger is not None

    async with _open_mcp_client(parent, token) as client:
        result = await _call_tool(
            client,
            request_id=17,
            tool_name="opencode.workspace_read",
            arguments={"path": "README.md"},
        )

    assert result["success"] is True
    assert len(registry.requests) == 1
    request = registry.requests[0]
    assert request.worker_run_id == run_id
    assert request.project_id
    assert request.caller_id == "test-worker"
    assert request.permission_scope == ["opencode.workspace_read", "repository.read"]
    assert request.tool_kwargs == {"path": "README.md", "workspace_run_id": str(run_id)}
    row = next(iter(ledger.rows.values()))
    assert row["state"] == "COMPLETED"
    assert row["tool_name"] == "opencode.workspace_read"
    assert row["request_id"] == UUID(row["idempotency_key"])
    assert row["response_json"]["result"]["call"] == 1


@pytest.mark.asyncio
async def test_completed_same_request_replays_without_second_registry_dispatch() -> None:
    parent, registry, ledger, _, token = _parent()
    assert ledger is not None
    async with _open_mcp_client(parent, token) as client:
        first = await _call_tool(client, request_id="write-4", arguments={"path": "a.txt"})
        row = next(iter(ledger.rows.values()))
        row["response_json"] = json.dumps(row["response_json"])
        replay = await _call_tool(client, request_id="write-4", arguments={"path": "a.txt"})

    assert first == replay
    assert first["result"]["call"] == 1
    assert len(registry.requests) == 1


@pytest.mark.asyncio
async def test_reused_request_identity_with_different_arguments_conflicts() -> None:
    parent, registry, _, _, token = _parent()
    async with _open_mcp_client(parent, token) as client:
        await _call_tool(client, request_id=25, arguments={"path": "first.txt"})
        result = await _call_tool(client, request_id=25, arguments={"path": "second.txt"})

    assert result["error_code"] == "TOOL_IDEMPOTENCY_CONFLICT"
    assert len(registry.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["IN_FLIGHT", "AMBIGUOUS"])
async def test_unfinished_effect_is_ambiguous_and_never_redispatched(state: str) -> None:
    parent, registry, ledger, run_id, token = _parent(preexisting_state=state)
    assert ledger is not None

    async with _open_mcp_client(parent, token) as client:
        result = await _call_tool(client, request_id=31, arguments={"path": "pending.txt"})

    assert result["error_code"] == "TOOL_EFFECT_AMBIGUOUS"
    assert registry.requests == []
    row = next(iter(ledger.rows.values()))
    assert row["run_id"] == run_id
    assert row["state"] == state


@pytest.mark.asyncio
async def test_unavailable_durable_ledger_fails_closed_before_registry_dispatch() -> None:
    parent, registry, _, run_id, token = _parent(with_ledger=False)
    async with _open_mcp_client(parent, token) as client:
        result = await _call_tool(client, request_id=42)

    assert result["error_code"] == "TOOL_EFFECT_LEDGER_UNAVAILABLE"
    assert result["worker_run_id"] == str(run_id)
    assert registry.requests == []


@pytest.mark.asyncio
async def test_registry_exception_is_marked_ambiguous_and_not_retried() -> None:
    parent, registry, ledger, _, token = _parent()
    assert ledger is not None
    registry.raise_on_execute = True

    async with _open_mcp_client(parent, token) as client:
        first = await _call_tool(client, request_id="uncertain-9", arguments={"path": "b.txt"})
        registry.raise_on_execute = False
        replay = await _call_tool(client, request_id="uncertain-9", arguments={"path": "b.txt"})

    row = next(iter(ledger.rows.values()))
    assert first["error_code"] == "TOOL_EFFECT_AMBIGUOUS"
    assert replay["error_code"] == "TOOL_EFFECT_AMBIGUOUS"
    assert row["state"] == "AMBIGUOUS"
    assert len(registry.requests) == 1


@pytest.mark.asyncio
async def test_jsonrpc_integer_and_string_ids_have_distinct_effect_identities() -> None:
    parent, registry, _, _, token = _parent()
    async with _open_mcp_client(parent, token) as client:
        integer_result = await _call_tool(client, request_id=7, arguments={"path": "same.txt"})
        string_result = await _call_tool(client, request_id="7", arguments={"path": "same.txt"})

    assert integer_result["success"] is True
    assert string_result["success"] is True
    assert len(registry.requests) == 2
