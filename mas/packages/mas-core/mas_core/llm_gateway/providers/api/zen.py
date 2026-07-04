"""OpenCode Zen provider with current free-model discovery."""

from __future__ import annotations

import logging

import httpx

from .. import MODEL_REGISTRY
from ..base import ApiStyle, ModelCapabilities, ModelEntry, ProviderConfig

logger = logging.getLogger(__name__)

ZEN_PROVIDER = ProviderConfig(
    provider_id="zen",
    base_url="https://opencode.ai/zen/v1",
    api_key_env_vars=["OPENCODE_API_KEY", "OPENCODE_ZEN_API_KEY", "ZEN_API_KEY"],
    default_api_key="",
    extra_headers={"HTTP-Referer": "https://opencode.ai/", "X-Title": "opencode"},
    description="OpenCode Zen OpenAI-compatible model API.",
)
MODEL_REGISTRY.register_provider(ZEN_PROVIDER)

_CC = f"{ZEN_PROVIDER.base_url}/chat/completions"
VERIFIED_ZEN_FREE_MODEL_IDS = (
    "big-pickle",
    "deepseek-v4-flash-free",
    "mimo-v2.5-free",
    "north-mini-code-free",
)


def _entry(model_id: str) -> ModelEntry:
    return ModelEntry(
        model_id=model_id,
        provider="zen",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=f"OpenCode Zen free model {model_id}.",
        max_context_tokens=None,
        supports_tools=False,
        supports_streaming=False,
        cost_per_1m_input=0.0,
        cost_per_1m_output=0.0,
        capabilities=ModelCapabilities(supports_reasoning=True),
        best_for=["general-purpose", "reasoning"],
        limits=["free-tier-rate-limited", "capabilities-must-be-live-verified"],
        compliance=["opencode-zen-tos", "api-key-required", "free-tier"],
        extra={"api_model_name": model_id, "discovered_via": "/v1/models"},
    )


for _model_id in VERIFIED_ZEN_FREE_MODEL_IDS:
    MODEL_REGISTRY.register(_entry(_model_id))


class ZenModelScanner:
    """Read Zen's authenticated model list and select free entries."""

    def __init__(self, *, timeout_s: float = 20.0) -> None:
        self._timeout_s = timeout_s

    def discover_models(self) -> list[str]:
        key = ZEN_PROVIDER.resolve_api_key()
        try:
            with httpx.Client(timeout=self._timeout_s) as client:
                response = client.get(
                    f"{ZEN_PROVIDER.base_url}/models",
                    headers={"Authorization": f"Bearer {key}"},
                )
                response.raise_for_status()
            return [item["id"] for item in response.json().get("data", []) if item.get("id")]
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            logger.warning("Failed to fetch Zen /models: %s", exc)
            return []

    def discover_free_models(self) -> list[str]:
        """Return explicitly free IDs plus Zen's free-router alias."""
        return [
            model_id
            for model_id in self.discover_models()
            if model_id == "big-pickle" or model_id.endswith("-free")
        ]
