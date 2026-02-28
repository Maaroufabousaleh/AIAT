"""OpenRouter provider — free-tier models via the OpenAI-compatible API.

OpenRouter (https://openrouter.ai) aggregates dozens of LLM providers behind
a single OpenAI-compatible endpoint.  Many models are available at **zero
cost** on the free tier (model IDs suffixed with ``:free``).

Authentication uses a standard ``Authorization: Bearer <key>`` header.
Get a free key at: https://openrouter.ai/keys

Free-tier constraints (as of mid-2025):
- Rate limits vary per model (~10–20 RPM for free).
- Responses may include a brief attribution footer (strippable).
- Free models may have higher latency and lower priority than paid.
- Context windows and capabilities depend on the underlying model.

The OpenRouter API is fully OpenAI-compatible (``/v1/chat/completions``),
so no changes to the gateway client are needed.  OpenRouter also supports
tool-calling, streaming, and ``provider`` routing hints via extra headers.
"""

from __future__ import annotations

from ..base import ApiStyle, ModelCapabilities, ModelEntry, ProviderConfig

# Deferred import — MODEL_REGISTRY is created in the parent __init__.py
# before sub-packages are imported.
from .. import MODEL_REGISTRY

# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

OPENROUTER_PROVIDER = ProviderConfig(
    provider_id="openrouter",
    base_url="https://openrouter.ai/api",
    api_key_env_vars=["OPENROUTER_API_KEY"],
    default_api_key="",
    extra_headers={
        "HTTP-Referer": "https://github.com/AIAT",
        "X-Title": "AIAT-MAS",
    },
    description=(
        "OpenRouter — multi-provider LLM gateway (openrouter.ai). "
        "Free-tier models available. OpenAI-compatible API."
    ),
)
MODEL_REGISTRY.register_provider(OPENROUTER_PROVIDER)

# ---------------------------------------------------------------------------
# Endpoint constant
# ---------------------------------------------------------------------------

_CC = "https://openrouter.ai/api/v1/chat/completions"

# Prefix used in registry keys to namespace OpenRouter models
_PREFIX = "openrouter/"


def _register(entry: ModelEntry) -> None:
    """Register an OpenRouter model with the correct wire model name.

    Registry keys are prefixed with ``openrouter/`` to avoid collisions
    (e.g. ``openrouter/google/gemma-...`` vs the direct ``gemma-...``).
    The ``api_model_name`` extra field stores the native OpenRouter model ID
    (without the prefix) so the client sends the correct name on the wire.
    """
    if entry.model_id.startswith(_PREFIX):
        entry.extra["api_model_name"] = entry.model_id[len(_PREFIX):]
    MODEL_REGISTRY.register(entry)


# ---------------------------------------------------------------------------
# Free-tier models — verified against GET /api/v1/models (July 2025)
# ---------------------------------------------------------------------------

# ---- Google Gemma (open-weight) ------------------------------------------

_register(
    ModelEntry(
        model_id="openrouter/google/gemma-3-27b-it:free",
        provider="openrouter",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Google Gemma 3 27B Instruct via OpenRouter (free). "
            "Open-weight multimodal model, 131 k context, 140+ languages."
        ),
        max_context_tokens=131_072,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=0.0,
        cost_per_1m_output=0.0,
        default_temperature=0.7,
        capabilities=ModelCapabilities(
            supports_images=True,
            supports_pdf=False,
            supports_video=False,
            supports_reasoning=False,
            image_how="image_url in content array (base64 data-URL or HTTPS URL)",
            pdf_how="extract text and send as message content",
        ),
        best_for=["general-conversation", "code-generation", "multimodal-vision", "multilingual"],
        limits=["free-tier-rate-limited", "open-weight-model"],
        compliance=["openrouter-tos", "free-tier", "api-key-required"],
    )
)

_register(
    ModelEntry(
        model_id="openrouter/google/gemma-3-12b-it:free",
        provider="openrouter",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Google Gemma 3 12B Instruct via OpenRouter (free). "
            "Mid-size multimodal open model, 131 k context."
        ),
        max_context_tokens=131_072,
        supports_tools=False,
        supports_streaming=True,
        cost_per_1m_input=0.0,
        cost_per_1m_output=0.0,
        default_temperature=0.7,
        capabilities=ModelCapabilities(
            supports_images=True,
            supports_pdf=False,
            supports_video=False,
            supports_reasoning=False,
            image_how="image_url in content array (base64 data-URL or HTTPS URL)",
            pdf_how="extract text and send as message content",
        ),
        best_for=["general-conversation", "multilingual", "lightweight-tasks"],
        limits=["free-tier-rate-limited", "open-weight-model"],
        compliance=["openrouter-tos", "free-tier", "api-key-required"],
    )
)

