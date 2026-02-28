"""Live integration tests for LLM providers.

These tests make **real API calls** to every configured provider.
They are skipped automatically when the required API key / binary is missing.

Run all live tests::

    pytest tests/test_llm_live.py -v --timeout=120

Run a single provider::

    pytest tests/test_llm_live.py -v -k gemini

Environment
-----------
API keys are read from env vars (or a root ``.env`` file via pydantic-settings):

- ``GEMINI_API_KEY``      — Google AI Studio
- ``GROQ_API_KEY``        — Groq (free tier, ultra-fast LPU inference)
- ``CEREBRAS_API_KEY``    — Cerebras Cloud (free tier, world's fastest inference)
- ``MISTRAL_API_KEY``     — Mistral AI (free Experiment tier)
- ``CLOUDFLARE_API_TOKEN`` + ``CLOUDFLARE_ACCOUNT_ID`` — Cloudflare Workers AI
- ``OPENROUTER_API_KEY``  — OpenRouter
- ``OPENAI_API_KEY``      — OpenAI (paid)
- ``OPENCODE_API_KEY``    — Zen / opencode.ai (default "public")
- Copilot CLI             — ``copilot`` binary on PATH + ``copilot auth login``

Each test sends a small prompt ("What is 2+2?") and asserts the response
contains meaningful text.  This validates auth, endpoint, parsing, and
basic model functionality end-to-end.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from typing import Any

import pytest

from dotenv import load_dotenv

# Load root .env so API keys are available even when CWD is not project root.
# Search upward from this test file AND from CWD to find the .env.
_loaded = False
for _base in (os.path.dirname(os.path.abspath(__file__)), os.getcwd()):
    _d = _base
    for _ in range(8):  # walk up at most 8 levels
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

from mas_core.llm_gateway.client import LLMGatewayClient, LLMGatewayError
from mas_core.llm_gateway.models import ChatResponse, LLMConfig
from mas_core.llm_gateway.providers import MODEL_REGISTRY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SIMPLE_PROMPT = [{"role": "user", "content": "What is 2+2? Reply with just the number."}]

VISION_PROMPT_TEXT = "Describe this image in one sentence."

# Tiny 1x1 red PNG as a base64 data-URL (validates multimodal path end-to-end)
_RED_PIXEL_PNG_B64 = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
    "nGP4z8BQDwAEgAF/pooBPQAAAABJRU5ErkJggg=="
)


def _vision_messages() -> list[dict[str, Any]]:
    """Build a multimodal message with a tiny inline image."""
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": VISION_PROMPT_TEXT},
                {"type": "image_url", "image_url": {"url": _RED_PIXEL_PNG_B64}},
            ],
        }
    ]


def _has_key(env_var: str) -> bool:
    """Check if an API key env var is set and non-empty."""
    val = os.environ.get(env_var, "")
    return bool(val) and val not in ("", "sk-...", "public")


def _has_copilot() -> bool:
    """Check if the copilot CLI binary is available."""
    return shutil.which("copilot") is not None


def _make_client() -> LLMGatewayClient:
    """Build a real client with default config (reads env vars)."""
    config = LLMConfig.model_construct(
        gateway_url=os.environ.get("LLM_GATEWAY_URL", "https://api.openai.com/v1"),
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        default_model="gemini-2.0-flash",
        timeout_s=90.0,
        max_retries=2,
        retry_min_wait_s=2.0,
        retry_max_wait_s=30.0,
    )
    return LLMGatewayClient(config, registry=MODEL_REGISTRY)


def _assert_valid_response(resp: ChatResponse, model_id: str) -> None:
    """Common assertions for any successful LLM response."""
    assert resp is not None, f"{model_id}: response is None"
    assert resp.message is not None, f"{model_id}: message is None"
    assert resp.message.role == "assistant", f"{model_id}: role={resp.message.role}"
    text = resp.text.strip()
    assert len(text) > 0, f"{model_id}: empty response text"
    # For the "2+2" prompt, the answer should contain "4" somewhere
    assert "4" in text, f"{model_id}: expected '4' in response, got: {text!r}"


def _assert_valid_vision_response(resp: ChatResponse, model_id: str) -> None:
    """Common assertions for a vision response (just check non-empty text)."""
    assert resp is not None, f"{model_id}: response is None"
    assert resp.message is not None, f"{model_id}: message is None"
    text = resp.text.strip()
    assert len(text) > 0, f"{model_id}: empty vision response text"


# ---------------------------------------------------------------------------
# Gemini (free tier)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _has_key("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY not set",
)
class TestGeminiLive:
    """Live tests against the Google Gemini / Gemma OpenAI-compatible endpoint."""

    @pytest.mark.asyncio
    async def test_gemini_25_flash(self):
        """gemini-2.5-flash: fast reasoning model."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT, model="gemini-2.5-flash",
            )
        _assert_valid_response(resp, "gemini-2.5-flash")

    @pytest.mark.asyncio
    async def test_gemini_25_flash_lite(self):
        """gemini-2.5-flash-lite: ultralight chat completion."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT, model="gemini-2.5-flash-lite",
            )
        _assert_valid_response(resp, "gemini-2.5-flash-lite")

    @pytest.mark.asyncio
    async def test_gemini_3_flash_preview(self):
        """gemini-3-flash-preview: next-gen fast model."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT, model="gemini-3-flash-preview",
            )
        _assert_valid_response(resp, "gemini-3-flash-preview")

    @pytest.mark.asyncio
    async def test_gemma3_27b(self):
        """gemma-3-27b-it: open-weight 27B on AI Studio (14k RPD)."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT, model="gemma-3-27b-it",
            )
        _assert_valid_response(resp, "gemma-3-27b-it")

    @pytest.mark.asyncio
    async def test_gemma3_4b(self):
        """gemma-3-4b-it: tiny open-weight 4B on AI Studio."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT, model="gemma-3-4b-it",
            )
        _assert_valid_response(resp, "gemma-3-4b-it")


