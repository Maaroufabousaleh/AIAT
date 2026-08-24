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
# LiteLLM's OpenAI-compatible adapter keeps the ``openai/`` provider prefix
# while forwarding ``auto/coding`` as the upstream model id.  Keep this as one
# governed constant so the compose file, evidence, and tests cannot drift.
LITELLM_AUTO_ROUTER_MODEL = "openai/auto/coding"
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

# The live GitHub certification intentionally uses one operator-supplied
# provider today.  The wider list is a recommendation for a future governed
# pool, not an instruction to enumerate the operator's environment or to add
# secrets automatically.
CERTIFICATION_PROVIDER_POOL = (CERTIFICATION_PROVIDER,)
RECOMMENDED_PROVIDER_POOL = ("groq", "gemini", "cerebras")

# OmniRoute v3.8.38 adds built-in no-auth candidates to every virtual
# auto-combo unless they are blocked in settings.  The disposable AIAT
# certification environment must exercise only its explicitly provisioned
# provider connection, so it blocks the exact no-auth ids and aliases present
# in that pinned release.  This is a routing-scope control, not a licence or
# component allow/deny list.
CERTIFICATION_NOAUTH_PROVIDER_BLOCKLIST = (
    "chipotle",
    "ddgw",
    "duckduckgo-web",
    "mcode",
    "mimocode",
    "oc",
    "opencode",
    "pepper",
    "theoldllm",
    "tllm",
    "veo-free",
    "veoaifree-web",
)

# Fixture-only failure labels accepted by ``simulate_auto_route``.  The live
# OmniRoute service owns the actual classification; keeping this list bounded
# prevents an arbitrary provider response or credential value from becoming
# evidence in an offline routing report.
AUTO_ROUTER_FAILURE_CLASSES = frozenset(
    {
        "INVALID_PROVIDER_CREDENTIAL",
        "PROVIDER_AUTHORIZATION_DENIED",
        "PROVIDER_MODEL_UNAVAILABLE",
        "PROVIDER_NETWORK_FAILURE",
        "PROVIDER_RATE_LIMIT",
        "PROVIDER_SERVER_ERROR",
        "PROVIDER_TIMEOUT",
    }
)


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
    expected_connection_id: str | None = None,
) -> dict[str, object]:
    """Evaluate live provider discovery without selecting a fallback.

    A live response must identify the provider and, when supplied, the exact
    connection that was refreshed.  The optional identity requirement keeps
    the pure helper useful for legacy fixture payloads while the live gateway
    provisioning path always supplies ``expected_connection_id``.
    """

    model_ids = normalize_model_ids(discovery_payload)
    source = discovery_payload.get("source") if isinstance(discovery_payload, Mapping) else None
    source_value = source.strip() if isinstance(source, str) else "unknown"
    source_is_live = source_value in {"api", "upstream"}
    present = desired_model in model_ids
    payload_provider = (
        discovery_payload.get("provider") if isinstance(discovery_payload, Mapping) else None
    )
    payload_connection_id = (
        discovery_payload.get("connectionId") if isinstance(discovery_payload, Mapping) else None
    )
    provider_matches = (
        payload_provider == provider
        if expected_connection_id is not None
        else payload_provider in (None, provider)
    )
    connection_matches = (
        expected_connection_id is None or str(payload_connection_id) == expected_connection_id
    )
    status = (
        "PASS"
        if present and source_is_live and provider_matches and connection_matches
        else "BASELINE_MODEL_UNAVAILABLE"
    )
    return {
        "status": status,
        "provider": provider,
        "requested_model": desired_model,
        "discovery_source": source_value,
        "discovered_model_count": len(model_ids),
        "model_present": present,
        "live_discovery": source_is_live,
        "discovery_provider": payload_provider if isinstance(payload_provider, str) else None,
        "expected_connection_id": expected_connection_id,
        "discovery_connection_id": (
            str(payload_connection_id) if payload_connection_id is not None else None
        ),
        "provider_identity_matches": provider_matches,
        "connection_identity_matches": connection_matches,
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


def parse_governed_provider_pool(
    value: str | None,
    *,
    default: Iterable[str] = CERTIFICATION_PROVIDER_POOL,
) -> tuple[str, ...]:
    """Parse an explicit provider allowlist without discovering credentials.

    A caller may provide a comma-separated allowlist (for example
    ``groq,gemini``).  Empty entries, duplicates, and unknown providers are
    rejected.  This function only validates names; it never reads an
    environment variable or returns a credential value.
    """

    if value is None or not value.strip():
        raw = [str(provider) for provider in default]
    else:
        raw = value.split(",")
    providers = tuple(item.strip().lower() for item in raw)
    if not providers or any(not provider for provider in providers):
        raise ValueError("provider pool must contain non-empty provider names")
    if len(set(providers)) != len(providers):
        raise ValueError("provider pool must not contain duplicates")
    governed_provider_secret_names(providers)
    return providers


def provider_pool_spec(value: str | None = None) -> dict[str, object]:
    """Return sanitized provider-pool governance metadata for evidence."""

    providers = parse_governed_provider_pool(value)
    return {
        "providers": list(providers),
        "secret_names": governed_provider_secret_names(providers),
        "allowlist_source": "explicit_governed_configuration",
        "arbitrary_environment_enumeration": False,
        "recommended_future_pool": list(RECOMMENDED_PROVIDER_POOL),
        "credential_values_retained": False,
    }


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

    allowed = {
        str(provider).strip().lower() for provider in allowed_providers if str(provider).strip()
    }
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
    failure_by_provider: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Return deterministic fixture evidence for an auto/coding selection.

    ``failure_by_provider`` is deliberately fixture-only metadata.  It lets
    tests distinguish a rate limit from a generic provider failure without
    accepting raw provider responses or credentials.  The live gateway still
    remains the authority for real provider selection and failure semantics.
    """

    pool = build_governed_auto_pool(connections, allowed_providers=allowed_providers)
    failures = {str(provider).strip().lower() for provider in failing_providers}
    failure_classes: dict[str, str] = {}
    for provider, failure_class in (failure_by_provider or {}).items():
        normalized_provider = str(provider).strip().lower()
        normalized_class = str(failure_class).strip().upper()
        if normalized_class not in AUTO_ROUTER_FAILURE_CLASSES:
            raise ValueError(f"unsupported auto-router fixture failure class: {failure_class}")
        failure_classes[normalized_provider] = normalized_class
        failures.add(normalized_provider)
    attempted = [item for item in pool if item["provider"] not in failures]
    if not attempted:
        return {
            "status": "BLOCKED_NO_VALID_PROVIDERS",
            "candidate_count": len(pool),
            "selected": None,
            "fallback_used": bool(pool),
            "failed_provider_classes": {
                provider: failure_classes[provider]
                for provider in sorted(failure_classes)
                if provider in failures
            },
            "credential_values_retained": False,
        }
    primary_provider = pool[0]["provider"] if pool else None
    return {
        "status": "PASS",
        "candidate_count": len(pool),
        "selected": attempted[0],
        "fallback_used": bool(pool and attempted[0] != pool[0]),
        "fallback_failure_class": failure_classes.get(primary_provider or ""),
        "failed_provider_classes": {
            provider: failure_classes[provider]
            for provider in sorted(failure_classes)
            if provider in failures
        },
        "credential_values_retained": False,
    }
