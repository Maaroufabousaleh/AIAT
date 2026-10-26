"""A stale WorkerRun claim must not enter the Tool Registry pipeline."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from tool_service.routes import ToolRunRequest, execute_tool, run_tool

from mas_core.protocols.enums import AgentRole
from mas_core.protocols.tool import ToolRequest, ToolResponse


class _ClaimValidator:
    def __init__(self, current: bool) -> None:
        self.current = current
        self.calls: list[dict[str, object]] = []

    async def validate_worker_run_claim(self, **values: object) -> bool:
        self.calls.append(values)
        return self.current


class _Registry:
    def __init__(self) -> None:
        self.requests = []

    async def execute(self, body):  # noqa: ANN001
        self.requests.append(body)
        return ToolResponse(
            tool_name=body.tool_name,
            worker_run_id=body.worker_run_id,
            idempotency_key=body.idempotency_key,
            success=True,
        )


def _request_context(validator: _ClaimValidator, registry: _Registry):
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(environment_is_production=False),
                worker_tool_effects=validator,
                registry=registry,
            )
        ),
        state=SimpleNamespace(),
    )


@pytest.mark.asyncio
async def test_reclaimed_attempt_is_denied_before_registry_dispatch() -> None:
    run_id = uuid4()
    validator = _ClaimValidator(current=False)
    registry = _Registry()
    request = _request_context(validator, registry)
    body = ToolRunRequest(
        caller_id="worker-1",
        caller_role=AgentRole.WORKER,
        worker_run_id=run_id,
        claim_owner="executor-old",
        claim_attempt_count=1,
        tool_name="repository.read",
        tool_kwargs={"path": "README.md"},
    )

    response = await run_tool("repository.read", request, body, _auth=None)  # type: ignore[arg-type]

    assert response.error_code == "WORKER_RUN_CLAIM_LOST"
    assert response.worker_run_id == run_id
    assert validator.calls == [
        {"run_id": run_id, "claim_owner": "executor-old", "attempt_count": 1}
    ]
    assert registry.requests == []


@pytest.mark.asyncio
async def test_missing_claim_validation_fails_closed() -> None:
    run_id = uuid4()
    registry = _Registry()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(environment_is_production=False),
                worker_tool_effects=None,
                usage_storage=None,
                registry=registry,
            )
        ),
        state=SimpleNamespace(),
    )
    body = ToolRunRequest(
        caller_id="worker-1",
        caller_role=AgentRole.WORKER,
        worker_run_id=run_id,
        claim_owner="executor-1",
        claim_attempt_count=1,
        tool_name="repository.read",
    )

    with pytest.raises(HTTPException, match="Durable WorkerRun claim validation is unavailable") as caught:
        await run_tool("repository.read", request, body, _auth=None)  # type: ignore[arg-type]

    assert caught.value.status_code == 503
    assert registry.requests == []


@pytest.mark.asyncio
async def test_legacy_execute_endpoint_rejects_worker_scoped_requests() -> None:
    run_id = uuid4()
    validator = _ClaimValidator(current=True)
    registry = _Registry()
    request = _request_context(validator, registry)
    body = ToolRequest(
        caller_id="worker-1",
        caller_role=AgentRole.WORKER,
        worker_run_id=run_id,
        tool_name="repository.read",
        tool_kwargs={"path": "README.md"},
    )

    with pytest.raises(HTTPException, match="claim-fenced path-driven endpoint") as caught:
        await execute_tool(request, body, _auth=None)  # type: ignore[arg-type]

    assert caught.value.status_code == 409
    assert validator.calls == []
    assert registry.requests == []
