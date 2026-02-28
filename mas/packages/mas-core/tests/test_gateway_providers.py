"""Tests for the multi-provider LLM gateway (providers + client routing).

Test classes
------------
TestModelRegistry       — register/get/list models and providers
TestProviderConfigAuth  — API key resolution from env vars
TestChatCompletions     — chat_completions routing to registered providers
TestResponsesAPI        — _call_responses_api payload building + parsing
TestCLIModel            — CLI subprocess execution path
TestFallback            — unknown models fall back to default LLMConfig
TestListModels          — convenience list_models() on client
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from mas_core.llm_gateway.client import (
    LLMGatewayClient,
    LLMGatewayError,
    LLMRateLimited,
)
from mas_core.llm_gateway.models import ChatResponse, LLMConfig, UsageStats
from mas_core.llm_gateway.providers import (
    MODEL_REGISTRY,
    ApiStyle,
    ModelEntry,
    ModelRegistry,
    ProviderConfig,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> LLMConfig:
    defaults = dict(
        gateway_url="http://fake-llm:8080",
        default_model="gpt-4o",
        api_key="test-key",
        max_retries=1,
        retry_min_wait_s=0.001,
        retry_max_wait_s=0.005,
        timeout_s=5.0,
    )
    defaults.update(overrides)
    return LLMConfig.model_construct(**defaults)


def _make_registry() -> ModelRegistry:
    """Build a fresh, isolated registry for testing."""
    reg = ModelRegistry()
    reg.register_provider(
        ProviderConfig(
            provider_id="test_openai",
            base_url="https://api.openai.com",
            api_key_env_vars=["OPENAI_API_KEY"],
            default_api_key="test-openai-key",
        )
    )
    reg.register_provider(
        ProviderConfig(
            provider_id="test_zen",
            base_url="https://opencode.ai/zen/v1",
            api_key_env_vars=["ZEN_API_KEY"],
            default_api_key="public",
            extra_headers={
                "HTTP-Referer": "https://opencode.ai/",
                "X-Title": "opencode",
            },
        )
    )
    reg.register(
        ModelEntry(
            model_id="test-gpt4o",
            provider="test_openai",
            api_style=ApiStyle.CHAT_COMPLETIONS,
            endpoint="https://api.openai.com/v1/chat/completions",
        )
    )
    reg.register(
        ModelEntry(
            model_id="test-pickle",
            provider="test_zen",
            api_style=ApiStyle.CHAT_COMPLETIONS,
            endpoint="https://opencode.ai/zen/v1/chat/completions",
        )
    )
    reg.register(
        ModelEntry(
            model_id="test-nano",
            provider="test_zen",
            api_style=ApiStyle.RESPONSES,
            endpoint="https://opencode.ai/zen/v1/responses",
        )
    )
    reg.register(
        ModelEntry(
            model_id="test-cli",
            provider="cli",
            api_style=ApiStyle.CLI,
            endpoint="echo",
            cli_args=["hello"],
        )
    )
    return reg


def _ok_chat_response(content: str = "ok", model: str = "test-pickle") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "id": "chatcmpl-test",
        "model": model,
        "choices": [
            {"finish_reason": "stop", "message": {"role": "assistant", "content": content}}
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }
    return resp


def _ok_responses_response(text: str = "analysis complete") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "id": "resp-test",
        "output_text": text,
        "usage": {"input_tokens": 10, "output_tokens": 6, "total_tokens": 16},
    }
    return resp


# ===========================================================================
# TestModelRegistry
# ===========================================================================


class TestModelRegistry:
    def test_register_and_get_model(self):
        reg = ModelRegistry()
        entry = ModelEntry(
            model_id="my-model",
            provider="test",
            endpoint="http://localhost/v1/chat/completions",
        )
        reg.register(entry)
        assert reg.get("my-model") is entry
        assert reg.get("nonexistent") is None

    def test_contains_and_len(self):
        reg = ModelRegistry()
        assert len(reg) == 0
        reg.register(
            ModelEntry(model_id="a", provider="p", endpoint="http://localhost/")
        )
        assert "a" in reg
        assert "b" not in reg
        assert len(reg) == 1

    def test_list_models_all_and_filtered(self):
        reg = _make_registry()
        all_models = reg.list_models()
        assert len(all_models) == 4
        zen_models = reg.list_models("test_zen")
        assert len(zen_models) == 2
        assert all(m.provider == "test_zen" for m in zen_models)

    def test_model_ids_sorted(self):
        reg = _make_registry()
        ids = reg.model_ids()
        assert ids == sorted(ids)
        assert "test-pickle" in ids
        assert "test-nano" in ids

    def test_register_provider_and_get(self):
        reg = ModelRegistry()
        p = ProviderConfig(provider_id="zen", base_url="https://example.com")
        reg.register_provider(p)
        assert reg.get_provider("zen") is p
        assert reg.get_provider("nonexistent") is None

    def test_list_providers(self):
        reg = _make_registry()
        providers = reg.list_providers()
        ids = {p.provider_id for p in providers}
        assert "test_openai" in ids
        assert "test_zen" in ids


class TestProviderConfigAuth:
    def test_resolve_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "secret123")
        p = ProviderConfig(
            provider_id="p",
            base_url="http://x",
            api_key_env_vars=["MISSING_KEY", "MY_KEY"],
            default_api_key="fallback",
        )
        assert p.resolve_api_key() == "secret123"

    def test_resolve_api_key_fallback(self, monkeypatch):
        # Clear any potentially set env vars
        monkeypatch.delenv("NEVER_SET_KEY", raising=False)
        p = ProviderConfig(
            provider_id="p",
            base_url="http://x",
            api_key_env_vars=["NEVER_SET_KEY"],
            default_api_key="fallback_val",
        )
        assert p.resolve_api_key() == "fallback_val"

    def test_resolve_api_key_public_default(self):
        p = ProviderConfig(
            provider_id="p",
            base_url="http://x",
            api_key_env_vars=[],
            default_api_key="",
        )
        assert p.resolve_api_key() == "public"


# ===========================================================================
# TestGlobalRegistry — validate built-in registered models
# ===========================================================================


class TestGlobalRegistry:
    def test_builtin_models_registered(self):
        assert "gpt-4o" in MODEL_REGISTRY
        assert "big-pickle" in MODEL_REGISTRY
        assert "minimax-m2.5-free" in MODEL_REGISTRY
        assert "gpt-5-nano" in MODEL_REGISTRY

    def test_gpt4o_is_chat_completions(self):
        entry = MODEL_REGISTRY.get("gpt-4o")
        assert entry is not None
        assert entry.api_style == ApiStyle.CHAT_COMPLETIONS
        assert entry.provider == "openai"

    def test_zen_models_styles(self):
        pickle = MODEL_REGISTRY.get("big-pickle")
        assert pickle is not None
        assert pickle.api_style == ApiStyle.CHAT_COMPLETIONS
        assert pickle.provider == "zen"

        nano = MODEL_REGISTRY.get("gpt-5-nano")
        assert nano is not None
        assert nano.api_style == ApiStyle.RESPONSES
        assert nano.provider == "zen"

    def test_zen_provider_headers(self):
        p = MODEL_REGISTRY.get_provider("zen")
        assert p is not None
        assert p.extra_headers.get("HTTP-Referer") == "https://opencode.ai/"
        assert p.extra_headers.get("X-Title") == "opencode"


# ===========================================================================
# TestChatCompletions — routing registered chat_completions models
# ===========================================================================


class TestChatCompletions:
    @pytest.mark.asyncio
    async def test_registered_model_uses_provider_client(self):
        """A registered chat_completions model uses a provider-specific HTTP client."""
        reg = _make_registry()
        config = _make_config()
        client = LLMGatewayClient(config, registry=reg)

        async def mock_post(_self, url, **kwargs):
            return _ok_chat_response("Zen reply", "test-pickle")

        with patch.object(httpx.AsyncClient, "post", new=mock_post):
            async with client:
                resp = await client.chat_completion(
                    [{"role": "user", "content": "hi"}],
                    model="test-pickle",
                )
        assert resp.text == "Zen reply"
        assert resp.usage.total_tokens == 8

    @pytest.mark.asyncio
    async def test_provider_client_reused(self):
        """Subsequent calls to the same provider reuse the HTTP client."""
        reg = _make_registry()
        config = _make_config()
        client = LLMGatewayClient(config, registry=reg)

        async def mock_post(_self, url, **kwargs):
            return _ok_chat_response()

        with patch.object(httpx.AsyncClient, "post", new=mock_post):
            async with client:
                await client.chat_completion(
                    [{"role": "user", "content": "a"}], model="test-pickle"
                )
                # Provider client created for test_zen
                assert "test_zen" in client._provider_clients
                first_client = client._provider_clients["test_zen"]
                await client.chat_completion(
                    [{"role": "user", "content": "b"}], model="test-pickle"
                )
                assert client._provider_clients["test_zen"] is first_client

    @pytest.mark.asyncio
    async def test_provider_client_has_extra_headers(self):
        """Provider-specific headers (Referer, X-Title) are set on the client."""
        reg = _make_registry()
        config = _make_config()
        client = LLMGatewayClient(config, registry=reg)

        captured_headers: dict[str, str] = {}

        real_init = httpx.AsyncClient.__init__

        def patched_init(self_client, *args, **kwargs):
            nonlocal captured_headers
            headers = kwargs.get("headers", {})
            captured_headers.update(headers)
            real_init(self_client, *args, **kwargs)

        async def mock_post(_self, url, **kwargs):
            return _ok_chat_response()

        with (
            patch.object(httpx.AsyncClient, "__init__", new=patched_init),
            patch.object(httpx.AsyncClient, "post", new=mock_post),
        ):
            # Manually start since we're patching __init__
            client._http = httpx.AsyncClient()
            try:
                # Trigger a registered model call to create provider client
                entry = reg.get("test-pickle")
                client._resolve_http_client_and_endpoint(entry)
                assert captured_headers.get("HTTP-Referer") == "https://opencode.ai/"
                assert captured_headers.get("X-Title") == "opencode"
            finally:
                await client.stop()

    @pytest.mark.asyncio
    async def test_chat_completions_retry_on_502(self):
        """Retries work for provider-routed chat_completions models."""
        reg = _make_registry()
        config = _make_config(max_retries=2)
        client = LLMGatewayClient(config, registry=reg)

        call_count = 0

        async def mock_post(_self, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                resp = MagicMock()
                resp.status_code = 502
                resp.text = "bad gateway"
                return resp
            return _ok_chat_response()

        with patch.object(httpx.AsyncClient, "post", new=mock_post):
            async with client:
                resp = await client.chat_completion(
                    [{"role": "user", "content": "hi"}],
                    model="test-pickle",
                )
        assert resp.text == "ok"
        assert call_count == 3


# ===========================================================================
# TestResponsesAPI — gpt-5-nano style
# ===========================================================================


class TestResponsesAPI:
    @pytest.mark.asyncio
    async def test_responses_api_success(self):
        """Responses API model returns normalised ChatResponse."""
        reg = _make_registry()
        config = _make_config()
        client = LLMGatewayClient(config, registry=reg)

        async def mock_post(_self, url, **kwargs):
            return _ok_responses_response("detailed analysis")

        with patch.object(httpx.AsyncClient, "post", new=mock_post):
            async with client:
                resp = await client.chat_completion(
                    [{"role": "user", "content": "analyse this"}],
                    model="test-nano",
                )
        assert resp.text == "detailed analysis"
        assert resp.message.role == "assistant"
        assert resp.finish_reason == "stop"
        assert resp.usage.prompt_tokens == 10
        assert resp.usage.completion_tokens == 6

    @pytest.mark.asyncio
    async def test_responses_api_payload_format(self):
        """The request payload sent to a responses model uses 'input' not 'messages'."""
        reg = _make_registry()
        config = _make_config()
        client = LLMGatewayClient(config, registry=reg)

        captured_payload: dict[str, Any] = {}

        async def mock_post(_self, url, **kwargs):
            captured_payload.update(kwargs.get("json", {}))
            return _ok_responses_response()

        with patch.object(httpx.AsyncClient, "post", new=mock_post):
            async with client:
                await client.chat_completion(
                    [
                        {"role": "system", "content": "You are a reviewer."},
                        {"role": "user", "content": "Review this doc."},
                    ],
                    model="test-nano",
                    temperature=0.5,
                    max_tokens=1000,
                )

        assert "input" in captured_payload, "Responses API should use 'input' not 'messages'"
        assert "messages" not in captured_payload
        assert captured_payload["model"] == "test-nano"
        assert captured_payload["stream"] is False
        assert captured_payload["temperature"] == 0.5
        assert captured_payload["max_output_tokens"] == 1000
        # Check the input blocks
        assert len(captured_payload["input"]) == 2
        assert captured_payload["input"][0]["role"] == "system"
        assert captured_payload["input"][1]["content"] == "Review this doc."

    def test_parse_responses_api_output_text(self):
        raw = {
            "id": "resp-123",
            "output_text": "answer here",
            "usage": {"input_tokens": 5, "output_tokens": 3, "total_tokens": 8},
        }
        resp = LLMGatewayClient._parse_responses_api(raw, model="test-nano")
        assert resp.text == "answer here"
        assert resp.usage.prompt_tokens == 5
        assert resp.usage.completion_tokens == 3
        assert resp.usage.total_tokens == 8
        assert resp.model == "test-nano"

    def test_parse_responses_api_nested_output(self):
        raw = {
            "id": "resp-456",
            "output": [
                {
                    "content": [
                        {"type": "text", "text": "Part 1. "},
                        {"type": "text", "text": "Part 2."},
                    ]
                }
            ],
            "usage": {},
        }
        resp = LLMGatewayClient._parse_responses_api(raw, model="test-nano")
        assert resp.text == "Part 1. Part 2."

    def test_parse_responses_api_empty(self):
        raw = {"id": "resp-789"}
        resp = LLMGatewayClient._parse_responses_api(raw, model="x")
        assert resp.text is None or resp.text == ""
        assert resp.message.role == "assistant"

    @pytest.mark.asyncio
    async def test_responses_api_retries_on_429(self):
        reg = _make_registry()
        config = _make_config(max_retries=2)
        client = LLMGatewayClient(config, registry=reg)

        call_count = 0

        async def mock_post(_self, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                resp = MagicMock()
                resp.status_code = 429
                resp.text = "rate limited"
                return resp
            return _ok_responses_response("finally")

        with patch.object(httpx.AsyncClient, "post", new=mock_post):
            async with client:
                resp = await client.chat_completion(
                    [{"role": "user", "content": "hi"}],
                    model="test-nano",
                )
        assert resp.text == "finally"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_responses_api_raises_after_max_retries(self):
        reg = _make_registry()
        config = _make_config(max_retries=1)
        client = LLMGatewayClient(config, registry=reg)

        async def mock_post(_self, url, **kwargs):
            resp = MagicMock()
            resp.status_code = 500
            resp.text = "server error"
            return resp

        with patch.object(httpx.AsyncClient, "post", new=mock_post):
            async with client:
                with pytest.raises(LLMGatewayError):
                    await client.chat_completion(
                        [{"role": "user", "content": "hi"}],
                        model="test-nano",
                    )

    @pytest.mark.asyncio
    async def test_responses_api_non_retryable_error(self):
        reg = _make_registry()
        config = _make_config()
        client = LLMGatewayClient(config, registry=reg)

        async def mock_post(_self, url, **kwargs):
            resp = MagicMock()
            resp.status_code = 403
            resp.text = "forbidden"
            return resp

        with patch.object(httpx.AsyncClient, "post", new=mock_post):
            async with client:
                with pytest.raises(LLMGatewayError) as exc_info:
                    await client.chat_completion(
                        [{"role": "user", "content": "hi"}],
                        model="test-nano",
                    )
                assert exc_info.value.status_code == 403


# ===========================================================================
# TestCLIModel
# ===========================================================================


class TestCLIModel:
    @pytest.mark.asyncio
    async def test_cli_model_success(self):
        reg = _make_registry()
        config = _make_config()
        client = LLMGatewayClient(config, registry=reg)

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"CLI output here", b"")
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            async with client:
                resp = await client.chat_completion(
                    [{"role": "user", "content": "test prompt"}],
                    model="test-cli",
                )
        assert resp.text == "CLI output here"
        assert resp.model == "test-cli"
        assert resp.finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_cli_model_failure(self):
        reg = _make_registry()
        config = _make_config()
        client = LLMGatewayClient(config, registry=reg)

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"command not found")
        mock_proc.returncode = 127

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            async with client:
                with pytest.raises(LLMGatewayError, match="CLI model test-cli failed"):
                    await client.chat_completion(
                        [{"role": "user", "content": "test"}],
                        model="test-cli",
                    )

    @pytest.mark.asyncio
    async def test_cli_builds_prompt_from_messages(self):
        reg = _make_registry()
        config = _make_config()
        client = LLMGatewayClient(config, registry=reg)

        captured_input: bytes = b""

        mock_proc = AsyncMock()
        mock_proc.returncode = 0

        async def mock_communicate(input_data):
            nonlocal captured_input
            captured_input = input_data
            return (b"CLI response", b"")

        mock_proc.communicate = mock_communicate

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            async with client:
                await client.chat_completion(
                    [
                        {"role": "system", "content": "Be brief"},
                        {"role": "user", "content": "Hello"},
                        {"role": "assistant", "content": "Hi"},
                        {"role": "user", "content": "More"},
                    ],
                    model="test-cli",
                )

        prompt = captured_input.decode("utf-8")
        assert "[System] Be brief" in prompt
        assert "[User] Hello" in prompt
        assert "[Assistant] Hi" in prompt
        assert "[User] More" in prompt


# ===========================================================================
# TestFallback — unknown models use default LLMConfig
# ===========================================================================


class TestFallback:
    @pytest.mark.asyncio
    async def test_unknown_model_uses_default_client(self):
        """Models not in the registry fall back to the default LLMConfig endpoint."""
        reg = _make_registry()
        config = _make_config(default_model="unknown-model-xyz")
        client = LLMGatewayClient(config, registry=reg)

        async def mock_post(_self, url, **kwargs):
            return _ok_chat_response("default reply", "unknown-model-xyz")

        with patch.object(httpx.AsyncClient, "post", new=mock_post):
            async with client:
                resp = await client.chat_completion(
                    [{"role": "user", "content": "hi"}],
                )
        assert resp.text == "default reply"

    @pytest.mark.asyncio
    async def test_explicit_unknown_model_falls_back(self):
        reg = _make_registry()
        config = _make_config()
        client = LLMGatewayClient(config, registry=reg)

        async def mock_post(_self, url, **kwargs):
            return _ok_chat_response("fallback", "custom-thing")

        with patch.object(httpx.AsyncClient, "post", new=mock_post):
            async with client:
                resp = await client.chat_completion(
                    [{"role": "user", "content": "hi"}],
                    model="custom-thing",
                )
        assert resp.text == "fallback"


# ===========================================================================
# TestListModels
# ===========================================================================


class TestListModels:
    def test_list_all_models(self):
        reg = _make_registry()
        config = _make_config()
        client = LLMGatewayClient(config, registry=reg)
        models = client.list_models()
        assert len(models) == 4

    def test_list_models_by_provider(self):
        reg = _make_registry()
        config = _make_config()
        client = LLMGatewayClient(config, registry=reg)
        zen_models = client.list_models("test_zen")
        assert len(zen_models) == 2
        assert all(m.provider == "test_zen" for m in zen_models)


# ===========================================================================
# TestProviderClientCleanup
# ===========================================================================


class TestProviderClientCleanup:
    @pytest.mark.asyncio
    async def test_stop_closes_provider_clients(self):
        reg = _make_registry()
        config = _make_config()
        client = LLMGatewayClient(config, registry=reg)

        async def mock_post(_self, url, **kwargs):
            return _ok_chat_response()

        with patch.object(httpx.AsyncClient, "post", new=mock_post):
            async with client:
                await client.chat_completion(
                    [{"role": "user", "content": "hi"}],
                    model="test-pickle",
                )
                assert "test_zen" in client._provider_clients

        # After context manager exits, provider clients should be cleared
        assert len(client._provider_clients) == 0


# ===========================================================================
# TestApiStyleEnum
# ===========================================================================


class TestApiStyleEnum:
    def test_enum_values(self):
        assert ApiStyle.CHAT_COMPLETIONS.value == "chat_completions"
        assert ApiStyle.RESPONSES.value == "responses"
        assert ApiStyle.CLI.value == "cli"

    def test_model_entry_defaults_to_chat_completions(self):
        entry = ModelEntry(model_id="x", provider="p", endpoint="http://test/")
        assert entry.api_style == ApiStyle.CHAT_COMPLETIONS
