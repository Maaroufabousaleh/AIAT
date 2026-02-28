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

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="big-pickle",
        provider="zen",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint="https://opencode.ai/zen/v1/chat/completions",
        description=(
            "Zen Big-Pickle — free chat-completions model on opencode.ai. "
            "General-purpose conversational model suitable for drafting, "
            "summarisation, and advisory tasks. No API key needed."
        ),
        supports_tools=False,
        supports_streaming=False,
        cost_per_1m_input=0.0,
        cost_per_1m_output=0.0,
        default_temperature=0.7,
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
        supports_tools=False,
        supports_streaming=False,
        cost_per_1m_input=0.0,
        cost_per_1m_output=0.0,
        default_temperature=0.7,
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
            "Uses the newer input/output format rather than chat-completions. "
            "Suitable for structured analysis and review tasks. No key needed."
        ),
        supports_tools=False,
        supports_streaming=False,
        cost_per_1m_input=0.0,
        cost_per_1m_output=0.0,
        default_temperature=0.7,
    )
)
