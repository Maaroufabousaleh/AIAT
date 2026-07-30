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


def test_token_scope_is_reported_but_not_used_as_persisted_account_evidence() -> None:
    state = provisioning.DiagnosticState(account_permission_persisted="PASS")
    assert not provisioning.token_scope_contains_create(
        {"permissions": ["sysApiKeyGet"]}, state
    )
    assert state.account_permission_persisted == "PASS"
    assert state.token_scope_contains_create == "FAIL"


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
        provisioning.extract_created_credential(
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
        == ("7", secret)
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


def _method_response(name: str, value: dict) -> dict:
    return {"methodResponses": [[name, value, "call"]]}


class PersistedPermissionTransport:
    def __init__(self, permissions):
        self.permissions = permissions

    def json(self, _url, _authorization=None, **kwargs):
        method = kwargs["payload"]["methodCalls"][0][0]
        if method == "x:Domain/query":
            return _method_response(method, {"ids": ["domain-id"]})
        if method == "x:Account/query":
            return _method_response(method, {"ids": ["account-id"]})
        if method == "x:Account/get":
            return _method_response(
                method,
                {
                    "list": [
                        {
                            "id": "account-id",
                            "permissions": self.permissions,
                        }
                    ]
                },
            )
        raise AssertionError(method)


def test_persisted_account_permission_absent_from_token_scope() -> None:
    state = provisioning.DiagnosticState()
    provisioning.prove_persisted_create_permission(
        transport=PersistedPermissionTransport(
            {
                "@type": "Merge",
                "enabledPermissions": {"sysApiKeyCreate": True},
                "disabledPermissions": {},
            }
        ),
        base_url=provisioning.LOCAL_URL,
        authorization="Bearer redacted",
        administrator_address="admin@agents.aiat.local",
        diagnostic=state,
    )
    assert state.account_permission_persisted == "PASS"
    assert not provisioning.token_scope_contains_create(
        {"permissions": ["sysAccountQuery"]}, state
    )
    assert state.token_scope_contains_create == "FAIL"


def test_real_missing_persisted_account_permission_is_refused() -> None:
    state = provisioning.DiagnosticState()
    with pytest.raises(provisioning.Refused, match="persisted administrator"):
        provisioning.prove_persisted_create_permission(
            transport=PersistedPermissionTransport(
                {
                    "@type": "Merge",
                    "enabledPermissions": {},
                    "disabledPermissions": {},
                }
            ),
            base_url=provisioning.LOCAL_URL,
            authorization="Bearer redacted",
            administrator_address="admin@agents.aiat.local",
            diagnostic=state,
        )
    assert state.account_permission_persisted == "FAIL"
    assert state.error_type == "missing-persisted-sysApiKeyCreate/missingPermission"


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
        account_permission_persisted="PASS",
        token_scope_contains_create="PASS",
        api_key_create_capability="PASS",
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
    assert "ACCOUNT_PERMISSION_PERSISTED=PASS" in output
    assert "TOKEN_SCOPE_CONTAINS_SYS_API_KEY_CREATE=PASS" in output
    assert "API_KEY_CREATE_CAPABILITY=PASS" in output
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
    assert state.account_permission_persisted == "NOT_ATTEMPTED"
    assert state.token_scope_contains_create == "NOT_ATTEMPTED"
    assert state.api_key_create_capability == "NOT_ATTEMPTED"
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
    assert "ACCOUNT_PERMISSION_PERSISTED=NOT_ATTEMPTED" in output
    assert "TOKEN_SCOPE_CONTAINS_SYS_API_KEY_CREATE=NOT_ATTEMPTED" in output
    assert "API_KEY_CREATE_CAPABILITY=NOT_ATTEMPTED" in output
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
    assert "ACCOUNT_PERMISSION_PERSISTED=NOT_ATTEMPTED" in output
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


class ProvisionTransport:
    def __init__(self, *, duplicate=False, forbid_create=False):
        self.duplicate = duplicate
        self.forbid_create = forbid_create
        self.destroyed = []
        self.create_calls = 0

    def form(self, _url, **_kwargs):
        return {"access_token": "access-token"}

    def json(self, url, authorization=None, **kwargs):
        if url.endswith("/api/auth"):
            return {"type": "authenticated", "clientCode": "code"}
        if url.endswith("/auth/userinfo"):
            return {
                "preferred_username": "admin@agents.aiat.local",
                "email": "admin@agents.aiat.local",
            }
        if url.endswith("/api/account"):
            return {"permissions": ["sysApiKeyCreate"]}
        method, arguments, _call_id = kwargs["payload"]["methodCalls"][0]
        if method == "x:Domain/query":
            return _method_response(method, {"ids": ["domain-id"]})
        if method == "x:Account/query":
            return _method_response(method, {"ids": ["account-id"]})
        if method == "x:Account/get":
            return _method_response(
                method,
                {
                    "list": [
                        {
                            "permissions": {
                                "@type": "Merge",
                                "enabledPermissions": {"sysApiKeyCreate": True},
                                "disabledPermissions": {},
                            }
                        }
                    ]
                },
            )
        if method == "x:ApiKey/query":
            return _method_response(
                method, {"ids": ["existing-id"] if self.duplicate else []}
            )
        if method == "x:ApiKey/get":
            return _method_response(
                method,
                {
                    "list": [
                        {
                            "id": "existing-id",
                            "description": provisioning.KEY_DESCRIPTION,
                        }
                    ]
                },
            )
        if method == "x:ApiKey/set" and "create" in arguments:
            self.create_calls += 1
            if self.forbid_create:
                raise provisioning.Refused("forbidden creation")
            return _method_response(
                method,
                {
                    "created": {
                        "aiat-resend-certification": {
                            "id": "created-id",
                            "secret": "API_" + "a" * 40,
                        }
                    }
                },
            )
        if method == "x:ApiKey/set" and "destroy" in arguments:
            self.destroyed.extend(arguments["destroy"])
            return _method_response(method, {"destroyed": arguments["destroy"]})
        raise AssertionError((method, arguments, authorization))


def _reserve_without_root(path: Path) -> int:
    return __import__("os").open(
        path,
        __import__("os").O_WRONLY
        | __import__("os").O_CREAT
        | __import__("os").O_EXCL,
        0o600,
    )


def _provision(
    tmp_path: Path,
    monkeypatch,
    transport: ProvisionTransport,
) -> tuple[Path, provisioning.DiagnosticState]:
    output = tmp_path / "resend-certification.env"
    state = provisioning.DiagnosticState()
    monkeypatch.setattr(provisioning, "reserve_output", _reserve_without_root)
    provisioning.provision(
        transport=transport,
        administrator_address="admin@agents.aiat.local",
        administrator_password="admin-password",
        app_password="app_redacted",
        base_url=provisioning.LOCAL_URL,
        output=output,
        expires_at="2026-07-31T12:00:00Z",
        server_image=next(iter(provisioning.PATCHED_IMAGE_REFS)),
        diagnostic=state,
    )
    return output, state


def test_safe_creation_capability_and_protected_output(tmp_path: Path, monkeypatch) -> None:
    transport = ProvisionTransport()
    output, state = _provision(tmp_path, monkeypatch, transport)
    assert output.exists()
    assert transport.create_calls == 1
    assert state.account_permission_persisted == "PASS"
    assert state.token_scope_contains_create == "PASS"
    assert state.api_key_create_capability == "PASS"
    assert state.mailbox_authentication == "PASS"


def test_forbidden_creation_removes_partial_output(tmp_path: Path, monkeypatch) -> None:
    transport = ProvisionTransport(forbid_create=True)
    output = tmp_path / "resend-certification.env"
    with pytest.raises(provisioning.Refused, match="forbidden creation"):
        _provision(tmp_path, monkeypatch, transport)
    assert not output.exists()
    assert transport.create_calls == 1


def test_duplicate_retry_is_refused_before_creation(tmp_path: Path, monkeypatch) -> None:
    transport = ProvisionTransport(duplicate=True)
    output = tmp_path / "resend-certification.env"
    with pytest.raises(provisioning.Refused, match="duplicate"):
        _provision(tmp_path, monkeypatch, transport)
    assert not output.exists()
    assert transport.create_calls == 0


def test_file_failure_destroys_created_key_and_removes_partial_output(
    tmp_path: Path, monkeypatch
) -> None:
    transport = ProvisionTransport()
    output = tmp_path / "resend-certification.env"
    monkeypatch.setattr(provisioning.os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError()))
    with pytest.raises(provisioning.Refused, match="created API key was removed"):
        _provision(tmp_path, monkeypatch, transport)
    assert not output.exists()
    assert transport.destroyed == ["created-id"]


