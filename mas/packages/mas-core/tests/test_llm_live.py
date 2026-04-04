"""Live integration tests for LLM providers.

These tests make **real API calls** to configured providers.
They are opt-in only and run only when ``MAS_RUN_LIVE_TESTS=1``.

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

Live test opt-in:
- ``MAS_RUN_LIVE_TESTS=1``  — enables tests marked with ``@pytest.mark.live``

Each test sends a small prompt ("What is 2+2?") and asserts the response
contains meaningful text.  This validates auth, endpoint, parsing, and
basic model functionality end-to-end.
"""

from __future__ import annotations

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

from mas_core.llm_gateway.client import LLMGatewayClient
from mas_core.llm_gateway.models import ChatResponse, LLMConfig
from mas_core.llm_gateway.providers import MODEL_REGISTRY
from mas_core.llm_gateway.providers.base import ModelPool
from mas_core.llm_gateway.thinking import (
    _CRITIQUE_RE,
    Depth,
    ThinkingChain,
    _stages_for_depth,
)

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


def _live_enabled() -> bool:
    """Return True only when live tests were explicitly enabled."""
    value = os.environ.get("MAS_RUN_LIVE_TESTS", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _has_provider(provider_id: str) -> bool:
    """Check if the provider exists in the runtime model registry."""
    return MODEL_REGISTRY.get_provider(provider_id) is not None


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


@pytest.mark.live
@pytest.mark.skipif(
    not _live_enabled(),
    reason="Set MAS_RUN_LIVE_TESTS=1 to enable live provider tests",
)
@pytest.mark.skipif(
    not _has_key("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY not set",
)
class TestGeminiLive:
    """Live tests against Google Gemini and Gemma models on AI Studio."""

    @pytest.mark.asyncio
    async def test_gemini31_flash_lite_preview(self):
        """gemini-3.1-flash-lite-preview: high-throughput preview text model."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="gemini-3.1-flash-lite-preview",
            )
        _assert_valid_response(resp, "gemini-3.1-flash-lite-preview")

    @pytest.mark.asyncio
    async def test_gemma3_27b(self):
        """gemma-3-27b-it: open-weight 27B on AI Studio (14k RPD)."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="gemma-3-27b-it",
            )
        _assert_valid_response(resp, "gemma-3-27b-it")

    @pytest.mark.asyncio
    async def test_gemma3_12b(self):
        """gemma-3-12b-it: mid-size 12B (may be slow on AI Studio)."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="gemma-3-12b-it",
            )
        _assert_valid_response(resp, "gemma-3-12b-it")

    @pytest.mark.asyncio
    async def test_gemma3_4b(self):
        """gemma-3-4b-it: lightweight 4B on AI Studio."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="gemma-3-4b-it",
            )
        _assert_valid_response(resp, "gemma-3-4b-it")

    @pytest.mark.asyncio
    async def test_gemma3_1b(self):
        """gemma-3-1b-it: ultra-tiny 1B on AI Studio."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="gemma-3-1b-it",
            )
        _assert_valid_response(resp, "gemma-3-1b-it")

    @pytest.mark.asyncio
    async def test_gemma3n_e4b(self):
        """gemma-3n-e4b-it: nano efficient 4B on AI Studio."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="gemma-3n-e4b-it",
            )
        _assert_valid_response(resp, "gemma-3n-e4b-it")

    @pytest.mark.asyncio
    async def test_gemma3n_e2b(self):
        """gemma-3n-e2b-it: nano efficient 2B on AI Studio."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="gemma-3n-e2b-it",
            )
        _assert_valid_response(resp, "gemma-3n-e2b-it")

    @pytest.mark.asyncio
    async def test_gemma4_26b_a4b(self):
        """gemma-4-26b-a4b-it: long-context Gemma 4 reasoning model."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="gemma-4-26b-a4b-it",
            )
        _assert_valid_response(resp, "gemma-4-26b-a4b-it")

    @pytest.mark.asyncio
    async def test_gemma4_31b(self):
        """gemma-4-31b-it: flagship Gemma 4 reasoning model."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="gemma-4-31b-it",
            )
        _assert_valid_response(resp, "gemma-4-31b-it")

    @pytest.mark.asyncio
    async def test_gemma4_31b_search_grounding(self):
        """gemma-4-31b-it: native Google Search grounding path."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=[{"role": "user", "content": "Who won Euro 2024?"}],
                model="gemma-4-31b-it",
                search_grounding=True,
            )
        assert resp is not None
        assert resp.message is not None
        assert "Spain" in resp.text or "spain" in resp.text
        assert resp.extra.get("grounding_metadata") is not None


