"""AIAT's internal LiteLLM/OmniRoute model aliases.

These aliases are gateway identities, not direct third-party provider models.
They are registered so governed profile reconciliation can bind the checked-in
OpenCode profile to the same route name used by Compose's LiteLLM config.
"""

from __future__ import annotations

import os

from .. import MODEL_REGISTRY
from ..base import ApiStyle, ModelCapabilities, ModelEntry, ProviderConfig

_gateway_url = os.environ.get("LLM_GATEWAY_URL", "http://litellm:4000").rstrip("/")
LITELLM_PROVIDER = ProviderConfig(
    provider_id="litellm",
    base_url=f"{_gateway_url}/v1",
    api_key_env_vars=["LITELLM_MASTER_KEY", "LLM_API_KEY", "MAS_API_KEY"],
    description="AIAT internal LiteLLM gateway forwarding stable aliases to OmniRoute.",
)
MODEL_REGISTRY.register_provider(LITELLM_PROVIDER)

MODEL_REGISTRY.register(
    ModelEntry(
        model_id="omniroute-coding",
        provider="litellm",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=f"{LITELLM_PROVIDER.base_url}/chat/completions",
        description="AIAT internal coding alias routed through LiteLLM and OmniRoute.",
        supports_tools=True,
        supports_streaming=True,
        capabilities=ModelCapabilities(supports_reasoning=True),
        best_for=["code-generation", "code-review", "testing"],
        limits=["requires-running-litellm-and-omniroute"],
        compliance=["internal-gateway-alias"],
        extra={"api_model_name": "omniroute-coding", "route_alias": "omniroute-coding"},
    )
)
