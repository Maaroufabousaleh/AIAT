#!/usr/bin/env python3
"""Interactively create one v0.16.7 ApiKey object and protect its one-time secret."""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
import secrets
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib import error, parse, request

LOCAL_URL = "http://127.0.0.1:18080"
GATEWAY_ACCOUNT = "gateway-test@agents.aiat.ca"
OAUTH_CLIENT_ID = "stalwart-webui"
OAUTH_REDIRECT_PATH = "/admin/oauth/callback"
REQUIRED_KEY_PERMISSIONS = [
    "authenticate",
    "sysAccountQuery",
    "sysDomainQuery",
    "sysMtaOutboundStrategyGet",
    "sysMtaRouteGet",
]


class Refused(RuntimeError):
    pass


class DiagnosedRefused(Refused):
    pass


class DiagnosticState:
    def __init__(
        self,
        *,
        endpoint_path: str = "not-reached",
        http_status: str = "not-reached",
        jmap_method: str = "not-reached",
        authentication_mechanism: str = "not-reached",
        error_type: str = "not-reached",
        description: str = "request did not reach Stalwart",
        administrator_authentication: str = "NOT_ATTEMPTED",
        create_permission_preflight: str = "NOT_ATTEMPTED",
        mailbox_authentication: str = "NOT_ATTEMPTED",
        sensitive_values: list[str] | None = None,
    ):
        self.endpoint_path = endpoint_path
        self.http_status = http_status
        self.jmap_method = jmap_method
        self.authentication_mechanism = authentication_mechanism
        self.error_type = error_type
        self.description = description
        self.administrator_authentication = administrator_authentication
        self.create_permission_preflight = create_permission_preflight
        self.mailbox_authentication = mailbox_authentication
        self.sensitive_values = sensitive_values or []


