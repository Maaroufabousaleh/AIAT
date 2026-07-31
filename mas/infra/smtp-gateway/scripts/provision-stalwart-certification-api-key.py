#!/usr/bin/env python3
"""Safely create one patched-v0.16 ApiKey and protect its one-time secret."""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib import error, parse, request

LOCAL_URL = "http://127.0.0.1:18080"
GATEWAY_ACCOUNT = "gateway-test@agents.aiat.ca"
PERMANENT_ADMINISTRATOR_ADDRESS = "admin@agents.aiat.local"
OAUTH_CLIENT_ID = "stalwart-webui"
OAUTH_REDIRECT_PATH = "/admin/oauth/callback"
STALWART_CONTAINER = "mas-stalwart-1"
PATCHED_IMAGE_REFS = {
    "ghcr.io/stalwartlabs/stalwart:v0.16.15@"
    "sha256:4f926193e5dd9ceb1e24ba48160702310381b12e51972c2fb0cc9de020388136",
    "ghcr.io/stalwartlabs/stalwart:v0.16.15@"
    "sha256:258b76c783f298500c5c065bebf09e1f9d773040803c5715b7c35357e529713c",
}
KEY_DESCRIPTION = "AIAT Resend certification read-only"
API_KEY_CREATE_ID = "certification-key"
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


class RecoveryAdministratorRefused(Refused):
    """The authenticated principal is the non-directory recovery administrator."""

    pass


class DiagnosticState:
    def __init__(
        self,
        *,
        endpoint_path: str = "not-reached",
        http_status: str = "not-reached",
        jmap_method: str = "not-reached",
        authentication_mechanism: str = "not-reached",
        exception_class: str = "NONE",
        error_type: str = "not-reached",
        description: str = "request did not reach Stalwart",
        administrator_authentication: str = "NOT_ATTEMPTED",
        account_permission_persisted: str = "NOT_ATTEMPTED",
        token_scope_contains_create: str = "NOT_ATTEMPTED",
        token_scope_contains_query: str = "NOT_ATTEMPTED",
        api_key_query: str = "NOT_ATTEMPTED",
        permanent_directory_principal: str = "NOT_ATTEMPTED",
        api_key_create_capability: str = "NOT_ATTEMPTED",
        mailbox_authentication: str = "NOT_ATTEMPTED",
        sensitive_values: list[str] | None = None,
    ):
        self.endpoint_path = endpoint_path
        self.http_status = http_status
        self.jmap_method = jmap_method
        self.authentication_mechanism = authentication_mechanism
        self.exception_class = exception_class
        self.error_type = error_type
        self.description = description
        self.administrator_authentication = administrator_authentication
        self.account_permission_persisted = account_permission_persisted
        self.token_scope_contains_create = token_scope_contains_create
        self.token_scope_contains_query = token_scope_contains_query
        self.api_key_query = api_key_query
        self.permanent_directory_principal = permanent_directory_principal
        self.api_key_create_capability = api_key_create_capability
        self.mailbox_authentication = mailbox_authentication
        self.sensitive_values = sensitive_values or []
        self.api_key_create_fields: list[str] = []
        self.api_key_create_field_types: list[str] = []
        self.api_key_invalid_properties: list[str] = []
        self.attempts: list[dict[str, str]] = []

    def record_attempt(
        self,
        *,
        endpoint_path: str,
        authentication_mechanism: str,
        http_status: str,
        jmap_method: str,
        error_type: str,
        description: str,
        exception_class: str = "NONE",
    ) -> None:
        item = {
            "endpoint_path": endpoint_path,
            "authentication_mechanism": authentication_mechanism,
            "http_status": http_status,
            "jmap_method": jmap_method,
            "error_type": error_type,
            "description": description,
            "exception_class": exception_class,
        }
        self.attempts.append(item)
        self.endpoint_path = endpoint_path
        self.authentication_mechanism = authentication_mechanism
        self.http_status = http_status
        self.jmap_method = jmap_method
        self.exception_class = exception_class
        self.error_type = error_type
        self.description = description


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
    text = " ".join(str(value or "").split())
    for secret in sorted(sensitive_values or [], key=len, reverse=True):
        if secret:
            text = text.replace(secret, "<redacted>")
    for pattern, replacement in SECRET_PATTERNS:
        text = re.sub(pattern, replacement, text)
    text = "".join(character for character in text if character.isprintable())
    return (text or "no description supplied")[:limit]


