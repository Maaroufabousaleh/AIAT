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
    assert provisioning.response_error(
        {
            "methodResponses": [
                [
                    "x:ApiKey/set",
                    {
                        "notCreated": {
                            "key": {
                                "type": "invalidProperties",
                                "description": "invalid expiry",
                            }
                        }
                    },
                    "create-api-key",
                ]
            ]
        }
    ) == ("invalidProperties", "invalid expiry")


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


@pytest.mark.parametrize(
    ("error_type", "authentication", "category"),
    [
        ("unauthorized", True, "authentication-failure"),
        ("unknownMethod", False, "unsupported-method"),
        ("urn:ietf:params:jmap:error:unknownMethod", False, "unsupported-method"),
        ("invalidArguments", False, "invalid-api-key-set-payload"),
        ("forbidden", False, "forbidden-permission-assignment"),
        ("invalidProperties", False, "server-side-validation-error"),
    ],
)
def test_diagnostic_error_categories(error_type, authentication, category) -> None:
    state = provisioning.DiagnosticState()
    with pytest.raises(provisioning.Refused):
        provisioning.fail_with_diagnostic(
            state,
            endpoint_path="/api",
            http_status="200",
            jmap_method="x:ApiKey/set",
            error_type=error_type,
            description="bounded failure",
            authentication=authentication,
        )
    assert state.error_type.startswith(f"{category}/")


def test_missing_create_permission_has_distinct_diagnostic() -> None:
    state = provisioning.DiagnosticState(administrator_authentication=True)
    with pytest.raises(provisioning.Refused, match="lacks sysApiKeyCreate"):
        provisioning.require_create_permission(
            {"permissions": ["sysApiKeyGet", "sysApiKeyQuery"]},
            state,
        )
    assert state.error_type == "missing-sysApiKeyCreate/missingPermission"
    assert state.create_permission_preflight is False


def test_diagnostic_output_redacts_all_secret_classes(capsys) -> None:
    state = provisioning.DiagnosticState(
        endpoint_path="/api",
        http_status="400",
        jmap_method="x:ApiKey/set",
        error_type="server-side-validation-error/invalidProperties",
        description=provisioning.sanitize_description(
            "password=hunter2 Authorization: Basic YWRtaW46c2VjcmV0 "
            "Bearer bearer.secret API_abcdef1234567890 app_abcdef1234567890"
        ),
        administrator_authentication=True,
        create_permission_preflight=True,
        mailbox_authentication=True,
        sensitive_values=["unique admin password with spaces"],
    )
    state.description = provisioning.sanitize_description(
        state.description + " unique admin password with spaces",
        sensitive_values=state.sensitive_values,
    )
    provisioning.print_diagnostic(state)
    output = capsys.readouterr().err
    for secret in (
        "hunter2",
        "YWRtaW46c2VjcmV0",
        "bearer.secret",
        "API_abcdef1234567890",
        "app_abcdef1234567890",
        "unique admin password with spaces",
    ):
        assert secret not in output
    assert len(state.description) <= 180
    assert "ADMINISTRATOR_AUTHENTICATION=PASS" in output
    assert "MAILBOX_APPLICATION_PASSWORD_VALIDATION=PASS" in output


def test_partial_output_is_removed_on_failure(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "resend-certification.env"
    state = provisioning.DiagnosticState()

    def reserve(path: Path) -> int:
        return __import__("os").open(path, __import__("os").O_WRONLY | __import__("os").O_CREAT)

    class FailingTransport:
        calls = 0

        def json(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return {"permissions": ["sysApiKeyCreate"]}
            raise provisioning.Refused("mailbox authentication failed")

    monkeypatch.setattr(provisioning, "reserve_output", reserve)
    with pytest.raises(provisioning.Refused):
        provisioning.provision(
            transport=FailingTransport(),
            admin_authorization="Basic redacted",
            app_password="app_redacted",
            output=output,
            expires_at="2026-07-31T12:00:00Z",
            diagnostic=state,
        )
    assert not output.exists()


def test_administrator_address_is_optional_non_secret_argument() -> None:
    args = provisioning.parser().parse_args(
        ["--administrator-address", "admin@example.test", "--diagnose"]
    )
    assert args.administrator_address == "admin@example.test"
    assert args.diagnose is True