# ---------------------------------------------------------------------------
# Groq (free tier — ultra-fast LPU inference)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _has_key("GROQ_API_KEY"),
    reason="GROQ_API_KEY not set",
)
class TestGroqLive:
    """Live tests against Groq models (api.groq.com, free tier)."""

    @pytest.mark.asyncio
    async def test_llama31_8b(self):
        """llama-3.1-8b-instant: ultra-fast lightweight on Groq LPU."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT, model="groq/llama-3.1-8b-instant",
            )
        _assert_valid_response(resp, "groq/llama-3.1-8b-instant")

    @pytest.mark.asyncio
    async def test_llama33_70b(self):
        """llama-3.3-70b-versatile: general-purpose workhorse on Groq LPU."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT, model="groq/llama-3.3-70b-versatile",
            )
        _assert_valid_response(resp, "groq/llama-3.3-70b-versatile")

    @pytest.mark.asyncio
    async def test_gpt_oss_120b(self):
        """openai/gpt-oss-120b: strong reasoning on Groq LPU."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT, model="groq/openai/gpt-oss-120b",
            )
        _assert_valid_response(resp, "groq/openai/gpt-oss-120b")

    @pytest.mark.asyncio
    async def test_gpt_oss_20b(self):
        """openai/gpt-oss-20b: fastest reasoning on Groq LPU (~1000 tps)."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT, model="groq/openai/gpt-oss-20b",
            )
        _assert_valid_response(resp, "groq/openai/gpt-oss-20b")

    @pytest.mark.asyncio
    async def test_llama4_scout(self):
        """llama-4-scout: multimodal vision on Groq LPU (preview)."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="groq/meta-llama/llama-4-scout-17b-16e-instruct",
            )
        _assert_valid_response(resp, "groq/meta-llama/llama-4-scout")

    @pytest.mark.asyncio
    async def test_qwen3_32b(self):
        """qwen3-32b: multilingual reasoning on Groq LPU (preview)."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT, model="groq/qwen/qwen3-32b",
            )
        _assert_valid_response(resp, "groq/qwen/qwen3-32b")


# ---------------------------------------------------------------------------
# Cerebras Cloud (free tier — world's fastest inference)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _has_key("CEREBRAS_API_KEY"),
    reason="CEREBRAS_API_KEY not set",
)
class TestCerebrasLive:
    """Live tests against Cerebras Cloud models (api.cerebras.ai, free tier)."""

    @pytest.mark.asyncio
    async def test_llama31_8b(self):
        """llama3.1-8b: ultra-fast lightweight on Cerebras (~2200 tps)."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT, model="cerebras/llama3.1-8b",
            )
        _assert_valid_response(resp, "cerebras/llama3.1-8b")

    @pytest.mark.asyncio
    async def test_gpt_oss_120b(self):
        """gpt-oss-120b: flagship reasoning on Cerebras (~3000 tps)."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT, model="cerebras/gpt-oss-120b",
            )
        _assert_valid_response(resp, "cerebras/gpt-oss-120b")