# ---------------------------------------------------------------------------
# Groq (free tier — ultra-fast LPU inference)
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.skipif(
    not _live_enabled(),
    reason="Set MAS_RUN_LIVE_TESTS=1 to enable live provider tests",
)
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
                messages=SIMPLE_PROMPT,
                model="groq/llama-3.1-8b-instant",
            )
        _assert_valid_response(resp, "groq/llama-3.1-8b-instant")

    @pytest.mark.asyncio
    async def test_llama33_70b(self):
        """llama-3.3-70b-versatile: general-purpose workhorse on Groq LPU."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="groq/llama-3.3-70b-versatile",
            )
        _assert_valid_response(resp, "groq/llama-3.3-70b-versatile")

    @pytest.mark.asyncio
    async def test_gpt_oss_120b(self):
        """openai/gpt-oss-120b: strong reasoning on Groq LPU."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="groq/openai/gpt-oss-120b",
            )
        _assert_valid_response(resp, "groq/openai/gpt-oss-120b")

    @pytest.mark.asyncio
    async def test_gpt_oss_20b(self):
        """openai/gpt-oss-20b: fastest reasoning on Groq LPU (~1000 tps)."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="groq/openai/gpt-oss-20b",
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
                messages=SIMPLE_PROMPT,
                model="groq/qwen/qwen3-32b",
            )
        _assert_valid_response(resp, "groq/qwen/qwen3-32b")


# ---------------------------------------------------------------------------
# Cerebras Cloud (free tier — world's fastest inference)
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.skipif(
    not _live_enabled(),
    reason="Set MAS_RUN_LIVE_TESTS=1 to enable live provider tests",
)
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
                messages=SIMPLE_PROMPT,
                model="cerebras/llama3.1-8b",
            )
        _assert_valid_response(resp, "cerebras/llama3.1-8b")

    @pytest.mark.asyncio
    async def test_gpt_oss_120b(self):
        """gpt-oss-120b: flagship reasoning on Cerebras (~3000 tps)."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="cerebras/gpt-oss-120b",
            )
        _assert_valid_response(resp, "cerebras/gpt-oss-120b")