def _safe_property_name(value: Any) -> str:
    """Return a bounded property name without ever exposing a property value."""
    text = "".join(character for character in str(value) if character.isprintable())
    text = re.sub(r"[^A-Za-z0-9_.@-]", "_", text)
    return text[:64] or "<empty>"


def _safe_value_type(value: Any) -> str:
    if isinstance(value, str):
        return "string"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, (int, float)):
        return "number"
    if value is None:
        return "null"
    return "other"


def record_api_key_create_payload(
    diagnostic: DiagnosticState, payload: dict[str, Any]
) -> None:
    """Record only create property names and JSON-safe value types."""
    for item in payload.get("methodCalls") or []:
        if not isinstance(item, list) or len(item) < 2 or item[0] != "x:ApiKey/set":
            continue
        arguments = item[1]
        if not isinstance(arguments, dict):
            continue
        creates = arguments.get("create")
        if not isinstance(creates, dict):
            continue
        create_object = creates.get(API_KEY_CREATE_ID)
        if not isinstance(create_object, dict):
            continue
        diagnostic.api_key_create_fields = [
            _safe_property_name(name) for name in create_object
        ]
        diagnostic.api_key_create_field_types = [
            f"{_safe_property_name(name)}:{_safe_value_type(value)}"
            for name, value in create_object.items()
        ]
        return


def record_invalid_api_key_properties(
    diagnostic: DiagnosticState, properties: Any
) -> None:
    if not isinstance(properties, list):
        return
    diagnostic.api_key_invalid_properties = [
        _safe_property_name(value) for value in properties if isinstance(value, str)
    ][:32]


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


def response_failure(value: dict[str, Any]) -> tuple[str, str, Any] | None:
    for item in value.get("methodResponses") or []:
        if not isinstance(item, list) or len(item) < 2 or not isinstance(item[1], dict):
            continue
        if item[0] == "error":
            return (
                str(item[1].get("type") or "jmapError"),
                str(item[1].get("description") or "JMAP method failed"),
                item[1].get("properties"),
            )
        if item[0] == "x:ApiKey/set":
            failed: dict[str, Any] = {}
            for key in ("notCreated", "notDestroyed"):
                values = item[1].get(key)
                if isinstance(values, dict):
                    failed.update(values)
            if isinstance(failed, dict) and failed:
                failure = next(iter(failed.values()))
                if isinstance(failure, dict):
                    return (
                        str(failure.get("type") or "setError"),
                        str(failure.get("description") or "ApiKey creation failed"),
                        failure.get("properties"),
                    )
    return None


def response_error(value: dict[str, Any]) -> tuple[str, str] | None:
    """Compatibility helper for callers that only need the type and description."""
    failure = response_failure(value)
    return failure[:2] if failure else None


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
    exception_class: str = "Refused",
) -> None:
    category = classify_error(error_type, authentication=authentication)
    safe_error_type = (
        f"{category}/{sanitize_description(error_type, 64, state.sensitive_values)}"
    )
    safe_description = sanitize_description(
        description, sensitive_values=state.sensitive_values
    )
    state.record_attempt(
        endpoint_path=endpoint_path,
        http_status=http_status,
        jmap_method=jmap_method,
        authentication_mechanism=authentication_mechanism,
        error_type=safe_error_type,
        description=safe_description,
        exception_class=exception_class,
    )
    raise Refused("Stalwart rejected the local provisioning request")


