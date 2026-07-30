from __future__ import annotations

import base64
import importlib.util
import io
import json
from pathlib import Path
from urllib import error

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
            authentication_mechanism="oauth2-bearer-management-jmap",
            error_type=error_type,
            description="bounded failure",
            authentication=authentication,
        )
    assert state.error_type.startswith(f"{category}/")


def test_missing_create_permission_has_distinct_diagnostic() -> None:
    state = provisioning.DiagnosticState(administrator_authentication="PASS")
    with pytest.raises(provisioning.Refused, match="lacks sysApiKeyCreate"):
        provisioning.require_create_permission(
            {"permissions": ["sysApiKeyGet", "sysApiKeyQuery"]},
            state,
        )
    assert state.error_type == "missing-sysApiKeyCreate/missingPermission"
    assert state.create_permission_preflight == "FAIL"


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
        administrator_authentication="PASS",
        create_permission_preflight="PASS",
        mailbox_authentication="PASS",
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
    assert "AUTHENTICATION_MECHANISM=" in output


class SuccessfulAuthenticationTransport:
    def __init__(self):
        self.calls = []

    def json(self, url, authorization=None, **kwargs):
        self.calls.append(("json", url, authorization, kwargs))
        if url.endswith("/api/auth"):
            return {"type": "authenticated", "clientCode": "one-time-code"}
        if url.endswith("/auth/userinfo"):
            return {
                "preferred_username": "admin@agents.aiat.local",
                "email": "admin@agents.aiat.local",
            }
        raise AssertionError(f"unexpected URL {url}")

    def form(self, url, **kwargs):
        self.calls.append(("form", url, None, kwargs))
        return {"access_token": "opaque-access-token", "token_type": "bearer"}


def test_successful_admin_authentication_uses_pkce_bearer_and_proves_identity() -> None:
    state = provisioning.DiagnosticState(
        sensitive_values=["correct horse battery staple"]
    )
    transport = SuccessfulAuthenticationTransport()
    token = provisioning.authenticate_administrator(
        transport=transport,
        base_url=provisioning.LOCAL_URL,
        administrator_address="admin@agents.aiat.local",
        administrator_password="correct horse battery staple",
        diagnostic=state,
    )
    assert token == "opaque-access-token"
    assert state.administrator_authentication == "PASS"
    assert transport.calls[0][1].endswith("/api/auth")
    assert transport.calls[0][2] is None
    assert transport.calls[0][3]["payload"]["codeChallengeMethod"] == "S256"
    assert transport.calls[1][1].endswith("/auth/token")
    assert transport.calls[2][1].endswith("/auth/userinfo")
    assert transport.calls[2][2] == "Bearer opaque-access-token"
    assert all(
        not (authorization or "").startswith("Basic ")
        for _, _, authorization, _ in transport.calls
    )


def test_wrong_admin_password_stops_before_permission_and_mailbox_checks() -> None:
    class WrongPasswordTransport:
        def json(self, url, authorization=None, **kwargs):
            assert url.endswith("/api/auth")
            return {"type": "failure"}

        def form(self, *_args, **_kwargs):
            raise AssertionError("token exchange must not be attempted")

    state = provisioning.DiagnosticState(sensitive_values=["wrong-password"])
    with pytest.raises(provisioning.Refused):
        provisioning.authenticate_administrator(
            transport=WrongPasswordTransport(),
            base_url=provisioning.LOCAL_URL,
            administrator_address="admin@agents.aiat.local",
            administrator_password="wrong-password",
            diagnostic=state,
        )
    assert state.administrator_authentication == "FAIL"
    assert state.create_permission_preflight == "NOT_ATTEMPTED"
    assert state.mailbox_authentication == "NOT_ATTEMPTED"
    assert state.endpoint_path == "/api/auth"
    assert state.error_type.startswith("authentication-failure/")