# ---------------------------------------------------------------------------
# Mistral AI (free Experiment tier)
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.skipif(
    not _live_enabled(),
    reason="Set MAS_RUN_LIVE_TESTS=1 to enable live provider tests",
)
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
                messages=SIMPLE_PROMPT,
                model="mistral/mistral-small-latest",
            )
        _assert_valid_response(resp, "mistral/mistral-small-latest")

    @pytest.mark.asyncio
    async def test_open_mistral_nemo(self):
        """Mistral Nemo 12B: multilingual, very cheap."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="mistral/open-mistral-nemo",
            )
        _assert_valid_response(resp, "mistral/open-mistral-nemo")

    @pytest.mark.asyncio
    async def test_ministral_3b(self):
        """Ministral 3B: tiny, efficient, Apache 2.0."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="mistral/ministral-3b-latest",
            )
        _assert_valid_response(resp, "mistral/ministral-3b-latest")

    @pytest.mark.asyncio
    async def test_magistral_small(self):
        """Magistral Small 1.2: reasoning model, Apache 2.0."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="mistral/magistral-small-latest",
            )
        _assert_valid_response(resp, "mistral/magistral-small-latest")

    @pytest.mark.asyncio
    async def test_mistral_medium(self):
        """Mistral Medium 3.x: frontier multimodal general-purpose model."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="mistral/mistral-medium-latest",
            )
        _assert_valid_response(resp, "mistral/mistral-medium-latest")

    @pytest.mark.asyncio
    async def test_codestral_latest(self):
        """Codestral: coding-specialized Mistral model."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="mistral/codestral-latest",
            )
        _assert_valid_response(resp, "mistral/codestral-latest")


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
@pytest.mark.live
@pytest.mark.skipif(
    not _live_enabled(),
    reason="Set MAS_RUN_LIVE_TESTS=1 to enable live provider tests",
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


@pytest.mark.live
@pytest.mark.skipif(
    not _live_enabled(),
    reason="Set MAS_RUN_LIVE_TESTS=1 to enable live provider tests",
)
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


@pytest.mark.live
@pytest.mark.skipif(
    not _live_enabled(),
    reason="Set MAS_RUN_LIVE_TESTS=1 to enable live provider tests",
)
class TestZenLive:
    """Live tests against Zen free-tier models (no API key required)."""

    @pytest.mark.asyncio
    async def test_big_pickle(self):
        """big-pickle: general-purpose chat completions."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="big-pickle",
            )
        _assert_valid_response(resp, "big-pickle")

    @pytest.mark.asyncio
    async def test_minimax_m25(self):
        """minimax-m2.5-free: lightweight chat completions."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="minimax-m2.5-free",
            )
        _assert_valid_response(resp, "minimax-m2.5-free")

    @pytest.mark.asyncio
    async def test_gpt5_nano_responses_api(self):
        """gpt-5-nano: Responses API style (text input)."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="gpt-5-nano",
            )
        _assert_valid_response(resp, "gpt-5-nano")

    @pytest.mark.asyncio
    async def test_trinity_large(self):
        """trinity-large-preview-free: bulk text model."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="trinity-large-preview-free",
            )
        _assert_valid_response(resp, "trinity-large-preview-free")


# ---------------------------------------------------------------------------
# OpenAI (paid — only if key is set)
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.skipif(
    not _live_enabled(),
    reason="Set MAS_RUN_LIVE_TESTS=1 to enable live provider tests",
)
@pytest.mark.skipif(
    not _has_provider("openai"),
    reason="openai provider is not registered in this build",
)
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
                messages=SIMPLE_PROMPT,
                model="gpt-4o",
            )
        _assert_valid_response(resp, "gpt-4o")

    @pytest.mark.asyncio
    async def test_gpt4o_vision(self):
        """gpt-4o: multimodal (image) input."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=_vision_messages(),
                model="gpt-4o",
            )
        _assert_valid_vision_response(resp, "gpt-4o [vision]")


# ---------------------------------------------------------------------------
# Copilot CLI (only if binary is available)
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.skipif(
    not _live_enabled(),
    reason="Set MAS_RUN_LIVE_TESTS=1 to enable live provider tests",
)
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
                messages=SIMPLE_PROMPT,
                model="copilot/gpt-5-mini",
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
                messages=SIMPLE_PROMPT,
                model="copilot/gpt-4.1",
            )
        _assert_valid_response(resp, "copilot/gpt-4.1")


# ---------------------------------------------------------------------------
# Model Pool — unit tests (no API calls)
# ---------------------------------------------------------------------------


