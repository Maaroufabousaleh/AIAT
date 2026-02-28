"""Zen (opencode.ai) provider — free-tier models.

Models registered here use both ``CHAT_COMPLETIONS`` and ``RESPONSES`` API
styles against the Zen endpoint on opencode.ai.  No API key required.
"""

from __future__ import annotations

from ..base import ApiStyle, ModelEntry, ProviderConfig

# Deferred import — MODEL_REGISTRY is created in the parent __init__.py
# before sub-packages are imported.
from .. import MODEL_REGISTRY

# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

ZEN_PROVIDER = ProviderConfig(
    provider_id="zen",
    base_url="https://opencode.ai/zen/v1",
    api_key_env_vars=["OPENCODE_API_KEY", "OPENCODE_ZEN_API_KEY", "ZEN_API_KEY"],
    default_api_key="public",
    extra_headers={
        "HTTP-Referer": "https://opencode.ai/",
        "X-Title": "opencode",
    },
    description=(
        "Zen free-tier models hosted on opencode.ai. "
        "No API key required (defaults to 'public'). "
        "Supports both chat-completions and responses API styles."
    ),
)
MODEL_REGISTRY.register_provider(ZEN_PROVIDER)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

from ..base import ModelCapabilities

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="big-pickle",
        provider="zen",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint="https://opencode.ai/zen/v1/chat/completions",
        description=(
            "Zen Big-Pickle — free chat-completions model on opencode.ai. "
            "General-purpose conversational model with reasoning, suitable for "
            "drafting, summarisation, and advisory tasks. No API key needed."
        ),
        max_context_tokens=200_000,
        supports_tools=False,
        supports_streaming=False,
        cost_per_1m_input=0.0,
        cost_per_1m_output=0.0,
        default_temperature=0.7,
        capabilities=ModelCapabilities(
            supports_images=False,
            supports_pdf=False,
            supports_video=False,
            supports_reasoning=True,
            image_how="not supported — text-only endpoint",
            pdf_how="extract text and send as message content",
        ),
        best_for=[
            "drafting",
            "summarisation",
            "advisory-responses",
            "general-conversation",
            "reasoning",
        ],
        limits=[
            "text-only",
            "no-tool-calling",
            "no-streaming",
            "no-vision",
        ],
        compliance=[
            "free-tier",
            "no-api-key-required",
            "public-api",
        ],
    )
)

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="minimax-m2.5-free",
        provider="zen",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint="https://opencode.ai/zen/v1/chat/completions",
        description=(
            "MiniMax M2.5 Free — compact, fast chat-completions model via "
            "Zen. Good for lightweight reasoning, code review comments, and "
            "quick advisory responses. Free, no API key needed."
        ),
        max_context_tokens=204_800,
        supports_tools=False,
        supports_streaming=False,
        cost_per_1m_input=0.0,
        cost_per_1m_output=0.0,
        default_temperature=0.7,
        capabilities=ModelCapabilities(
            supports_images=False,
            supports_pdf=False,
            supports_video=False,
            supports_reasoning=True,
            image_how="not supported — text-only endpoint",
            pdf_how="extract text and send as message content",
        ),
        best_for=[
            "lightweight-reasoning",
            "code-review-comments",
            "quick-advisory",
            "fast-iteration",
        ],
        limits=[
            "text-only",
            "no-tool-calling",
            "no-streaming",
            "no-vision",
            "compact-model",
        ],
        compliance=[
            "free-tier",
            "no-api-key-required",
            "public-api",
        ],
    )
)

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="gpt-5-nano",
        provider="zen",
        api_style=ApiStyle.RESPONSES,
        endpoint="https://opencode.ai/zen/v1/responses",
        description=(
            "GPT-5 Nano — free Responses-API model on opencode.ai/zen. "
            "Supports text + image input, reasoning, and the newer input/output "
            "format. Best path for PDF/file inputs when upstream is stable. No key needed."
        ),
        max_context_tokens=400_000,
        supports_tools=False,
        supports_streaming=False,
        cost_per_1m_input=0.0,
        cost_per_1m_output=0.0,
        default_temperature=0.7,
        capabilities=ModelCapabilities(
            supports_images=True,
            supports_pdf=True,
            supports_video=False,
            supports_reasoning=True,
            image_how="image_url in content array (base64 or URL) via Responses API",
            pdf_how="file input (base64/file-id/URL) in Responses API input block",
        ),
        best_for=[
            "structured-analysis",
            "document-review",
            "pdf-ingestion",
            "report-generation",
            "reasoning",
            "multimodal-vision",
        ],
        limits=[
            "upstream-currently-unstable (Zen 500 on input_tokens)",
            "no-tool-calling",
            "no-streaming",
        ],
        compliance=[
            "free-tier",
            "no-api-key-required",
            "public-api",
        ],
    )
)

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="trinity-large-preview-free",
        provider="zen",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint="https://opencode.ai/zen/v1/chat/completions",
        description=(
            "Trinity Large Preview — free chat-completions model on opencode.ai/zen. "
            "Text-only, no reasoning. Good for bulk text processing. No key needed."
        ),
        max_context_tokens=131_072,
        supports_tools=False,
        supports_streaming=False,
        cost_per_1m_input=0.0,
        cost_per_1m_output=0.0,
        default_temperature=0.7,
        capabilities=ModelCapabilities(
            supports_images=False,
            supports_pdf=False,
            supports_video=False,
            supports_reasoning=False,
            image_how="not supported — text-only endpoint",
            pdf_how="extract text and send as message content",
        ),
        best_for=[
            "bulk-text-processing",
            "simple-generation",
            "translation",
            "reformatting",
        ],
        limits=[
            "text-only",
            "no-reasoning",
            "no-tool-calling",
            "no-streaming",
            "no-vision",
            "preview-model",
        ],
        compliance=[
            "free-tier",
            "no-api-key-required",
            "public-api",
        ],
    )
)
