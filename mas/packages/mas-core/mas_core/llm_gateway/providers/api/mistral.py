"""Mistral AI provider and model scanner.

This module does two things:

1. Registers a verified fallback snapshot of chat-capable Mistral models so the
   gateway has useful defaults without making import-time network calls.
2. Exposes ``MistralModelScanner`` so callers can refresh available models from
   ``GET /v1/models`` and optionally verify them with a minimal chat request.

The verified fallback snapshot below was smoke-tested against a local
``MISTRAL_API_KEY`` on March 1, 2026.
"""

from __future__ import annotations

import logging
from typing import Iterable

import httpx

from ..base import (
    ApiStyle,
    ModelCapabilities,
    ModelEntry,
    ModelRegistry,
    ProviderConfig,
)

# Deferred import - MODEL_REGISTRY is created in the parent __init__.py
# before sub-packages are imported.
from .. import MODEL_REGISTRY

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

MISTRAL_PROVIDER = ProviderConfig(
    provider_id="mistral",
    base_url="https://api.mistral.ai/v1",
    api_key_env_vars=["MISTRAL_API_KEY"],
    description=(
        "Mistral AI - frontier open-weight and commercial language models. "
        "Use MistralModelScanner to refresh the registry from /v1/models."
    ),
)
MODEL_REGISTRY.register_provider(MISTRAL_PROVIDER)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

_CHAT_ENDPOINT = f"{MISTRAL_PROVIDER.base_url}/chat/completions"


# ---------------------------------------------------------------------------
# Verified fallback snapshot
# ---------------------------------------------------------------------------