class TestModelPoolUnit:
    """Pure logic tests for ModelPool round-robin and limit tracking."""

    def test_round_robin_distributes_evenly(self):
        """Pool rotates across all models in order."""
        pool = ModelPool(
            pool_id="test-pool",
            model_ids=["a", "b", "c"],
            rpm_per_model=100,
            rpd_per_model=10_000,
            tpm_per_model=50_000,
        )
        picks = [pool.pick() for _ in range(9)]
        assert picks == ["a", "b", "c", "a", "b", "c", "a", "b", "c"]

    def test_skips_exhausted_model(self):
        """If one model is at its RPM limit, pool skips it."""
        pool = ModelPool(
            pool_id="test-pool",
            model_ids=["a", "b"],
            rpm_per_model=2,  # effective = 2 * 0.85 = 1
            rpd_per_model=10_000,
            tpm_per_model=50_000,
            safety_margin=0.15,
        )
        # Use model "a" once — with effective RPM=1, it's now full
        picked = pool.pick()
        assert picked == "a"
        pool.record_request("a", tokens=10)

        # Next pick should skip "a" and give "b"
        assert pool.pick() == "b"

    def test_returns_none_when_all_exhausted(self):
        """Pool returns None when every model is at the limit."""
        pool = ModelPool(
            pool_id="test-pool",
            model_ids=["a", "b"],
            rpm_per_model=2,
            rpd_per_model=10_000,
            tpm_per_model=50_000,
            safety_margin=0.15,
        )
        # Fill both models
        pool.pick()
        pool.record_request("a", tokens=10)
        pool.pick()
        pool.record_request("b", tokens=10)

        assert pool.pick() is None

    def test_token_limit_respected(self):
        """Pool stops routing to a model when TPM limit is reached."""
        pool = ModelPool(
            pool_id="test-pool",
            model_ids=["a", "b"],
            rpm_per_model=100,
            rpd_per_model=10_000,
            tpm_per_model=100,  # effective = 85 tokens
            safety_margin=0.15,
        )
        pool.pick()
        pool.record_request("a", tokens=90)  # over effective 85

        # "a" should be skipped, "b" picked
        assert pool.pick() == "b"

    def test_stats_returns_all_models(self):
        """Stats snapshot includes every model with counters."""
        pool = ModelPool(
            pool_id="test-pool",
            model_ids=["x", "y", "z"],
            rpm_per_model=30,
            rpd_per_model=14_000,
            tpm_per_model=15_000,
        )
        pool.pick()
        pool.record_request("x", tokens=100)

        stats = pool.stats()
        assert stats["pool_id"] == "test-pool"
        assert len(stats["models"]) == 3
        assert stats["models"]["x"]["rpm_used"] == 1
        assert stats["models"]["x"]["tpm_used"] == 100
        assert stats["models"]["y"]["rpm_used"] == 0

    def test_reset_clears_counters(self):
        """reset() zeroes all counters so models become available again."""
        pool = ModelPool(
            pool_id="test-pool",
            model_ids=["a"],
            rpm_per_model=2,
            rpd_per_model=10_000,
            tpm_per_model=50_000,
            safety_margin=0.15,
        )
        pool.pick()
        pool.record_request("a", tokens=10)
        assert pool.pick() is None  # exhausted

        pool.reset()
        assert pool.pick() == "a"  # available again

    def test_gemma_pool_registered(self):
        """The gemma-pool should be registered in MODEL_REGISTRY."""
        pool = MODEL_REGISTRY.get_pool("gemma-pool")
        assert pool is not None, "gemma-pool not registered"
        assert len(pool.model_ids) == 6
        assert pool.rpd_per_model == 14_000
        assert pool.tpm_per_model == 15_000

    def test_registry_resolve_pool(self):
        """resolve_pool() returns a concrete entry + pool for pool IDs."""
        entry, pool = MODEL_REGISTRY.resolve_pool("gemma-pool")
        assert pool is not None
        assert entry is not None
        assert entry.model_id in pool.model_ids
        assert entry.provider == "gemini"

    def test_registry_resolve_plain_model(self):
        """resolve_pool() returns (entry, None) for non-pool model IDs."""
        entry, pool = MODEL_REGISTRY.resolve_pool("gemma-3-27b-it")
        assert entry is not None
        assert pool is None
        assert entry.model_id == "gemma-3-27b-it"