# ---------------------------------------------------------------------------
# Mistral AI (free Experiment tier)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _has_key("MISTRAL_API_KEY"),
    reason="MISTRAL_API_KEY not set",
)
class TestMistralLive:
    """Live tests against Mistral AI models (api.mistral.ai, free tier)."""

    @pytest.mark.asyncio
    async def test_mistral_small(self):
        """Mistral Small 3.2 (24B): fast, multimodal, Apache 2.0."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT, model="mistral/mistral-small-latest",
            )
        _assert_valid_response(resp, "mistral/mistral-small-latest")

    @pytest.mark.asyncio
    async def test_open_mistral_nemo(self):
        """Mistral Nemo 12B: multilingual, very cheap."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT, model="mistral/open-mistral-nemo",
            )
        _assert_valid_response(resp, "mistral/open-mistral-nemo")

    @pytest.mark.asyncio
    async def test_ministral_3b(self):
        """Ministral 3B: tiny, efficient, Apache 2.0."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT, model="mistral/ministral-3b-latest",
            )
        _assert_valid_response(resp, "mistral/ministral-3b-latest")

    @pytest.mark.asyncio
    async def test_magistral_small(self):
        """Magistral Small 1.2: reasoning model, Apache 2.0."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT, model="mistral/magistral-small-latest",
            )
        _assert_valid_response(resp, "mistral/magistral-small-latest")


# ---------------------------------------------------------------------------
# Cloudflare Workers AI (free 10k neurons/day)
# ---------------------------------------------------------------------------


def _has_cloudflare() -> bool:
    """Check both CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID are set."""
    return _has_key("CLOUDFLARE_API_TOKEN") and bool(
        os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    )


@pytest.mark.skipif(
    not _has_cloudflare(),
    reason="CLOUDFLARE_API_TOKEN or CLOUDFLARE_ACCOUNT_ID not set",
)
class TestCloudflareLive:
    """Live tests against Cloudflare Workers AI (free tier, 10k neurons/day)."""

    @pytest.mark.asyncio
    async def test_llama31_8b_fast(self):
        """Llama 3.1 8B FP8 fast on Cloudflare."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="cloudflare/llama-3.1-8b-instruct-fp8-fast",
            )
        _assert_valid_response(resp, "cloudflare/llama-3.1-8b")

    @pytest.mark.asyncio
    async def test_llama33_70b_fast(self):
        """Llama 3.3 70B FP8 fast on Cloudflare."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="cloudflare/llama-3.3-70b-instruct-fp8-fast",
            )
        _assert_valid_response(resp, "cloudflare/llama-3.3-70b")

    @pytest.mark.asyncio
    async def test_qwen3_30b(self):
        """Qwen 3 30B MoE FP8 on Cloudflare."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="cloudflare/qwen3-30b-a3b-fp8",
            )
        _assert_valid_response(resp, "cloudflare/qwen3-30b")

    @pytest.mark.asyncio
    async def test_gpt_oss_120b(self):
        """GPT-OSS 120B on Cloudflare."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="cloudflare/gpt-oss-120b",
            )
        _assert_valid_response(resp, "cloudflare/gpt-oss-120b")


