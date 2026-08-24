"""Create and verify the one disposable OmniRoute provider route.

Only the selected provider is sent to OmniRoute.  The provider credential is
used in-memory for the request and is never printed or written to evidence.
The management key is a run-scoped AIAT gateway credential, not a provider
credential and not a persistent GitHub secret.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import httpx

try:
    from openhands_gateway_errors import classify_failure
except ImportError:  # pragma: no cover - package invocation fallback
    from scripts.openhands_gateway_errors import classify_failure  # type: ignore

try:
    from openhands_model_routing import (
        AIAT_MODEL_ID,
        CERTIFICATION_BASELINE_MODEL,
        CERTIFICATION_NOAUTH_PROVIDER_BLOCKLIST,
        CERTIFICATION_PROVIDER,
        baseline_discovery_status,
        provider_pool_spec,
    )
except ImportError:  # pragma: no cover - package invocation fallback
    from scripts.openhands_model_routing import (  # type: ignore
        AIAT_MODEL_ID,
        CERTIFICATION_BASELINE_MODEL,
        CERTIFICATION_NOAUTH_PROVIDER_BLOCKLIST,
        CERTIFICATION_PROVIDER,
        baseline_discovery_status,
        provider_pool_spec,
    )

SCHEMA = "aiat.openhands-certification-gateway-provisioning.v1"
PROVIDER = CERTIFICATION_PROVIDER
PROVIDER_NAME = "AIAT OpenHands certification Groq"
PROVIDER_MODEL = CERTIFICATION_BASELINE_MODEL
EXPECTED_ROUTE = f"{PROVIDER}/{PROVIDER_MODEL}"


class GatewayProvisioningError(RuntimeError):
    """A disposable provider route could not be created or verified."""

    def __init__(
        self,
        reason: str,
        *,
        stage: str = "omniroute_health",
        http_status: int | None = None,
        exception_type: str | None = None,
    ) -> None:
        super().__init__(reason)
        self.stage = stage
        self.http_status = http_status
        self.exception_type = exception_type


def _json(
    response: httpx.Response,
    *,
    expected: set[int] | None = None,
    stage: str = "omniroute_health",
) -> Any:
    if expected is not None and response.status_code not in expected:
        raise GatewayProvisioningError(
            f"omniroute_http_{response.status_code}",
            stage=stage,
            http_status=response.status_code,
        )
    if response.status_code >= 400:
        raise GatewayProvisioningError(
            f"omniroute_http_{response.status_code}",
            stage=stage,
            http_status=response.status_code,
        )
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise GatewayProvisioningError("omniroute_invalid_json") from exc


def _request(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    stage: str,
    **kwargs: Any,
) -> httpx.Response:
    """Perform one bounded control/provider request with an explicit stage."""

    try:
        return client.request(method, path, **kwargs)
    except httpx.TimeoutException as exc:
        raise GatewayProvisioningError(
            "omniroute_request_timeout",
            stage=stage,
            exception_type="ReadTimeout",
        ) from exc
    except httpx.TransportError as exc:
        raise GatewayProvisioningError(
            "omniroute_request_transport_error",
            stage=stage,
            exception_type="ConnectError",
        ) from exc


def _connections(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict) or not isinstance(value.get("connections"), list):
        raise GatewayProvisioningError("omniroute_provider_readback_not_a_list")
    connections = value["connections"]
    if any(not isinstance(item, dict) for item in connections):
        raise GatewayProvisioningError("omniroute_provider_readback_contains_invalid_entry")
    return list(connections)


def _settings(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GatewayProvisioningError(
            "omniroute_settings_readback_not_an_object", stage="gateway_auth"
        )
    return value


def _configure_auto_router_scope(client: httpx.Client) -> dict[str, object]:
    """Restrict OmniRoute auto/coding to explicitly provisioned connections.

    OmniRoute v3.8.38 adds built-in no-auth providers to virtual auto-combos
    unless their ids/aliases are blocked in settings.  The certification
    container is disposable, so this authenticated settings update is scoped to
    the run and is verified by a redacted readback before any model request.
    """

    current = _settings(
        _json(
            _request(client, "GET", "/api/settings", stage="gateway_auth"),
            expected={200},
            stage="gateway_auth",
        )
    )
    raw_blocked = current.get("blockedProviders", [])
    if raw_blocked is None:
        raw_blocked = []
    if not isinstance(raw_blocked, list) or any(not isinstance(item, str) for item in raw_blocked):
        raise GatewayProvisioningError(
            "omniroute_blocked_provider_settings_invalid", stage="gateway_auth"
        )
    blocked = sorted(
        {item.strip() for item in raw_blocked if item.strip()}
        | set(CERTIFICATION_NOAUTH_PROVIDER_BLOCKLIST)
    )
    _json(
        _request(
            client,
            "PATCH",
            "/api/settings",
            stage="gateway_auth",
            json={"autoRoutingEnabled": True, "blockedProviders": blocked},
        ),
        expected={200},
        stage="gateway_auth",
    )
    readback = _settings(
        _json(
            _request(client, "GET", "/api/settings", stage="gateway_auth"),
            expected={200},
            stage="gateway_auth",
        )
    )
    readback_blocked = readback.get("blockedProviders")
    if not isinstance(readback_blocked, list) or not set(
        CERTIFICATION_NOAUTH_PROVIDER_BLOCKLIST
    ).issubset({item for item in readback_blocked if isinstance(item, str)}):
        raise GatewayProvisioningError(
            "omniroute_noauth_provider_scope_readback_mismatch", stage="gateway_auth"
        )
    if readback.get("autoRoutingEnabled") is False:
        raise GatewayProvisioningError("omniroute_auto_routing_disabled", stage="gateway_auth")
    return {
        "status": "PASS",
        "auto_routing_enabled": True,
        "blocked_provider_count": len(readback_blocked),
        "blocked_noauth_provider_ids": list(CERTIFICATION_NOAUTH_PROVIDER_BLOCKLIST),
        "scope_basis": "pinned_omniroute_v3.8.38_noauth_catalog",
        "credential_values_retained": False,
    }


def _connection(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GatewayProvisioningError("omniroute_create_readback_not_an_object")
    connection = value.get("connection", value)
    if not isinstance(connection, dict) or not connection.get("id"):
        raise GatewayProvisioningError("omniroute_provider_connection_id_missing")
    return connection


def _validate_connection(connection: dict[str, Any]) -> None:
    if connection.get("provider") != PROVIDER:
        raise GatewayProvisioningError("omniroute_provider_mismatch")
    if connection.get("name") != PROVIDER_NAME:
        raise GatewayProvisioningError("omniroute_provider_name_mismatch")
    if connection.get("defaultModel") != PROVIDER_MODEL:
        raise GatewayProvisioningError("omniroute_provider_model_mismatch")


def _discover_baseline_model(
    client: httpx.Client,
    connection_id: str,
) -> dict[str, object]:
    """Require the frozen baseline to be present in live provider discovery.

    OmniRoute may fall back to its local catalog when the upstream model list is
    unavailable.  That fallback is useful for the dashboard but cannot prove a
    live certification baseline, so only ``api``/``upstream`` discovery passes.
    """

    response = _request(
        client,
        "GET",
        f"/api/providers/{connection_id}/models?refresh=true",
        stage="provider",
    )
    payload = _json(response, expected={200}, stage="provider")
    result = baseline_discovery_status(
        provider=PROVIDER,
        desired_model=PROVIDER_MODEL,
        discovery_payload=payload,
        expected_connection_id=connection_id,
    )
    if result["status"] != "PASS":
        raise GatewayProvisioningError(
            "baseline_model_unavailable",
            stage="provider",
            http_status=response.status_code,
        )
    return result


def provision(
    *,
    base_url: str,
    management_key: str,
    provider_key: str,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    if not management_key:
        raise GatewayProvisioningError("omniroute_management_key_missing", stage="gateway_auth")
    if not provider_key:
        raise GatewayProvisioningError(
            "selected_provider_credential_missing", stage="provider_preflight"
        )
    if not base_url.startswith(("http://", "https://")):
        raise GatewayProvisioningError("omniroute_base_url_must_be_http")

    created_client = client is None
    client = client or httpx.Client(
        base_url=base_url.rstrip("/"),
        headers={"Accept": "application/json", "Authorization": f"Bearer {management_key}"},
        timeout=httpx.Timeout(60.0, connect=10.0),
        follow_redirects=False,
    )
    try:
        current = _connections(
            _json(
                _request(client, "GET", "/api/providers", stage="omniroute_health"),
                stage="omniroute_health",
            )
        )
        existing = next(
            (
                item
                for item in current
                if item.get("name") == PROVIDER_NAME and item.get("provider") == PROVIDER
            ),
            None,
        )
        if current and not (len(current) == 1 and existing is not None):
            raise GatewayProvisioningError(
                "omniroute_provider_state_not_empty", stage="gateway_auth"
            )
        auto_router_scope = _configure_auto_router_scope(client)
        payload = {
            "provider": PROVIDER,
            "apiKey": provider_key,
            "name": PROVIDER_NAME,
            "defaultModel": PROVIDER_MODEL,
            "priority": 1,
        }
        if existing is None:
            connection = _connection(
                _json(
                    _request(
                        client, "POST", "/api/providers", stage="omniroute_health", json=payload
                    ),
                    expected={200, 201},
                    stage="omniroute_health",
                )
            )
            action = "created"
        else:
            connection = _connection(
                _json(
                    _request(
                        client,
                        "PUT",
                        f"/api/providers/{existing['id']}",
                        stage="omniroute_health",
                        json={key: value for key, value in payload.items() if key != "provider"},
                    ),
                    expected={200, 201},
                    stage="gateway_auth",
                )
            )
            action = "updated"
        _validate_connection(connection)
        connection_id = str(connection["id"])

        baseline = _discover_baseline_model(client, connection_id)

        tested = _json(
            _request(
                client, "POST", f"/api/providers/{connection_id}/test", stage="provider", json={}
            ),
            expected={200},
            stage="provider",
        )
        if not isinstance(tested, dict) or tested.get("valid") is not True:
            raise GatewayProvisioningError("selected_provider_validation_failed")

        readback = _connections(
            _json(
                _request(client, "GET", "/api/providers", stage="omniroute_health"),
                stage="omniroute_health",
            )
        )
        if len(readback) != 1:
            raise GatewayProvisioningError("omniroute_provider_count_is_not_exactly_one")
        selected = next((item for item in readback if str(item.get("id")) == connection_id), None)
        if selected is None:
            raise GatewayProvisioningError("selected_provider_missing_after_readback")
        _validate_connection(selected)
        return {
            "schema_version": SCHEMA,
            "status": "PASS",
            "provider": PROVIDER,
            "provider_name": PROVIDER_NAME,
            "requested_aiat_model": AIAT_MODEL_ID,
            "resolved_provider_model": EXPECTED_ROUTE,
            "baseline_discovery": baseline,
            "auto_router_model": "auto/coding",
            "auto_router_scope": auto_router_scope,
            "management_endpoint": "http://omniroute:20128",
            "openai_compatible_endpoint": "http://omniroute:20129/v1",
            "connection_id": connection_id,
            "action": action,
            "provider_validation": "PASS",
            "provider_count": len(readback),
            "provider_pool": provider_pool_spec(),
            "provider_credential_retained": False,
            "management_key_retained": False,
            "response_payloads_retained": False,
        }
    finally:
        if created_client:
            client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("OMNIROUTE_BASE_URL", ""))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = provision(
            base_url=args.base_url,
            management_key=os.getenv("OMNIROUTE_API_KEY", "").strip(),
            provider_key=os.getenv("GROQ_API_KEY", "").strip(),
        )
    except (GatewayProvisioningError, httpx.HTTPError) as exc:
        reason = (
            str(exc) if isinstance(exc, GatewayProvisioningError) else "omniroute_transport_error"
        )
        if isinstance(exc, GatewayProvisioningError):
            failure = classify_failure(
                stage=exc.stage,
                http_status=exc.http_status,
                error_code=reason,
                exception_type=exc.exception_type,
                provider_secret_present=exc.stage != "provider_preflight",
            )
        elif "credential_missing" in reason:
            failure = classify_failure(stage="provider_preflight", provider_secret_present=False)
        elif "http_401" in reason:
            failure = classify_failure(stage="provider", http_status=401)
        elif "http_403" in reason:
            failure = classify_failure(stage="provider", http_status=403)
        elif isinstance(exc, httpx.TimeoutException):
            failure = classify_failure(stage="omniroute_health", exception_type="ReadTimeout")
        elif isinstance(exc, httpx.TransportError):
            failure = classify_failure(stage="omniroute_health", exception_type="ConnectError")
        else:
            failure = classify_failure(stage="omniroute_health", error_code=reason)
        report = {
            "schema_version": SCHEMA,
            "status": "BLOCKED",
            "failure": reason,
            "failure_class": failure.failure_class,
            "failure_stage": failure.stage,
            "failure_http_status": failure.http_status,
            "failure_retryable": failure.retryable,
            "provider": PROVIDER,
            "provider_model": PROVIDER_MODEL,
            "requested_provider_model": EXPECTED_ROUTE,
            "requested_aiat_model": AIAT_MODEL_ID,
            "provider_credential_retained": False,
            "management_key_retained": False,
            "response_payloads_retained": False,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps({"status": "BLOCKED", "failure": report["failure"]}, sort_keys=True))
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "provider": PROVIDER}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