def fail_recovery_administrator_query(
    state: DiagnosticState,
    *,
    endpoint_path: str,
    jmap_method: str,
    authentication_mechanism: str,
    description: Any,
) -> None:
    safe_description = sanitize_description(
        description,
        sensitive_values=state.sensitive_values,
    )
    state.record_attempt(
        endpoint_path=endpoint_path,
        http_status="200",
        jmap_method=jmap_method,
        authentication_mechanism=authentication_mechanism,
        error_type="recovery-administrator/account-not-found",
        description=safe_description,
        exception_class="JmapMethodError",
    )
    raise RecoveryAdministratorRefused(
        "authenticated principal appears to be the recovery administrator; "
        "use the permanent directory administrator account"
    )


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
        if payload is not None:
            record_api_key_create_payload(self.diagnostic, payload)
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
                exception_class=type(exc).__name__,
            )
        except (error.URLError, TimeoutError, OSError) as exc:
            fail_with_diagnostic(
                self.diagnostic,
                endpoint_path=endpoint_path,
                http_status="unavailable",
                jmap_method=jmap_method,
                authentication_mechanism=authentication_mechanism,
                error_type="transportError",
                description="request did not reach Stalwart",
                authentication=False,
                exception_class=type(exc).__name__,
            )
        except json.JSONDecodeError as exc:
            fail_with_diagnostic(
                self.diagnostic,
                endpoint_path=endpoint_path,
                http_status="200",
                jmap_method=jmap_method,
                authentication_mechanism=authentication_mechanism,
                error_type="notJson",
                description="Stalwart returned malformed JSON",
                exception_class=type(exc).__name__,
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
                exception_class="ResponseValidationError",
            )
        failure = response_failure(value)
        if failure:
            if jmap_method == "x:ApiKey/set" and failure[0].lower() == "invalidpatch":
                record_invalid_api_key_properties(self.diagnostic, failure[2])
            if (
                jmap_method == "x:ApiKey/query"
                and failure[0].lower() == "forbidden"
                and "account not found" in failure[1].lower()
            ):
                fail_recovery_administrator_query(
                    self.diagnostic,
                    endpoint_path=endpoint_path,
                    jmap_method=jmap_method,
                    authentication_mechanism=authentication_mechanism,
                    description=(
                        "Account not found; supplied credential appears to be the "
                        "recovery administrator"
                    ),
                )
            fail_with_diagnostic(
                self.diagnostic,
                endpoint_path=endpoint_path,
                http_status="200",
                jmap_method=jmap_method,
                authentication_mechanism=authentication_mechanism,
                error_type=failure[0],
                description=failure[1],
                exception_class="JmapMethodError",
            )
        self.diagnostic.record_attempt(
            endpoint_path=endpoint_path,
            http_status="200",
            jmap_method=jmap_method,
            authentication_mechanism=authentication_mechanism,
            error_type="none",
            description="request succeeded",
            exception_class="NONE",
        )
        return value


def api_key_permissions(
    *, permission_mode: str = "replace", permissions: list[str] | None = None
) -> dict[str, Any]:
    if permission_mode == "inherit":
        return {"@type": "Inherit"}
    if permission_mode != "replace":
        raise ValueError("permission_mode must be replace or inherit")
    selected = list(REQUIRED_KEY_PERMISSIONS if permissions is None else permissions)
    if not selected or any(not isinstance(value, str) or not value for value in selected):
        raise ValueError("a Replace API key requires non-empty permission names")
    return {
        "@type": "Replace",
        "permissions": {permission: True for permission in selected},
    }


def api_key_payload(
    expires_at: str,
    *,
    permission_mode: str = "replace",
    allowed_ips: Any = None,
) -> dict[str, Any]:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", expires_at):
        raise ValueError("expires_at must be an RFC 3339 UTC timestamp without fractions")
    create_object: dict[str, Any] = {
        "description": KEY_DESCRIPTION,
        "expiresAt": expires_at,
        "permissions": api_key_permissions(permission_mode=permission_mode),
    }
    if allowed_ips:
        create_object["allowedIps"] = allowed_ips
    return {
        "using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"],
        "methodCalls": [
            [
                "x:ApiKey/set",
                {
                    "create": {
                        API_KEY_CREATE_ID: create_object,
                    }
                },
                "create-certification-api-key",
            ]
        ],
    }


