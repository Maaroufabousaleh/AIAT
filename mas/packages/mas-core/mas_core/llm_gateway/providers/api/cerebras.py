"""Cerebras Cloud provider — ultra-fast inference via the OpenAI-compatible API.

Cerebras (https://cerebras.ai) provides the world's fastest AI inference on
custom wafer-scale hardware.  The API is fully **OpenAI-compatible** at
``api.cerebras.ai/v1``, so models registered here use the standard
``CHAT_COMPLETIONS`` API style with no changes to the gateway client.

Authentication uses a standard ``Authorization: Bearer <key>`` header.
Get a free key at: https://cloud.cerebras.ai

Free-tier details (as of early 2026):
- Access to ALL Cerebras-powered models.
- 20x faster than OpenAI / Anthropic.
- Rate-limited (lower than Developer tier).
- No credit card required.

Production models:
- llama3.1-8b        — Meta Llama 3.1 8B, ~2200 tps
- gpt-oss-120b       — OpenAI GPT-OSS 120B, ~3000 tps

All models support tool-calling, streaming, and structured outputs.
"""

from __future__ import annotations

# Deferred import — MODEL_REGISTRY is created in the parent __init__.py
# before sub-packages are imported.
from .. import MODEL_REGISTRY
from ..base import ApiStyle, ModelCapabilities, ModelEntry, ProviderConfig

# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

CEREBRAS_PROVIDER = ProviderConfig(
    provider_id="cerebras",
    base_url="https://api.cerebras.ai/v1",
    api_key_env_vars=["CEREBRAS_API_KEY"],
    description=(
        "Cerebras Cloud — world's fastest AI inference on wafer-scale hardware. "
        "Free tier available. OpenAI-compatible API."
    ),
)
MODEL_REGISTRY.register_provider(CEREBRAS_PROVIDER)

# ---------------------------------------------------------------------------
# Endpoint constant (all models share the same chat/completions path)
# ---------------------------------------------------------------------------

_CC = "https://api.cerebras.ai/v1/chat/completions"

# ---------------------------------------------------------------------------
# Production models — stable, recommended for production use
# ---------------------------------------------------------------------------

# ---- Meta Llama 3.1 8B (~2200 tps) --------------------------------------

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="cerebras/llama3.1-8b",
        provider="cerebras",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Meta Llama 3.1 8B on Cerebras wafer-scale hardware (~2200 tps). "
            "Ultra-fast lightweight model. Free tier available. "
            "131 k context. Ideal for simple tasks and classification."
        ),
        max_context_tokens=131_072,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=0.10,
        cost_per_1m_output=0.10,
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
            "free-tier-rate-limited",
            "smaller-model (8B)",
        ],
        compliance=[
            "cerebras-tos",
            "api-key-required",
            "free-tier",
        ],
        extra={"api_model_name": "llama3.1-8b"},
    )
)

# ---- OpenAI GPT-OSS 120B (~3000 tps) ------------------------------------

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="cerebras/gpt-oss-120b",
        provider="cerebras",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "OpenAI GPT-OSS 120B on Cerebras wafer-scale hardware (~3000 tps). "
            "Fastest inference for a 120B reasoning model. Free tier available. "
            "Open-weight MoE, Apache 2.0."
        ),
        max_context_tokens=131_072,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=0.35,
        cost_per_1m_output=0.75,
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
            "free-tier-rate-limited",
        ],
        compliance=[
            "cerebras-tos",
            "api-key-required",
            "free-tier",
            "apache-2.0",
        ],
        extra={"api_model_name": "gpt-oss-120b"},
    )
)