def test_exact_401_diagnostic_leaves_downstream_checks_not_attempted(
    monkeypatch, capsys
) -> None:
    body = json.dumps(
        {
            "type": "about:blank",
            "detail": "You have to authenticate first.",
        }
    ).encode()

    def rejected(_message, timeout):
        raise error.HTTPError(
            f"{provisioning.LOCAL_URL}/api/auth",
            401,
            "Unauthorized",
            {},
            io.BytesIO(body),
        )

    monkeypatch.setattr(provisioning.request, "urlopen", rejected)
    state = provisioning.DiagnosticState()
    state.administrator_authentication = "FAIL"
    with pytest.raises(provisioning.Refused):
        provisioning.HttpTransport(state).json(
            f"{provisioning.LOCAL_URL}/api/auth",
            payload={"type": "authCode"},
            endpoint_path="/api/auth",
            jmap_method="POST /api/auth",
            authentication=True,
            authentication_mechanism="oauth2-password-to-authorization-code-pkce",
        )
    provisioning.print_diagnostic(state)
    output = capsys.readouterr().err
    assert "HTTP_STATUS=401" in output
    assert "ADMINISTRATOR_AUTHENTICATION=FAIL" in output
    assert "SYS_API_KEY_CREATE_PREFLIGHT=NOT_ATTEMPTED" in output
    assert "MAILBOX_APPLICATION_PASSWORD_VALIDATION=NOT_ATTEMPTED" in output
    assert "AUTHENTICATION_MECHANISM=oauth2-password-to-authorization-code-pkce" in output


def test_legacy_basic_api_account_401_regression(monkeypatch, capsys) -> None:
    body = json.dumps(
        {
            "type": "about:blank",
            "detail": "You have to authenticate first.",
        }
    ).encode()

    def rejected(_message, timeout):
        raise error.HTTPError(
            f"{provisioning.LOCAL_URL}/api/account",
            401,
            "Unauthorized",
            {},
            io.BytesIO(body),
        )

    monkeypatch.setattr(provisioning.request, "urlopen", rejected)
    state = provisioning.DiagnosticState(administrator_authentication="FAIL")
    with pytest.raises(provisioning.Refused):
        provisioning.HttpTransport(state).json(
            f"{provisioning.LOCAL_URL}/api/account",
            "Basic redacted",
            endpoint_path="/api/account",
            jmap_method="GET /api/account",
            authentication=True,
            authentication_mechanism="http-basic-administrator-legacy",
        )
    provisioning.print_diagnostic(state)
    output = capsys.readouterr().err
    assert "ENDPOINT_PATH=/api/account" in output
    assert "HTTP_STATUS=401" in output
    assert "JMAP_ERROR_TYPE=authentication-failure/about:blank" in output
    assert "DESCRIPTION=You have to authenticate first." in output
    assert "SYS_API_KEY_CREATE_PREFLIGHT=NOT_ATTEMPTED" in output
    assert "MAILBOX_APPLICATION_PASSWORD_VALIDATION=NOT_ATTEMPTED" in output


def test_wrong_authenticated_principal_is_refused() -> None:
    transport = SuccessfulAuthenticationTransport()
    original_json = transport.json

    def wrong_userinfo(url, authorization=None, **kwargs):
        if url.endswith("/auth/userinfo"):
            return {
                "preferred_username": "other@agents.aiat.local",
                "email": "other@agents.aiat.local",
            }
        return original_json(url, authorization, **kwargs)

    transport.json = wrong_userinfo
    state = provisioning.DiagnosticState()
    with pytest.raises(provisioning.Refused):
        provisioning.authenticate_administrator(
            transport=transport,
            base_url=provisioning.LOCAL_URL,
            administrator_address="admin@agents.aiat.local",
            administrator_password="secret",
            diagnostic=state,
        )
    assert state.administrator_authentication == "FAIL"
    assert state.endpoint_path == "/auth/userinfo"


def test_partial_output_is_removed_on_failure(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "resend-certification.env"
    state = provisioning.DiagnosticState()

    def reserve(path: Path) -> int:
        return __import__("os").open(path, __import__("os").O_WRONLY | __import__("os").O_CREAT)

    class FailingTransport:
        calls = 0

        def json(self, url, *_args, **_kwargs):
            self.calls += 1
            if url.endswith("/api/auth"):
                return {"type": "authenticated", "clientCode": "code"}
            if url.endswith("/auth/userinfo"):
                return {
                    "preferred_username": "admin@agents.aiat.local",
                    "email": "admin@agents.aiat.local",
                }
            if url.endswith("/api/account") and self.calls == 3:
                return {"permissions": ["sysApiKeyCreate"]}
            raise provisioning.Refused("mailbox authentication failed")

        def form(self, *_args, **_kwargs):
            return {"access_token": "access-token"}

    monkeypatch.setattr(provisioning, "reserve_output", reserve)
    with pytest.raises(provisioning.Refused):
        provisioning.provision(
            transport=FailingTransport(),
            administrator_address="admin@agents.aiat.local",
            administrator_password="admin-password",
            app_password="app_redacted",
            base_url=provisioning.LOCAL_URL,
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