# ---- Meta Llama ----------------------------------------------------------

_register(
    ModelEntry(
        model_id="openrouter/meta-llama/llama-3.3-70b-instruct:free",
        provider="openrouter",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Meta Llama 3.3 70B Instruct via OpenRouter (free). "
            "Multilingual (8 languages), 128 k context, "
            "strong instruction-following."
        ),
        max_context_tokens=128_000,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=0.0,
        cost_per_1m_output=0.0,
        default_temperature=0.7,
        capabilities=ModelCapabilities(
            supports_images=False,
            supports_pdf=False,
            supports_video=False,
            supports_reasoning=False,
            image_how="not supported — text-only model",
            pdf_how="extract text and send as message content",
        ),
        best_for=["general-conversation", "code-generation", "multilingual"],
        limits=["free-tier-rate-limited"],
        compliance=["openrouter-tos", "free-tier", "api-key-required", "meta-llama-license"],
    )
)

# ---- Mistral -------------------------------------------------------------

_register(
    ModelEntry(
        model_id="openrouter/mistralai/mistral-small-3.1-24b-instruct:free",
        provider="openrouter",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Mistral Small 3.1 24B Instruct via OpenRouter (free). "
            "24B multimodal model with 128 k context. Strong coding, "
            "function calling, and multilingual support."
        ),
        max_context_tokens=128_000,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=0.0,
        cost_per_1m_output=0.0,
        default_temperature=0.7,
        capabilities=ModelCapabilities(
            supports_images=True,
            supports_pdf=False,
            supports_video=False,
            supports_reasoning=False,
            image_how="image_url in content array (base64 data-URL or HTTPS URL)",
            pdf_how="extract text and send as message content",
        ),
        best_for=["code-generation", "tool-calling", "multilingual", "vision"],
        limits=["free-tier-rate-limited"],
        compliance=["openrouter-tos", "free-tier", "api-key-required"],
    )
)

# ---- Qwen ----------------------------------------------------------------

_register(
    ModelEntry(
        model_id="openrouter/qwen/qwen3-coder:free",
        provider="openrouter",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Qwen3 Coder 480B (35B active MoE) via OpenRouter (free). "
            "Optimized for agentic coding, function calling, tool use, "
            "and long-context reasoning over repos. 262 k context."
        ),
        max_context_tokens=262_144,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=0.0,
        cost_per_1m_output=0.0,
        default_temperature=0.7,
        capabilities=ModelCapabilities(
            supports_images=False,
            supports_pdf=False,
            supports_video=False,
            supports_reasoning=True,
            image_how="not supported — text-only model",
            pdf_how="extract text and send as message content",
        ),
        best_for=["code-generation", "agentic-coding", "tool-calling", "debugging"],
        limits=["free-tier-rate-limited", "moe-model"],
        compliance=["openrouter-tos", "free-tier", "api-key-required", "qwen-license"],
    )
)

_register(
    ModelEntry(
        model_id="openrouter/qwen/qwen3-next-80b-a3b-instruct:free",
        provider="openrouter",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Qwen3 Next 80B (3B active MoE) Instruct via OpenRouter (free). "
            "Fast stable responses, strong reasoning + code."
        ),
        max_context_tokens=131_072,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=0.0,
        cost_per_1m_output=0.0,
        default_temperature=0.7,
        capabilities=ModelCapabilities(
            supports_images=False,
            supports_pdf=False,
            supports_video=False,
            supports_reasoning=True,
            image_how="not supported — text-only model",
            pdf_how="extract text and send as message content",
        ),
        best_for=["reasoning", "code-generation", "multilingual", "structured-output"],
        limits=["free-tier-rate-limited", "moe-model"],
        compliance=["openrouter-tos", "free-tier", "api-key-required", "qwen-license"],
    )
)

_register(
    ModelEntry(
        model_id="openrouter/qwen/qwen3-4b:free",
        provider="openrouter",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Qwen3 4B dense via OpenRouter (free). "
            "Small but capable. Thinking/non-thinking modes. 40 k context."
        ),
        max_context_tokens=40_960,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=0.0,
        cost_per_1m_output=0.0,
        default_temperature=0.7,
        capabilities=ModelCapabilities(
            supports_images=False,
            supports_pdf=False,
            supports_video=False,
            supports_reasoning=True,
            image_how="not supported — text-only model",
            pdf_how="extract text and send as message content",
        ),
        best_for=["fast-classification", "simple-qa", "lightweight-reasoning"],
        limits=["free-tier-rate-limited", "compact-model"],
        compliance=["openrouter-tos", "free-tier", "api-key-required", "qwen-license"],
    )
)

# ---- Arcee AI -----------------------------------------------------------

