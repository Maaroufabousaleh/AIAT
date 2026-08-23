"""Governed model-routing constants and pure helpers for OpenHands certification.

The worker-visible model id is deliberately stable.  OmniRoute owns provider
selection behind that alias; the certification harness separately freezes one
provider/model baseline so an auto-router success cannot hide a broken direct
provider path.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

AIAT_MODEL_ID = "omniroute-coding"
AUTO_ROUTER_MODEL = "auto/coding"
CERTIFICATION_PROVIDER = "groq"

# Groq retired llama-3.3-70b-versatile on 2026-08-16.  This is the explicit
# governed baseline selected from Groq's published replacement list and the
# pinned OmniRoute v3.8.38 Groq catalog.  It is not a runtime fallback: if the
# provider does not advertise it during a run, the baseline is unavailable and
# the run fails closed with that precise status.
CERTIFICATION_BASELINE_MODEL = "openai/gpt-oss-120b"

CERTIFICATION_PROVIDER_SECRET_NAMES: Mapping[str, str] = {
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
}


def baseline_route(provider: str = CERTIFICATION_PROVIDER) -> str:
    """Return the exact provider-qualified baseline route."""

    return f"{provider}/{CERTIFICATION_BASELINE_MODEL}"


def normalize_model_ids(payload: object) -> list[str]:
    """Extract only scalar model ids from an OmniRoute discovery response."""

    if not isinstance(payload, Mapping):
        return []
    models = payload.get("models")
    if not isinstance(models, list):
        return []
    result: list[str] = []
    for item in models:
        if isinstance(item, Mapping):
            value = item.get("id")
            if isinstance(value, str) and value.strip():
                result.append(value.strip())
    return result


def baseline_discovery_status(
    *,
    provider: str,
    desired_model: str,
    discovery_payload: object,
) -> dict[str, object]:
    """Evaluate a provider model discovery response without selecting a fallback."""

    model_ids = normalize_model_ids(discovery_payload)
    source = discovery_payload.get("source") if isinstance(discovery_payload, Mapping) else None
    source_value = source.strip() if isinstance(source, str) else "unknown"
    source_is_live = source_value in {"api", "upstream"}
    present = desired_model in model_ids
    status = "PASS" if present and source_is_live else "BASELINE_MODEL_UNAVAILABLE"
    return {
        "status": status,
        "provider": provider,
        "requested_model": desired_model,
        "discovery_source": source_value,
        "discovered_model_count": len(model_ids),
        "model_present": present,
        "live_discovery": source_is_live,
    }


def governed_provider_secret_names(providers: Iterable[str]) -> dict[str, str]:
    """Resolve a bounded provider allowlist to explicit secret names.

    Unknown providers are rejected rather than turning arbitrary environment
    variables into credentials.
    """

    normalized = [str(provider).strip().lower() for provider in providers if str(provider).strip()]
    result: dict[str, str] = {}
    for provider in normalized:
        secret_name = CERTIFICATION_PROVIDER_SECRET_NAMES.get(provider)
        if secret_name is None:
            raise ValueError(f"unsupported certification provider: {provider}")
        result[provider] = secret_name
    return result


def auto_router_model_override_allowed(requested_model: object) -> bool:
    """Return whether a caller may supply a model to the governed worker.

    Only the AIAT-owned alias is accepted; provider-specific and auto-router
    model strings remain gateway configuration, never task input.
    """

    return requested_model == AIAT_MODEL_ID


def build_governed_auto_pool(
    connections: Iterable[Mapping[str, object]],
    *,
    allowed_providers: Iterable[str],
) -> list[dict[str, str]]:
    """Build the bounded provider pool used by offline auto-router fixtures.

    This mirrors the governance boundary around OmniRoute's active-connection
    discovery: only explicitly allowlisted providers with a usable credential,
    healthy status, and a concrete model may participate.
    """

    allowed = {str(provider).strip().lower() for provider in allowed_providers if str(provider).strip()}
    governed_provider_secret_names(allowed)
    pool: list[dict[str, str]] = []
    for connection in connections:
        provider = str(connection.get("provider") or "").strip().lower()
        model = str(connection.get("model") or "").strip()
        if provider not in allowed or not model:
            continue
        if connection.get("credential_present") is not True:
            continue
        if connection.get("healthy", True) is not True:
            continue
        pool.append({"provider": provider, "model": model})
    return pool


def simulate_auto_route(
    connections: Iterable[Mapping[str, object]],
    *,
    allowed_providers: Iterable[str],
    failing_providers: Iterable[str] = (),
) -> dict[str, object]:
    """Return deterministic fixture evidence for an auto/coding selection."""

    pool = build_governed_auto_pool(connections, allowed_providers=allowed_providers)
    failures = {str(provider).strip().lower() for provider in failing_providers}
    attempted = [item for item in pool if item["provider"] not in failures]
    if not attempted:
        return {
            "status": "BLOCKED_NO_VALID_PROVIDERS",
            "candidate_count": len(pool),
            "selected": None,
            "fallback_used": bool(pool),
            "credential_values_retained": False,
        }
    return {
        "status": "PASS",
        "candidate_count": len(pool),
        "selected": attempted[0],
        "fallback_used": bool(pool and attempted[0] != pool[0]),
        "credential_values_retained": False,
    }