def test_v0167_is_blocked_for_scoped_api_key_security_behavior() -> None:
    state = provisioning.DiagnosticState()
    vulnerable = (
        "ghcr.io/stalwartlabs/stalwart:v0.16.7@"
        "sha256:6a8ddaa5728a5e78a8611085069f63414cd43c3a669471785dd41aad1ca16e63"
    )
    with pytest.raises(provisioning.Refused, match="v0.16.15"):
        provisioning.require_patched_server(vulnerable, state)
    assert state.error_type == "unsafe-stalwart-version/scopedCredentialEscalation"
    assert state.administrator_authentication == "NOT_ATTEMPTED"
    assert state.api_key_create_capability == "NOT_ATTEMPTED"


def test_security_upgrade_override_is_digest_pinned_and_stalwart_only() -> None:
    override = (
        ROOT
        / "mas/infra/smtp-gateway/home/"
        "docker-compose.stalwart-v0.16.15-security-upgrade.yml"
    ).read_text(encoding="utf-8")
    assert (
        "ghcr.io/stalwartlabs/stalwart:v0.16.15@"
        "sha256:4f926193e5dd9ceb1e24ba48160702310381b12e51972c2fb0cc9de020388136"
        in override
    )
    assert "identity-service" not in override
    assert "volumes:" not in override


def test_administrator_address_is_optional_non_secret_argument() -> None:
    args = provisioning.parser().parse_args(
        ["--administrator-address", "admin@example.test", "--diagnose"]
    )
    assert args.administrator_address == "admin@example.test"
    assert args.diagnose is True
