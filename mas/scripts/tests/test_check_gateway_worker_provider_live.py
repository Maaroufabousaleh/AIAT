from __future__ import annotations

import importlib.util
from pathlib import Path

from mas_core.llm_gateway.models import ChatMessage, ChatResponse, UsageStats

SCRIPT = Path(__file__).resolve().parents[1] / "check_gateway_worker_provider_live.py"


def _module():
    spec = importlib.util.spec_from_file_location(
        "check_gateway_worker_provider_live", SCRIPT
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def chat_completion(self, **kwargs: object) -> ChatResponse:
        self.calls.append(dict(kwargs))
        return ChatResponse(
            model=str(kwargs["model"]),
            message=ChatMessage(role="assistant", content="ready"),
            usage=UsageStats(prompt_tokens=7, completion_tokens=1, total_tokens=8),
        )


def test_checker_requires_explicit_external_opt_in() -> None:
    module = _module()
    report = __import__("asyncio").run(
        module._run(
            gateway_url=None,
            api_key=None,
            model_id="omniroute-coding",
            provider_id="litellm",
            allow_external_provider=False,
        )
    )
    assert report["status"] == "blocked"
    assert report["reason"] == "external_provider_dispatch_requires_explicit_opt_in"
    assert report["network_access_performed"] is False
    assert report["licence_metadata_is_gate"] is False


def test_checker_rejects_auto_model_before_network() -> None:
    module = _module()
    report = __import__("asyncio").run(
        module._run(
            gateway_url="https://gateway.invalid",
            api_key="secret-is-not-reported",
            model_id="auto",
            provider_id="litellm",
            allow_external_provider=True,
        )
    )
    assert report["status"] == "blocked"
    assert report["reason"] == "selected_exact_model_id_is_required"
    assert report["network_access_performed"] is False


def test_checker_runs_worker_plane_with_injected_gateway_without_network() -> None:
    module = _module()
    gateway = _FakeGateway()
    report = __import__("asyncio").run(
        module._run(
            gateway_url=None,
            api_key=None,
            model_id="omniroute-coding",
            provider_id="litellm",
            allow_external_provider=True,
            gateway_client=gateway,
            listed_model_ids={"omniroute-coding"},
        )
    )
    assert report["status"] == "pass"
    assert report["controller_terminal_state"] == "SUCCEEDED"
    assert report["worker_dispatch_performed"] is True
    assert report["provider_dispatch_attempted"] is True
    assert report["external_provider_call_performed"] is False
    assert report["network_access_performed"] is False
    assert report["selected_model_id"] == "omniroute-coding"
    assert report["usage"]["provider_id"] == "litellm"
    assert report["usage"]["exact_model_id"] == "omniroute-coding"
    assert gateway.calls[0]["model"] == "omniroute-coding"
    assert gateway.calls[0]["max_tokens"] == module.MAX_TOKENS
    assert gateway.calls[0]["temperature"] == module.TEMPERATURE


def test_checker_rejects_unlisted_model_without_dispatch() -> None:
    module = _module()

    async def _listed(*, gateway_url: str, api_key: str, timeout_s: float):
        del gateway_url, api_key, timeout_s
        return {"other-model"}, 1

    original = module._listed_models
    module._listed_models = _listed
    try:
        report = __import__("asyncio").run(
            module._run(
                gateway_url="https://gateway.invalid",
                api_key="secret-is-not-reported",
                model_id="omniroute-coding",
                provider_id="litellm",
                allow_external_provider=True,
            )
        )
    finally:
        module._listed_models = original
    assert report["status"] == "blocked"
    assert report["reason"] == "selected_model_is_not_listed_by_live_gateway"
    assert report["network_access_performed"] is True
    assert report["external_provider_call_performed"] is False