_register(
    ModelEntry(
        model_id="openrouter/arcee-ai/trinity-large-preview:free",
        provider="openrouter",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Arcee Trinity Large Preview 400B (13B active MoE) via "
            "OpenRouter (free). Creative writing, roleplay, "
            "agentic workflows. 128 k context."
        ),
        max_context_tokens=128_000,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=0.0,
        cost_per_1m_output=0.0,
        default_temperature=0.7,
        capabilities=ModelCapabilities(
            supports_images=False,
            supports_pdf=False,
            supports_video=False,
            supports_reasoning=True,
            image_how="not supported — text-only model",
            pdf_how="extract text and send as message content",
        ),
        best_for=["creative-writing", "agentic-workflows", "tool-calling"],
        limits=["free-tier-rate-limited", "moe-model"],
        compliance=["openrouter-tos", "free-tier", "api-key-required"],
    )
)

_register(
    ModelEntry(
        model_id="openrouter/arcee-ai/trinity-mini:free",
        provider="openrouter",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Arcee Trinity Mini 26B (3B active MoE) via "
            "OpenRouter (free). Efficient reasoning, function "
            "calling, and agent workflows. 131 k context."
        ),
        max_context_tokens=131_072,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=0.0,
        cost_per_1m_output=0.0,
        default_temperature=0.7,
        capabilities=ModelCapabilities(
            supports_images=False,
            supports_pdf=False,
            supports_video=False,
            supports_reasoning=True,
            image_how="not supported — text-only model",
            pdf_how="extract text and send as message content",
        ),
        best_for=["fast-reasoning", "tool-calling", "lightweight-analysis"],
        limits=["free-tier-rate-limited", "compact-model"],
        compliance=["openrouter-tos", "free-tier", "api-key-required"],
    )
)

# ---- NVIDIA Nemotron -----------------------------------------------------

_register(
    ModelEntry(
        model_id="openrouter/nvidia/nemotron-3-nano-30b-a3b:free",
        provider="openrouter",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "NVIDIA Nemotron 3 Nano 30B (3B active MoE) via OpenRouter (free). "
            "Highest compute efficiency for agentic AI. 262 k context."
        ),
        max_context_tokens=262_144,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=0.0,
        cost_per_1m_output=0.0,
        default_temperature=0.7,
        capabilities=ModelCapabilities(
            supports_images=False,
            supports_pdf=False,
            supports_video=False,
            supports_reasoning=True,
            image_how="not supported — text-only model",
            pdf_how="extract text and send as message content",
        ),
        best_for=["agentic-workflows", "code-generation", "reasoning"],
        limits=["free-tier-rate-limited", "moe-model"],
        compliance=["openrouter-tos", "free-tier", "api-key-required", "nvidia-license"],
    )
)

# ---- NousResearch --------------------------------------------------------

_register(
    ModelEntry(
        model_id="openrouter/nousresearch/hermes-3-llama-3.1-405b:free",
        provider="openrouter",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Nous Hermes 3 (Llama 3.1 405B base) via OpenRouter (free). "
            "Generalist fine-tuned for instruction-following, "
            "tool use, and multi-turn reasoning."
        ),
        max_context_tokens=131_072,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=0.0,
        cost_per_1m_output=0.0,
        default_temperature=0.7,
        capabilities=ModelCapabilities(
            supports_images=False,
            supports_pdf=False,
            supports_video=False,
            supports_reasoning=False,
            image_how="not supported — text-only model",
            pdf_how="extract text and send as message content",
        ),
        best_for=["general-conversation", "tool-calling", "multi-turn-reasoning"],
        limits=["free-tier-rate-limited"],
        compliance=["openrouter-tos", "free-tier", "api-key-required", "llama-license"],
    )
)

# ---- Z.ai (GLM) ---------------------------------------------------------

_register(
    ModelEntry(
        model_id="openrouter/z-ai/glm-4.5-air:free",
        provider="openrouter",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Z.ai GLM 4.5 Air via OpenRouter (free). "
            "Lightweight MoE for agent-centric applications. "
            "Hybrid thinking/non-thinking modes. 131 k context."
        ),
        max_context_tokens=131_072,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=0.0,
        cost_per_1m_output=0.0,
        default_temperature=0.7,
        capabilities=ModelCapabilities(
            supports_images=False,
            supports_pdf=False,
            supports_video=False,
            supports_reasoning=True,
            image_how="not supported — text-only model",
            pdf_how="extract text and send as message content",
        ),
        best_for=["agentic-workflows", "reasoning", "tool-calling"],
        limits=["free-tier-rate-limited", "moe-model"],
        compliance=["openrouter-tos", "free-tier", "api-key-required"],
    )
)
