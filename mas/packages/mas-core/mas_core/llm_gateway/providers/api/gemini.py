"""Google AI Studio provider and live model discovery.

The checked-in entries are a small, verified fallback catalog.  Google retires
preview and open-weight model IDs regularly, so callers that need current
availability should use :class:`GeminiModelScanner` rather than treating the
fallback catalog as provider truth.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

from .. import MODEL_REGISTRY
from ..base import ApiStyle, ModelCapabilities, ModelEntry, ModelPool, ModelRegistry, ProviderConfig

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

GEMINI_PROVIDER = ProviderConfig(
    provider_id="gemini",
    base_url="https://generativelanguage.googleapis.com/v1beta/openai",
    api_key_env_vars=["GEMINI_API_KEY", "GOOGLE_AI_API_KEY", "GOOGLE_API_KEY"],
    description="Google AI Studio via its OpenAI-compatible API.",
)
MODEL_REGISTRY.register_provider(GEMINI_PROVIDER)

_CC = f"{GEMINI_PROVIDER.base_url}/chat/completions"
_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"


def _build_entry(model_id: str) -> ModelEntry:
    """Build a conservative catalog entry for a discovered Gemini model."""
    is_gemma = model_id.startswith("gemma-")
    is_flash = "flash" in model_id
    return ModelEntry(
        model_id=model_id,
        provider="gemini",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        description=f"Google AI Studio model discovered as {model_id}.",
        max_context_tokens=None,
        supports_tools=not is_gemma,
        supports_streaming=True,
        cost_per_1m_input=None,
        cost_per_1m_output=None,
        default_temperature=0.7,
        capabilities=ModelCapabilities(
            supports_images=True,
            supports_reasoning=is_gemma or not is_flash,
            supports_search_grounding=not is_gemma,
            supports_url_context=not is_gemma,
            image_how="image_url in content array (base64 data-URL or HTTPS URL)",
            pdf_how="extract text and send as message content",
        ),
        best_for=(
            ["reasoning", "code-generation", "long-context"]
            if is_gemma
            else ["general-purpose", "tool-calling", "search-grounding"]
        ),
        limits=["availability-and-pricing-must-be-discovered-at-runtime"],
        compliance=["google-ai-studio-tos", "api-key-required", "dynamic-catalog"],
        extra={"api_model_name": model_id, "discovered_via": "/v1beta/models"},
    )


# Verified against ListModels and a real generateContent request on 2026-07-03.
VERIFIED_GEMINI_MODEL_IDS = (
    "gemini-2.5-flash",
    "gemini-3.1-flash-lite-preview",
    "gemini-3.1-flash-lite",
    "gemma-4-31b-it",
)

for _model_id in VERIFIED_GEMINI_MODEL_IDS:
    MODEL_REGISTRY.register(_build_entry(_model_id))


# Compatibility alias for callers that request the historical Gemma pool.  Only
# models that pass a real generation request belong here; ListModels alone is
# insufficient because Google currently lists a 26B model that returns 500.
GEMMA_POOL = ModelPool(
    pool_id="gemma-pool",
    model_ids=["gemma-4-31b-it"],
    rpm_per_model=15,
    rpd_per_model=500,
    tpm_per_model=250_000,
    safety_margin=0.15,
    description="Compatibility alias for the currently working Gemma model.",
)
MODEL_REGISTRY.register_pool(GEMMA_POOL)


# Virtual reasoning pipeline.  The client dispatches this to ThinkingChain.
MODEL_REGISTRY.register(
    ModelEntry(
        model_id="gemma-think",
        provider="gemini",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CC,
        supports_tools=False,
        supports_streaming=False,
        max_context_tokens=None,
        cost_per_1m_input=None,
        cost_per_1m_output=None,
        capabilities=ModelCapabilities(supports_reasoning=True),
        best_for=["complex-reasoning", "multi-step-analysis", "structured-synthesis"],
        limits=["virtual-model", "sequential-provider-calls", "no-tool-calling"],
        compliance=["google-ai-studio-tos", "api-key-required"],
        extra={"virtual": True},
    )
)


class GeminiModelScanner:
    """Discover models that currently support ``generateContent``."""

    def __init__(
        self,
        *,
        registry: ModelRegistry | None = None,
        models_url: str = _MODELS_URL,
        timeout_s: float = 20.0,
    ) -> None:
        self._registry = registry if registry is not None else MODEL_REGISTRY
        self._models_url = models_url
        self._timeout_s = timeout_s

    def _api_key(self) -> str:
        provider = self._registry.get_provider("gemini") or GEMINI_PROVIDER
        key = provider.resolve_api_key()
        return "" if key == "public" else key

    def discover_models(self) -> list[str]:
        """Return current model IDs accepted by ``generateContent``."""
        key = self._api_key()
        if not key:
            logger.warning("GEMINI_API_KEY not set - skipping Gemini model scan")
            return []
        try:
            with httpx.Client(timeout=self._timeout_s) as client:
                response = client.get(self._models_url, headers={"x-goog-api-key": key})
                response.raise_for_status()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Failed to fetch Gemini /models: %s", exc)
            return []

        discovered: list[str] = []
        for item in response.json().get("models", []):
            if "generateContent" not in item.get("supportedGenerationMethods", []):
                continue
            name = str(item.get("name", ""))
            model_id = name.removeprefix("models/")
            if model_id and model_id not in discovered:
                discovered.append(model_id)
        return discovered

    def available_registered_models(self) -> list[str]:
        """Return concrete AIAT Gemini entries still exposed by Google."""
        discovered = set(self.discover_models())
        return [
            entry.model_id
            for entry in self._registry.list_models("gemini")
            if not entry.extra.get("virtual") and entry.model_id in discovered
        ]

    def scan_and_register(self, *, include: Iterable[str] | None = None) -> list[ModelEntry]:
        """Register discovered models, optionally restricted to an allowlist."""
        model_ids = self.discover_models()
        if include is not None:
            allowed = set(include)
            model_ids = [model_id for model_id in model_ids if model_id in allowed]
        entries = [_build_entry(model_id) for model_id in model_ids]
        for entry in entries:
            self._registry.register(entry)
        return entries
