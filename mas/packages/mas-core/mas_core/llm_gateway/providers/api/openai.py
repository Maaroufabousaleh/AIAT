"""OpenAI provider — GPT-4o and related models.

Models registered here use the ``CHAT_COMPLETIONS`` API style against the
official OpenAI endpoint.
"""

from __future__ import annotations

from ..base import ApiStyle, ModelEntry, ProviderConfig

# Deferred import to avoid circular reference — MODEL_REGISTRY lives in the
# parent __init__.py and is created before sub-packages are imported.
from .. import MODEL_REGISTRY

# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

OPENAI_PROVIDER = ProviderConfig(
    provider_id="openai",
    base_url="https://api.openai.com",
    api_key_env_vars=["OPENAI_API_KEY", "LLM_API_KEY"],
    description="OpenAI official API — GPT-4o, o-series, etc.",
)
MODEL_REGISTRY.register_provider(OPENAI_PROVIDER)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="gpt-4o",
        provider="openai",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint="https://api.openai.com/v1/chat/completions",
        description=(
            "OpenAI GPT-4o — fast multimodal flagship. 128 k context, "
            "tool-calling, structured output. Good default for agent work."
        ),
        max_context_tokens=128_000,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=5.0,
        cost_per_1m_output=15.0,
    )
)