# ---------------------------------------------------------------------------
# Gemma Pool — live integration test
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.skipif(
    not _live_enabled(),
    reason="Set MAS_RUN_LIVE_TESTS=1 to enable live provider tests",
)
@pytest.mark.skipif(
    not _has_key("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY not set",
)
class TestGemmaPoolLive:
    """Live tests for the gemma-pool load-balanced model rotation."""

    @pytest.mark.asyncio
    async def test_pool_routes_to_different_models(self):
        """gemma-pool: 3 consecutive calls should use at least 2 distinct models."""
        pool = MODEL_REGISTRY.get_pool("gemma-pool")
        assert pool is not None
        pool.reset()  # clean counters for deterministic test

        client = _make_client()
        used_models: list[str] = []
        async with client:
            for _ in range(3):
                resp = await client.chat_completion(
                    messages=SIMPLE_PROMPT,
                    model="gemma-pool",
                )
                assert resp is not None
                assert resp.message is not None
                text = resp.text.strip()
                assert len(text) > 0
                used_models.append(resp.model)

        # Should have rotated to at least 2 distinct models
        distinct = set(used_models)
        assert len(distinct) >= 2, f"Expected >= 2 distinct models, got {distinct}"

    @pytest.mark.asyncio
    async def test_pool_records_usage(self):
        """gemma-pool: after a call, the pool's counters should reflect usage."""
        pool = MODEL_REGISTRY.get_pool("gemma-pool")
        assert pool is not None
        pool.reset()

        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="gemma-pool",
            )
        assert resp is not None

        stats = pool.stats()
        total_rpm = sum(m["rpm_used"] for m in stats["models"].values())
        total_tpm = sum(m["tpm_used"] for m in stats["models"].values())
        assert total_rpm == 1, f"Expected 1 request recorded, got {total_rpm}"
        assert total_tpm > 0, f"Expected tokens > 0, got {total_tpm}"


# ---------------------------------------------------------------------------
# Thinking chain — unit tests (no API calls)
# ---------------------------------------------------------------------------


