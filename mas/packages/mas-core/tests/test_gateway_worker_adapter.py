"""Contract tests for the AIAT model-gateway worker adapter."""

from __future__ import annotations

from typing import Any

import pytest

from mas_core.llm_gateway.models import ChatMessage, ChatResponse, UsageStats
from mas_core.protocols.worker_manifest import WorkerRuntime
from mas_core.worker_contract.controller import WorkerRunController
from mas_core.worker_contract.models import ModelProfileReference, WorkerRunRequest
from mas_core.worker_registry.runtime_adapters import GatewayWorkerAdapter, adapter_for_transport
from mas_core.worker_registry.runtime_catalog import RUNTIME_CATALOG


class _FakeGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def chat_completion(self, **kwargs: Any) -> ChatResponse:
        self.calls.append(kwargs)
        return ChatResponse(
            model=str(kwargs["model"]),
            message=ChatMessage(role="assistant", content="fixture answer"),
            usage=UsageStats(prompt_tokens=4, completion_tokens=3, total_tokens=7),
        )


def _request(**task_input: Any) -> WorkerRunRequest:
    return WorkerRunRequest(
        idempotency_key="gateway-adapter-test",
        worker_id="gateway-worker",
        task_type="gateway-fixture",
        task_input=task_input or {"prompt": "reply with fixture answer"},
        resolved_model_profile=ModelProfileReference(
            profile_id="profile-v1",
            version="v1",
            exact_model_id="fixture/model-v1",
        ),
    )


@pytest.mark.asyncio
async def test_gateway_worker_adapter_normalizes_model_response() -> None:
    gateway = _FakeGateway()
    adapter = GatewayWorkerAdapter(
        worker_id="gateway-worker",
        provider_id="fixture-provider",
        gateway_client=gateway,
    )
    outcome = await WorkerRunController().execute(_request(), adapter)

    assert outcome.state == "SUCCEEDED"
    assert outcome.result is not None
    assert outcome.result.output == {"text": "fixture answer", "finish_reason": "stop"}
    assert outcome.result.usage.provider == "fixture-provider"
    assert outcome.result.usage.exact_model_id == "fixture/model-v1"
    assert outcome.result.usage.total_tokens == 7
    assert gateway.calls == [
        {
            "messages": [{"role": "user", "content": "reply with fixture answer"}],
            "model": "fixture/model-v1",
            "max_tokens": 256,
            "temperature": 0.2,
        }
    ]
    await adapter.close()


@pytest.mark.asyncio
async def test_gateway_worker_adapter_requires_exact_resolved_model() -> None:
    gateway = _FakeGateway()
    adapter = GatewayWorkerAdapter(worker_id="gateway-worker", gateway_client=gateway)
    request = _request()
    request = request.model_copy(update={"resolved_model_profile": None})

    readiness = await adapter.readiness(request)

    assert readiness.ready is False
    assert "resolved Model Profile" in readiness.blockers[0]
    assert gateway.calls == []
    await adapter.close()


def test_gateway_transport_factory_uses_aiat_owned_secret_boundary() -> None:
    adapter = adapter_for_transport(
        "aiat_gateway",
        worker_id="gateway-worker",
        config={
            "gateway_url": "http://fixture-gateway",
            "provider_id": "fixture-provider",
            "default_model": "fixture/model-v1",
        },
    )

    assert isinstance(adapter, GatewayWorkerAdapter)
    assert adapter.provider_id == "fixture-provider"
    assert adapter.gateway_client._config.gateway_url == "http://fixture-gateway"


def test_gateway_transport_is_declared_in_the_worker_manifest_contract() -> None:
    assert WorkerRuntime(transport="aiat_gateway").transport == "aiat_gateway"
    assert "aiat_gateway" in RUNTIME_CATALOG["builtin"].supported_transports
