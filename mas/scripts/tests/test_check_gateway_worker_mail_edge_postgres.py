from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_gateway_worker_mail_edge_postgres.py"


def _module():
    spec = importlib.util.spec_from_file_location(
        "check_gateway_worker_mail_edge_postgres", SCRIPT
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checker_blocks_without_both_database_dsns() -> None:
    module = _module()
    report = asyncio.run(module._run(None, None))

    assert report["schema_version"] == (
        "aiat.gateway-worker-mail-edge-postgres-certification.v1"
    )
    assert report["status"] == "blocked"
    assert report["mutation_performed"] is False
    assert report["external_provider_mutation_performed"] is False
    assert report["licence_metadata_is_gate"] is False


def test_live_provider_mode_blocks_before_database_or_network_without_opt_in() -> None:
    module = _module()
    report = asyncio.run(
        module._run(
            None,
            None,
            live_provider=True,
            model_id="llama-3.3-70b-versatile",
            provider_id="litellm",
        )
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "external_provider_dispatch_requires_explicit_opt_in"
    assert report["mutation_performed"] is False
    assert report["external_network_access_performed"] is False
    assert report["licence_metadata_is_gate"] is False


def test_provider_recovery_can_use_the_local_fixture_without_external_access() -> None:
    module = _module()
    report = asyncio.run(
        module._run(
            None,
            None,
            provider_recovery=True,
            model_id="llama-3.3-70b-versatile",
            provider_id="litellm",
        )
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "gateway_worker_mail_edge_postgres_database_not_configured"
    assert report["mutation_performed"] is False
    assert report["external_network_access_performed"] is False


def test_local_recovery_wrapper_keeps_fixture_call_ledger() -> None:
    module = _module()

    fixture = module._FixtureGateway()
    gateway = module._TransientOnceGateway(fixture)
    try:
        asyncio.run(gateway.chat_completion(model="fixture/model-v1"))
    except module.LLMGatewayError as exc:
        assert exc.status_code == 429
    else:
        raise AssertionError("the first recovery attempt must be transient")
    response = asyncio.run(gateway.chat_completion(model="fixture/model-v1"))

    assert response.text == "durable mail-edge fixture answer"
    assert len(gateway.calls) == 1
    assert gateway.attempts == 2
    assert gateway.forwarded_calls == 1


def test_transient_once_gateway_injects_only_one_failure() -> None:
    module = _module()

    class _Gateway:
        def __init__(self) -> None:
            self.calls = 0

        async def chat_completion(self, **kwargs):
            self.calls += 1
            return module.ChatResponse(
                model=kwargs["model"],
                message=module.ChatMessage(role="assistant", content="recovered"),
                usage=module.UsageStats(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )

    delegate = _Gateway()
    gateway = module._TransientOnceGateway(delegate)
    try:
        asyncio.run(gateway.chat_completion(model="fixture/model-v1"))
    except module.LLMGatewayError as exc:
        assert exc.status_code == 429
    else:
        raise AssertionError("the first recovery attempt must be transient")
    response = asyncio.run(gateway.chat_completion(model="fixture/model-v1"))

    assert response.text == "recovered"
    assert gateway.injected is True
    assert gateway.forwarded_calls == 1
    assert delegate.calls == 1


def test_redacting_gateway_keeps_usage_but_discards_generated_text() -> None:
    module = _module()

    class _Gateway:
        async def chat_completion(self, **kwargs):
            return module.ChatResponse(
                model=kwargs["model"],
                message=module.ChatMessage(role="assistant", content="secret output"),
                usage=module.UsageStats(prompt_tokens=5, completion_tokens=2, total_tokens=7),
            )

    gateway = module._RedactingGateway(_Gateway())
    response = asyncio.run(
        gateway.chat_completion(
            model="llama-3.3-70b-versatile",
            max_tokens=16,
            temperature=0.0,
            messages=[{"role": "user", "content": "ready"}],
        )
    )

    assert response.text == ""
    assert response.message.content is None
    assert response.usage.total_tokens == 7
    assert gateway.calls == [
        {
            "model": "llama-3.3-70b-versatile",
            "max_tokens": 16,
            "temperature": 0.0,
            "message_count": 1,
        }
    ]


def test_fixture_observations_are_correlated_and_payload_free() -> None:
    module = _module()
    observations = module._fixture_observations()

    assert len(observations) == 3
    assert {item.trace_id for item in observations} == {module.TRACE_ID}
    assert {item.worker_id for item in observations} == {module.WORKER_ID_TEXT}
    assert {item.event_type for item in observations} == {"queued", "delivered", "bounced"}
    assert all(module.PAYLOAD_MARKER not in item.model_dump_json() for item in observations)


def test_storage_projection_ignores_storage_only_columns() -> None:
    module = _module()
    observation = module._fixture_observations()[1]
    row = {
        **observation.model_dump(mode="python"),
        "metadata_json": observation.metadata,
        "received_at": observation.occurred_at,
    }

    projected = module._safe_mail_rows([row])

    assert projected == [observation]
    assert module.PAYLOAD_MARKER not in projected[0].model_dump_json()