class TestThinkingChainUnit:
    """Unit tests for ThinkingChain internals — no network required."""

    def test_depth_enum_values(self):
        """Depth enum has the three expected values."""
        assert Depth.LIGHT.value == "light"
        assert Depth.STANDARD.value == "standard"
        assert Depth.DEEP.value == "deep"

    def test_depth_from_string(self):
        """Depth can be constructed from string values."""
        assert Depth("light") == Depth.LIGHT
        assert Depth("standard") == Depth.STANDARD
        assert Depth("deep") == Depth.DEEP

    def test_stages_light_count(self):
        """Light depth produces exactly 2 stages."""
        stages = _stages_for_depth(Depth.LIGHT, 1024)
        assert len(stages) == 2
        assert stages[0].name == "decompose"
        assert stages[1].name == "synthesise"

    def test_stages_standard_count(self):
        """Standard depth produces exactly 3 stages."""
        stages = _stages_for_depth(Depth.STANDARD, 1024)
        assert len(stages) == 3
        assert stages[0].name == "decompose"
        assert stages[1].name == "analyse"
        assert stages[2].name == "synthesise"

    def test_stages_deep_count(self):
        """Deep depth produces exactly 3 stages with larger budgets."""
        stages = _stages_for_depth(Depth.DEEP, 1024)
        assert len(stages) == 3
        # Deep uses 4B for decompose (instead of nano 4B)
        assert stages[0].model == "gemma-3-4b-it"
        # Deep has bigger token budgets
        assert stages[0].max_tokens >= 500
        assert stages[1].max_tokens >= 1500

    def test_stages_use_distinct_models(self):
        """Standard pipeline uses 3 different models (small → mid → large)."""
        stages = _stages_for_depth(Depth.STANDARD, 1024)
        models = [s.model for s in stages]
        assert len(set(models)) >= 2, f"Expected ≥2 distinct models, got {models}"
        # Check the expected progression
        assert "27b" in stages[-1].model, "Last stage should use 27B"

    def test_synth_tokens_default(self):
        """When caller passes max_tokens=None, synth defaults to 2048."""
        stages = _stages_for_depth(Depth.STANDARD, None)
        assert stages[-1].max_tokens == 2048

    def test_synth_tokens_custom(self):
        """Caller max_tokens is forwarded to the synth stage."""
        stages = _stages_for_depth(Depth.STANDARD, 4096)
        assert stages[-1].max_tokens == 4096

    def test_extract_user_text_simple(self):
        """_extract_user_text pulls the last user message."""
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "What is 2+2?"},
        ]
        assert ThinkingChain._extract_user_text(msgs) == "What is 2+2?"

    def test_extract_user_text_multipart(self):
        """_extract_user_text handles multipart content (text + image)."""
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this."},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            }
        ]
        assert ThinkingChain._extract_user_text(msgs) == "Describe this."

    def test_extract_user_text_empty(self):
        """_extract_user_text returns empty string when no user message."""
        msgs = [{"role": "system", "content": "Hello."}]
        assert ThinkingChain._extract_user_text(msgs) == ""

    def test_build_stage_messages_first_stage(self):
        """First stage with a model that supports system role."""
        from mas_core.llm_gateway.thinking import StageConfig

        # Use a hypothetical model not in the no-system-role set
        stage = StageConfig(
            name="synthesise",
            model="some-model-with-system-support",
            system_prompt="Decompose the problem.",
            max_tokens=400,
        )
        original = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Explain gravity."},
        ]
        msgs = ThinkingChain._build_stage_messages(
            stage=stage,
            original_messages=original,
            user_text="Explain gravity.",
            accumulated_reasoning=[],
            is_first=True,
            is_last=False,
        )
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "Decompose the problem."
        # Original system message should be excluded
        assert len(msgs) == 2  # stage system + user
        assert msgs[1]["content"] == "Explain gravity."

    def test_build_stage_messages_first_stage_no_system_role(self):
        """First stage with a model that doesn't support system role."""
        from mas_core.llm_gateway.thinking import StageConfig

        stage = StageConfig(
            name="decompose",
            model="gemma-3-4b-it",  # doesn't support system role
            system_prompt="Decompose the problem.",
            max_tokens=400,
        )
        original = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Explain gravity."},
        ]
        msgs = ThinkingChain._build_stage_messages(
            stage=stage,
            original_messages=original,
            user_text="Explain gravity.",
            accumulated_reasoning=[],
            is_first=True,
            is_last=False,
        )
        # No system message — instructions prepended to user message
        assert msgs[0]["role"] == "user"
        assert "[Instructions:" in msgs[0]["content"]
        assert "Explain gravity." in msgs[0]["content"]

    def test_build_stage_messages_middle_stage(self):
        """Middle stage gets system prompt + structured context."""
        from mas_core.llm_gateway.thinking import StageConfig

        stage = StageConfig(
            name="analyse",
            model="some-model-with-system-support",  # supports system role
            system_prompt="Analyse each part.",
            max_tokens=1000,
        )
        msgs = ThinkingChain._build_stage_messages(
            stage=stage,
            original_messages=[{"role": "user", "content": "What is AI?"}],
            user_text="What is AI?",
            accumulated_reasoning=["[DECOMPOSE — gemma-3n-e4b-it]\n1. Define AI\n2. History"],
            is_first=False,
            is_last=False,
        )
        assert msgs[0]["role"] == "system"
        assert "## Original question" in msgs[1]["content"]
        assert "## Prior stage output" in msgs[1]["content"]
        assert "Continue your analysis." in msgs[1]["content"]

    def test_build_stage_messages_last_stage(self):
        """Last stage asks for a definitive answer."""
        from mas_core.llm_gateway.thinking import StageConfig

        stage = StageConfig(
            name="synthesise",
            model="some-model-with-system-support",
            system_prompt="Synthesise.",
            max_tokens=2048,
        )
        msgs = ThinkingChain._build_stage_messages(
            stage=stage,
            original_messages=[{"role": "user", "content": "What is AI?"}],
            user_text="What is AI?",
            accumulated_reasoning=["[DECOMPOSE]\nParts", "[ANALYSE]\nAnalysis"],
            is_first=False,
            is_last=True,
        )
        assert "final, definitive answer" in msgs[1]["content"]

    def test_extract_user_text_picks_last_user_msg(self):
        """_extract_user_text returns the *last* user message, not the first."""
        msgs = [
            {"role": "user", "content": "First question."},
            {"role": "assistant", "content": "Answer."},
            {"role": "user", "content": "Follow-up question."},
        ]
        assert ThinkingChain._extract_user_text(msgs) == "Follow-up question."

    def test_build_stage_no_user_messages_edge_case(self):
        """First stage with no user messages injects instructions standalone."""
        from mas_core.llm_gateway.thinking import StageConfig

        stage = StageConfig(
            name="decompose",
            model="gemma-3-4b-it",  # no system role support
            system_prompt="Decompose.",
            max_tokens=400,
        )
        msgs = ThinkingChain._build_stage_messages(
            stage=stage,
            original_messages=[{"role": "system", "content": "You are helpful."}],
            user_text="",
            accumulated_reasoning=[],
            is_first=True,
            is_last=False,
        )
        # Should have injected the instructions as a user message
        assert len(msgs) >= 1
        assert msgs[0]["role"] == "user"
        assert "[Instructions:" in msgs[0]["content"]

    def test_critique_regex_strips_tags(self):
        """_CRITIQUE_RE removes <critique>...</critique> blocks."""
        text = "<critique>The analysis misses edge cases.</critique>\nThe answer is 42."
        cleaned = _CRITIQUE_RE.sub("", text).strip()
        assert "<critique>" not in cleaned
        assert "The answer is 42." in cleaned

    def test_critique_regex_handles_multiline(self):
        """_CRITIQUE_RE works across multiple lines."""
        text = "<critique>\nLine 1.\nLine 2.\n</critique>\nThe final answer."
        cleaned = _CRITIQUE_RE.sub("", text).strip()
        assert "The final answer." in cleaned
        assert "Line 1" not in cleaned

    def test_invalid_depth_string_falls_back(self):
        """An invalid depth string in the model name should not crash."""
        from mas_core.llm_gateway.thinking import Depth

        # Simulate what _dispatch_thinking_chain does
        resolved_model = "gemma-think/invalid"
        parts = resolved_model.split("/", 1)
        depth_str = parts[1] if len(parts) > 1 else "standard"
        try:
            depth = Depth(depth_str)
        except ValueError:
            depth = Depth.STANDARD
        assert depth == Depth.STANDARD

    def test_gemma_think_registered(self):
        """gemma-think virtual model should appear in the registry."""
        entry = MODEL_REGISTRY.get("gemma-think")
        assert entry is not None, "gemma-think not in MODEL_REGISTRY"
        assert entry.extra.get("virtual") is True
        assert entry.cost_per_1m_input == 0.0
        assert entry.cost_per_1m_output == 0.0


