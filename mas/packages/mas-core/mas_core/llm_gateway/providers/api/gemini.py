"""Google Gemini provider — free models via the OpenAI-compatible endpoint.

The Gemini API on Google AI Studio exposes an **OpenAI-compatible** layer at
``generativelanguage.googleapis.com/v1beta/openai/``.  This means models
registered here can use the standard ``CHAT_COMPLETIONS`` API style with no
changes to the gateway client.

Authentication uses a Google AI Studio API key sent as ``Bearer`` token
(the OpenAI-compat layer accepts this).  Get a free key at:
https://aistudio.google.com/apikey

Free-tier rate limits (AI Studio, as of Feb 2026):
- gemini-2.5-flash:       10 RPM / 250 k TPM / 500 RPD
- gemini-2.5-flash-lite:  30 RPM / 1 M TPM / 1 500 RPD
- gemini-3-flash-preview: preview, rate-limited
- gemma-3-27b-it:         free via AI Studio, 14 000 RPD
- gemma-3-4b-it:          free via AI Studio

Note: gemini-2.0-flash / gemini-2.0-flash-lite free-tier quota has been set
to 0 by Google (deprecated on free tier as of late 2025).

Native Gemini API
-----------------
Google also offers a **native** REST API at
``generativelanguage.googleapis.com/v1beta/models/{model}:generateContent``
which supports additional features (native PDF/audio/video input, grounding
with Google Search, code execution, etc.).  If those features are needed,
add a ``GEMINI_NATIVE`` API style to the gateway client.  For now the
OpenAI-compat layer covers all MAS requirements.
"""

from __future__ import annotations

from ..base import ApiStyle, ModelCapabilities, ModelEntry, ProviderConfig

# Deferred import — MODEL_REGISTRY is created in the parent __init__.py
# before sub-packages are imported.
from .. import MODEL_REGISTRY

# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

GEMINI_PROVIDER = ProviderConfig(
    provider_id="gemini",
    base_url="https://generativelanguage.googleapis.com/v1beta/openai",
    api_key_env_vars=["GEMINI_API_KEY", "GOOGLE_AI_API_KEY", "GOOGLE_API_KEY"],
    description=(
        "Google Gemini via the OpenAI-compatible endpoint on AI Studio. "
        "Free tier available (rate-limited). Supports vision, tool-calling, "
        "and streaming."
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