def token_scope_contains_create(
    account: dict[str, Any], state: DiagnosticState | None = None
) -> bool:
    permissions = account.get("permissions")
    present = isinstance(permissions, list) and "sysApiKeyCreate" in permissions
    if state is not None:
        state.token_scope_contains_create = "PASS" if present else "FAIL"
    return present


def token_scope_contains_query(
    account: dict[str, Any], state: DiagnosticState | None = None
) -> bool:
    permissions = account.get("permissions")
    present = isinstance(permissions, list) and "sysApiKeyQuery" in permissions
    if state is not None:
        state.token_scope_contains_query = "PASS" if present else "FAIL"
    return present


def require_permanent_directory_principal(
    administrator_address: str,
    diagnostic: DiagnosticState,
) -> None:
    if administrator_address == PERMANENT_ADMINISTRATOR_ADDRESS:
        diagnostic.permanent_directory_principal = "PASS"
        return
    diagnostic.permanent_directory_principal = "FAIL"
    fail_with_diagnostic(
        diagnostic,
        endpoint_path="/jmap/",
        http_status="200",
        jmap_method="x:ApiKey/query",
        authentication_mechanism="oauth2-bearer-management-jmap",
        error_type="recovery-administrator/permanentDirectoryPrincipalRequired",
        description=(
            "API-key ownership requires the permanent directory administrator "
            "account; the recovery administrator must not be used"
        ),
    )


def method_result(value: dict[str, Any], method_name: str) -> dict[str, Any]:
    for item in value.get("methodResponses") or []:
        if (
            isinstance(item, list)
            and len(item) >= 2
            and item[0] == method_name
            and isinstance(item[1], dict)
        ):
            return item[1]
    raise Refused(f"Stalwart response did not include {method_name}")


def endpoint_path(url: str) -> str:
    parts = parse.urlsplit(url)
    return parts.path or "/"


def resolve_jmap_api_url(base_url: str, advertised_url: str) -> str:
    """Keep the advertised JMAP path/query while binding it to base_url."""
    base = parse.urlsplit(base_url)
    advertised = parse.urlsplit(advertised_url)
    if (
        base.scheme not in {"http", "https"}
        or not base.hostname
        or base.username is not None
        or base.password is not None
        or advertised.scheme not in {"http", "https"}
        or not advertised.hostname
        or advertised.username is not None
        or advertised.password is not None
        or not advertised.path.startswith("/")
        or advertised.fragment
    ):
        raise ValueError("JMAP session apiUrl is not a valid absolute URL")
    # urlsplit preserves the advertised path exactly, including a trailing
    # slash and query string, while the configured base supplies authority.
    return parse.urlunsplit(
        (base.scheme, base.netloc, advertised.path, advertised.query, "")
    )


def discover_jmap_api_url(
    *,
    transport: HttpTransport,
    base_url: str,
    authorization: str,
    diagnostic: DiagnosticState,
) -> str:
    response = transport.json(
        f"{base_url}/jmap/session",
        authorization,
        endpoint_path="/jmap/session",
        jmap_method="GET /jmap/session",
        authentication=True,
        authentication_mechanism="oauth2-bearer-jmap-session",
    )
    advertised = response.get("apiUrl")
    if not isinstance(advertised, str) or not advertised:
        fail_with_diagnostic(
            diagnostic,
            endpoint_path="/jmap/session",
            http_status="200",
            jmap_method="GET /jmap/session",
            authentication_mechanism="oauth2-bearer-jmap-session",
            error_type="malformedJmapSession",
            description="Stalwart JMAP session did not contain a valid apiUrl",
            exception_class="SessionValidationError",
        )
    try:
        return resolve_jmap_api_url(base_url, advertised)
    except ValueError as exc:
        fail_with_diagnostic(
            diagnostic,
            endpoint_path="/jmap/session",
            http_status="200",
            jmap_method="GET /jmap/session",
            authentication_mechanism="oauth2-bearer-jmap-session",
            error_type="malformedJmapSession",
            description="Stalwart JMAP session apiUrl was invalid",
            exception_class=type(exc).__name__,
        )