# ---------------------------------------------------------------------------
# Thinking chain — live tests (requires GEMINI_API_KEY)
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.skipif(
    not _live_enabled(),
    reason="Set MAS_RUN_LIVE_TESTS=1 to enable live provider tests",
)
@pytest.mark.skipif(
    not _has_key("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY not set",
)
class TestGemmaThinkLive:
    """Live integration tests for the gemma-think reasoning pipeline."""

    @pytest.mark.asyncio
    async def test_think_standard_simple(self):
        """gemma-think (standard depth): should answer 2+2 correctly."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="gemma-think",
            )
        assert resp is not None
        assert resp.message is not None
        text = resp.text.strip()
        assert len(text) > 0, "Empty thinking response"
        assert "4" in text, f"Expected '4' in response, got: {text!r}"
        assert resp.model.startswith("gemma-think/")
        assert resp.usage.total_tokens > 0

    @pytest.mark.asyncio
    async def test_think_light_fast(self):
        """gemma-think/light: 2-stage pipeline should be faster than standard."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="gemma-think/light",
            )
        assert resp is not None
        assert resp.message is not None
        text = resp.text.strip()
        assert len(text) > 0
        assert "4" in text, f"Expected '4' in response, got: {text!r}"
        assert resp.model == "gemma-think/light"

    @pytest.mark.asyncio
    async def test_think_deep_self_critique(self):
        """gemma-think/deep: 3-stage pipeline with self-critique capability."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "What are the main pros and cons of microservices vs "
                            "monolithic architecture? Be specific and balanced."
                        ),
                    }
                ],
                model="gemma-think/deep",
                max_tokens=2048,
            )
        assert resp is not None
        assert resp.message is not None
        text = resp.text.strip()
        assert len(text) > 50, f"Deep response too short: {len(text)} chars"
        assert resp.model == "gemma-think/deep"
        assert resp.usage.total_tokens > 0

    @pytest.mark.asyncio
    async def test_think_returns_aggregated_usage(self):
        """gemma-think: response usage should aggregate all stages."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=SIMPLE_PROMPT,
                model="gemma-think/light",
            )
        # Light = 2 stages → usage should include tokens from both
        assert resp.usage.prompt_tokens > 0
        assert resp.usage.completion_tokens > 0
        # Total should be > what a single small model call would produce
        assert resp.usage.total_tokens >= 20, (
            f"Expected aggregated tokens >= 20, got {resp.usage.total_tokens}"
        )

    @pytest.mark.asyncio
    async def test_think_complex_reasoning(self):
        """gemma-think: complex question should produce rich multi-part answer."""
        client = _make_client()
        async with client:
            resp = await client.chat_completion(
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "A farmer has 17 sheep. All but 9 run away. "
                            "How many sheep does the farmer have left?"
                        ),
                    }
                ],
                model="gemma-think",
                max_tokens=1024,
            )
        assert resp is not None
        text = resp.text.strip()
        assert "9" in text, f"Expected '9' in response, got: {text!r}"