SECRET_PATTERNS = (
    (r"API_[A-Za-z0-9_-]+", "<redacted-api-key>"),
    (r"app_[A-Za-z0-9_-]+", "<redacted-app-password>"),
    (r"(?i)\bBasic\s+[A-Za-z0-9+/=_-]+", "Basic <redacted>"),
    (r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+", "Bearer <redacted>"),
    (
        r'(?i)(password|secret|authorization|token)\s*["\']?\s*[:=]\s*["\']?[^,\s"\'}]+',
        r"\1=<redacted>",
    ),
)


def sanitize_description(
    value: Any,
    limit: int = 180,
    sensitive_values: list[str] | None = None,
) -> str:
    import re

    text = " ".join(str(value or "").split())
    for secret in sorted(sensitive_values or [], key=len, reverse=True):
        if secret:
            text = text.replace(secret, "<redacted>")
    for pattern, replacement in SECRET_PATTERNS:
        text = re.sub(pattern, replacement, text)
    text = "".join(character for character in text if character.isprintable())
    return (text or "no description supplied")[:limit]


def classify_error(error_type: str, *, authentication: bool = False) -> str:
    lowered = error_type.rsplit("/", 1)[-1].rsplit(":", 1)[-1].lower()
    if authentication or lowered in {"unauthorized", "authenticationfailed"}:
        return "authentication-failure"
    if lowered in {"unknownmethod", "unknowncapability", "notsupported"}:
        return "unsupported-method"
    if lowered in {"invalidarguments", "invalidpatch", "invalidresultreference", "notjson"}:
        return "invalid-api-key-set-payload"
    if lowered in {"forbidden", "accountreadonly", "permissiondenied"}:
        return "forbidden-permission-assignment"
    return "server-side-validation-error"


def response_error(value: dict[str, Any]) -> tuple[str, str] | None:
    for item in value.get("methodResponses") or []:
        if not isinstance(item, list) or len(item) < 2 or not isinstance(item[1], dict):
            continue
        if item[0] == "error":
            return str(item[1].get("type") or "jmapError"), str(
                item[1].get("description") or "JMAP method failed"
            )
        if item[0] == "x:ApiKey/set":
            not_created = item[1].get("notCreated") or {}
            if isinstance(not_created, dict) and not_created:
                failure = next(iter(not_created.values()))
                if isinstance(failure, dict):
                    return str(failure.get("type") or "setError"), str(
                        failure.get("description") or "ApiKey creation failed"
                    )
    return None


def fail_with_diagnostic(
    state: DiagnosticState,
    *,
    endpoint_path: str,
    http_status: str,
    jmap_method: str,
    authentication_mechanism: str,
    error_type: str,
    description: Any,
    authentication: bool = False,
) -> None:
    category = classify_error(error_type, authentication=authentication)
    state.endpoint_path = endpoint_path
    state.http_status = http_status
    state.jmap_method = jmap_method
    state.authentication_mechanism = authentication_mechanism
    state.error_type = (
        f"{category}/{sanitize_description(error_type, 64, state.sensitive_values)}"
    )
    state.description = sanitize_description(
        description, sensitive_values=state.sensitive_values
    )
    raise Refused("Stalwart rejected the local provisioning request")


class HttpTransport:
    def __init__(self, diagnostic: DiagnosticState):
        self.diagnostic = diagnostic

    def json(
        self,
        url: str,
        authorization: str | None = None,
        *,
        payload: dict[str, Any] | None = None,
        endpoint_path: str,
        jmap_method: str,
        authentication: bool = False,
        authentication_mechanism: str = "none",
    ) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        return self._request(
            url,
            authorization,
            body=body,
            content_type="application/json",
            method="GET" if body is None else "POST",
            endpoint_path=endpoint_path,
            jmap_method=jmap_method,
            authentication=authentication,
            authentication_mechanism=authentication_mechanism,
        )

    def form(
        self,
        url: str,
        *,
        payload: dict[str, str],
        endpoint_path: str,
        jmap_method: str,
        authentication: bool = False,
        authentication_mechanism: str = "oauth2-authorization-code-pkce",
    ) -> dict[str, Any]:
        return self._request(
            url,
            None,
            body=parse.urlencode(payload).encode(),
            content_type="application/x-www-form-urlencoded",
            method="POST",
            endpoint_path=endpoint_path,
            jmap_method=jmap_method,
            authentication=authentication,
            authentication_mechanism=authentication_mechanism,
        )

    def _request(
        self,
        url: str,
        authorization: str | None,
        *,
        body: bytes | None,
        content_type: str,
        method: str,
        endpoint_path: str,
        jmap_method: str,
        authentication: bool,
        authentication_mechanism: str,
    ) -> dict[str, Any]:
        headers = {"Content-Type": content_type}
        if authorization is not None:
            headers["Authorization"] = authorization
        message = request.Request(
            url,
            data=body,
            method=method,
            headers=headers,
        )
        try:
            with request.urlopen(message, timeout=20) as response:
                value = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            raw = exc.read(8192)
            try:
                problem = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                problem = {}
            error_type = problem.get("type") if isinstance(problem, dict) else None
            if not error_type:
                if authentication or exc.code == 401:
                    error_type = "unauthorized"
                elif exc.code == 403:
                    error_type = "forbidden"
                else:
                    error_type = "httpError"
            description = (
                problem.get("detail") or problem.get("description") or problem.get("title")
                if isinstance(problem, dict)
                else None
            ) or exc.reason or "HTTP request failed"
            fail_with_diagnostic(
                self.diagnostic,
                endpoint_path=endpoint_path,
                http_status=str(exc.code),
                jmap_method=jmap_method,
                authentication_mechanism=authentication_mechanism,
                error_type=str(error_type),
                description=description,
                authentication=authentication and exc.code in {401, 403},
            )
        except (error.URLError, TimeoutError) as exc:
            fail_with_diagnostic(
                self.diagnostic,
                endpoint_path=endpoint_path,
                http_status="unavailable",
                jmap_method=jmap_method,
                authentication_mechanism=authentication_mechanism,
                error_type="transportError",
                description=getattr(exc, "reason", None) or "local endpoint unavailable",
                authentication=authentication,
            )
        except json.JSONDecodeError:
            fail_with_diagnostic(
                self.diagnostic,
                endpoint_path=endpoint_path,
                http_status="200",
                jmap_method=jmap_method,
                authentication_mechanism=authentication_mechanism,
                error_type="notJson",
                description="Stalwart returned malformed JSON",
            )
        if not isinstance(value, dict):
            fail_with_diagnostic(
                self.diagnostic,
                endpoint_path=endpoint_path,
                http_status="200",
                jmap_method=jmap_method,
                authentication_mechanism=authentication_mechanism,
                error_type="invalidResponse",
                description="Stalwart returned a non-object response",
            )
        failure = response_error(value)
        if failure:
            fail_with_diagnostic(
                self.diagnostic,
                endpoint_path=endpoint_path,
                http_status="200",
                jmap_method=jmap_method,
                authentication_mechanism=authentication_mechanism,
                error_type=failure[0],
                description=failure[1],
            )
        return value


def api_key_payload(expires_at: str) -> dict[str, Any]:
    return {
        "using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"],
        "methodCalls": [
            [
                "x:ApiKey/set",
                {
                    "create": {
                        "aiat-resend-certification": {
                            "description": "AIAT Resend certification read-only",
                            "expiresAt": expires_at,
                            "permissions": {
                                "@type": "Replace",
                                "permissions": REQUIRED_KEY_PERMISSIONS,
                            },
                            "allowedIps": {},
                        }
                    }
                },
                "create-api-key",
            ]
        ],
    }