def _enabled_permission(permissions: Any, permission: str) -> bool:
    if not isinstance(permissions, dict) or permissions.get("@type") not in {
        "Merge",
        "Replace",
    }:
        return False
    enabled = permissions.get("enabledPermissions")
    disabled = permissions.get("disabledPermissions")
    enabled_set = (
        {key for key, value in enabled.items() if value}
        if isinstance(enabled, dict)
        else set(enabled or [])
    )
    disabled_set = (
        {key for key, value in disabled.items() if value}
        if isinstance(disabled, dict)
        else set(disabled or [])
    )
    return permission in enabled_set and permission not in disabled_set


def prove_persisted_create_permission(
    *,
    transport: HttpTransport,
    jmap_url: str,
    authorization: str,
    administrator_address: str,
    diagnostic: DiagnosticState,
) -> None:
    local_part, separator, domain = administrator_address.rpartition("@")
    if not separator or not local_part or not domain:
        raise Refused("administrator address must contain a local part and domain")
    diagnostic.account_permission_persisted = "FAIL"
    jmap_endpoint_path = endpoint_path(jmap_url)
    domain_response = transport.json(
        jmap_url,
        authorization,
        payload={
            "using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"],
            "methodCalls": [
                [
                    "x:Domain/query",
                    {"filter": {"name": domain}, "limit": 2},
                    "administrator-domain",
                ]
            ],
        },
        endpoint_path=jmap_endpoint_path,
        jmap_method="x:Domain/query",
        authentication_mechanism="oauth2-bearer-management-jmap",
    )
    domain_ids = method_result(domain_response, "x:Domain/query").get("ids") or []
    if len(domain_ids) != 1:
        raise Refused("administrator domain did not resolve to exactly one persisted object")
    query_response = transport.json(
        jmap_url,
        authorization,
        payload={
            "using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"],
            "methodCalls": [
                [
                    "x:Account/query",
                    {
                        "filter": {"name": local_part, "domainId": str(domain_ids[0])},
                        "limit": 2,
                    },
                    "administrator-account",
                ]
            ],
        },
        endpoint_path=jmap_endpoint_path,
        jmap_method="x:Account/query",
        authentication_mechanism="oauth2-bearer-management-jmap",
    )
    account_ids = method_result(query_response, "x:Account/query").get("ids") or []
    if len(account_ids) != 1:
        raise Refused("administrator address did not resolve to exactly one persisted account")
    get_response = transport.json(
        jmap_url,
        authorization,
        payload={
            "using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"],
            "methodCalls": [
                [
                    "x:Account/get",
                    {"ids": [str(account_ids[0])], "properties": ["name", "domainId", "permissions"]},
                    "administrator-account-permissions",
                ]
            ],
        },
        endpoint_path=jmap_endpoint_path,
        jmap_method="x:Account/get",
        authentication_mechanism="oauth2-bearer-management-jmap",
    )
    accounts = method_result(get_response, "x:Account/get").get("list") or []
    if len(accounts) != 1 or not _enabled_permission(
        accounts[0].get("permissions") if isinstance(accounts[0], dict) else None,
        "sysApiKeyCreate",
    ):
        diagnostic.error_type = "missing-persisted-sysApiKeyCreate/missingPermission"
        diagnostic.description = "persisted account object does not enable sysApiKeyCreate"
        raise Refused("persisted administrator account lacks sysApiKeyCreate")
    diagnostic.account_permission_persisted = "PASS"
    diagnostic.permanent_directory_principal = "PASS"


def inspect_running_image(container_name: str = STALWART_CONTAINER) -> str:
    try:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.Config.Image}}",
                container_name,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        raise Refused("cannot verify the running Stalwart image safely") from exc
    image = result.stdout.strip()
    if not image:
        raise Refused("running Stalwart image reference is empty")
    return image


