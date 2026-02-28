"""Groq provider — ultra-fast inference via the OpenAI-compatible API.

Groq (https://groq.com) provides blazing-fast LLM inference on custom LPU
hardware.  The API is fully **OpenAI-compatible** at
``api.groq.com/openai/v1``, so models registered here use the standard
``CHAT_COMPLETIONS`` API style with no changes to the gateway client.

Authentication uses a standard ``Authorization: Bearer <key>`` header.
Get a free key at: https://console.groq.com/keys

Free-tier rate limits (as of early 2026):
- 30 RPM / 6–30 k TPM per model (varies by model)
- 1–14.4 k RPD (requests per day)
- No credit card required — fully free with rate limits.
- Cached tokens do not count towards rate limits.

Production models available on free tier:
- llama-3.3-70b-versatile     — 280 tps, 131 k ctx, general-purpose
- llama-3.1-8b-instant        — 560 tps, 131 k ctx, ultra-fast lightweight
- openai/gpt-oss-120b         — 500 tps, 131 k ctx, strong reasoning
- openai/gpt-oss-20b          — 1000 tps, 131 k ctx, fast reasoning

Preview models (may be deprecated at short notice):
- meta-llama/llama-4-scout-17b-16e-instruct — 750 tps, 131 k ctx, vision
- qwen/qwen3-32b              — 400 tps, 131 k ctx, multilingual

All text models support tool-calling, streaming, and structured outputs.
"""

from __future__ import annotations

from ..base import ApiStyle, ModelCapabilities, ModelEntry, ProviderConfig

# Deferred import — MODEL_REGISTRY is created in the parent __init__.py
# before sub-packages are imported.
from .. import MODEL_REGISTRY

# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

GROQ_PROVIDER = ProviderConfig(
    provider_id="groq",
    base_url="https://api.groq.com/openai/v1",
    api_key_env_vars=["GROQ_API_KEY"],
    description=(
        "Groq — ultra-fast LLM inference on LPU hardware (groq.com). "
        "Free tier with rate limits. OpenAI-compatible API."
    ),
)
MODEL_REGISTRY.register_provider(GROQ_PROVIDER)

# ---------------------------------------------------------------------------
# Endpoint constant (all models share the same chat/completions path)
# ---------------------------------------------------------------------------

_CC = "https://api.groq.com/openai/v1/chat/completions"

# ---------------------------------------------------------------------------
# Production models — stable, recommended for production use
# ---------------------------------------------------------------------------

# ---- Meta Llama 3.1 8B (ultra-fast, lightweight) -------------------------

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="groq/llama-3.1-8b-instant",
        provider="groq",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Meta Llama 3.1 8B Instant on Groq LPU (~560 tps). "
            "Ultra-fast lightweight model. Free tier: 30 RPM, 6k TPM. "
            "131 k context. Ideal for simple tasks and classification."
        ),
        max_context_tokens=131_072,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=0.05,
        cost_per_1m_output=0.08,
        default_temperature=0.7,
        capabilities=ModelCapabilities(
            supports_images=False,
            supports_pdf=False,
            supports_video=False,
            supports_reasoning=False,
            image_how="not supported — text-only model",
            pdf_how="extract text and send as message content",
        ),
        best_for=[
            "fast-classification",
            "routing-decisions",
            "simple-qa",
            "tool-calling",
            "structured-output",
        ],
        limits=[
            "free-tier-rate-limited (30 RPM, 6k TPM)",
            "smaller-model (8B)",
        ],
        compliance=[
            "groq-tos",
            "api-key-required",
            "free-tier",
        ],
        extra={"api_model_name": "llama-3.1-8b-instant"},
    )
)

# ---- Meta Llama 3.3 70B (general-purpose workhorse) ---------------------

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="groq/llama-3.3-70b-versatile",
        provider="groq",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Meta Llama 3.3 70B Versatile on Groq LPU (~280 tps). "
            "Strong general-purpose model. Free tier: 30 RPM, 12k TPM. "
            "131 k context (32 k max output). Great for agent work."
        ),
        max_context_tokens=131_072,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=0.59,
        cost_per_1m_output=0.79,
        default_temperature=0.7,
        capabilities=ModelCapabilities(
            supports_images=False,
            supports_pdf=False,
            supports_video=False,
            supports_reasoning=False,
            image_how="not supported — text-only model",
            pdf_how="extract text and send as message content",
        ),
        best_for=[
            "general-purpose",
            "code-generation",
            "analysis",
            "tool-calling",
            "agent-advisory",
            "structured-output",
        ],
        limits=[
            "free-tier-rate-limited (30 RPM, 12k TPM)",
            "32k max output tokens",
        ],
        compliance=[
            "groq-tos",
            "api-key-required",
            "free-tier",
        ],
        extra={"api_model_name": "llama-3.3-70b-versatile"},
    )
)

