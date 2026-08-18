"""Contract tests for the AIAT model-gateway worker adapter."""

from __future__ import annotations

from typing import Any

import pytest

from mas_core.llm_gateway.client import LLMGatewayError
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


class _OwnedGateway(_FakeGateway):
    def __init__(self) -> None:
        super().__init__()
        self.started = 0
        self.stopped = 0

    async def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        self.stopped += 1


class _ErrorGateway:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def chat_completion(self, **_kwargs: Any) -> ChatResponse:
        raise self.error


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "code", "retryable", "terminal"),
    [
        (LLMGatewayError(429, "provider secret must not appear"), "MODEL_GATEWAY_TRANSIENT_FAILURE", True, False),
        (LLMGatewayError(401, "provider secret must not appear"), "MODEL_GATEWAY_REQUEST_REJECTED", False, True),
    ],
)
async def test_gateway_worker_adapter_classifies_dispatch_failures(
    error: Exception,
    code: str,
    retryable: bool,
    terminal: bool,
) -> None:
    adapter = GatewayWorkerAdapter(
        worker_id="gateway-worker",
        provider_id="fixture-provider",
        gateway_client=_ErrorGateway(error),
    )

    result = await adapter._run_gateway(_request())

    assert result.success is False
    assert result.error is not None
    assert result.error.code == code
    assert result.error.retryable is retryable
    assert result.error.terminal is terminal
    assert "provider secret" not in result.error.message
    assert "provider secret" not in str(result.error.details)
    await adapter.close()


@pytest.mark.asyncio
async def test_gateway_worker_adapter_rejects_invalid_input_without_dispatch() -> None:
    gateway = _FakeGateway()
    adapter = GatewayWorkerAdapter(
        worker_id="gateway-worker",
        provider_id="fixture-provider",
        gateway_client=gateway,
    )

    result = await adapter._run_gateway(_request(prompt=""))

    assert result.success is False
    assert result.error is not None
    assert result.error.code == "MODEL_GATEWAY_INPUT_REJECTED"
    assert result.error.retryable is False
    assert result.error.terminal is True
    assert gateway.calls == []
    await adapter.close()


@pytest.mark.asyncio
async def test_gateway_worker_adapter_manages_owned_gateway_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = _OwnedGateway()
    monkeypatch.setattr(
        "mas_core.llm_gateway.client.LLMGatewayClient",
        lambda _config: gateway,
    )
    adapter = GatewayWorkerAdapter(worker_id="gateway-worker", gateway_config=object())

    outcome = await WorkerRunController().execute(_request(), adapter)

    assert outcome.state == "SUCCEEDED"
    assert gateway.started == 1
    await adapter.close()
    assert gateway.stopped == 1


@pytest.mark.parametrize(
    "task_input",
    [
        {"prompt": "x" * 32_001},
        {"messages": [{"role": "user", "content": "ok"}] * 65},
        {"messages": [{"role": "user", "content": "x" * 32_001}]},
    ],
)
def test_gateway_worker_adapter_bounds_message_input(task_input: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="gateway (content|message) limit"):
        GatewayWorkerAdapter._messages_from_request(_request(**task_input))


@pytest.mark.parametrize("temperature", [float("nan"), float("inf"), float("-inf")])
def test_gateway_worker_adapter_rejects_non_finite_temperature(temperature: float) -> None:
    with pytest.raises(ValueError, match="temperature"):
        GatewayWorkerAdapter._bounded_generation_options(_request(temperature=temperature))


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
