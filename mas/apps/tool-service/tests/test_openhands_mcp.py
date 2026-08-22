"""Governance tests for the minimum OpenHands MCP bridge."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from tool_service.openhands_mcp import _execute_granted_tool, _requires_approval

from mas_core.worker_contract.openhands_bridge import (
    issue_openhands_tool_grant,
    verify_openhands_tool_grant,
)


class _Response:
    def model_dump(self, **_: object) -> dict[str, object]:
        return {"success": True, "result": {"ok": True}}


class _Registry:
    def __init__(self, tools: dict[str, object] | None = None) -> None:
        self._tools = tools or {}
        self.requests = []

    async def execute(self, request):  # noqa: ANN001
        self.requests.append(request)
        return _Response()


def _grant(*tools: str):
    token = issue_openhands_tool_grant(
        "bridge-secret",
        worker_id="worker-from-aiat",
        run_id=uuid4(),
        project_id=None,
        tool_names=tools,
        now=100,
    )
    return verify_openhands_tool_grant(token, "bridge-secret", now=110)


@pytest.mark.asyncio
async def test_bridge_reconstructs_worker_identity_and_rejects_forged_context() -> None:
    registry = _Registry()
    parent = SimpleNamespace(state=SimpleNamespace(registry=registry))
    grant = _grant("repository.read")

    result = await _execute_granted_tool(
        parent,
        grant,
        "repository.read",
        {"_aiat_context": {"caller_id": "attacker", "caller_role": "orchestrator"}},
    )
    assert result["success"] is True
    request = registry.requests[0]
    assert request.caller_id == "worker-from-aiat"
    assert request.caller_role.value == "worker"
    assert request.worker_run_id == grant.run_id


@pytest.mark.asyncio
async def test_bridge_denies_unlisted_and_approval_required_tools() -> None:
    registry = _Registry({"system.restart": SimpleNamespace(risk_tier="critical")})
    parent = SimpleNamespace(state=SimpleNamespace(registry=registry))

    assert _requires_approval(registry, "system.restart") is True
    denied = await _execute_granted_tool(parent, _grant("repository.read"), "repository.write", {})
    assert denied["error_code"] == "FORBIDDEN"
    pending = await _execute_granted_tool(parent, _grant("system.restart"), "system.restart", {})
    assert pending["error_code"] == "APPROVAL_REQUIRED"
    assert registry.requests == []