# ---------------------------------------------------------------------------
# OpenRouter (free tier) — model IDs verified against /api/v1/models
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _has_key("OPENROUTER_API_KEY"),
    reason="OPENROUTER_API_KEY not set",
)
class TestOpenRouterLive:
    """Live tests against OpenRouter free-tier models."""

    @pytest.mark.asyncio
    async def test_gemma3_27b(self):
        """Google Gemma 3 27B: open-weight multimodal."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="openrouter/google/gemma-3-27b-it:free",
            )
        _assert_valid_response(resp, "gemma-3-27b-it:free")

    @pytest.mark.asyncio
    async def test_gemma3_12b(self):
        """Google Gemma 3 12B: mid-size open model."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="openrouter/google/gemma-3-12b-it:free",
            )
        _assert_valid_response(resp, "gemma-3-12b-it:free")

    @pytest.mark.asyncio
    async def test_llama33_70b(self):
        """Meta Llama 3.3 70B: strong general-purpose."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="openrouter/meta-llama/llama-3.3-70b-instruct:free",
            )
        _assert_valid_response(resp, "llama-3.3-70b-instruct:free")

    @pytest.mark.asyncio
    async def test_mistral_small(self):
        """Mistral Small 3.1 24B: multimodal + coding."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="openrouter/mistralai/mistral-small-3.1-24b-instruct:free",
            )
        _assert_valid_response(resp, "mistral-small-3.1:free")

    @pytest.mark.asyncio
    async def test_qwen3_coder(self):
        """Qwen3 Coder 480B: agentic coding model."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="openrouter/qwen/qwen3-coder:free",
            )
        _assert_valid_response(resp, "qwen3-coder:free")

    @pytest.mark.asyncio
    async def test_qwen3_next_80b(self):
        """Qwen3 Next 80B: reasoning + code."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="openrouter/qwen/qwen3-next-80b-a3b-instruct:free",
            )
        _assert_valid_response(resp, "qwen3-next-80b:free")

    @pytest.mark.asyncio
    async def test_qwen3_4b(self):
        """Qwen3 4B: lightweight free model."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="openrouter/qwen/qwen3-4b:free",
            )
        _assert_valid_response(resp, "qwen3-4b:free")

    @pytest.mark.asyncio
    async def test_arcee_trinity_large(self):
        """Arcee Trinity Large Preview 400B: creative & agentic MoE."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="openrouter/arcee-ai/trinity-large-preview:free",
            )
        _assert_valid_response(resp, "trinity-large-preview:free")

    @pytest.mark.asyncio
    async def test_arcee_trinity_mini(self):
        """Arcee Trinity Mini 26B: lightweight reasoning MoE."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="openrouter/arcee-ai/trinity-mini:free",
            )
        _assert_valid_response(resp, "trinity-mini:free")

    @pytest.mark.asyncio
    async def test_nemotron_nano(self):
        """NVIDIA Nemotron 3 Nano 30B: agentic MoE."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="openrouter/nvidia/nemotron-3-nano-30b-a3b:free",
            )
        _assert_valid_response(resp, "nemotron-3-nano:free")

    @pytest.mark.asyncio
    async def test_glm_45_air(self):
        """Z.ai GLM 4.5 Air: agent-focused MoE."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="openrouter/z-ai/glm-4.5-air:free",
            )
        _assert_valid_response(resp, "glm-4.5-air:free")


# ---------------------------------------------------------------------------
# Zen / opencode.ai (always available — no key needed)
# ---------------------------------------------------------------------------

class TestZenLive:
    """Live tests against Zen free-tier models (no API key required)."""

    @pytest.mark.asyncio
    async def test_big_pickle(self):
        """big-pickle: general-purpose chat completions."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT, model="big-pickle",
            )
        _assert_valid_response(resp, "big-pickle")

    @pytest.mark.asyncio
    async def test_minimax_m25(self):
        """minimax-m2.5-free: lightweight chat completions."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT, model="minimax-m2.5-free",
            )
        _assert_valid_response(resp, "minimax-m2.5-free")

    @pytest.mark.asyncio
    async def test_gpt5_nano_responses_api(self):
        """gpt-5-nano: Responses API style (text input)."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT, model="gpt-5-nano",
            )
        _assert_valid_response(resp, "gpt-5-nano")

    @pytest.mark.asyncio
    async def test_trinity_large(self):
        """trinity-large-preview-free: bulk text model."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT, model="trinity-large-preview-free",
            )
        _assert_valid_response(resp, "trinity-large-preview-free")


# ---------------------------------------------------------------------------
# OpenAI (paid — only if key is set)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _has_key("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set",
)
class TestOpenAILive:
    """Live tests against OpenAI (paid, requires API key)."""

    @pytest.mark.asyncio
    async def test_gpt4o_chat(self):
        """gpt-4o: chat completion with tool support."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT, model="gpt-4o",
            )
        _assert_valid_response(resp, "gpt-4o")

    @pytest.mark.asyncio
    async def test_gpt4o_vision(self):
        """gpt-4o: multimodal (image) input."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=_vision_messages(), model="gpt-4o",
            )
        _assert_valid_vision_response(resp, "gpt-4o [vision]")


# ---------------------------------------------------------------------------
# Copilot CLI (only if binary is available)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _has_copilot(),
    reason="copilot binary not found on PATH",
)
class TestCopilotCLILive:
    """Live tests against the GitHub Copilot CLI (subprocess)."""

    @pytest.mark.asyncio
    async def test_copilot_gpt5_mini(self):
        """copilot/gpt-5-mini: free CLI model."""
        from mas_core.llm_gateway.providers.cli.copilot import CopilotModelScanner
        scanner = CopilotModelScanner(registry=MODEL_REGISTRY)
        scanner.register_known_free_models()

        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT, model="copilot/gpt-5-mini",
            )
        _assert_valid_response(resp, "copilot/gpt-5-mini")

    @pytest.mark.asyncio
    async def test_copilot_gpt41(self):
        """copilot/gpt-4.1: free CLI model."""
        from mas_core.llm_gateway.providers.cli.copilot import CopilotModelScanner
        scanner = CopilotModelScanner(registry=MODEL_REGISTRY)
        scanner.register_known_free_models()

        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT, model="copilot/gpt-4.1",
            )
        _assert_valid_response(resp, "copilot/gpt-4.1")


# ---------------------------------------------------------------------------
# Cross-provider sanity: registry completeness
# ---------------------------------------------------------------------------

class TestRegistryCompleteness:
    """Verify all expected providers and models are registered."""

    def test_all_providers_present(self):
        providers = {p.provider_id for p in MODEL_REGISTRY.list_providers()}
        for expected in (
            "openai", "gemini", "groq", "cerebras",
            "mistral", "cloudflare", "openrouter", "zen", "copilot",
        ):
            assert expected in providers, f"Provider '{expected}' not registered"

    def test_gemini_models_count(self):
        models = MODEL_REGISTRY.list_models("gemini")
        assert len(models) >= 5, f"Expected >= 5 Gemini/Gemma models, got {len(models)}"

    def test_groq_models_count(self):
        models = MODEL_REGISTRY.list_models("groq")
        assert len(models) >= 6, f"Expected >= 6 Groq models, got {len(models)}"

    def test_cerebras_models_count(self):
        models = MODEL_REGISTRY.list_models("cerebras")
        assert len(models) >= 2, f"Expected >= 2 Cerebras models, got {len(models)}"

    def test_mistral_models_count(self):
        models = MODEL_REGISTRY.list_models("mistral")
        assert len(models) >= 4, f"Expected >= 4 Mistral models, got {len(models)}"

    def test_cloudflare_models_count(self):
        models = MODEL_REGISTRY.list_models("cloudflare")
        assert len(models) >= 4, f"Expected >= 4 Cloudflare models, got {len(models)}"

    def test_openrouter_models_count(self):
        models = MODEL_REGISTRY.list_models("openrouter")
        assert len(models) >= 11, f"Expected >= 11 OpenRouter models, got {len(models)}"

    def test_zen_models_count(self):
        models = MODEL_REGISTRY.list_models("zen")
        assert len(models) >= 4, f"Expected >= 4 Zen models, got {len(models)}"

    def test_all_free_models_zero_cost(self):
        """Every model with cost_per_1m_input == 0.0 should truly be free."""
        for m in MODEL_REGISTRY.list_models():
            if ":free" in m.model_id or m.cost_per_1m_input == 0.0:
                assert m.cost_per_1m_input == 0.0, (
                    f"{m.model_id}: input cost should be 0.0, got {m.cost_per_1m_input}"
                )
                assert m.cost_per_1m_output == 0.0, (
                    f"{m.model_id}: output cost should be 0.0, got {m.cost_per_1m_output}"
                )

    def test_every_model_has_endpoint(self):
        for m in MODEL_REGISTRY.list_models():
            assert m.endpoint, f"{m.model_id}: endpoint is empty"

    def test_every_model_has_provider(self):
        providers = {p.provider_id for p in MODEL_REGISTRY.list_providers()}
        for m in MODEL_REGISTRY.list_models():
            assert m.provider in providers, (
                f"{m.model_id}: provider '{m.provider}' not registered"
            )
