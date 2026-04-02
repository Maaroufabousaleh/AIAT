"""Sync the generated OpenRouter free-model catalog from OpenRouter's models API."""

from __future__ import annotations

import importlib.util
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from pprint import pformat
from typing import Any
from urllib.request import Request, urlopen

MODELS_URL = "https://openrouter.ai/api/v1/models"
OUT_FILE = Path(__file__).with_name("generated_free_models.py")
USER_AGENT = "AIAT-OpenRouter-Sync/1.0"
MAX_MISSING_SYNCS = 3
OPENROUTER_FREE_ROUTER_WIRE_MODEL = "openrouter/free"

_DEFAULT_COMPLIANCE = ["openrouter-tos", "free-tier", "api-key-required"]
_DEFAULT_LIMITS = ["free-tier-rate-limited"]
_CURATED_FIELDS = (
    "best_for",
    "compliance",
    "default_temperature",
    "image_how",
    "limits",
    "pdf_how",
)


def _as_list(value: Any) -> list[str]:
    """Normalize a possibly missing list-valued field."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _price_is_zero(value: Any) -> bool:
    """Return True when a pricing field is effectively zero."""
    if value in (None, ""):
        return True
    try:
        return Decimal(str(value)) == 0
    except (InvalidOperation, ValueError):
        return False


def fetch_catalog() -> list[dict[str, Any]]:
    """Fetch the normalized OpenRouter models catalog."""
    request = Request(MODELS_URL, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return list(payload.get("data") or [])


def load_previous_models() -> list[dict[str, Any]]:
    """Load the previously generated free-model catalog if it exists."""
    if not OUT_FILE.exists():
        return []

    spec = importlib.util.spec_from_file_location("openrouter_generated_free_models", OUT_FILE)
    if spec is None or spec.loader is None:
        return []

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return list(getattr(module, "FREE_OPENROUTER_MODELS", []))


def is_free_model(model: dict[str, Any]) -> bool:
    """Apply the free-only admission policy for OpenRouter models."""
    model_id = str(model.get("id") or "")
    pricing = model.get("pricing") or {}

    if model_id == OPENROUTER_FREE_ROUTER_WIRE_MODEL:
        return True
    if not model_id.endswith(":free"):
        return False

    return (
        _price_is_zero(pricing.get("prompt"))
        and _price_is_zero(pricing.get("completion"))
        and _price_is_zero(pricing.get("request"))
    )


def supports(param: str, model: dict[str, Any]) -> bool:
    """Return whether the catalog marks a model as supporting a parameter."""
    supported_parameters = _as_list(model.get("supported_parameters"))
    return param in supported_parameters


def map_model(model: dict[str, Any]) -> dict[str, Any]:
    """Normalize an OpenRouter catalog record to the generated file shape."""
    api_model_name = str(model["id"])
    architecture = model.get("architecture") or {}
    input_modalities = _as_list(architecture.get("input_modalities"))
    output_modalities = _as_list(architecture.get("output_modalities")) or ["text"]
    supported_parameters = _as_list(model.get("supported_parameters"))
    description = str(model.get("description") or model.get("name") or api_model_name)
    description_lc = description.lower()

    supports_images = "image" in input_modalities
    supports_video = "video" in input_modalities
    supports_pdf = any("pdf" in modality for modality in input_modalities)
    supports_tools = supports("tools", model)
    supports_reasoning = (
        "reasoning" in supported_parameters
        or "thinking" in description_lc
        or "reasoning" in description_lc
    )

    best_for = ["safe-default", "free-only-routing"] if api_model_name == OPENROUTER_FREE_ROUTER_WIRE_MODEL else []
    image_how = (
        "depends on the selected free backend"
        if api_model_name == OPENROUTER_FREE_ROUTER_WIRE_MODEL
        else (
            "image_url in content array (base64 data-URL or HTTPS URL)"
            if supports_images
            else "not supported - text-only model"
        )
    )

    return {
        "active": True,
        "api_model_name": api_model_name,
        "best_for": best_for,
        "compliance": list(_DEFAULT_COMPLIANCE),
        "default_temperature": 0.7,
        "description": description,
        "image_how": image_how,
        "input_modalities": input_modalities or ["text"],
        "limits": list(_DEFAULT_LIMITS),
        "max_context_tokens": int(model.get("context_length") or 0),
        "missing_syncs": 0,
        "model_id": f"openrouter/{api_model_name}",
        "name": str(model.get("name") or api_model_name),
        "output_modalities": output_modalities,
        "pdf_how": "extract text and send as message content",
        "seen_syncs": 1,
        "supported_parameters": supported_parameters,
        "supports_images": supports_images,
        "supports_pdf": supports_pdf,
        "supports_reasoning": supports_reasoning,
        "supports_streaming": True,
        "supports_tools": supports_tools,
        "supports_video": supports_video,
    }


def merge_with_previous(
    previous: list[dict[str, Any]],
    current: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Preserve curated fields and soft-disable models missing from the latest sync."""
    prev_by_api_name = {str(model["api_model_name"]): dict(model) for model in previous}
    curr_by_api_name = {str(model["api_model_name"]): dict(model) for model in current}
    merged: list[dict[str, Any]] = []

    for api_model_name, model in curr_by_api_name.items():
        old = prev_by_api_name.get(api_model_name, {})
        for field in _CURATED_FIELDS:
            if old.get(field):
                model[field] = old[field]
        model["active"] = True
        model["seen_syncs"] = int(old.get("seen_syncs", 0)) + 1
        model["missing_syncs"] = 0
        merged.append(model)

    for api_model_name, old in prev_by_api_name.items():
        if api_model_name in curr_by_api_name:
            continue
        retained = dict(old)
        retained["active"] = False
        retained["missing_syncs"] = int(old.get("missing_syncs", 0)) + 1
        if retained["missing_syncs"] < MAX_MISSING_SYNCS:
            merged.append(retained)

    merged.sort(key=lambda model: str(model["api_model_name"]))
    return merged


def render_python(models: list[dict[str, Any]]) -> str:
    """Render the generated catalog as a deterministic Python module."""
    body = pformat(models, sort_dicts=True, width=100)
    return (
        "# AUTO-GENERATED. DO NOT EDIT.\n"
        "# Generated by sync_free_models.py\n\n"
        f"FREE_OPENROUTER_MODELS = {body}\n"
    )


def main() -> None:
    """Fetch the latest OpenRouter catalog and rewrite the generated file."""
    catalog = fetch_catalog()
    current = [map_model(model) for model in catalog if is_free_model(model)]
    merged = merge_with_previous(load_previous_models(), current)
    OUT_FILE.write_text(render_python(merged), encoding="utf-8")
    active_count = sum(1 for model in merged if model.get("active", True))
    print(f"Wrote {active_count} active OpenRouter free models to {OUT_FILE}")


if __name__ == "__main__":
    main()