# ---- OpenAI GPT-OSS 120B (strong reasoning) -----------------------------

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="groq/openai/gpt-oss-120b",
        provider="groq",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "OpenAI GPT-OSS 120B on Groq LPU (~500 tps). "
            "Open-weight MoE, strong reasoning and tool use. "
            "Free tier: 30 RPM, 8k TPM. 131 k context."
        ),
        max_context_tokens=131_072,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=0.15,
        cost_per_1m_output=0.60,
        default_temperature=0.7,
        capabilities=ModelCapabilities(
            supports_images=False,
            supports_pdf=False,
            supports_video=False,
            supports_reasoning=True,
            image_how="not supported — text-only model",
            pdf_how="extract text and send as message content",
        ),
        best_for=[
            "reasoning",
            "code-generation",
            "tool-calling",
            "structured-output",
            "complex-analysis",
        ],
        limits=[
            "free-tier-rate-limited (30 RPM, 8k TPM)",
            "65k max output tokens",
        ],
        compliance=[
            "groq-tos",
            "api-key-required",
            "free-tier",
            "apache-2.0",
        ],
        extra={"api_model_name": "openai/gpt-oss-120b"},
    )
)

# ---- OpenAI GPT-OSS 20B (fast reasoning) --------------------------------

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="groq/openai/gpt-oss-20b",
        provider="groq",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "OpenAI GPT-OSS 20B on Groq LPU (~1000 tps). "
            "Fastest reasoning model on Groq. Open-weight MoE. "
            "Free tier: 30 RPM, 8k TPM. 131 k context."
        ),
        max_context_tokens=131_072,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=0.075,
        cost_per_1m_output=0.30,
        default_temperature=0.7,
        capabilities=ModelCapabilities(
            supports_images=False,
            supports_pdf=False,
            supports_video=False,
            supports_reasoning=True,
            image_how="not supported — text-only model",
            pdf_how="extract text and send as message content",
        ),
        best_for=[
            "fast-reasoning",
            "lightweight-analysis",
            "tool-calling",
            "structured-output",
            "budget-friendly",
        ],
        limits=[
            "free-tier-rate-limited (30 RPM, 8k TPM)",
            "65k max output tokens",
        ],
        compliance=[
            "groq-tos",
            "api-key-required",
            "free-tier",
            "apache-2.0",
        ],
        extra={"api_model_name": "openai/gpt-oss-20b"},
    )
)

# ---------------------------------------------------------------------------
# Preview models — for evaluation, may be deprecated at short notice
# ---------------------------------------------------------------------------

# ---- Meta Llama 4 Scout (vision + text) ----------------------------------

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="groq/meta-llama/llama-4-scout-17b-16e-instruct",
        provider="groq",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Meta Llama 4 Scout 17B (16 experts MoE) on Groq LPU (~750 tps). "
            "Multimodal: text + vision. Free tier: 30 RPM, 30k TPM. "
            "131 k context. Preview model."
        ),
        max_context_tokens=131_072,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=0.11,
        cost_per_1m_output=0.34,
        default_temperature=0.7,
        capabilities=ModelCapabilities(
            supports_images=True,
            supports_pdf=False,
            supports_video=False,
            supports_reasoning=False,
            image_how="image_url in content array (base64 data-URL or HTTPS URL, up to 20 MB)",
            pdf_how="extract text and send as message content",
        ),
        best_for=[
            "multimodal-vision",
            "image-understanding",
            "general-purpose",
            "tool-calling",
        ],
        limits=[
            "free-tier-rate-limited (30 RPM, 30k TPM)",
            "preview-model (may be deprecated)",
            "8k max output tokens",
        ],
        compliance=[
            "groq-tos",
            "api-key-required",
            "free-tier",
            "preview-model",
        ],
        extra={"api_model_name": "meta-llama/llama-4-scout-17b-16e-instruct"},
    )
)

# ---- Qwen3 32B (multilingual reasoning) ---------------------------------

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="groq/qwen/qwen3-32b",
        provider="groq",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Alibaba Qwen3 32B on Groq LPU (~400 tps). "
            "Multilingual reasoning model. Free tier: 60 RPM, 6k TPM. "
            "131 k context. Preview model."
        ),
        max_context_tokens=131_072,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=0.29,
        cost_per_1m_output=0.59,
        default_temperature=0.7,
        capabilities=ModelCapabilities(
            supports_images=False,
            supports_pdf=False,
            supports_video=False,
            supports_reasoning=True,
            image_how="not supported — text-only model",
            pdf_how="extract text and send as message content",
        ),
        best_for=[
            "multilingual",
            "reasoning",
            "code-generation",
            "tool-calling",
            "structured-output",
        ],
        limits=[
            "free-tier-rate-limited (60 RPM, 6k TPM)",
            "preview-model (may be deprecated)",
            "40k max output tokens",
        ],
        compliance=[
            "groq-tos",
            "api-key-required",
            "free-tier",
            "preview-model",
        ],
        extra={"api_model_name": "qwen/qwen3-32b"},
    )
)
