"""Governance tests for the minimum OpenHands MCP bridge."""

from __future__ import annotations

import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from tool_service.openhands_mcp import (
    _execute_granted_tool,
    _grant_context,
    _mcp_effect_request_id,
    _requires_approval,
    create_openhands_mcp_app,
)

from mas_core.protocols.tool import ToolResponse
from mas_core.worker_contract.openhands_bridge import (
    issue_openhands_tool_grant,
    verify_openhands_tool_grant,
)


class _Registry:
    def __init__(self, tools: dict[str, object] | None = None) -> None:
        self._tools = tools or {}
        self.requests = []

    async def execute(self, request):  # noqa: ANN001
        self.requests.append(request)
        return ToolResponse(
            tool_name=request.tool_name,
            worker_run_id=request.worker_run_id,
            idempotency_key=request.idempotency_key,
            success=True,
            result={"ok": True},
        )


class _EffectLedger:
    def __init__(self, *, current_claim_owner: str = "executor-1", current_attempt_count: int = 1) -> None:
        self.current_claim_owner = current_claim_owner
        self.current_attempt_count = current_attempt_count
        self.rows: dict[tuple[object, str], dict[str, object]] = {}
        self.completion_fences: list[dict[str, object]] = []

    async def validate_worker_run_claim(self, *, run_id, claim_owner, attempt_count):  # noqa: ANN001
        return (
            claim_owner == self.current_claim_owner
            and attempt_count == self.current_attempt_count
        )

    async def begin_worker_tool_effect(self, **values):  # noqa: ANN003
        key = (values["run_id"], values["idempotency_key"])
        row = self.rows.get(key)
        if row is None:
            row = {
                "id": uuid4(),
                **values,
                "state": "IN_FLIGHT",
                "response_json": None,
            }
            self.rows[key] = row
            disposition = "NEW"
        elif row["request_sha256"] != values["request_sha256"]:
            disposition = "CONFLICT"
        elif row["state"] == "COMPLETED" and row.get("response_json") is not None:
            disposition = "REPLAY"
        else:
            disposition = "AMBIGUOUS"
        return {**row, "disposition": disposition}

    async def complete_worker_tool_effect(
        self,
        effect_id,
        *,
        run_id,
        claim_owner,
        attempt_count,
        state,
        response_json,
    ):  # noqa: ANN001
        self.completion_fences.append(
            {
                "run_id": run_id,
                "claim_owner": claim_owner,
                "attempt_count": attempt_count,
                "state": state,
            }
        )
        if state == "COMPLETED" and (
            claim_owner != self.current_claim_owner
            or attempt_count != self.current_attempt_count
            or not claim_owner
            or attempt_count < 1
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


def _grant(
    *tools: str,
    project_id=None,
    claim_owner: str | None = "executor-1",
    attempt_count: int = 1,
):
    token = issue_openhands_tool_grant(
        "bridge-secret",
        worker_id="worker-from-aiat",
        run_id=uuid4(),
        project_id=project_id,
        tool_names=tools,
        claim_owner=claim_owner,
        attempt_count=attempt_count,
        now=100,
    )
    return verify_openhands_tool_grant(token, "bridge-secret", now=110)


@pytest.mark.asyncio
async def test_bridge_reconstructs_worker_identity_and_rejects_forged_context() -> None:
    registry = _Registry()
    ledger = _EffectLedger()
    parent = SimpleNamespace(
        state=SimpleNamespace(registry=registry, worker_tool_effects=ledger)
    )
    grant = _grant("repository.read", project_id=uuid4())
    request_id = uuid4()

    result = await _execute_granted_tool(
        parent,
        grant,
        "repository.read",
        {"_aiat_context": {"caller_id": "attacker", "caller_role": "orchestrator"}},
        request_id=request_id,
    )
    assert result["success"] is True
    request = registry.requests[0]
    assert request.caller_id == "worker-from-aiat"
    assert request.caller_role.value == "worker"
    assert request.worker_run_id == grant.run_id
    assert request.project_id == str(grant.project_id)
    assert request.idempotency_key == request_id
    effect = next(iter(ledger.rows.values()))
    assert effect["state"] == "COMPLETED"
    assert effect["response_json"] == result
    assert ledger.completion_fences == [
        {
            "run_id": grant.run_id,
            "claim_owner": grant.claim_owner,
            "attempt_count": grant.attempt_count,
            "state": "COMPLETED",
        }
    ]


@pytest.mark.asyncio
async def test_bridge_replays_a_durable_tool_effect_without_redispatch() -> None:
    registry = _Registry()
    ledger = _EffectLedger()
    parent = SimpleNamespace(
        state=SimpleNamespace(registry=registry, worker_tool_effects=ledger)
    )
    grant = _grant("repository.read")
    request_id = uuid4()

    first = await _execute_granted_tool(
        parent, grant, "repository.read", {"path": "README.md"}, request_id=request_id
    )
    second = await _execute_granted_tool(
        parent, grant, "repository.read", {"path": "README.md"}, request_id=request_id
    )

    assert first == second
    assert first["success"] is True
    assert len(registry.requests) == 1
    assert len(ledger.rows) == 1


@pytest.mark.asyncio
async def test_bridge_rejects_replay_with_mismatched_effect_request_hash() -> None:
    class CorruptReplayLedger(_EffectLedger):
        async def begin_worker_tool_effect(self, **values):  # noqa: ANN003
            effect = await super().begin_worker_tool_effect(**values)
            if effect.get("disposition") == "REPLAY":
                effect["request_sha256"] = "0" * 64
            return effect

    registry = _Registry()
    ledger = CorruptReplayLedger()
    parent = SimpleNamespace(
        state=SimpleNamespace(registry=registry, worker_tool_effects=ledger)
    )
    grant = _grant("repository.read")
    request_id = uuid4()

    first = await _execute_granted_tool(
        parent, grant, "repository.read", {"path": "README.md"}, request_id=request_id
    )
    replay = await _execute_granted_tool(
        parent, grant, "repository.read", {"path": "README.md"}, request_id=request_id
    )

    assert first["success"] is True
    assert replay["success"] is False
    assert replay["error_code"] == "TOOL_EFFECT_AMBIGUOUS"
    assert len(registry.requests) == 1


@pytest.mark.asyncio
async def test_bridge_fails_closed_without_ledger_or_current_claim() -> None:
    registry = _Registry()
    grant = _grant("repository.read")
    request_id = uuid4()
    no_ledger = await _execute_granted_tool(
        SimpleNamespace(state=SimpleNamespace(registry=registry)),
        grant,
        "repository.read",
        {},
        request_id=request_id,
    )
    stale_ledger = _EffectLedger(current_claim_owner="executor-new", current_attempt_count=2)
    stale_claim = await _execute_granted_tool(
        SimpleNamespace(
            state=SimpleNamespace(registry=registry, worker_tool_effects=stale_ledger)
        ),
        grant,
        "repository.read",
        {},
        request_id=uuid4(),
    )

    assert no_ledger["error_code"] == "TOOL_EFFECT_LEDGER_UNAVAILABLE"
    assert stale_claim["error_code"] == "WORKER_RUN_CLAIM_LOST"
    assert registry.requests == []


@pytest.mark.asyncio
async def test_bridge_does_not_return_success_when_effect_settlement_is_unconfirmed() -> None:
    class UnsettledLedger(_EffectLedger):
        async def complete_worker_tool_effect(self, *_args, **_kwargs):
            return None

    registry = _Registry()
    ledger = UnsettledLedger()
    parent = SimpleNamespace(
        state=SimpleNamespace(registry=registry, worker_tool_effects=ledger)
    )
    grant = _grant("repository.read")
    request_id = uuid4()

    result = await _execute_granted_tool(
        parent,
        grant,
        "repository.read",
        {"path": "README.md"},
        request_id=request_id,
    )

    assert result["success"] is False
    assert result["error_code"] == "TOOL_EFFECT_COMPLETION_UNAVAILABLE"
    assert len(registry.requests) == 1
    effect = next(iter(ledger.rows.values()))
    assert effect["state"] == "IN_FLIGHT"


@pytest.mark.asyncio
async def test_bridge_claim_reassigned_after_dispatch_cannot_return_success() -> None:
    grant = _grant("repository.read")
    ledger = _EffectLedger()

    class ReassigningRegistry(_Registry):
        async def execute(self, request):  # noqa: ANN001
            response = await super().execute(request)
            ledger.current_claim_owner = "executor-new"
            ledger.current_attempt_count = grant.attempt_count + 1
            return response

    registry = ReassigningRegistry()
    parent = SimpleNamespace(
        state=SimpleNamespace(registry=registry, worker_tool_effects=ledger)
    )
    result = await _execute_granted_tool(
        parent,
        grant,
        "repository.read",
        {"path": "README.md"},
        request_id=uuid4(),
    )

    assert result["success"] is False
    assert result["error_code"] == "TOOL_EFFECT_COMPLETION_UNAVAILABLE"
    assert len(registry.requests) == 1
    effect = next(iter(ledger.rows.values()))
    assert effect["state"] == "IN_FLIGHT"


def test_bridge_request_identity_is_stable_within_mcp_session() -> None:
    run_id = uuid4()

    def context(session_id: str, request_id: int):
        return SimpleNamespace(
            request_context=SimpleNamespace(
                request_id=request_id,
                request=SimpleNamespace(headers={"mcp-session-id": session_id}),
            )
        )

    first = _mcp_effect_request_id(run_id, context("session-a", 42))
    replay = _mcp_effect_request_id(run_id, context("session-a", 42))
    other_session = _mcp_effect_request_id(run_id, context("session-b", 42))

    assert first == replay
    assert first != other_session


@pytest.mark.asyncio
async def test_bridge_denies_unlisted_and_approval_required_tools() -> None:
    registry = _Registry({"system.restart": SimpleNamespace(risk_tier="critical")})
    parent = SimpleNamespace(state=SimpleNamespace(registry=registry))

    assert _requires_approval(registry, "system.restart") is True
    denied = await _execute_granted_tool(
        parent,
        _grant("repository.read"),
        "repository.write",
        {},
        request_id=uuid4(),
    )
    assert denied["error_code"] == "FORBIDDEN"
    pending = await _execute_granted_tool(
        parent,
        _grant("system.restart"),
        "system.restart",
        {},
        request_id=uuid4(),
    )
    assert pending["error_code"] == "APPROVAL_REQUIRED"
    assert registry.requests == []


def _asgi_grant(*, worker_id: str, run_id, project_id) -> str:  # noqa: ANN001
    return issue_openhands_tool_grant(
        "bridge-secret",
        worker_id=worker_id,
        run_id=run_id,
        project_id=project_id,
        tool_names=["repository.read"],
        claim_owner="executor-1",
        attempt_count=1,
        now=int(time.time()),
    )


def _asgi_parent() -> tuple[FastAPI, object, _Registry, _EffectLedger]:
    parent = FastAPI()
    registry = _Registry()
    ledger = _EffectLedger()
    parent.state.settings = SimpleNamespace(tool_secret="bridge-secret")
    parent.state.registry = registry
    parent.state.worker_tool_effects = ledger
    bridge = create_openhands_mcp_app(parent)
    parent.mount("/openhands", bridge)
    return parent, bridge, registry, ledger


async def _initialize_mcp_client(client: httpx.AsyncClient) -> None:
    initialized = await client.post(
        "/openhands/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "AIAT OpenHands bridge test", "version": "1"},
            },
        },
    )
    assert initialized.status_code == 200, initialized.text
    session_id = initialized.headers.get("mcp-session-id")
    assert session_id
    client.headers["MCP-Session-Id"] = session_id
    client.headers["MCP-Protocol-Version"] = "2025-03-26"
    notification = await client.post(
        "/openhands/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert notification.status_code in {200, 202, 204}, notification.text


async def _post_mcp_tool_call(client: httpx.AsyncClient, request_id: int) -> httpx.Response:
    return await client.post(
        "/openhands/mcp",
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {
                "name": "aiat_tool",
                "arguments": {"tool_name": "repository.read", "arguments": {"path": "README.md"}},
            },
        },
    )