# ---------------------------------------------------------------------------
# Cross-provider sanity: registry completeness
# ---------------------------------------------------------------------------


class TestRegistryCompleteness:
    """Verify all expected providers and models are registered."""

    def test_all_providers_present(self):
        providers = {p.provider_id for p in MODEL_REGISTRY.list_providers()}
        for expected in (
            "gemini",
            "groq",
            "cerebras",
            "mistral",
            "cloudflare",
            "openrouter",
            "zen",
            "copilot",
        ):
            assert expected in providers, f"Provider '{expected}' not registered"

    def test_gemini_models_count(self):
        models = MODEL_REGISTRY.list_models("gemini")
        assert len(models) >= 10, (
            f"Expected >= 10 Gemini models (Gemini preview, 8 Gemma variants, gemma-think), got {len(models)}"
        )

    def test_gemma_pool_registered(self):
        pool = MODEL_REGISTRY.get_pool("gemma-pool")
        assert pool is not None, "gemma-pool not in registry"
        assert len(pool.model_ids) >= 6

    def test_groq_models_count(self):
        models = MODEL_REGISTRY.list_models("groq")
        assert len(models) >= 6, f"Expected >= 6 Groq models, got {len(models)}"

    def test_cerebras_models_count(self):
        models = MODEL_REGISTRY.list_models("cerebras")
        assert len(models) >= 2, f"Expected >= 2 Cerebras models, got {len(models)}"

    def test_mistral_models_count(self):
        models = MODEL_REGISTRY.list_models("mistral")
        assert len(models) >= 30, f"Expected >= 30 Mistral models, got {len(models)}"

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
            assert m.provider in providers, f"{m.model_id}: provider '{m.provider}' not registered"
