#!/usr/bin/env python3
"""Interactively create one v0.16.7 ApiKey object and protect its one-time secret."""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import getpass
import json
import os
from pathlib import Path
import sys
from typing import Any
from urllib import error, request


LOCAL_URL = "http://127.0.0.1:18080"
GATEWAY_ACCOUNT = "gateway-test@agents.aiat.ca"
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
        error_type: str = "not-reached",
        description: str = "request did not reach Stalwart",
        administrator_authentication: bool = False,
        create_permission_preflight: bool = False,
        mailbox_authentication: bool = False,
        sensitive_values: list[str] | None = None,
    ):
        self.endpoint_path = endpoint_path
        self.http_status = http_status
        self.jmap_method = jmap_method
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
    error_type: str,
    description: Any,
    authentication: bool = False,
) -> None:
    category = classify_error(error_type, authentication=authentication)
    state.endpoint_path = endpoint_path
    state.http_status = http_status
    state.jmap_method = jmap_method
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
        authorization: str,
        *,
        payload: dict[str, Any] | None = None,
        endpoint_path: str,
        jmap_method: str,
        authentication: bool = False,
    ) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        message = request.Request(
            url,
            data=body,
            method="GET" if body is None else "POST",
            headers={
                "Authorization": authorization,
                "Content-Type": "application/json",
            },
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
                error_type="notJson",
                description="Stalwart returned malformed JSON",
            )
        if not isinstance(value, dict):
            fail_with_diagnostic(
                self.diagnostic,
                endpoint_path=endpoint_path,
                http_status="200",
                jmap_method=jmap_method,
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
            state.error_type = "missing-sysApiKeyCreate/missingPermission"
            state.description = "authenticated account lacks sysApiKeyCreate"
        raise Refused(
            "authenticated account lacks sysApiKeyCreate; its role/password was not modified"
        )
    if state is not None:
        state.create_permission_preflight = True


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
    admin_authorization: str,
    app_password: str,
    output: Path,
    expires_at: str,
    diagnostic: DiagnosticState,
) -> None:
    descriptor = reserve_output(output)
    completed = False
    try:
        account = transport.json(
            f"{LOCAL_URL}/api/account",
            admin_authorization,
            endpoint_path="/api/account",
            jmap_method="GET /api/account",
            authentication=True,
        )
        diagnostic.administrator_authentication = True
        require_create_permission(account, diagnostic)
        mailbox_basic = base64.b64encode(
            f"{GATEWAY_ACCOUNT}:{app_password}".encode()
        ).decode()
        diagnostic.sensitive_values.extend(
            [mailbox_basic, f"Basic {mailbox_basic}"]
        )
        transport.json(
            f"{LOCAL_URL}/api/account",
            f"Basic {mailbox_basic}",
            endpoint_path="/api/account",
            jmap_method="GET /api/account",
            authentication=True,
        )
        diagnostic.mailbox_authentication = True
        response = transport.json(
            f"{LOCAL_URL}/api",
            admin_authorization,
            payload=api_key_payload(expires_at),
            endpoint_path="/api",
            jmap_method="x:ApiKey/set",
        )
        api_key = extract_created_secret(response)
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
    print(f"HTTP_STATUS={state.http_status}", file=sys.stderr)
    print(f"JMAP_METHOD={state.jmap_method}", file=sys.stderr)
    print(f"JMAP_ERROR_TYPE={state.error_type}", file=sys.stderr)
    print(f"DESCRIPTION={state.description}", file=sys.stderr)
    print(
        "SYS_API_KEY_CREATE_PREFLIGHT="
        + ("PASS" if state.create_permission_preflight else "FAIL"),
        file=sys.stderr,
    )
    print(
        "ADMINISTRATOR_AUTHENTICATION="
        + ("PASS" if state.administrator_authentication else "FAIL"),
        file=sys.stderr,
    )
    print(
        "MAILBOX_APPLICATION_PASSWORD_VALIDATION="
        + ("PASS" if state.mailbox_authentication else "FAIL"),
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
    admin_basic = base64.b64encode(f"{admin_name}:{admin_password}".encode()).decode()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(hours=args.expires_in_hours)
    ).isoformat(timespec="seconds").replace("+00:00", "Z")
    diagnostic = DiagnosticState()
    diagnostic.sensitive_values.extend(
        [admin_password, app_password, admin_basic, f"Basic {admin_basic}"]
    )
    try:
        try:
            provision(
                transport=HttpTransport(diagnostic),
                admin_authorization=f"Basic {admin_basic}",
                app_password=app_password,
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
        admin_basic = ""
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
        raise SystemExit(1)
    except Refused as exc:
        print(f"Stalwart ApiKey provisioning refused: {exc}", file=sys.stderr)
        raise SystemExit(1)