@pytest.mark.asyncio
async def test_asgi_bridge_rejects_missing_and_invalid_grants_before_dispatch() -> None:
    parent, bridge, registry, ledger = _asgi_parent()
    grant = _asgi_grant(worker_id="worker-a", run_id=uuid4(), project_id=uuid4())
    headers = {
        "Accept": "application/json, text/event-stream",
        "X-AIAT-OpenHands-Grant": grant,
    }

    async with bridge.aiat_lifespan(), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=parent, raise_app_exceptions=False),
        base_url="http://tool-service:8002",
        headers=headers,
    ) as client:
        await _initialize_mcp_client(client)
        assert _grant_context.get() is None

        client.headers.pop("X-AIAT-OpenHands-Grant")
        missing = await _post_mcp_tool_call(client, request_id=41)
        client.headers["X-AIAT-OpenHands-Grant"] = "not-a-valid-grant"
        invalid = await _post_mcp_tool_call(client, request_id=42)

    assert missing.status_code == 403
    assert invalid.status_code == 403
    assert registry.requests == []
    assert ledger.rows == {}


@pytest.mark.asyncio
async def test_asgi_bridge_grant_context_is_scoped_to_each_request() -> None:
    parent, bridge, registry, _ledger = _asgi_parent()
    run_a, run_b = uuid4(), uuid4()
    project_a, project_b = uuid4(), uuid4()
    grant_a = _asgi_grant(worker_id="worker-a", run_id=run_a, project_id=project_a)
    grant_b = _asgi_grant(worker_id="worker-b", run_id=run_b, project_id=project_b)
    common_headers = {"Accept": "application/json, text/event-stream"}

    async with bridge.aiat_lifespan(), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=parent, raise_app_exceptions=False),
        base_url="http://tool-service:8002",
        headers={**common_headers, "X-AIAT-OpenHands-Grant": grant_a},
    ) as client_a, httpx.AsyncClient(
        transport=httpx.ASGITransport(app=parent, raise_app_exceptions=False),
        base_url="http://tool-service:8002",
        headers={**common_headers, "X-AIAT-OpenHands-Grant": grant_b},
    ) as client_b:
        await _initialize_mcp_client(client_a)
        assert _grant_context.get() is None
        result_a = await _post_mcp_tool_call(client_a, request_id=51)
        assert result_a.status_code == 200, result_a.text
        assert _grant_context.get() is None

        await _initialize_mcp_client(client_b)
        assert _grant_context.get() is None
        result_b = await _post_mcp_tool_call(client_b, request_id=52)
        assert result_b.status_code == 200, result_b.text
        assert _grant_context.get() is None

    assert len(registry.requests) == 2
    request_a, request_b = registry.requests
    assert (request_a.caller_id, request_a.worker_run_id, request_a.project_id) == (
        "worker-a",
        run_a,
        str(project_a),
    )
    assert (request_b.caller_id, request_b.worker_run_id, request_b.project_id) == (
        "worker-b",
        run_b,
        str(project_b),
    )
