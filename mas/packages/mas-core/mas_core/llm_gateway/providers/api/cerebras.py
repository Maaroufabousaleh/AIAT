"""Cerebras Cloud provider with a verified fallback catalog."""

from __future__ import annotations

import logging

import httpx

from .. import MODEL_REGISTRY
from ..base import ApiStyle, ModelCapabilities, ModelEntry, ProviderConfig

logger = logging.getLogger(__name__)

CEREBRAS_PROVIDER = ProviderConfig(
    provider_id="cerebras",
    base_url="https://api.cerebras.ai/v1",
    api_key_env_vars=["CEREBRAS_API_KEY"],
    description="Cerebras Cloud OpenAI-compatible inference API.",
)
MODEL_REGISTRY.register_provider(CEREBRAS_PROVIDER)

_CC = f"{CEREBRAS_PROVIDER.base_url}/chat/completions"
VERIFIED_CEREBRAS_MODEL_IDS = ("gpt-oss-120b", "zai-glm-4.7", "gemma-4-31b")


def _entry(api_model_name: str) -> ModelEntry:
    return ModelEntry(
        model_id=f"cerebras/{api_model_name}",
        provider="cerebras",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=f"Cerebras model {api_model_name}, verified via /v1/models.",
        max_context_tokens=None,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=None,
        cost_per_1m_output=None,
        capabilities=ModelCapabilities(supports_reasoning=True),
        best_for=["reasoning", "code-generation", "tool-calling"],
        limits=["availability-and-pricing-must-be-discovered-at-runtime"],
        compliance=["cerebras-tos", "api-key-required", "dynamic-catalog"],
        extra={"api_model_name": api_model_name, "discovered_via": "/v1/models"},
    )


for _model_id in VERIFIED_CEREBRAS_MODEL_IDS:
    MODEL_REGISTRY.register(_entry(_model_id))


class CerebrasModelScanner:
    """Read the authenticated Cerebras model catalog."""

    def __init__(self, *, timeout_s: float = 20.0) -> None:
        self._timeout_s = timeout_s

    def discover_models(self) -> list[str]:
        key = CEREBRAS_PROVIDER.resolve_api_key()
        if key == "public":
            return []
        try:
            with httpx.Client(timeout=self._timeout_s) as client:
                response = client.get(
                    f"{CEREBRAS_PROVIDER.base_url}/models",
                    headers={"Authorization": f"Bearer {key}"},
                )
                response.raise_for_status()
            return [item["id"] for item in response.json().get("data", []) if item.get("id")]
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            logger.warning("Failed to fetch Cerebras /models: %s", exc)
            return []
