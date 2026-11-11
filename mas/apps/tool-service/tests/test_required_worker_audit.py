"""Failure injection at the governed tool boundary (no external services)."""

from uuid import uuid4

import pytest

from mas_core.protocols.enums import AgentRole
from mas_core.protocols.tool import ToolRequest
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup


class _MutatingTool(BaseTool):
    name = "test.audit_mutation"
    group = ToolGroup.KPI_UTILITY
    side_effect = True
    idempotent = False

    def __init__(self, *, fail=False):
        self.calls = 0
        self.fail = fail

    async def execute(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("partial external effect")
        return {"ok": True}


class _Audit:
    def __init__(self, fail_at=None):
        self.fail_at = fail_at
        self.rows = {}

    async def record_required_tool_audit(self, **values):
        status = values["status"]
        if status == self.fail_at:
            raise RuntimeError("database unavailable")
        phase = status if status in {"intent", "forbidden"} else "outcome"
        key = (values["run_id"], values["idempotency_key"], phase)
        if key in self.rows:
            return False
        self.rows[key] = status
        return True

    async def record_project_usage(self, **values):
        return {}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["intent", "success", "partial", "none"])
async def test_governed_effect_audit_failure_and_retry(make_registry, failure):
    registry = make_registry()
    tool = _MutatingTool(fail=failure == "partial")
    registry.register(tool)
    registry.grant_tool("worker", tool.name)
    audit = _Audit(fail_at=failure if failure in {"intent", "success"} else None)
    registry.set_usage_storage(audit)
    request = ToolRequest(
        caller_id="worker", caller_role=AgentRole.WORKER, tool_name=tool.name,
        project_id=str(uuid4()), worker_run_id=uuid4(), idempotency_key=uuid4(),
    )

    result = await registry.execute(request)
    if failure == "intent":
        assert result.error_code == "AUDIT_INTENT_UNAVAILABLE"
        assert tool.calls == 0
        return
    assert tool.calls == 1
    if failure in {"success", "partial"}:
        assert result.error_code == "TOOL_AUDIT_OUTCOME_UNCERTAIN"
        assert not result.success
        assert "intent" in audit.rows.values()
        if failure == "partial":
            assert "uncertain" in audit.rows.values()
    else:
        assert result.success
        assert set(audit.rows.values()) == {"intent", "success"}

    audit.fail_at = None
    replay = await registry.execute(request)
    assert replay.error_code == "AUDIT_INTENT_UNAVAILABLE"
    assert tool.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("storage_down", [False, True])
async def test_denied_worker_request_requires_durable_audit(make_registry, storage_down):
    registry = make_registry()
    tool = _MutatingTool()
    registry.register(tool)
    audit = _Audit(fail_at="forbidden" if storage_down else None)
    registry.set_usage_storage(audit)
    request = ToolRequest(
        caller_id="ungranted-worker", caller_role=AgentRole.WORKER, tool_name=tool.name,
        project_id=str(uuid4()), worker_run_id=uuid4(), idempotency_key=uuid4(),
    )
    result = await registry.execute(request)
    assert result.error_code == ("AUDIT_DENIAL_UNAVAILABLE" if storage_down else "FORBIDDEN")
    assert tool.calls == 0
    if not storage_down:
        assert list(audit.rows.values()) == ["forbidden"]


@pytest.mark.asyncio
async def test_production_worker_cannot_bypass_run_audit(make_registry, monkeypatch):
    registry = make_registry()
    tool = _MutatingTool()
    registry.register(tool)
    registry.grant_tool("worker", tool.name)
    monkeypatch.setenv("MAS_ENVIRONMENT", "production")
    request = ToolRequest(caller_id="worker", caller_role=AgentRole.WORKER, tool_name=tool.name)
    result = await registry.execute(request)
    assert result.error_code == "WORKER_RUN_REQUIRED"
    assert tool.calls == 0


@pytest.mark.asyncio
async def test_worker_run_project_cannot_be_overridden_in_tool_arguments(make_registry):
    class ReadProject(_MutatingTool):
        name = "test.project_isolation"
        side_effect = False

        async def execute(self, **kwargs):
            return {"project_id": kwargs["project_id"]}

    registry = make_registry()
    tool = ReadProject()
    registry.register(tool)
    registry.grant_tool("worker", tool.name)
    project_id = str(uuid4())
    request = ToolRequest(caller_id="worker", caller_role=AgentRole.WORKER,
        tool_name=tool.name, project_id=project_id, worker_run_id=uuid4(),
        tool_kwargs={"project_id": str(uuid4())})
    response = await registry.execute(request)
    assert response.success
    assert response.result["project_id"] == project_id