def require_create_permission(account: dict[str, Any], state: DiagnosticState | None = None) -> None:
    permissions = account.get("permissions")
    if not isinstance(permissions, list) or "sysApiKeyCreate" not in permissions:
        if state is not None:
            state.endpoint_path = "/api/account"
            state.http_status = "200"
            state.jmap_method = "GET /api/account"
            state.authentication_mechanism = "oauth2-bearer"
            state.error_type = "missing-sysApiKeyCreate/missingPermission"
            state.description = "authenticated account lacks sysApiKeyCreate"
            state.create_permission_preflight = "FAIL"
        raise Refused(
            "authenticated account lacks sysApiKeyCreate; its role/password was not modified"
        )
    if state is not None:
        state.create_permission_preflight = "PASS"


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def authenticate_administrator(
    *,
    transport: HttpTransport,
    base_url: str,
    administrator_address: str,
    administrator_password: str,
    diagnostic: DiagnosticState,
) -> str:
    """Authenticate through the v0.16.7 WebUI OAuth code/PKCE flow."""

    verifier = _base64url(secrets.token_bytes(48))
    challenge = _base64url(hashlib.sha256(verifier.encode("ascii")).digest())
    redirect_uri = f"{base_url}{OAUTH_REDIRECT_PATH}"
    diagnostic.sensitive_values.extend([verifier])
    diagnostic.administrator_authentication = "FAIL"
    login = transport.json(
        f"{base_url}/api/auth",
        payload={
            "type": "authCode",
            "accountName": administrator_address,
            "accountSecret": administrator_password,
            "clientId": OAUTH_CLIENT_ID,
            "redirectUri": redirect_uri,
            "scope": "openid",
            "codeChallenge": challenge,
            "codeChallengeMethod": "S256",
        },
        endpoint_path="/api/auth",
        jmap_method="POST /api/auth",
        authentication=True,
        authentication_mechanism="oauth2-password-to-authorization-code-pkce",
    )
    client_code = login.get("clientCode") or login.get("client_code")
    if login.get("type") != "authenticated" or not isinstance(client_code, str):
        fail_with_diagnostic(
            diagnostic,
            endpoint_path="/api/auth",
            http_status="200",
            jmap_method="POST /api/auth",
            authentication_mechanism="oauth2-password-to-authorization-code-pkce",
            error_type="authenticationFailed",
            description="administrator credential was not accepted",
            authentication=True,
        )
    diagnostic.sensitive_values.append(client_code)
    token_response = transport.form(
        f"{base_url}/auth/token",
        payload={
            "grant_type": "authorization_code",
            "code": client_code,
            "code_verifier": verifier,
            "client_id": OAUTH_CLIENT_ID,
            "redirect_uri": redirect_uri,
        },
        endpoint_path="/auth/token",
        jmap_method="POST /auth/token",
        authentication=True,
        authentication_mechanism="oauth2-authorization-code-pkce",
    )
    access_token = token_response.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        fail_with_diagnostic(
            diagnostic,
            endpoint_path="/auth/token",
            http_status="200",
            jmap_method="POST /auth/token",
            authentication_mechanism="oauth2-authorization-code-pkce",
            error_type=str(token_response.get("error") or "invalidTokenResponse"),
            description="Stalwart did not issue an OAuth access token",
            authentication=True,
        )
    diagnostic.sensitive_values.extend([access_token, f"Bearer {access_token}"])
    userinfo = transport.json(
        f"{base_url}/auth/userinfo",
        f"Bearer {access_token}",
        endpoint_path="/auth/userinfo",
        jmap_method="GET /auth/userinfo",
        authentication=True,
        authentication_mechanism="oauth2-bearer",
    )
    if (
        userinfo.get("preferred_username") != administrator_address
        or userinfo.get("email") != administrator_address
    ):
        fail_with_diagnostic(
            diagnostic,
            endpoint_path="/auth/userinfo",
            http_status="200",
            jmap_method="GET /auth/userinfo",
            authentication_mechanism="oauth2-bearer",
            error_type="authenticatedIdentityMismatch",
            description="authenticated principal does not match administrator address",
            authentication=True,
        )
    diagnostic.administrator_authentication = "PASS"
    return access_token


