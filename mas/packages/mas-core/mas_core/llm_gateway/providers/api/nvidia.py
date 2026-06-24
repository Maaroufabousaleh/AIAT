"""NVIDIA NIM provider — NVIDIA Inference Microservice hosted at build.nvidia.com.

NVIDIA NIM (https://build.nvidia.com) exposes an **OpenAI-compatible** API at
``https://integrate.api.nvidia.com/v1``, so models registered here use the
standard ``CHAT_COMPLETIONS`` API style with no changes to the gateway client.

Authentication uses a standard ``Authorization: Bearer <key>`` header, with
the key read from the ``NVIDIA_API_KEY`` environment variable.  Get a key at
https://build.nvidia.com → "Get API Key".

Models registered here (small curated set, 2026):

- nvidia/meta/llama-3.1-70b-instruct      — general-purpose 70B, 128 k ctx
- nvidia/meta/llama-3.1-8b-instruct       — lightweight 8B, 128 k ctx
- nvidia/nemotron-4-340b-instruct         — NVIDIA flagship reasoning MoE
- nvidia/nemotron-mini-4b-instruct        — ultra-lightweight 4B instruct

Cost values are intentionally left as ``None`` (unknown).  Pricing on
build.nvidia.com changes frequently and varies per model (some have a free
credits tier, others are paid-per-token); baking in unverified numbers would
mislead cost-based routing.  Update the per-model ``cost_per_1m_*`` fields
once the published rates are confirmed.
"""

from __future__ import annotations

# Deferred import — MODEL_REGISTRY is created in the parent __init__.py
# before sub-packages are imported.
from .. import MODEL_REGISTRY
from ..base import ApiStyle, ModelCapabilities, ModelEntry, ProviderConfig

# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

NVIDIA_PROVIDER = ProviderConfig(
    provider_id="nvidia",
    base_url=NVIDIA_BASE_URL,
    api_key_env_vars=["NVIDIA_API_KEY"],
    description=(
        "NVIDIA NIM — NVIDIA Inference Microservice hosted at "
        "build.nvidia.com. OpenAI-compatible /v1/chat/completions endpoint. "
        "Set NVIDIA_API_KEY."
    ),
)
MODEL_REGISTRY.register_provider(NVIDIA_PROVIDER)

# ---------------------------------------------------------------------------
# Endpoint constant (all models share the same chat/completions path)
# ---------------------------------------------------------------------------

_CC = f"{NVIDIA_BASE_URL}/chat/completions"

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

# ---- Meta Llama 3.1 70B Instruct -----------------------------------------

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="nvidia/meta/llama-3.1-70b-instruct",
        provider="nvidia",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Meta Llama 3.1 70B Instruct on NVIDIA NIM. "
            "General-purpose 70B, 128 k context. Supports tool-calling "
            "and streaming. Pricing on build.nvidia.com — see "
            "https://build.nvidia.com for current rates."
        ),
        max_context_tokens=131_072,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=None,
        cost_per_1m_output=None,
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
            "agentic-workflows",
        ],
        limits=[
            "cost-unverified (set cost_per_1m_* to None until build.nvidia.com pricing is confirmed)",
        ],
        compliance=[
            "nvidia-tos",
            "api-key-required",
        ],
        extra={"api_model_name": "meta/llama-3.1-70b-instruct"},
    )
)

# ---- Meta Llama 3.1 8B Instruct ------------------------------------------

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="nvidia/meta/llama-3.1-8b-instruct",
        provider="nvidia",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Meta Llama 3.1 8B Instruct on NVIDIA NIM. "
            "Lightweight 8B, 128 k context. Supports tool-calling "
            "and streaming. Good for fast classification and routing."
        ),
        max_context_tokens=131_072,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=None,
        cost_per_1m_output=None,
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
            "cost-unverified (set cost_per_1m_* to None until build.nvidia.com pricing is confirmed)",
        ],
        compliance=[
            "nvidia-tos",
            "api-key-required",
        ],
        extra={"api_model_name": "meta/llama-3.1-8b-instruct"},
    )
)

# ---- NVIDIA Nemotron-4 340B Instruct -------------------------------------

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="nvidia/nemotron-4-340b-instruct",
        provider="nvidia",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "NVIDIA Nemotron-4 340B Instruct on NVIDIA NIM. "
            "NVIDIA flagship reasoning model — strong chain-of-thought, "
            "synthesis, and complex analysis. Tool-calling and streaming "
            "support vary by deployment; check build.nvidia.com."
        ),
        max_context_tokens=None,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=None,
        cost_per_1m_output=None,
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
            "complex-analysis",
            "structured-synthesis",
            "code-generation",
        ],
        limits=[
            "max_context_tokens-unverified (set explicitly once build.nvidia.com docs are checked)",
            "cost-unverified",
        ],
        compliance=[
            "nvidia-tos",
            "api-key-required",
        ],
        extra={"api_model_name": "nvidia/nemotron-4-340b-instruct"},
    )
)

# ---- NVIDIA Nemotron-Mini 4B Instruct ------------------------------------

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="nvidia/nemotron-mini-4b-instruct",
        provider="nvidia",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "NVIDIA Nemotron-Mini 4B Instruct on NVIDIA NIM. "
            "Ultra-lightweight 4B model — fast and cheap, suitable for "
            "classification, routing, and simple Q&A. Tool-calling and "
            "streaming support vary by deployment."
        ),
        max_context_tokens=None,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=None,
        cost_per_1m_output=None,
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
            "budget-friendly",
        ],
        limits=[
            "max_context_tokens-unverified (set explicitly once build.nvidia.com docs are checked)",
            "cost-unverified",
            "smaller-model (4B)",
        ],
        compliance=[
            "nvidia-tos",
            "api-key-required",
        ],
        extra={"api_model_name": "nvidia/nemotron-mini-4b-instruct"},
    )
)