def require_patched_server(image: str, diagnostic: DiagnosticState) -> None:
    if image not in PATCHED_IMAGE_REFS:
        diagnostic.endpoint_path = "local-docker-inspect"
        diagnostic.http_status = "not-applicable"
        diagnostic.jmap_method = "not-attempted"
        diagnostic.authentication_mechanism = "none"
        diagnostic.error_type = "unsafe-stalwart-version/scopedCredentialEscalation"
        diagnostic.description = (
            "ApiKey provisioning requires the pinned v0.16.15 security-patched image"
        )
        raise Refused(
            "Stalwart ApiKey provisioning is blocked until the running image is "
            "the approved v0.16.15 security-patched digest"
        )


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
    """Authenticate through the pinned v0.16.15 WebUI OAuth code/PKCE flow."""

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


def extract_created_credential(response: dict[str, Any]) -> tuple[str, str]:
    for item in response.get("methodResponses") or []:
        if isinstance(item, list) and len(item) >= 2 and item[0] == "x:ApiKey/set":
            result = item[1] if isinstance(item[1], dict) else {}
            created = result.get("created") or {}
            value = created.get(API_KEY_CREATE_ID) or {}
            secret = value.get("secret") if isinstance(value, dict) else None
            credential_id = value.get("id") if isinstance(value, dict) else None
            if (
                isinstance(credential_id, str)
                and credential_id
                and isinstance(secret, str)
                and secret.startswith("API_")
                and len(secret) > 20
            ):
                return credential_id, secret
    raise Refused("Stalwart did not return the one-time ApiKey secret")


def refuse_duplicate_key(
    *,
    transport: HttpTransport,
    jmap_url: str,
    authorization: str,
    diagnostic: DiagnosticState,
) -> None:
    jmap_endpoint_path = endpoint_path(jmap_url)
    diagnostic.api_key_query = "FAIL"
    query_response = transport.json(
        jmap_url,
        authorization,
        payload={
            "using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"],
            "methodCalls": [["x:ApiKey/query", {"limit": 100}, "existing-api-keys"]],
        },
        endpoint_path=jmap_endpoint_path,
        jmap_method="x:ApiKey/query",
        authentication_mechanism="oauth2-bearer-management-jmap",
    )
    ids = method_result(query_response, "x:ApiKey/query").get("ids") or []
    diagnostic.api_key_query = "PASS"
    if not ids:
        return
    get_response = transport.json(
        jmap_url,
        authorization,
        payload={
            "using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"],
            "methodCalls": [
                [
                    "x:ApiKey/get",
                    {"ids": [str(value) for value in ids], "properties": ["description", "expiresAt"]},
                    "existing-api-key-details",
                ]
            ],
        },
        endpoint_path=jmap_endpoint_path,
        jmap_method="x:ApiKey/get",
        authentication_mechanism="oauth2-bearer-management-jmap",
    )
    existing = method_result(get_response, "x:ApiKey/get").get("list") or []
    if any(
        isinstance(value, dict) and value.get("description") == KEY_DESCRIPTION
        for value in existing
    ):
        description = (
            "an AIAT Resend certification API key already exists; revoke it explicitly "
            "before retrying"
        )
        diagnostic.record_attempt(
            endpoint_path=jmap_endpoint_path,
            http_status="200",
            jmap_method="x:ApiKey/query+x:ApiKey/get",
            authentication_mechanism="oauth2-bearer-management-jmap",
            error_type="duplicate-api-key/existingCredential",
            description=description,
            exception_class="DuplicateCredentialError",
        )
        raise Refused("duplicate AIAT Resend certification API key refused")


