"""Cloudflare Workers AI provider — free-tier inference via the OpenAI-compatible API.

Cloudflare Workers AI (https://developers.cloudflare.com/workers-ai/) runs
popular open-weight models on Cloudflare's global network.  The API has an
**OpenAI-compatible** endpoint at
``api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/v1``, so models
registered here use the standard ``CHAT_COMPLETIONS`` API style.

Authentication uses ``Authorization: Bearer <token>``.  You also need your
Cloudflare **Account ID** (found in the dashboard sidebar).

Create a free API token at:
    Dashboard → Workers AI → Use REST API → Create a Workers AI API Token

Free-tier details (as of early 2026):
- 10,000 Neurons per day free on Workers Free plan.
- 10,000 Neurons per day free on Workers Paid plan (then $0.011 / 1k Neurons).
- No credit card required on free plan.
- All limits reset daily at 00:00 UTC.

Env vars required:
- ``CLOUDFLARE_API_TOKEN``   — Bearer token for REST API
- ``CLOUDFLARE_ACCOUNT_ID``  — Your Cloudflare account ID (for base URL)

Text generation models registered here:
- @cf/meta/llama-3.1-8b-instruct-fp8-fast  — fast 8B, $0.045/$0.384 per M
- @cf/meta/llama-3.3-70b-instruct-fp8-fast — strong 70B, $0.293/$2.253 per M
- @cf/qwen/qwen3-30b-a3b-fp8               — efficient MoE 30B, $0.051/$0.335
- @cf/openai/gpt-oss-120b                  — big reasoning, $0.35/$0.75 per M
"""

from __future__ import annotations

import os

# Deferred import — MODEL_REGISTRY is created in the parent __init__.py
# before sub-packages are imported.
from .. import MODEL_REGISTRY
from ..base import ApiStyle, ModelCapabilities, ModelEntry, ProviderConfig

# ---------------------------------------------------------------------------
# Dynamic base URL — includes the user's Cloudflare Account ID
# ---------------------------------------------------------------------------

_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
_BASE = f"https://api.cloudflare.com/client/v4/accounts/{_ACCOUNT_ID}/ai/v1"
_CC = f"{_BASE}/chat/completions"

# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

CLOUDFLARE_PROVIDER = ProviderConfig(
    provider_id="cloudflare",
    base_url=_BASE,
    api_key_env_vars=["CLOUDFLARE_API_TOKEN"],
    description=(
        "Cloudflare Workers AI — open models on Cloudflare's global network. "
        "Free tier: 10,000 Neurons/day. OpenAI-compatible API."
    ),
)
MODEL_REGISTRY.register_provider(CLOUDFLARE_PROVIDER)

# ---------------------------------------------------------------------------
# Models — all available on the free tier (within neuron budget)
# ---------------------------------------------------------------------------

# ---- Meta Llama 3.1 8B FP8 Fast -----------------------------------------

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="cloudflare/llama-3.1-8b-instruct-fp8-fast",
        provider="cloudflare",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Meta Llama 3.1 8B (FP8, fast) on Cloudflare Workers AI. "
            "$0.045/$0.384 per M tokens. Free tier: 10k neurons/day. "
            "Good for fast, simple tasks."
        ),
        max_context_tokens=8_192,
        supports_tools=False,
        supports_streaming=True,
        cost_per_1m_input=0.045,
        cost_per_1m_output=0.384,
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
            "simple-qa",
            "routing-decisions",
        ],
        limits=[
            "free-tier (10k neurons/day)",
            "8k context window",
        ],
        compliance=[
            "cloudflare-tos",
            "api-token-required",
            "cloudflare-account-id-required",
            "free-tier",
        ],
        extra={"api_model_name": "@cf/meta/llama-3.1-8b-instruct-fp8-fast"},
    )
)

# ---- Meta Llama 3.3 70B FP8 Fast ----------------------------------------

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="cloudflare/llama-3.3-70b-instruct-fp8-fast",
        provider="cloudflare",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Meta Llama 3.3 70B (FP8, fast) on Cloudflare Workers AI. "
            "$0.293/$2.253 per M tokens. Free tier: 10k neurons/day. "
            "Strong general-purpose model with function calling."
        ),
        max_context_tokens=8_192,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=0.293,
        cost_per_1m_output=2.253,
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
            "tool-calling",
            "code-generation",
            "complex-qa",
        ],
        limits=[
            "free-tier (10k neurons/day)",
            "8k context window",
        ],
        compliance=[
            "cloudflare-tos",
            "api-token-required",
            "cloudflare-account-id-required",
            "free-tier",
        ],
        extra={"api_model_name": "@cf/meta/llama-3.3-70b-instruct-fp8-fast"},
    )
)

# ---- Qwen 3 30B A3B FP8 (efficient MoE) ---------------------------------

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="cloudflare/qwen3-30b-a3b-fp8",
        provider="cloudflare",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Qwen 3 30B (3B active MoE, FP8) on Cloudflare Workers AI. "
            "$0.051/$0.335 per M tokens. Free tier: 10k neurons/day. "
            "Very efficient MoE with function calling."
        ),
        max_context_tokens=8_192,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=0.051,
        cost_per_1m_output=0.335,
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
            "multilingual",
            "tool-calling",
            "efficient-inference",
        ],
        limits=[
            "free-tier (10k neurons/day)",
            "8k context window",
        ],
        compliance=[
            "cloudflare-tos",
            "api-token-required",
            "cloudflare-account-id-required",
            "free-tier",
        ],
        extra={"api_model_name": "@cf/qwen/qwen3-30b-a3b-fp8"},
    )
)

# ---- OpenAI GPT-OSS 120B ------------------------------------------------

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="cloudflare/gpt-oss-120b",
        provider="cloudflare",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "OpenAI GPT-OSS 120B on Cloudflare Workers AI. "
            "$0.35/$0.75 per M tokens. Free tier: 10k neurons/day. "
            "Powerful reasoning model, Apache 2.0 open-weight."
        ),
        max_context_tokens=8_192,
        supports_tools=False,
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
            "complex-analysis",
        ],
        limits=[
            "free-tier (10k neurons/day)",
            "8k context window",
        ],
        compliance=[
            "cloudflare-tos",
            "api-token-required",
            "cloudflare-account-id-required",
            "free-tier",
            "apache-2.0",
        ],
        extra={"api_model_name": "@cf/openai/gpt-oss-120b"},
    )
)
