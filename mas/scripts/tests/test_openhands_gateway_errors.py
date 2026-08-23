"""Offline tests for OpenHands gateway/provider failure semantics."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load(name: str):
    path = Path(__file__).resolve().parents[1] / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_provider_failure_taxonomy_is_sanitized_and_distinct() -> None:
    module = _load("openhands_gateway_errors")
    cases = {
        "missing": module.MISSING_PROVIDER_SECRET,
        "invalid": module.INVALID_PROVIDER_CREDENTIAL,
        "denied": module.PROVIDER_AUTHORIZATION_DENIED,
        "rate": module.PROVIDER_RATE_LIMIT,
        "quota": module.PROVIDER_QUOTA_EXHAUSTED,
        "network": module.PROVIDER_NETWORK_FAILURE,
        "timeout": module.PROVIDER_TIMEOUT,
        "server": module.PROVIDER_SERVER_ERROR,
        "unavailable": module.PROVIDER_MODEL_UNAVAILABLE,
        "not_found": module.PROVIDER_MODEL_NOT_FOUND,
        "litellm_start": module.LITELLM_STARTUP_FAILURE,
        "litellm_health": module.LITELLM_HEALTH_FAILURE,
        "omni_start": module.OMNIROUTE_STARTUP_FAILURE,
        "omni_health": module.OMNIROUTE_HEALTH_FAILURE,
        "omni_health_auth": module.OMNIROUTE_HEALTH_AUTH_CONTRACT_FAILURE,
        "omni_health_app": module.OMNIROUTE_APPLICATION_HEALTH_FAILURE,
        "omni_health_timeout": module.OMNIROUTE_HEALTH_TIMEOUT,
        "route": module.LITELLM_TO_OMNIROUTE_ROUTE_FAILURE,
        "openhands_network": module.OPENHANDS_TO_GATEWAY_NETWORK_FAILURE,
        "gateway_auth": module.MODEL_GATEWAY_AUTH_FAILURE,
        "gateway_transport": module.MODEL_GATEWAY_TRANSPORT_FAILURE,
        "invalid_response": module.MODEL_GATEWAY_RESPONSE_INVALID,
        "execution": module.MODEL_EXECUTION_FAILURE,
    }
    observations = {
        "missing": module.classify_failure(stage="provider_preflight", provider_secret_present=False),
        "invalid": module.classify_failure(stage="provider", http_status=401),
        "denied": module.classify_failure(stage="provider", http_status=403),
        "rate": module.classify_failure(stage="provider", http_status=429, headers={"Retry-After": "2"}),
        "quota": module.classify_failure(stage="provider", error_code="insufficient_quota"),
        "network": module.classify_failure(stage="provider", exception_type="ConnectError"),
        "timeout": module.classify_failure(stage="provider", exception_type="ReadTimeout"),
        "server": module.classify_failure(stage="provider", http_status=503),
        "unavailable": module.classify_failure(stage="provider", error_code="model_unavailable"),
        "not_found": module.classify_failure(stage="provider", http_status=404),
        "litellm_start": module.classify_failure(stage="litellm_startup"),
        "litellm_health": module.classify_failure(stage="litellm_health"),
        "omni_start": module.classify_failure(stage="omniroute_startup"),
        "omni_health": module.classify_failure(stage="omniroute_health"),
        "omni_health_auth": module.classify_failure(stage="omniroute_health", http_status=401),
        "omni_health_app": module.classify_failure(stage="omniroute_health", http_status=503),
        "omni_health_timeout": module.classify_failure(stage="omniroute_health", exception_type="ReadTimeout"),
        "route": module.classify_failure(stage="litellm_to_omniroute"),
        "openhands_network": module.classify_failure(stage="openhands_to_gateway"),
        "gateway_auth": module.classify_failure(stage="gateway_auth"),
        "gateway_transport": module.classify_failure(stage="gateway_auth", exception_type="ConnectError"),
        "invalid_response": module.classify_failure(stage="gateway_response"),
        "execution": module.classify_failure(stage="model_execution"),
    }
    assert {key: value.failure_class for key, value in observations.items()} == cases
    assert all("response_payload_retained" in value.as_dict() for value in observations.values())


def test_provider_configuration_never_persists_secret() -> None:
    module = _load("check_openhands_provider_configuration")
    report = module.check_provider_configuration("super-secret-provider-key")
    assert report["status"] == "PASS"
    assert "super-secret-provider-key" not in str(report)
    blocked = module.check_provider_configuration("")
    assert blocked["status"] == "BLOCKED_MISSING_OPERATOR_SECRET"
    assert blocked["failure"]["failure_class"] == "MISSING_PROVIDER_SECRET"


def test_gateway_authentication_is_not_reported_as_provider_credential_failure() -> None:
    module = _load("openhands_gateway_errors")
    gateway = module.classify_failure(stage="gateway_auth", http_status=401)
    assert gateway.failure_class == module.MODEL_GATEWAY_AUTH_FAILURE
    provider = module.classify_failure(stage="provider", http_status=401)
    assert provider.failure_class == module.INVALID_PROVIDER_CREDENTIAL


def test_gateway_transport_is_not_reported_as_auth_or_provider_failure() -> None:
    module = _load("openhands_gateway_errors")
    failure = module.classify_failure(stage="gateway_auth", exception_type="ConnectError")
    assert failure.failure_class == module.MODEL_GATEWAY_TRANSPORT_FAILURE
    assert failure.retryable is True


def test_internal_gateway_stages_precede_provider_http_heuristics() -> None:
    module = _load("openhands_gateway_errors")
    assert module.classify_failure(stage="omniroute_health", http_status=503).failure_class == module.OMNIROUTE_APPLICATION_HEALTH_FAILURE
    assert module.classify_failure(stage="litellm_health", exception_type="ConnectError").failure_class == module.LITELLM_HEALTH_FAILURE
    assert module.classify_failure(stage="litellm_to_omniroute", http_status=502).failure_class == module.LITELLM_TO_OMNIROUTE_ROUTE_FAILURE
    assert module.classify_failure(stage="openhands_to_gateway", exception_type="ConnectError").failure_class == module.OPENHANDS_TO_GATEWAY_NETWORK_FAILURE
    assert module.classify_failure(stage="gateway_response", http_status=500).failure_class == module.MODEL_GATEWAY_RESPONSE_INVALID


def test_auto_router_failure_classes_are_distinct_from_fixed_model_not_found() -> None:
    module = _load("openhands_gateway_errors")
    assert (
        module.classify_failure(stage="provider", error_code="auto_no_valid_providers").failure_class
        == module.AUTO_ROUTER_NO_VALID_PROVIDERS
    )
    assert (
        module.classify_failure(stage="litellm_to_omniroute", error_code="auto_route_failure").failure_class
        == module.AUTO_ROUTER_ROUTE_FAILURE
    )


def test_baseline_model_unavailability_is_a_provider_availability_class() -> None:
    module = _load("openhands_gateway_errors")
    failure = module.classify_failure(
        stage="provider",
        error_code="baseline_model_unavailable",
    )
    assert failure.failure_class == module.PROVIDER_MODEL_UNAVAILABLE
