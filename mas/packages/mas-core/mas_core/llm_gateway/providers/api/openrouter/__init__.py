"""OpenRouter provider runtime wiring backed by a generated free-model catalog."""

from __future__ import annotations

from typing import Any

from ... import MODEL_REGISTRY
from ...base import ApiStyle, ModelCapabilities, ModelEntry, ProviderConfig
from .generated_free_models import FREE_OPENROUTER_MODELS

OPENROUTER_PROVIDER = ProviderConfig(
    provider_id="openrouter",
    base_url="https://openrouter.ai/api",
    api_key_env_vars=["OPENROUTER_API_KEY"],
    default_api_key="",
    extra_headers={
        "HTTP-Referer": "https://github.com/AIAT",
        "X-Title": "AIAT-MAS",
    },
    description=(
        "OpenRouter - multi-provider LLM gateway (openrouter.ai). "
        "Free-tier models are synced into the registry."
    ),
)
MODEL_REGISTRY.register_provider(OPENROUTER_PROVIDER)

OPENROUTER_CHAT_COMPLETIONS_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
_PREFIX = "openrouter/"
OPENROUTER_FREE_ROUTER_WIRE_MODEL = "openrouter/free"
OPENROUTER_FREE_ROUTER_MODEL_ID = f"{_PREFIX}{OPENROUTER_FREE_ROUTER_WIRE_MODEL}"

_DEFAULT_LIMITS = ["free-tier-rate-limited"]
_DEFAULT_COMPLIANCE = ["openrouter-tos", "free-tier", "api-key-required"]


def _listify(value: Any) -> list[str]:
    """Normalize optional sequence fields from generated metadata."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _wire_model_name(model_id: str) -> str:
    """Return the native OpenRouter model name from a registry or wire ID."""
    if model_id == OPENROUTER_FREE_ROUTER_WIRE_MODEL:
        return model_id
    if model_id.startswith(_PREFIX):
        return model_id[len(_PREFIX):]
    return model_id


def ensure_free_openrouter_model(model_id: str) -> str:
    """Allow only the free router or model IDs explicitly marked ``:free``."""
    wire_model = _wire_model_name(model_id)
    if wire_model == OPENROUTER_FREE_ROUTER_WIRE_MODEL:
        return wire_model
    if wire_model.endswith(":free"):
        return wire_model
    raise ValueError(f"Paid or unapproved OpenRouter model blocked: {model_id}")


def _build_entry(model: dict[str, Any]) -> ModelEntry:
    """Convert one generated catalog record into a ``ModelEntry``."""
    api_model_name = ensure_free_openrouter_model(str(model["api_model_name"]))
    max_context_tokens = int(model.get("max_context_tokens") or 0) or None
    supports_images = bool(model.get("supports_images", False))

    return ModelEntry(
        model_id=str(model["model_id"]),
        provider="openrouter",
        api_style=ApiStyle.CHAT_COMPLETIONS,
        endpoint=OPENROUTER_CHAT_COMPLETIONS_ENDPOINT,
        description=str(model.get("description") or model.get("name") or api_model_name),
        max_context_tokens=max_context_tokens,
        supports_tools=bool(model.get("supports_tools", False)),
        supports_streaming=bool(model.get("supports_streaming", True)),
        cost_per_1m_input=0.0,
        cost_per_1m_output=0.0,
        default_temperature=float(model.get("default_temperature", 0.7)),
        capabilities=ModelCapabilities(
            supports_images=supports_images,
            supports_pdf=bool(model.get("supports_pdf", False)),
            supports_video=bool(model.get("supports_video", False)),
            supports_reasoning=bool(model.get("supports_reasoning", False)),
            image_how=str(
                model.get("image_how")
                or (
                    "image_url in content array (base64 data-URL or HTTPS URL)"
                    if supports_images
                    else "not supported - text-only model"
                )
            ),
            pdf_how=str(model.get("pdf_how") or "extract text and send as message content"),
        ),
        best_for=_listify(model.get("best_for")),
        limits=_listify(model.get("limits")) or list(_DEFAULT_LIMITS),
        compliance=_listify(model.get("compliance")) or list(_DEFAULT_COMPLIANCE),
        extra={
            "api_model_name": api_model_name,
            "name": str(model.get("name") or api_model_name),
            "supported_parameters": _listify(model.get("supported_parameters")),
            "input_modalities": _listify(model.get("input_modalities")),
            "output_modalities": _listify(model.get("output_modalities")),
            "active": bool(model.get("active", True)),
            "seen_syncs": int(model.get("seen_syncs", 0)),
            "missing_syncs": int(model.get("missing_syncs", 0)),
        },
    )


def _register(entry: ModelEntry) -> None:
    """Register an OpenRouter model with a validated free-only wire model."""
    if not entry.model_id.startswith(_PREFIX):
        raise ValueError(
            f"OpenRouter registry model IDs must start with '{_PREFIX}': {entry.model_id}"
        )

    wire_model = entry.extra.get("api_model_name") or _wire_model_name(entry.model_id)
    entry.extra["api_model_name"] = ensure_free_openrouter_model(str(wire_model))
    MODEL_REGISTRY.register(entry)


def _register_generated() -> None:
    """Load the generated free-model catalog into ``MODEL_REGISTRY``."""
    for model in FREE_OPENROUTER_MODELS:
        if not bool(model.get("active", True)):
            continue
        _register(_build_entry(model))


_register_generated()

__all__ = [
    "FREE_OPENROUTER_MODELS",
    "OPENROUTER_CHAT_COMPLETIONS_ENDPOINT",
    "OPENROUTER_FREE_ROUTER_MODEL_ID",
    "OPENROUTER_FREE_ROUTER_WIRE_MODEL",
    "OPENROUTER_PROVIDER",
    "ensure_free_openrouter_model",
]
