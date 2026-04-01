"""Google Gemma provider — free open-weight models via the OpenAI-compatible endpoint.

The Google AI Studio exposes an **OpenAI-compatible** layer at
``generativelanguage.googleapis.com/v1beta/openai/``.  Gemma models
registered here use the standard ``CHAT_COMPLETIONS`` API style with no
changes to the gateway client.

Authentication uses a Google AI Studio API key sent as ``Bearer`` token.
Get a free key at: https://aistudio.google.com/apikey

Free-tier rate limits per Gemma model (AI Studio, as of 2026):
- gemma-3-27b-it:   30 RPM / 15 k TPM / 14 000 RPD
- gemma-3-12b-it:   30 RPM / 15 k TPM / 14 000 RPD
- gemma-3-4b-it:    30 RPM / 15 k TPM / 14 000 RPD
- gemma-3-1b-it:    30 RPM / 15 k TPM / 14 000 RPD
- gemma-3n-e4b-it:  30 RPM / 15 k TPM / 14 000 RPD
- gemma-3n-e2b-it:  30 RPM / 15 k TPM / 14 000 RPD

All six models are pooled under ``gemma-pool`` for automatic load balancing
(aggregate ~84 k RPD / 90 k TPM).
"""

from __future__ import annotations

# Deferred import — MODEL_REGISTRY is created in the parent __init__.py
# before sub-packages are imported.
from .. import MODEL_REGISTRY
from ..base import ApiStyle, ModelCapabilities, ModelEntry, ModelPool, ProviderConfig

# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

GEMINI_PROVIDER = ProviderConfig(
    provider_id="gemini",
    base_url="https://generativelanguage.googleapis.com/v1beta/openai",
    api_key_env_vars=["GEMINI_API_KEY", "GOOGLE_AI_API_KEY", "GOOGLE_API_KEY"],
    description=(
        "Google AI Studio — Gemma open-weight models via the OpenAI-compatible "
        "endpoint. Free tier available (rate-limited). Supports vision, "
        "tool-calling, and streaming."
    ),
)
MODEL_REGISTRY.register_provider(GEMINI_PROVIDER)

# ---------------------------------------------------------------------------
# Endpoint constant (all models share the same chat/completions path)
# ---------------------------------------------------------------------------

_CC = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"

# ---------------------------------------------------------------------------
# Free-tier models (verified working Feb 2026)
# ---------------------------------------------------------------------------

# ---- Gemma 3 27B (open-weight, free on AI Studio) -----------------------

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="gemma-3-27b-it",
        provider="gemini",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Google Gemma 3 27B Instruct — open-weight model hosted free on "
            "AI Studio. 14 000 RPD. Strong multilingual, code, and reasoning."
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
        best_for=[
            "general-purpose",
            "code-generation",
            "multilingual",
            "tool-calling",
            "structured-output",
        ],
        limits=[
            "free-tier (14k RPD on AI Studio)",
            "131k context (vs 1M for Gemini)",
        ],
        compliance=[
            "google-ai-studio-tos",
            "free-tier",
            "api-key-required",
            "gemma-open-weight",
            "data-used-for-improvement (free tier)",
        ],
    )
)

# ---- Gemma 3 4B (tiny open-weight, free on AI Studio) -------------------

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="gemma-3-4b-it",
        provider="gemini",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Google Gemma 3 4B Instruct — tiny open-weight model hosted free "
            "on AI Studio. Excellent for routing, classification, simple tasks."
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
        best_for=[
            "fast-classification",
            "routing-decisions",
            "simple-qa",
            "edge-deployment",
        ],
        limits=[
            "free-tier on AI Studio",
            "small-model (4B, less reasoning depth)",
        ],
        compliance=[
            "google-ai-studio-tos",
            "free-tier",
            "api-key-required",
            "gemma-open-weight",
            "data-used-for-improvement (free tier)",
        ],
    )
)

# ---- Gemma 3 12B (mid-size open-weight, free on AI Studio) --------------

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="gemma-3-12b-it",
        provider="gemini",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Google Gemma 3 12B Instruct — mid-size open-weight model hosted "
            "free on AI Studio. Good balance of quality and speed. "
            "Note: may be slow on AI Studio (long first-token latency)."
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
        best_for=[
            "general-purpose",
            "code-generation",
            "multilingual",
            "summarisation",
        ],
        limits=[
            "free-tier on AI Studio",
            "slow-cold-start (may timeout on first call)",
            "131k context",
        ],
        compliance=[
            "google-ai-studio-tos",
            "free-tier",
            "api-key-required",
            "gemma-open-weight",
            "data-used-for-improvement (free tier)",
        ],
    )
)

# ---- Gemma 3 1B (ultra-tiny open-weight, free on AI Studio) -------------

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="gemma-3-1b-it",
        provider="gemini",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Google Gemma 3 1B Instruct — ultra-tiny open-weight model. "
            "Fastest inference, minimal resource use. Ideal for simple "
            "routing, classification, and lightweight tasks."
        ),
        max_context_tokens=32_768,
        supports_tools=False,
        supports_streaming=True,
        cost_per_1m_input=0.0,
        cost_per_1m_output=0.0,
        default_temperature=0.7,
        capabilities=ModelCapabilities(
            supports_images=False,
            supports_pdf=False,
            supports_video=False,
            supports_reasoning=False,
        ),
        best_for=[
            "fast-classification",
            "routing-decisions",
            "simple-qa",
            "edge-deployment",
        ],
        limits=[
            "free-tier on AI Studio",
            "very-small-model (1B, limited reasoning)",
            "32k context",
            "no-vision",
        ],
        compliance=[
            "google-ai-studio-tos",
            "free-tier",
            "api-key-required",
            "gemma-open-weight",
            "data-used-for-improvement (free tier)",
        ],
    )
)

