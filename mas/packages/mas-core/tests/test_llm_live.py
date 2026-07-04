"""Live tests for AIAT's supported LLM gateway path.

These tests make real OpenAI-compatible calls through the product route:

    AIAT -> LiteLLM -> OmniRoute -> provider

They intentionally do not test the old built-in direct provider catalog. Legacy
direct-provider routing is now a rollback path behind ``legacy:<model>`` and is
covered by unit tests, not by the no-mock live acceptance gate.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

import httpx
import pytest
from dotenv import load_dotenv

from mas_core.llm_gateway.client import LLMGatewayClient
from mas_core.llm_gateway.models import ChatResponse, LLMConfig

# Load root .env so local live runs inherit the same keys as Compose.
_loaded = False
for _base in (os.path.dirname(os.path.abspath(__file__)), os.getcwd()):
    _d = _base
    for _ in range(8):
        _candidate = os.path.join(_d, ".env")
        if os.path.isfile(_candidate):
            load_dotenv(_candidate, override=False)
            _loaded = True
            break
        _parent = os.path.dirname(_d)
        if _parent == _d:
            break
        _d = _parent
    if _loaded:
        break

SIMPLE_PROMPT = [{"role": "user", "content": "What is 2+2? Reply with just the number."}]
EXPECTED_ALIASES = {
    "auto",
    "omniroute-auto",
    "omniroute-free",
    "omniroute-coding",
    "omniroute-smart",
}


def _live_enabled() -> bool:
    value = os.environ.get("MAS_RUN_LIVE_TESTS", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _gateway_url() -> str:
    """Return a host-reachable gateway URL for live tests.

    In containers, ``LLM_GATEWAY_URL=http://litellm:4000`` is correct. From the
    host, the current dev stack may not publish LiteLLM, so the authoritative
    reachable route is the orchestrator's OpenAI-compatible ``/v1`` adapter.
    Operators can override this with ``AIAT_LIVE_LLM_GATEWAY_URL``.
    """
    explicit = os.environ.get("AIAT_LIVE_LLM_GATEWAY_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")

    configured = os.environ.get("LLM_GATEWAY_URL", "").strip()
    if configured:
        parsed = urlparse(configured)
        if parsed.hostname not in {"litellm", "omniroute"}:
            return configured.rstrip("/")

    return "http://127.0.0.1:8000"


def _gateway_key(gateway_url: str) -> str:
    explicit = os.environ.get("AIAT_LIVE_LLM_API_KEY", "").strip()
    if explicit:
        return explicit

    parsed = urlparse(gateway_url)
    if parsed.hostname in {"127.0.0.1", "localhost"} and parsed.port == 8000:
        return os.environ.get("GATEWAY_API_KEY") or os.environ.get("MAS_API_KEY", "")

    return (
        os.environ.get("LLM_API_KEY")
        or os.environ.get("LITELLM_MASTER_KEY")
        or os.environ.get("OMNIROUTE_API_KEY")
        or ""
    )


def _make_live_config() -> LLMConfig:
    gateway_url = _gateway_url()
    return LLMConfig.model_construct(
        backend="litellm",
        gateway_url=gateway_url,
        api_key=_gateway_key(gateway_url),
        default_model=os.environ.get("LLM_DEFAULT_MODEL", "auto") or "auto",
        timeout_s=float(os.environ.get("LLM_TIMEOUT_S", "120")),
        max_retries=int(os.environ.get("LLM_MAX_RETRIES", "3")),
        retry_min_wait_s=float(os.environ.get("LLM_RETRY_MIN_WAIT_S", "1")),
        retry_max_wait_s=float(os.environ.get("LLM_RETRY_MAX_WAIT_S", "60")),
    )


def _assert_gateway_configured(config: LLMConfig) -> None:
    assert config.backend == "litellm"
    assert config.gateway_url
    assert config.api_key, (
        "Set MAS_API_KEY/GATEWAY_API_KEY for orchestrator live tests or "
        "LLM_API_KEY/LITELLM_MASTER_KEY for direct LiteLLM live tests."
    )


def _assert_valid_response(resp: ChatResponse, model_id: str) -> None:
    assert resp is not None, f"{model_id}: response is None"
    assert resp.message is not None, f"{model_id}: message is None"
    assert resp.message.role == "assistant", f"{model_id}: role={resp.message.role}"
    text = resp.text.strip()
    assert text, f"{model_id}: empty response text"
    assert "4" in text, f"{model_id}: expected '4' in response, got {text!r}"
    assert resp.usage.total_tokens > 0, f"{model_id}: missing token usage"


@pytest.mark.live
@pytest.mark.skipif(
    not _live_enabled(),
    reason="Set MAS_RUN_LIVE_TESTS=1 to enable live gateway tests",
)
class TestLiteLLMOmniRouteLive:
    """No-mock checks for the current AIAT LLM route."""

    @pytest.mark.asyncio
    async def test_gateway_models_endpoint_exposes_omniroute_aliases(self):
        config = _make_live_config()
        _assert_gateway_configured(config)
        headers = {"Authorization": f"Bearer {config.api_key}"}

        async with httpx.AsyncClient(
            base_url=config.gateway_url,
            headers=headers,
            timeout=min(config.timeout_s, 20.0),
        ) as client:
            response = await client.get("/v1/models")

        assert response.status_code == 200, response.text
        payload = response.json()
        ids = {str(item.get("id")) for item in payload.get("data", [])}
        assert EXPECTED_ALIASES.issubset(ids)

    @pytest.mark.asyncio
    async def test_auto_alias_routes_through_litellm_omniroute(self):
        config = _make_live_config()
        _assert_gateway_configured(config)

        async with LLMGatewayClient(config) as client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="auto",
                max_tokens=16,
            )

        _assert_valid_response(resp, "auto")

    @pytest.mark.asyncio
    async def test_omniroute_auto_alias_routes_successfully(self):
        config = _make_live_config()
        _assert_gateway_configured(config)

        async with LLMGatewayClient(config) as client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="omniroute-auto",
                max_tokens=16,
            )

        _assert_valid_response(resp, "omniroute-auto")

    @pytest.mark.asyncio
    async def test_legacy_aiat_alias_still_flows_through_omniroute(self):
        config = _make_live_config()
        _assert_gateway_configured(config)

        async with LLMGatewayClient(config) as client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="gpt-4o-mini",
                max_tokens=16,
            )

        _assert_valid_response(resp, "gpt-4o-mini")

    @pytest.mark.asyncio
    async def test_gateway_accepts_provider_agnostic_coding_alias(self):
        config = _make_live_config()
        _assert_gateway_configured(config)

        async with LLMGatewayClient(config) as client:
            resp = await client.chat_completion(
                messages=[{"role": "user", "content": "Return only: ok"}],
                model="omniroute-coding",
                max_tokens=16,
            )

        assert resp.text.strip(), "omniroute-coding returned empty text"
        assert resp.usage.total_tokens > 0
