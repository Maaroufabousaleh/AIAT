"""Contract and transport tests for the generated Python API SDK."""

from __future__ import annotations

import httpx
import pytest
from mas_api_sdk import (
    MODEL_COUNT,
    OPERATION_COUNT,
    OPERATIONS,
    ApiError,
    OperationNotFoundError,
    OrchestratorClient,
)


def _operation(path: str, method: str) -> str:
    return next(
        operation_id
        for operation_id, operation in OPERATIONS.items()
        if operation.path == path and operation.method == method
    )


def test_generated_contract_counts_and_parameter_metadata() -> None:
    assert MODEL_COUNT == 135
    assert OPERATION_COUNT == 271
    operation = OPERATIONS[_operation("/projects/{project_id}", "GET")]
    assert operation.path_params == ("project_id",)
    assert operation.method == "GET"
    improvement = OPERATIONS[_operation("/projects/self-improvement", "POST")]
    assert improvement.request_body_type == "ImprovementOpportunity"


@pytest.mark.anyio
async def test_client_renders_path_query_and_api_key() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["query"] = request.url.params.get("limit")
        seen["api_key"] = request.headers.get("X-API-Key")
        return httpx.Response(200, json={"id": "project-1"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://orchestrator", transport=transport) as raw:
        client = OrchestratorClient("http://orchestrator", api_key="test-key", client=raw)
        result = await client.request(
            _operation("/projects", "GET"),
            query={"limit": 5},
        )

    assert result == {"id": "project-1"}
    assert seen == {"path": "/projects", "query": "5", "api_key": "test-key"}


@pytest.mark.anyio
async def test_client_surfaces_api_errors_and_unknown_operations() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "stale revision"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://orchestrator", transport=transport) as raw:
        client = OrchestratorClient("http://orchestrator", client=raw)
        with pytest.raises(ApiError, match="stale revision"):
            await client.request(_operation("/projects", "GET"))

    with pytest.raises(OperationNotFoundError):
        OrchestratorClient.operation("not-an-operation")