# ---- Gemma 3n E4B (nano, efficient 4B, free on AI Studio) ---------------

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="gemma-3n-e4b-it",
        provider="gemini",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Google Gemma 3n E4B Instruct — nano-architecture efficient 4B "
            "model. Optimised for on-device / edge deployment with strong "
            "quality-per-parameter ratio."
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
        best_for=[
            "edge-deployment",
            "fast-classification",
            "routing-decisions",
            "on-device-inference",
        ],
        limits=[
            "free-tier on AI Studio",
            "nano-model (optimised for efficiency)",
        ],
        compliance=[
            "google-ai-studio-tos",
            "free-tier",
            "api-key-required",
            "gemma-open-weight",
            "data-used-for-improvement (free tier)",
        ],
    )
)

# ---- Gemma 3n E2B (nano, efficient 2B, free on AI Studio) ---------------

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="gemma-3n-e2b-it",
        provider="gemini",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=(
            "Google Gemma 3n E2B Instruct — nano-architecture efficient 2B "
            "model. Smallest nano variant, ultra-fast inference."
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
        best_for=[
            "edge-deployment",
            "fast-classification",
            "simple-qa",
            "on-device-inference",
        ],
        limits=[
            "free-tier on AI Studio",
            "nano-model (2B, limited reasoning)",
        ],
        compliance=[
            "google-ai-studio-tos",
            "free-tier",
            "api-key-required",
            "gemma-open-weight",
            "data-used-for-improvement (free tier)",
        ],
    )
)

# ---------------------------------------------------------------------------
# Gemma model pool — load-balanced rotation across all Gemma models
# ---------------------------------------------------------------------------
#
# Each Gemma model on AI Studio has **independent** rate limits:
#   - 14 000 requests/day   (RPD)
#   - 15 000 tokens/minute   (TPM)
#   - ~30 requests/minute    (RPM, varies by model size)
#
# With 6 models that gives us aggregate capacity of:
#   - 84 000 RPD   (6 × 14k)
#   - 90 000 TPM   (6 × 15k)
#
# CRITICAL: if ANY single model exceeds its limit, Google returns 429 for
# ALL Gemma models on the account.  The pool keeps a 15 % safety margin
# on every model to avoid this.
#
# Agents should request model="gemma-pool" to benefit from automatic
# load balancing.  They can still request a specific model directly.
# ---------------------------------------------------------------------------

_GEMMA_POOL_MODELS = [
    "gemma-3-27b-it",
    "gemma-3-12b-it",
    "gemma-3-4b-it",
    "gemma-3-1b-it",
    "gemma-3n-e4b-it",
    "gemma-3n-e2b-it",
]

GEMMA_POOL = ModelPool(
    pool_id="gemma-pool",
    model_ids=_GEMMA_POOL_MODELS,
    rpm_per_model=30,
    rpd_per_model=14_000,
    tpm_per_model=15_000,
    safety_margin=0.15,
    description=(
        "Load-balanced pool across all 6 Gemma models on Google AI Studio. "
        "Aggregate capacity: ~84k RPD, ~90k TPM. Round-robin with per-model "
        "tracking to avoid triggering the global 429 kill-switch."
    ),
)
MODEL_REGISTRY.register_pool(GEMMA_POOL)

# ---------------------------------------------------------------------------
# Thinking chain — multi-model reasoning virtual model
# ---------------------------------------------------------------------------
#
# "gemma-think" is a *virtual* model that triggers a multi-stage reasoning
# pipeline (see ``mas_core.llm_gateway.thinking``).  The client detects the
# "gemma-think" prefix and delegates to ``ThinkingChain``, so no real
# endpoint call is needed.  We register a placeholder ``ModelEntry`` here
# so the model appears in listings and registry queries.
#
# Depth variants: gemma-think (standard), gemma-think/light, gemma-think/deep.
# ---------------------------------------------------------------------------

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="gemma-think",
        provider="gemini",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=GEMINI_PROVIDER.base_url + "/chat/completions",
        supports_tools=False,
        supports_streaming=False,
        max_context_tokens=8_192,
        cost_per_1m_input=0.0,
        cost_per_1m_output=0.0,
        capabilities=ModelCapabilities(
            supports_reasoning=True,
        ),
        best_for=[
            "complex-reasoning",
            "multi-step-analysis",
            "structured-synthesis",
        ],
        limits=[
            "virtual-model (multi-model pipeline, not a single-call model)",
            "no tool-calling or streaming",
            "higher latency (sequential stages)",
        ],
        compliance=[
            "free-tier",
            "gemma-open-weight",
        ],
        extra={
            "virtual": True,
            "description": (
                "Multi-model reasoning pipeline: chains Gemma 4B → 12B → 27B "
                "through decompose → analyse → synthesise stages. "
                "Depths: light (2 stages), standard (3 stages), deep (3 + self-critique). "
                "Use model='gemma-think' or 'gemma-think/light|standard|deep'."
            ),
        },
    )
)