def destroy_created_key(
    *,
    transport: HttpTransport,
    jmap_url: str,
    authorization: str,
    credential_id: str,
) -> None:
    response = transport.json(
        jmap_url,
        authorization,
        payload={
            "using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"],
            "methodCalls": [
                [
                    "x:ApiKey/set",
                    {"destroy": [credential_id]},
                    "cleanup-created-api-key",
                ]
            ],
        },
        endpoint_path=endpoint_path(jmap_url),
        jmap_method="x:ApiKey/set cleanup",
        authentication_mechanism="oauth2-bearer-management-jmap",
    )
    destroyed = method_result(response, "x:ApiKey/set").get("destroyed") or []
    if credential_id not in destroyed:
        raise Refused("failed to clean up the created API key after local file failure")


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
    server_image: str,
    diagnostic: DiagnosticState,
) -> None:
    descriptor = reserve_output(output)
    completed = False
    try:
        require_patched_server(server_image, diagnostic)
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
        if not token_scope_contains_create(account, diagnostic):
            diagnostic.endpoint_path = "/api/account"
            diagnostic.http_status = "200"
            diagnostic.jmap_method = "GET /api/account"
            diagnostic.authentication_mechanism = "oauth2-bearer"
            diagnostic.error_type = "token-scope-missing-sysApiKeyCreate/missingPermission"
            diagnostic.description = (
                "authenticated account lacks sysApiKeyCreate"
            )
            raise Refused("OAuth Bearer token lacks sysApiKeyCreate")
        if not token_scope_contains_query(account, diagnostic):
            diagnostic.endpoint_path = "/api/account"
            diagnostic.http_status = "200"
            diagnostic.jmap_method = "GET /api/account"
            diagnostic.authentication_mechanism = "oauth2-bearer"
            diagnostic.error_type = "token-scope-missing-sysApiKeyQuery/missingPermission"
            diagnostic.description = (
                "authenticated account lacks sysApiKeyQuery"
            )
            raise Refused("OAuth Bearer token lacks sysApiKeyQuery")
        jmap_url = discover_jmap_api_url(
            transport=transport,
            base_url=base_url,
            authorization=admin_authorization,
            diagnostic=diagnostic,
        )
        refuse_duplicate_key(
            transport=transport,
            jmap_url=jmap_url,
            authorization=admin_authorization,
            diagnostic=diagnostic,
        )
        require_permanent_directory_principal(administrator_address, diagnostic)
        prove_persisted_create_permission(
            transport=transport,
            jmap_url=jmap_url,
            authorization=admin_authorization,
            administrator_address=administrator_address,
            diagnostic=diagnostic,
        )
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
        diagnostic.api_key_create_capability = "FAIL"
        response = transport.json(
            jmap_url,
            admin_authorization,
            payload=api_key_payload(expires_at),
            endpoint_path=endpoint_path(jmap_url),
            jmap_method="x:ApiKey/set",
            authentication_mechanism="oauth2-bearer-management-jmap",
        )
        credential_id, api_key = extract_created_credential(response)
        diagnostic.api_key_create_capability = "PASS"
        diagnostic.sensitive_values.append(api_key)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                handle.write(f"STALWART_API_KEY={api_key}\n")
                handle.write(f"STALWART_JMAP_SERVICE_TOKEN=Basic {mailbox_basic}\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            diagnostic.api_key_create_capability = "FAIL"
            destroy_created_key(
                transport=transport,
                jmap_url=jmap_url,
                authorization=admin_authorization,
                credential_id=credential_id,
            )
            raise Refused(
                "protected credential file write failed; created API key was removed"
            ) from exc
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
    value.add_argument("--container-name", default=STALWART_CONTAINER)
    value.add_argument("--diagnose", action="store_true")
    return value


def print_diagnostic(state: DiagnosticState) -> None:
    print(f"ENDPOINT_PATH={state.endpoint_path}", file=sys.stderr)
    print(f"AUTHENTICATION_MECHANISM={state.authentication_mechanism}", file=sys.stderr)
    print(f"HTTP_STATUS={state.http_status}", file=sys.stderr)
    print(f"JMAP_METHOD={state.jmap_method}", file=sys.stderr)
    print(f"EXCEPTION_CLASS={state.exception_class}", file=sys.stderr)
    print(f"JMAP_ERROR_TYPE={state.error_type}", file=sys.stderr)
    print(f"DESCRIPTION={state.description}", file=sys.stderr)
    print(
        "ADMINISTRATOR_AUTHENTICATION="
        + state.administrator_authentication,
        file=sys.stderr,
    )
    print(
        "ACCOUNT_PERMISSION_PERSISTED="
        + state.account_permission_persisted,
        file=sys.stderr,
    )
    print(
        "TOKEN_SCOPE_CONTAINS_SYS_API_KEY_CREATE="
        + state.token_scope_contains_create,
        file=sys.stderr,
    )
    print(
        "TOKEN_SCOPE_CONTAINS_SYS_API_KEY_QUERY="
        + state.token_scope_contains_query,
        file=sys.stderr,
    )
    print("API_KEY_QUERY=" + state.api_key_query, file=sys.stderr)
    print(
        "PERMANENT_DIRECTORY_PRINCIPAL="
        + state.permanent_directory_principal,
        file=sys.stderr,
    )
    print(
        "API_KEY_CREATE_CAPABILITY="
        + state.api_key_create_capability,
        file=sys.stderr,
    )
    print(
        "MAILBOX_APPLICATION_PASSWORD_VALIDATION="
        + state.mailbox_authentication,
        file=sys.stderr,
    )
    print(
        "API_KEY_CREATE_FIELDS="
        + (",".join(state.api_key_create_fields) or "NONE"),
        file=sys.stderr,
    )
    print(
        "API_KEY_CREATE_FIELD_TYPES="
        + (",".join(state.api_key_create_field_types) or "NONE"),
        file=sys.stderr,
    )
    print(
        "API_KEY_CREATE_INVALID_PROPERTIES="
        + (",".join(state.api_key_invalid_properties) or "NONE"),
        file=sys.stderr,
    )
    for index, attempt in enumerate(state.attempts, start=1):
        prefix = f"ATTEMPT_{index}_"
        print(prefix + "ENDPOINT_PATH=" + attempt["endpoint_path"], file=sys.stderr)
        print(
            prefix + "AUTHENTICATION_MECHANISM="
            + attempt["authentication_mechanism"],
            file=sys.stderr,
        )
        print(prefix + "HTTP_STATUS=" + attempt["http_status"], file=sys.stderr)
        print(prefix + "JMAP_METHOD=" + attempt["jmap_method"], file=sys.stderr)
        print(prefix + "EXCEPTION_CLASS=" + attempt["exception_class"], file=sys.stderr)
        print(prefix + "JMAP_ERROR_TYPE=" + attempt["error_type"], file=sys.stderr)
        print(prefix + "DESCRIPTION=" + attempt["description"], file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.url != LOCAL_URL:
        raise Refused(f"provisioning must remain local at {LOCAL_URL}")
    if not 1 <= args.expires_in_hours <= 168:
        raise Refused("--expires-in-hours must be between 1 and 168")
    diagnostic = DiagnosticState()
    try:
        server_image = inspect_running_image(args.container_name)
        require_patched_server(server_image, diagnostic)
    except Refused:
        if args.diagnose:
            print_diagnostic(diagnostic)
            raise DiagnosedRefused("diagnostic emitted") from None
        raise
    admin_name = args.administrator_address or input(
        "Permanent Stalwart administrator address "
        f"[{PERMANENT_ADMINISTRATOR_ADDRESS}]: "
    ).strip() or PERMANENT_ADMINISTRATOR_ADDRESS
    admin_password = getpass.getpass("Existing Stalwart administrator password: ")
    app_password = getpass.getpass(
        f"Existing application password for {GATEWAY_ACCOUNT}: "
    )
    if not admin_name or not admin_password:
        raise Refused("existing administrator credential is required")
    if not app_password.startswith("app_") or any(
        character.isspace() for character in app_password
    ):
        raise Refused("a valid gateway-test application password is required")
    expires_at = (
        datetime.now(UTC) + timedelta(hours=args.expires_in_hours)
    ).isoformat(timespec="seconds").replace("+00:00", "Z")
    diagnostic.sensitive_values.extend([admin_password, app_password])
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
                server_image=server_image,
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
