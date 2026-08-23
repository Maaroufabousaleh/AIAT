"""Sanitized failure taxonomy for the OpenHands certification gateway.

The gateway is disposable and its responses can contain provider-specific
diagnostics.  This module intentionally classifies from status/exception
metadata only; callers must not persist response bodies, credentials, or
model payloads.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

MISSING_PROVIDER_SECRET = "MISSING_PROVIDER_SECRET"
INVALID_PROVIDER_CREDENTIAL = "INVALID_PROVIDER_CREDENTIAL"
PROVIDER_AUTHORIZATION_DENIED = "PROVIDER_AUTHORIZATION_DENIED"
PROVIDER_RATE_LIMIT = "PROVIDER_RATE_LIMIT"
PROVIDER_QUOTA_EXHAUSTED = "PROVIDER_QUOTA_EXHAUSTED"
PROVIDER_NETWORK_FAILURE = "PROVIDER_NETWORK_FAILURE"
PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
PROVIDER_SERVER_ERROR = "PROVIDER_SERVER_ERROR"
PROVIDER_MODEL_UNAVAILABLE = "PROVIDER_MODEL_UNAVAILABLE"
PROVIDER_MODEL_NOT_FOUND = "PROVIDER_MODEL_NOT_FOUND"
AUTO_ROUTER_NO_VALID_PROVIDERS = "AUTO_ROUTER_NO_VALID_PROVIDERS"
AUTO_ROUTER_ROUTE_FAILURE = "AUTO_ROUTER_ROUTE_FAILURE"
LITELLM_STARTUP_FAILURE = "LITELLM_STARTUP_FAILURE"
LITELLM_HEALTH_FAILURE = "LITELLM_HEALTH_FAILURE"
OMNIROUTE_STARTUP_FAILURE = "OMNIROUTE_STARTUP_FAILURE"
OMNIROUTE_HEALTH_FAILURE = "OMNIROUTE_HEALTH_FAILURE"
OMNIROUTE_HEALTH_AUTH_CONTRACT_FAILURE = "OMNIROUTE_HEALTH_AUTH_CONTRACT_FAILURE"
OMNIROUTE_APPLICATION_HEALTH_FAILURE = "OMNIROUTE_APPLICATION_HEALTH_FAILURE"
OMNIROUTE_HEALTH_TIMEOUT = "OMNIROUTE_HEALTH_TIMEOUT"
LITELLM_TO_OMNIROUTE_ROUTE_FAILURE = "LITELLM_TO_OMNIROUTE_ROUTE_FAILURE"
OPENHANDS_TO_GATEWAY_NETWORK_FAILURE = "OPENHANDS_TO_GATEWAY_NETWORK_FAILURE"
MODEL_GATEWAY_AUTH_FAILURE = "MODEL_GATEWAY_AUTH_FAILURE"
MODEL_GATEWAY_TRANSPORT_FAILURE = "MODEL_GATEWAY_TRANSPORT_FAILURE"
MODEL_GATEWAY_RESPONSE_INVALID = "MODEL_GATEWAY_RESPONSE_INVALID"
MODEL_EXECUTION_FAILURE = "MODEL_EXECUTION_FAILURE"

_PROVIDER_CLASSES = {
    MISSING_PROVIDER_SECRET,
    INVALID_PROVIDER_CREDENTIAL,
    PROVIDER_AUTHORIZATION_DENIED,
    PROVIDER_RATE_LIMIT,
    PROVIDER_QUOTA_EXHAUSTED,
    PROVIDER_NETWORK_FAILURE,
    PROVIDER_TIMEOUT,
    PROVIDER_SERVER_ERROR,
    PROVIDER_MODEL_UNAVAILABLE,
    PROVIDER_MODEL_NOT_FOUND,
    AUTO_ROUTER_NO_VALID_PROVIDERS,
    AUTO_ROUTER_ROUTE_FAILURE,
}


@dataclass(frozen=True)
class GatewayFailure:
    """A payload-free, machine-readable failure observation."""

    failure_class: str
    stage: str
    http_status: int | None = None
    retryable: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "failure_class": self.failure_class,
            "stage": self.stage,
            "http_status": self.http_status,
            "retryable": self.retryable,
            "response_payload_retained": False,
        }


def _header(headers: Mapping[str, str] | None, name: str) -> str:
    if not headers:
        return ""
    return str(next((value for key, value in headers.items() if key.lower() == name.lower()), "")).lower()


def classify_failure(
    *,
    stage: str,
    http_status: int | None = None,
    error_code: str | None = None,
    headers: Mapping[str, str] | None = None,
    exception_type: str | None = None,
    provider_secret_present: bool = True,
) -> GatewayFailure:
    """Classify a bounded gateway observation without inspecting response text.

    ``error_code`` is expected to be a short, already-sanitized provider code
    such as ``model_not_found`` or ``insufficient_quota``.  Arbitrary response
    bodies are deliberately not accepted by this API.
    """

    normalized_stage = stage.strip().lower().replace("-", "_")
    code = (error_code or "").strip().lower().replace("-", "_")
    exception = (exception_type or "").strip().lower()

    if normalized_stage in {"provider_config", "provider_preflight"} and not provider_secret_present:
        return GatewayFailure(MISSING_PROVIDER_SECRET, normalized_stage)
    stage_defaults = {
        "litellm_startup": LITELLM_STARTUP_FAILURE,
        "litellm_health": LITELLM_HEALTH_FAILURE,
        "omniroute_startup": OMNIROUTE_STARTUP_FAILURE,
        "omniroute_health": OMNIROUTE_HEALTH_FAILURE,
        "litellm_to_omniroute": LITELLM_TO_OMNIROUTE_ROUTE_FAILURE,
        "openhands_to_gateway": OPENHANDS_TO_GATEWAY_NETWORK_FAILURE,
        "gateway_auth": MODEL_GATEWAY_AUTH_FAILURE,
        "gateway_response": MODEL_GATEWAY_RESPONSE_INVALID,
        "model_execution": MODEL_EXECUTION_FAILURE,
    }
    if normalized_stage in stage_defaults and http_status is None and not exception and not code:
        return GatewayFailure(stage_defaults[normalized_stage], normalized_stage)

    # Authentication at the disposable AIAT gateway is distinct from a
    # provider credential rejection returned after a route is selected.
    if normalized_stage in {"gateway_auth", "model_gateway_auth"}:
        if exception in {"readtimeout", "connecttimeout", "connecterror", "connectionerror", "networkerror", "dnserror"}:
            return GatewayFailure(MODEL_GATEWAY_TRANSPORT_FAILURE, normalized_stage, retryable=True)
        if http_status in {401, 403}:
            return GatewayFailure(MODEL_GATEWAY_AUTH_FAILURE, normalized_stage, http_status)
        if http_status is not None and http_status >= 400:
            return GatewayFailure(MODEL_GATEWAY_RESPONSE_INVALID, normalized_stage, http_status)

    # Preserve harness/gateway stages before applying provider HTTP/exception
    # heuristics.  A failed health probe or an internal route transport error
    # is not evidence of a provider credential or availability failure.
    internal_stage_defaults = {
        "litellm_startup": LITELLM_STARTUP_FAILURE,
        "litellm_health": LITELLM_HEALTH_FAILURE,
        "omniroute_startup": OMNIROUTE_STARTUP_FAILURE,
        "omniroute_health": OMNIROUTE_HEALTH_FAILURE,
        "litellm_to_omniroute": LITELLM_TO_OMNIROUTE_ROUTE_FAILURE,
        "openhands_to_gateway": OPENHANDS_TO_GATEWAY_NETWORK_FAILURE,
        "gateway_response": MODEL_GATEWAY_RESPONSE_INVALID,
    }
    if normalized_stage in internal_stage_defaults:
        if code in {"auto_no_valid_providers", "no_valid_providers"}:
            return GatewayFailure(AUTO_ROUTER_NO_VALID_PROVIDERS, normalized_stage, http_status)
        if code in {"auto_route_failure", "auto_router_failure"}:
            return GatewayFailure(AUTO_ROUTER_ROUTE_FAILURE, normalized_stage, http_status)
        if normalized_stage == "omniroute_health":
            if http_status in {401, 403}:
                return GatewayFailure(OMNIROUTE_HEALTH_AUTH_CONTRACT_FAILURE, normalized_stage, http_status)
            if http_status is not None and http_status >= 500:
                return GatewayFailure(OMNIROUTE_APPLICATION_HEALTH_FAILURE, normalized_stage, http_status, retryable=True)
            if exception in {"readtimeout", "connecttimeout", "connecterror", "connectionerror", "networkerror", "dnserror"}:
                return GatewayFailure(OMNIROUTE_HEALTH_TIMEOUT, normalized_stage, http_status, retryable=True)
        retryable = bool(
            exception in {"readtimeout", "connecttimeout", "connecterror", "connectionerror", "networkerror", "dnserror"}
            or (http_status is not None and http_status >= 500)
        )
        return GatewayFailure(
            internal_stage_defaults[normalized_stage],
            normalized_stage,
            http_status,
            retryable=retryable,
        )

    if "timeout" in exception or exception in {"readtimeout", "connecttimeout"}:
        return GatewayFailure(PROVIDER_TIMEOUT, normalized_stage, retryable=True)
    if exception in {"connecterror", "connectionerror", "networkerror", "dnserror"}:
        return GatewayFailure(PROVIDER_NETWORK_FAILURE, normalized_stage, retryable=True)

    if code in {"insufficient_quota", "quota_exceeded", "billing_hard_limit"}:
        return GatewayFailure(PROVIDER_QUOTA_EXHAUSTED, normalized_stage, http_status)
    if code in {"baseline_model_unavailable", "provider_model_unavailable"}:
        return GatewayFailure(PROVIDER_MODEL_UNAVAILABLE, normalized_stage, http_status)
    if code in {"auto_no_valid_providers", "no_valid_providers"}:
        return GatewayFailure(AUTO_ROUTER_NO_VALID_PROVIDERS, normalized_stage, http_status)
    if code in {"auto_route_failure", "auto_router_failure"}:
        return GatewayFailure(AUTO_ROUTER_ROUTE_FAILURE, normalized_stage, http_status)
    if code in {"model_not_found", "unknown_model", "invalid_model"} or http_status == 404:
        return GatewayFailure(PROVIDER_MODEL_NOT_FOUND, normalized_stage, http_status)
    if code in {"model_unavailable", "service_unavailable", "overloaded"}:
        return GatewayFailure(PROVIDER_MODEL_UNAVAILABLE, normalized_stage, http_status, retryable=True)
    if http_status == 401 or code in {"invalid_api_key", "invalid_credential", "authentication_error"}:
        return GatewayFailure(INVALID_PROVIDER_CREDENTIAL, normalized_stage, http_status)
    if http_status == 403 or code in {"permission_denied", "forbidden", "authorization_denied"}:
        return GatewayFailure(PROVIDER_AUTHORIZATION_DENIED, normalized_stage, http_status)
    if http_status == 429 or code in {"rate_limit", "rate_limit_exceeded", "too_many_requests"}:
        retry_after = _header(headers, "retry-after")
        return GatewayFailure(PROVIDER_RATE_LIMIT, normalized_stage, http_status, retryable=bool(retry_after or http_status == 429))
    if http_status is not None and 500 <= http_status <= 599:
        return GatewayFailure(PROVIDER_SERVER_ERROR, normalized_stage, http_status, retryable=True)
    if http_status is not None and http_status >= 400:
        return GatewayFailure(MODEL_EXECUTION_FAILURE, normalized_stage, http_status)
    if normalized_stage in stage_defaults:
        return GatewayFailure(stage_defaults[normalized_stage], normalized_stage, http_status)
    return GatewayFailure(MODEL_EXECUTION_FAILURE, normalized_stage, http_status)


def is_provider_failure(failure_class: str) -> bool:
    """Return whether a class is provider-facing rather than harness-facing."""

    return failure_class in _PROVIDER_CLASSES