VERIFIED_MISTRAL_CHAT_MODEL_IDS: list[str] = [
    "mistral-medium-2505",
    "mistral-medium-2508",
    "mistral-medium-latest",
    "mistral-medium",
    "mistral-vibe-cli-with-tools",
    "open-mistral-nemo",
    "open-mistral-nemo-2407",
    "mistral-tiny-2407",
    "mistral-tiny-latest",
    "mistral-large-2411",
    "pixtral-large-2411",
    "pixtral-large-latest",
    "mistral-large-pixtral-2411",
    "codestral-2508",
    "codestral-latest",
    "devstral-small-2507",
    "devstral-medium-2507",
    "devstral-2512",
    "mistral-vibe-cli-latest",
    "devstral-medium-latest",
    "devstral-latest",
    "labs-devstral-small-2512",
    "devstral-small-latest",
    "mistral-small-2506",
    "mistral-small-latest",
    "labs-mistral-small-creative",
    "magistral-medium-2509",
    "magistral-medium-latest",
    "magistral-small-2509",
    "magistral-small-latest",
    "voxtral-mini-2507",
    "voxtral-mini-latest",
    "voxtral-small-2507",
    "voxtral-small-latest",
    "mistral-large-2512",
    "mistral-large-latest",
    "ministral-3b-2512",
    "ministral-3b-latest",
    "ministral-8b-2512",
    "ministral-8b-latest",
    "ministral-14b-2512",
    "ministral-14b-latest",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_model_ids(model_ids: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for model_id in model_ids:
        if model_id and model_id not in seen:
            seen.add(model_id)
            result.append(model_id)
    return result


def _is_embedding_model(model_id: str) -> bool:
    low = model_id.lower()
    return model_id == "mistral-embed" or "embed" in low


def _is_moderation_model(model_id: str) -> bool:
    return model_id.lower().startswith("mistral-moderation")


def _is_ocr_model(model_id: str) -> bool:
    return "ocr" in model_id.lower()


def _is_transcription_model(model_id: str) -> bool:
    return "transcribe" in model_id.lower()


def is_chat_capable_model(model_id: str) -> bool:
    """Best-effort filter for chat-completions-capable Mistral models."""
    if _is_embedding_model(model_id):
        return False
    if _is_moderation_model(model_id):
        return False
    if _is_ocr_model(model_id):
        return False
    if _is_transcription_model(model_id):
        return False
    return True


def _capabilities(
    *,
    supports_images: bool = False,
    supports_reasoning: bool = False,
) -> ModelCapabilities:
    return ModelCapabilities(
        supports_images=supports_images,
        supports_pdf=False,
        supports_video=False,
        supports_reasoning=supports_reasoning,
        image_how=(
            "pass image URL or base64 in message content"
            if supports_images
            else "not supported - text or audio only for this gateway path"
        ),
        pdf_how="extract text and send as message content",
    )


def build_mistral_entry(api_model_name: str) -> ModelEntry:
    """Build a chat-completions ``ModelEntry`` for a Mistral model ID."""
    low = api_model_name.lower()
    description = f"Mistral API model discovered via /v1/models ({api_model_name})."
    max_context_tokens: int | None = None
    supports_images = False
    supports_reasoning = False
    cost_in: float | None = None
    cost_out: float | None = None
    best_for = ["general-purpose"]
    limits = ["availability-varies-by-plan"]

    if low.startswith("mistral-small") or low == "labs-mistral-small-creative":
        description = (
            "Mistral Small 3.2 - fast multimodal 24B model for general chat, "
            "tool-calling, and vision."
            if low != "labs-mistral-small-creative"
            else "Mistral Small Creative - Labs variant of Mistral Small tuned "
            "for richer ideation, drafting, and creative writing."
        )
        max_context_tokens = 128_000
        supports_images = True
        cost_in = 0.10 if low != "labs-mistral-small-creative" else None
        cost_out = 0.30 if low != "labs-mistral-small-creative" else None
        best_for = [
            "general-purpose",
            "tool-calling",
            "structured-output",
            "vision",
        ]
    elif low.startswith("mistral-medium") or low == "mistral-medium":
        description = (
            "Mistral Medium 3.x - frontier-class multimodal model for higher "
            "quality chat, tools, and image-grounded tasks."
        )
        max_context_tokens = 128_000
        supports_images = True
        cost_in = 0.40
        cost_out = 2.00
        best_for = [
            "general-purpose",
            "complex-analysis",
            "tool-calling",
            "vision",
        ]
    elif low.startswith("mistral-large-pixtral") or low.startswith("pixtral-large"):
        description = (
            "Pixtral Large - frontier multimodal model for image reasoning, "
            "document understanding, and higher-end vision work."
        )
        max_context_tokens = 128_000
        supports_images = True
        cost_in = 2.00
        cost_out = 6.00
        best_for = [
            "vision",
            "document-understanding",
            "general-purpose",
            "tool-calling",
        ]
    elif low.startswith("mistral-large"):
        description = (
            "Mistral Large - flagship general-purpose multimodal model for "
            "complex tasks, longer context, and stronger answer quality."
        )
        max_context_tokens = 256_000
        supports_images = True
        cost_in = 0.50
        cost_out = 1.50
        best_for = [
            "complex-analysis",
            "general-purpose",
            "tool-calling",
            "vision",
        ]
    elif low.startswith("open-mistral-nemo"):
        description = (
            "Mistral Nemo 12B - open-weight multilingual model with strong "
            "efficiency for lightweight chat and classification."
        )
        max_context_tokens = 128_000
        best_for = [
            "multilingual",
            "general-purpose",
            "classification",
            "simple-qa",
        ]
        limits.append("text-only")
    elif low.startswith("mistral-tiny"):
        description = (
            "Mistral Tiny - legacy lightweight text model for low-latency chat, "
            "routing, and simple automation."
        )
        max_context_tokens = 32_000
        best_for = ["simple-qa", "classification", "routing-decisions"]
        limits.append("text-only")
    elif low.startswith("codestral"):
        description = (
            "Codestral - coding-specialized model for code generation, "
            "fill-in-the-middle, refactors, and test generation."
        )
        max_context_tokens = 128_000
        cost_in = 0.30
        cost_out = 0.90
        best_for = [
            "code-generation",
            "refactoring",
            "test-generation",
            "tool-calling",
        ]
        limits.append("text-only")
    elif low.startswith("devstral-medium"):
        description = (
            "Devstral Medium - enterprise SWE model optimized for repo "
            "exploration, multi-file edits, and code-agent workflows."
        )
        max_context_tokens = 128_000
        cost_in = 0.40
        cost_out = 2.00
        best_for = [
            "software-engineering",
            "code-generation",
            "tool-calling",
            "repo-analysis",
        ]
        limits.append("text-only")
    elif low in {"devstral-2512", "devstral-latest"}:
        description = (
            "Devstral 2 - frontier coding and agent model for repository work, "
            "multi-step engineering tasks, and tool-rich flows."
        )
        max_context_tokens = 128_000
        best_for = [
            "software-engineering",
            "repo-analysis",
            "tool-calling",
            "code-generation",
        ]
        limits.append("text-only")
    elif low.startswith("devstral-small") or low.startswith("labs-devstral-small"):
        description = (
            "Devstral Small - compact coding and agent model for repo "
            "navigation, file edits, and affordable SWE automation."
        )
        max_context_tokens = 128_000
        best_for = [
            "software-engineering",
            "repo-analysis",
            "tool-calling",
            "classification",
        ]
        limits.append("text-only")
    elif low.startswith("mistral-vibe-cli"):
        description = (
            "Mistral Vibe CLI - terminal-first coding assistant model tuned for "
            "project-aware code work and tool-driven agent tasks."
        )
        max_context_tokens = 128_000
        best_for = [
            "software-engineering",
            "tool-calling",
            "terminal-agents",
            "repo-analysis",
        ]
        limits.append("text-only")
    elif low.startswith("magistral-medium"):
        description = (
            "Magistral Medium 1.2 - frontier multimodal reasoning model for "
            "deeper analysis, planning, math, and high-precision tool use."
        )
        max_context_tokens = 128_000
        supports_images = True
        supports_reasoning = True
        cost_in = 2.00
        cost_out = 5.00
        best_for = [
            "reasoning",
            "complex-analysis",
            "math",
            "tool-calling",
        ]
    elif low.startswith("magistral-small"):
        description = (
            "Magistral Small 1.2 - compact multimodal reasoning model for "
            "math, coding, and deliberate step-by-step analysis."
        )
        max_context_tokens = 128_000
        supports_images = True
        supports_reasoning = True
        cost_in = 0.50
        cost_out = 1.50
        best_for = [
            "reasoning",
            "math",
            "code-generation",
            "tool-calling",
        ]
    elif low.startswith("voxtral-small"):
        description = (
            "Voxtral Small - chat-capable speech/audio model for voice-first "
            "assistants, transcription-adjacent tasks, and tool use."
        )
        max_context_tokens = 32_000
        cost_in = 0.10
        cost_out = 0.30
        best_for = [
            "audio-chat",
            "tool-calling",
            "general-purpose",
            "speech-workflows",
        ]
        limits.append("audio-features-not-exposed-by-gateway")
    elif low.startswith("voxtral-mini"):
        description = (
            "Voxtral Mini - lightweight chat-capable speech/audio model for "
            "fast voice workflows and low-cost assistant tasks."
        )
        max_context_tokens = 32_000
        best_for = [
            "audio-chat",
            "simple-qa",
            "tool-calling",
            "speech-workflows",
        ]
        limits.append("audio-features-not-exposed-by-gateway")
    elif low.startswith("ministral-14b"):
        description = (
            "Ministral 3 14B - efficient multimodal edge model with stronger "
            "quality than the smaller Ministral variants."
        )
        max_context_tokens = 256_000
        supports_images = True
        best_for = [
            "edge-deployment",
            "general-purpose",
            "tool-calling",
            "vision",
        ]
    elif low.startswith("ministral-8b"):
        description = (
            "Ministral 3 8B - efficient multimodal model built for local or "
            "edge deployment with strong performance per dollar."
        )
        max_context_tokens = 256_000
        supports_images = True
        cost_in = 0.15
        cost_out = 0.15
        best_for = [
            "edge-deployment",
            "tool-calling",
            "vision",
            "general-purpose",
        ]
    elif low.startswith("ministral-3b"):
        description = (
            "Ministral 3 3B - tiny multimodal edge model for routing, "
            "classification, and fast low-cost assistant workloads."
        )
        max_context_tokens = 256_000
        supports_images = True
        best_for = [
            "edge-deployment",
            "routing-decisions",
            "classification",
            "vision",
        ]

    return ModelEntry(
        model_id=f"mistral/{api_model_name}",
        provider="mistral",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=_CHAT_ENDPOINT,
        description=description,
        max_context_tokens=max_context_tokens,
        supports_tools=True,
        supports_streaming=True,
        cost_per_1m_input=cost_in,
        cost_per_1m_output=cost_out,
        default_temperature=0.7,
        capabilities=_capabilities(
            supports_images=supports_images,
            supports_reasoning=supports_reasoning,
        ),
        best_for=best_for,
        limits=_unique_model_ids(limits),
        compliance=[
            "mistral-tos",
            "api-key-required",
            "dynamic-catalog",
        ],
        extra={
            "api_model_name": api_model_name,
            "discovered_via": "/v1/models",
        },
    )


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


class MistralModelScanner:
    """Discover and register Mistral chat models from ``GET /v1/models``."""

    def __init__(
        self,
        *,
        registry: ModelRegistry | None = None,
        base_url: str | None = None,
        timeout_s: float = 20.0,
    ) -> None:
        self._registry = registry if registry is not None else MODEL_REGISTRY
        self._base_url = (base_url or MISTRAL_PROVIDER.base_url).rstrip("/")
        self._timeout_s = timeout_s

    def _resolve_api_key(self) -> str:
        provider = self._registry.get_provider("mistral") or MISTRAL_PROVIDER
        api_key = provider.resolve_api_key()
        return "" if api_key == "public" else api_key

    def _headers(self) -> dict[str, str] | None:
        api_key = self._resolve_api_key()
        if not api_key:
            return None
        return {"Authorization": f"Bearer {api_key}"}

    def discover_models(self) -> list[str]:
        """Return raw model IDs from ``GET /v1/models``."""
        headers = self._headers()
        if headers is None:
            logger.warning("MISTRAL_API_KEY not set - skipping Mistral model scan")
            return []

        try:
            with httpx.Client(timeout=self._timeout_s) as client:
                response = client.get(f"{self._base_url}/models", headers=headers)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Failed to fetch Mistral /models: %s", exc)
            return []

        payload = response.json()
        raw_ids = [
            item.get("id", "")
            for item in payload.get("data", [])
            if item.get("id")
        ]
        model_ids = _unique_model_ids(raw_ids)
        logger.info("Discovered %d Mistral model(s)", len(model_ids))
        return model_ids

    def filter_chat_models(self, model_ids: Iterable[str]) -> list[str]:
        """Keep only chat-completions candidates."""
        return [
            model_id
            for model_id in _unique_model_ids(model_ids)
            if is_chat_capable_model(model_id)
        ]

    def verify_chat_models(self, model_ids: Iterable[str]) -> list[str]:
        """Smoke-test candidate models with a minimal chat-completions call."""
        headers = self._headers()
        if headers is None:
            logger.warning("MISTRAL_API_KEY not set - cannot verify Mistral models")
            return []

        verified: list[str] = []
        payload_template = {
            "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
            "max_tokens": 8,
            "temperature": 0,
        }

        with httpx.Client(timeout=self._timeout_s) as client:
            for model_id in _unique_model_ids(model_ids):
                payload = dict(payload_template, model=model_id)
                try:
                    response = client.post(
                        f"{self._base_url}/chat/completions",
                        headers=headers,
                        json=payload,
                    )
                except httpx.HTTPError as exc:
                    logger.debug("Verification failed for %s: %s", model_id, exc)
                    continue

                if response.status_code == 200:
                    verified.append(model_id)
                else:
                    logger.debug(
                        "Verification rejected Mistral model %s: status=%s body=%s",
                        model_id,
                        response.status_code,
                        response.text[:200],
                    )

        return verified

    def register_known_chat_models(
        self,
        *,
        model_ids: Iterable[str] | None = None,
    ) -> list[ModelEntry]:
        """Register the local verified fallback snapshot."""
        if self._registry.get_provider("mistral") is None:
            self._registry.register_provider(MISTRAL_PROVIDER)

        entries: list[ModelEntry] = []
        for api_model_name in _unique_model_ids(model_ids or VERIFIED_MISTRAL_CHAT_MODEL_IDS):
            entry = build_mistral_entry(api_model_name)
            self._registry.register(entry)
            entries.append(entry)
        return entries

    def scan_and_register(self, *, verify_chat: bool = False) -> list[ModelEntry]:
        """Discover from ``/v1/models`` and register chat-capable models."""
        discovered = self.discover_models()
        chat_models = self.filter_chat_models(discovered)
        if verify_chat:
            chat_models = self.verify_chat_models(chat_models)

        if not chat_models:
            return []

        if self._registry.get_provider("mistral") is None:
            self._registry.register_provider(MISTRAL_PROVIDER)

        entries: list[ModelEntry] = []
        for api_model_name in chat_models:
            entry = build_mistral_entry(api_model_name)
            self._registry.register(entry)
            entries.append(entry)
        logger.info("Registered %d Mistral chat model(s)", len(entries))
        return entries


# Register the verified fallback snapshot at import time.
MistralModelScanner(registry=MODEL_REGISTRY).register_known_chat_models()