def extract_created_secret(response: dict[str, Any]) -> str:
    for item in response.get("methodResponses") or []:
        if isinstance(item, list) and len(item) >= 2 and item[0] == "x:ApiKey/set":
            result = item[1] if isinstance(item[1], dict) else {}
            created = result.get("created") or {}
            value = created.get("aiat-resend-certification") or {}
            secret = value.get("secret") if isinstance(value, dict) else None
            if isinstance(secret, str) and secret.startswith("API_") and len(secret) > 20:
                return secret
    raise Refused("Stalwart did not return the one-time ApiKey secret")


def reserve_output(path: Path) -> int:
    if os.geteuid() != 0:
        raise Refused("run as root so the certification file is root-owned")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise Refused(f"refusing to overwrite {path}") from exc


def provision(
    *,
    transport: HttpTransport,
    administrator_address: str,
    administrator_password: str,
    app_password: str,
    base_url: str,
    output: Path,
    expires_at: str,
    diagnostic: DiagnosticState,
) -> None:
    descriptor = reserve_output(output)
    completed = False
    try:
        access_token = authenticate_administrator(
            transport=transport,
            base_url=base_url,
            administrator_address=administrator_address,
            administrator_password=administrator_password,
            diagnostic=diagnostic,
        )
        admin_authorization = f"Bearer {access_token}"
        account = transport.json(
            f"{base_url}/api/account",
            admin_authorization,
            endpoint_path="/api/account",
            jmap_method="GET /api/account",
            authentication=True,
            authentication_mechanism="oauth2-bearer",
        )
        require_create_permission(account, diagnostic)
        mailbox_basic = base64.b64encode(
            f"{GATEWAY_ACCOUNT}:{app_password}".encode()
        ).decode()
        diagnostic.sensitive_values.extend(
            [mailbox_basic, f"Basic {mailbox_basic}"]
        )
        diagnostic.mailbox_authentication = "FAIL"
        transport.json(
            f"{base_url}/api/account",
            f"Basic {mailbox_basic}",
            endpoint_path="/api/account",
            jmap_method="GET /api/account",
            authentication=True,
            authentication_mechanism="http-basic-application-password",
        )
        diagnostic.mailbox_authentication = "PASS"
        response = transport.json(
            f"{base_url}/api",
            admin_authorization,
            payload=api_key_payload(expires_at),
            endpoint_path="/api",
            jmap_method="x:ApiKey/set",
            authentication_mechanism="oauth2-bearer-management-jmap",
        )
        api_key = extract_created_secret(response)
        diagnostic.sensitive_values.append(api_key)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(f"STALWART_API_KEY={api_key}\n")
            handle.write(f"STALWART_JMAP_SERVICE_TOKEN=Basic {mailbox_basic}\n")
            handle.flush()
            os.fsync(handle.fileno())
        completed = True
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not completed:
            output.unlink(missing_ok=True)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument(
        "--output",
        type=Path,
        default=Path("/etc/aiat/resend-certification.env"),
    )
    value.add_argument("--expires-in-hours", type=int, default=24)
    value.add_argument("--url", default=LOCAL_URL)
    value.add_argument("--administrator-address")
    value.add_argument("--diagnose", action="store_true")
    return value


