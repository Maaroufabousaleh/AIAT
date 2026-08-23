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

SCHEMA = "aiat.openhands-certification-gateway-provisioning.v1"
PROVIDER = "groq"
PROVIDER_NAME = "AIAT OpenHands certification Groq"
PROVIDER_MODEL = "llama-3.3-70b-versatile"
EXPECTED_ROUTE = f"{PROVIDER}/{PROVIDER_MODEL}"


class GatewayProvisioningError(RuntimeError):
    """A disposable provider route could not be created or verified."""


def _json(response: httpx.Response, *, expected: set[int] | None = None) -> Any:
    if expected is not None and response.status_code not in expected:
        raise GatewayProvisioningError(f"omniroute_http_{response.status_code}")
    if response.status_code >= 400:
        raise GatewayProvisioningError(f"omniroute_http_{response.status_code}")
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise GatewayProvisioningError("omniroute_invalid_json") from exc


def _connections(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict) or not isinstance(value.get("connections"), list):
        raise GatewayProvisioningError("omniroute_provider_readback_not_a_list")
    return [item for item in value["connections"] if isinstance(item, dict)]


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


def provision(
    *,
    base_url: str,
    management_key: str,
    provider_key: str,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    if not management_key:
        raise GatewayProvisioningError("omniroute_management_key_missing")
    if not provider_key:
        raise GatewayProvisioningError("selected_provider_credential_missing")
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
        current = _connections(_json(client.get("/api/providers")))
        existing = next(
            (
                item
                for item in current
                if item.get("name") == PROVIDER_NAME and item.get("provider") == PROVIDER
            ),
            None,
        )
        payload = {
            "provider": PROVIDER,
            "apiKey": provider_key,
            "name": PROVIDER_NAME,
            "defaultModel": PROVIDER_MODEL,
            "priority": 1,
        }
        if existing is None:
            connection = _connection(
                _json(client.post("/api/providers", json=payload), expected={200, 201})
            )
            action = "created"
        else:
            connection = _connection(
                _json(
                    client.put(
                        f"/api/providers/{existing['id']}",
                        json={key: value for key, value in payload.items() if key != "provider"},
                    ),
                    expected={200, 201},
                )
            )
            action = "updated"
        _validate_connection(connection)
        connection_id = str(connection["id"])

        tested = _json(
            client.post(f"/api/providers/{connection_id}/test", json={}),
            expected={200},
        )
        if not isinstance(tested, dict) or tested.get("valid") is not True:
            raise GatewayProvisioningError("selected_provider_validation_failed")

        readback = _connections(_json(client.get("/api/providers")))
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
            "requested_aiat_model": "omniroute-coding",
            "resolved_provider_model": EXPECTED_ROUTE,
            "management_route": "http://omniroute:20128/v1",
            "connection_id": connection_id,
            "action": action,
            "provider_validation": "PASS",
            "provider_count": len(readback),
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
        report = {
            "schema_version": SCHEMA,
            "status": "BLOCKED",
            "failure": str(exc) if isinstance(exc, GatewayProvisioningError) else "omniroute_transport_error",
            "provider_credential_retained": False,
            "management_key_retained": False,
            "response_payloads_retained": False,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": "BLOCKED", "failure": report["failure"]}, sort_keys=True))
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "provider": PROVIDER}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
