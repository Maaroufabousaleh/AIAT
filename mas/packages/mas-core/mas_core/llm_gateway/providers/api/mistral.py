"""Mistral AI provider — frontier open-weight models via the OpenAI-compatible API.

Mistral AI (https://mistral.ai) builds state-of-the-art open and commercial
language models.  The API is fully **OpenAI-compatible** at
``api.mistral.ai/v1``, so models registered here use the standard
``CHAT_COMPLETIONS`` API style with no changes to the gateway client.

Authentication uses a standard ``Authorization: Bearer <key>`` header.
Get a free key at: https://console.mistral.ai/api-keys

Free-tier ("Experiment" plan) details (as of early 2026):
- Free credits when you sign up — no credit card required.
- Access to all models (rate-limited on free tier).
- Rate limits vary by model and plan.

Models registered here (available on the free Experiment tier):
- mistral-small-latest        — Mistral Small 3.2, 24B, $0.1/$0.3 per M
- open-mistral-nemo           — Mistral Nemo 12B, multilingual, very cheap
- ministral-3b-latest         — Ministral 3B, tiny & efficient
- magistral-small-latest      — Magistral Small 1.2, reasoning model

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

MISTRAL_PROVIDER = ProviderConfig(
    provider_id="mistral",
    base_url="https://api.mistral.ai/v1",
    api_key_env_vars=["MISTRAL_API_KEY"],
    description=(
        "Mistral AI — frontier open-weight language models. "
        "Free Experiment tier available. OpenAI-compatible API."
    ),
)
MODEL_REGISTRY.register_provider(MISTRAL_PROVIDER)

# ---------------------------------------------------------------------------
# Endpoint constant (all models share the same chat/completions path)
# ---------------------------------------------------------------------------

_CC = "https://api.mistral.ai/v1/chat/completions"

# ---------------------------------------------------------------------------
# Models — available on the free "Experiment" tier
# ---------------------------------------------------------------------------

# ---- Mistral Small 3.2 (24B, fast & capable) ----------------------------

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="mistral/mistral-small-latest",
        provider="mistral",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Mistral Small 3.2 (24B) — fast, multimodal, 128 k context. "
            "$0.1/$0.3 per M tokens. Apache 2.0. "
            "Great for general-purpose tasks, vision, and tool-calling."
        ),
        max_context_tokens=128_000,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=0.10,
        cost_per_1m_output=0.30,
        default_temperature=0.7,
        capabilities=ModelCapabilities(
            supports_images=True,
            supports_pdf=False,
            supports_video=False,
            supports_reasoning=False,
            image_how="pass image URL or base64 in message content",
            pdf_how="extract text and send as message content",
        ),
        best_for=[
            "general-purpose",
            "tool-calling",
            "structured-output",
            "vision",
            "multilingual",
        ],
        limits=[
            "free-tier-rate-limited",
        ],
        compliance=[
            "mistral-tos",
            "api-key-required",
            "free-tier",
            "apache-2.0",
        ],
        extra={"api_model_name": "mistral-small-latest"},
    )
)

# ---- Mistral Nemo 12B (multilingual, ultra-cheap) -----------------------

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="mistral/open-mistral-nemo",
        provider="mistral",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Mistral Nemo 12B — excellent multilingual model, 128 k context. "
            "Very low cost. Apache 2.0. Released July 2024."
        ),
        max_context_tokens=128_000,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=0.15,
        cost_per_1m_output=0.15,
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
            "multilingual",
            "general-purpose",
            "simple-qa",
            "classification",
        ],
        limits=[
            "free-tier-rate-limited",
            "smaller-model (12B)",
        ],
        compliance=[
            "mistral-tos",
            "api-key-required",
            "free-tier",
            "apache-2.0",
        ],
        extra={"api_model_name": "open-mistral-nemo"},
    )
)

# ---- Ministral 3B (tiny, efficient) -------------------------------------

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="mistral/ministral-3b-latest",
        provider="mistral",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Ministral 3 3B — tiny, efficient, multimodal. "
            "Apache 2.0. Released Dec 2025. "
            "Excellent for edge, routing, and classification tasks."
        ),
        max_context_tokens=128_000,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=0.04,
        cost_per_1m_output=0.10,
        default_temperature=0.7,
        capabilities=ModelCapabilities(
            supports_images=True,
            supports_pdf=False,
            supports_video=False,
            supports_reasoning=False,
            image_how="pass image URL or base64 in message content",
            pdf_how="extract text and send as message content",
        ),
        best_for=[
            "edge-deployment",
            "routing-decisions",
            "classification",
            "simple-qa",
        ],
        limits=[
            "free-tier-rate-limited",
            "very-small-model (3B)",
        ],
        compliance=[
            "mistral-tos",
            "api-key-required",
            "free-tier",
            "apache-2.0",
        ],
        extra={"api_model_name": "ministral-3b-latest"},
    )
)

# ---- Magistral Small 1.2 (reasoning) ------------------------------------

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="mistral/magistral-small-latest",
        provider="mistral",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Magistral Small 1.2 — multimodal reasoning model, Apache 2.0. "
            "Strong chain-of-thought reasoning. Released Sept 2025."
        ),
        max_context_tokens=128_000,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=0.10,
        cost_per_1m_output=0.30,
        default_temperature=0.7,
        capabilities=ModelCapabilities(
            supports_images=True,
            supports_pdf=False,
            supports_video=False,
            supports_reasoning=True,
            image_how="pass image URL or base64 in message content",
            pdf_how="extract text and send as message content",
        ),
        best_for=[
            "reasoning",
            "complex-analysis",
            "math",
            "code-generation",
            "tool-calling",
        ],
        limits=[
            "free-tier-rate-limited",
        ],
        compliance=[
            "mistral-tos",
            "api-key-required",
            "free-tier",
            "apache-2.0",
        ],
        extra={"api_model_name": "magistral-small-latest"},
    )
)