def print_diagnostic(state: DiagnosticState) -> None:
    print(f"ENDPOINT_PATH={state.endpoint_path}", file=sys.stderr)
    print(f"AUTHENTICATION_MECHANISM={state.authentication_mechanism}", file=sys.stderr)
    print(f"HTTP_STATUS={state.http_status}", file=sys.stderr)
    print(f"JMAP_METHOD={state.jmap_method}", file=sys.stderr)
    print(f"JMAP_ERROR_TYPE={state.error_type}", file=sys.stderr)
    print(f"DESCRIPTION={state.description}", file=sys.stderr)
    print(
        "SYS_API_KEY_CREATE_PREFLIGHT="
        + state.create_permission_preflight,
        file=sys.stderr,
    )
    print(
        "ADMINISTRATOR_AUTHENTICATION="
        + state.administrator_authentication,
        file=sys.stderr,
    )
    print(
        "MAILBOX_APPLICATION_PASSWORD_VALIDATION="
        + state.mailbox_authentication,
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.url != LOCAL_URL:
        raise Refused(f"provisioning must remain local at {LOCAL_URL}")
    if not 1 <= args.expires_in_hours <= 168:
        raise Refused("--expires-in-hours must be between 1 and 168")
    admin_name = (
        args.administrator_address
        or input("Existing Stalwart administrator address: ").strip()
    )
    admin_password = getpass.getpass("Existing Stalwart administrator password: ")
    app_password = getpass.getpass(
        f"Existing application password for {GATEWAY_ACCOUNT}: "
    )
    if not admin_name or not admin_password:
        raise Refused("existing administrator credential is required")
    if not app_password.startswith("app_") or any(character.isspace() for character in app_password):
        raise Refused("a valid v0.16.7 gateway-test application password is required")
    expires_at = (
        datetime.now(UTC) + timedelta(hours=args.expires_in_hours)
    ).isoformat(timespec="seconds").replace("+00:00", "Z")
    diagnostic = DiagnosticState()
    diagnostic.sensitive_values.extend(
        [admin_password, app_password]
    )
    try:
        try:
            provision(
                transport=HttpTransport(diagnostic),
                administrator_address=admin_name,
                administrator_password=admin_password,
                app_password=app_password,
                base_url=args.url,
                output=args.output.resolve(),
                expires_at=expires_at,
                diagnostic=diagnostic,
            )
        except Refused:
            if args.diagnose:
                print_diagnostic(diagnostic)
                raise DiagnosedRefused("diagnostic emitted") from None
            raise
    finally:
        admin_password = ""
        app_password = ""
        diagnostic.sensitive_values.clear()
    print("STALWART_API_KEY_PROVISIONING=PASS")
    print(f"PROTECTED_CREDENTIAL_FILE={args.output.resolve()}")
    print(f"API_KEY_EXPIRES_AT={expires_at}")
    print("API_KEY_SECRET_PRINTED=NONE")
    print("ADMIN_ACCOUNT_MUTATION=NONE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DiagnosedRefused:
        raise SystemExit(1) from None
    except Refused as exc:
        print(f"Stalwart ApiKey provisioning refused: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
