from __future__ import annotations

import base64
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[4]
SCRIPT = (
    ROOT
    / "mas/infra/smtp-gateway/scripts/provision-stalwart-certification-api-key.py"
)
SPEC = importlib.util.spec_from_file_location("api_key_provisioning", SCRIPT)
assert SPEC and SPEC.loader
provisioning = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provisioning)


def test_payload_creates_standalone_api_key_object() -> None:
    payload = provisioning.api_key_payload("2026-07-31T12:00:00Z")
    method, arguments, _call_id = payload["methodCalls"][0]
    assert method == "x:ApiKey/set"
    assert "x:Account/set" not in str(payload)
    assert "credentials" not in str(payload)
    created = arguments["create"]["aiat-resend-certification"]
    assert created["allowedIps"] == {}
    assert created["permissions"] == {
        "@type": "Replace",
        "permissions": provisioning.REQUIRED_KEY_PERMISSIONS,
    }
    assert "secret" not in created
    assert "createdAt" not in created


def test_cannot_modify_server_set_property_regression() -> None:
    payload_text = str(provisioning.api_key_payload("2026-07-31T12:00:00Z"))
    assert "Account.credentials" not in payload_text
    assert "'secret':" not in payload_text
    assert "'createdAt':" not in payload_text


def test_missing_sys_api_key_create_is_refused() -> None:
    with pytest.raises(provisioning.Refused, match="lacks sysApiKeyCreate"):
        provisioning.require_create_permission({"permissions": ["sysApiKeyGet"]})


def test_server_side_creation_failure_is_sanitized() -> None:
    with pytest.raises(provisioning.Refused, match="standalone ApiKey"):
        provisioning.extract_created_secret(
            {
                "methodResponses": [
                    [
                        "x:ApiKey/set",
                        {"notCreated": {"key": {"type": "forbidden"}}},
                        "create-api-key",
                    ]
                ]
            }
        )


def test_generated_secret_is_extracted_only_from_create_response() -> None:
    secret = "API_" + "a" * 40
    assert (
        provisioning.extract_created_secret(
            {
                "methodResponses": [
                    [
                        "x:ApiKey/set",
                        {
                            "created": {
                                "aiat-resend-certification": {
                                    "id": "7",
                                    "secret": secret,
                                }
                            }
                        },
                        "create-api-key",
                    ]
                ]
            }
        )
        == secret
    )


def test_final_credential_file_shape_contains_no_plain_app_password() -> None:
    app_password = "app_test-secret"
    encoded = base64.b64encode(
        f"{provisioning.GATEWAY_ACCOUNT}:{app_password}".encode()
    ).decode()
    content = (
        "STALWART_API_KEY=API_generated\n"
        f"STALWART_JMAP_SERVICE_TOKEN=Basic {encoded}\n"
    )
    assert content.splitlines()[0].startswith("STALWART_API_KEY=")
    assert content.splitlines()[1].startswith("STALWART_JMAP_SERVICE_TOKEN=Basic ")
    assert app_password not in content
