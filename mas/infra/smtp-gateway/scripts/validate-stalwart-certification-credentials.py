#!/usr/bin/env python3
"""Read-only validation of least-privilege Stalwart v0.16 credentials."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any
from urllib import error, parse, request

EXPECTED_DOMAIN = "agents.aiat.ca"
EXPECTED_ADDRESS = "gateway-test@agents.aiat.ca"
EXPECTED_URL = "http://127.0.0.1:18080"
MANAGEMENT_PERMISSIONS = {
    "authenticate",
    "sysAccountQuery",
    "sysDomainQuery",
    "sysMtaOutboundStrategyGet",
    "sysMtaRouteGet",
}
MAIL_PERMISSIONS = {
    "authenticate",
    "jmapEmailCreate",
    "jmapEmailGet",
    "jmapEmailSubmissionCreate",
    "jmapEmailUpdate",
    "jmapIdentityGet",
    "jmapMailboxGet",
}


class Refused(RuntimeError):
    pass


def endpoint_path(url: str) -> str:
    return parse.urlsplit(url).path or "/"


def resolve_jmap_api_url(base_url: str, advertised_url: str) -> str:
    """Resolve only the expected loopback JMAP endpoint."""
    base = parse.urlsplit(base_url)
    advertised = parse.urlsplit(advertised_url)
    if (
        base.scheme != "http"
        or base.hostname not in {"127.0.0.1", "localhost"}
        or base.username is not None
        or base.password is not None
        or base.query
        or base.fragment
        or advertised.scheme != "http"
        or advertised.hostname not in {"127.0.0.1", "localhost"}
        or advertised.username is not None
        or advertised.password is not None
        or advertised.port != base.port
        or advertised.path.rstrip("/") != "/jmap"
        or advertised.query
        or advertised.fragment
    ):
        raise Refused("Stalwart advertised an invalid JMAP apiUrl")
    return parse.urlunsplit((base.scheme, base.netloc, "/jmap/", "", ""))


def sanitize_diagnostic(value: Any, sensitive_values: list[str], limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    for secret in sorted(sensitive_values, key=len, reverse=True):
        if secret:
            text = text.replace(secret, "<redacted>")
    for pattern, replacement in (
        (r"API_[A-Za-z0-9_-]+", "<redacted-api-key>"),
        (r"(?i)\bBasic\s+[A-Za-z0-9+/=_-]+", "Basic <redacted>"),
        (r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+", "Bearer <redacted>"),
        (r"(?i)(password|secret|authorization|token)\s*[:=]\s*[^,\s]+", r"\1=<redacted>"),
    ):
        text = re.sub(pattern, replacement, text)
    text = "".join(character for character in text if character.isprintable())
    return (text or "no description supplied")[:limit]


def read_credentials(path: Path) -> dict[str, str]:
    try:
        details = path.stat()
    except FileNotFoundError as exc:
        raise Refused("credential file is missing") from exc
    if details.st_uid not in {0, os.getuid()}:
        raise Refused("credential file must be owned by root or the current user")
    if stat.S_IMODE(details.st_mode) != 0o600:
        raise Refused("credential file must have mode 0600")
    lines = path.read_text(encoding="utf-8").splitlines()
    expected = ["STALWART_API_KEY", "STALWART_JMAP_SERVICE_TOKEN"]
    if len(lines) != 2:
        raise Refused("credential file must contain exactly two lines")
    values: dict[str, str] = {}
    for index, key in enumerate(expected):
        prefix = f"{key}="
        if not lines[index].startswith(prefix) or not lines[index][len(prefix) :]:
            raise Refused(f"credential file line {index + 1} must contain exactly {key}")
        values[key] = lines[index][len(prefix) :]
    return values


def basic_mail_authorization(value: str) -> str:
    if not value.startswith("Basic "):
        raise Refused("STALWART_JMAP_SERVICE_TOKEN must be a Basic application-password credential")
    try:
        decoded = base64.b64decode(value[6:], validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise Refused("mail credential is not valid Basic authentication") from exc
    username, separator, password = decoded.partition(":")
    if separator != ":" or username.lower() != EXPECTED_ADDRESS or not password:
        raise Refused(f"mail credential must authenticate as {EXPECTED_ADDRESS}")
    return value


def require_exact_permissions(actual: Any, expected: set[str], credential: str) -> None:
    if not isinstance(actual, list) or any(not isinstance(item, str) for item in actual):
        raise Refused(f"{credential} effective permissions were not returned")
    actual_set = set(actual)
    missing = sorted(expected - actual_set)
    extra = sorted(actual_set - expected)
    if missing:
        raise Refused(f"{credential} is missing required permissions: {','.join(missing)}")
    if extra:
        raise Refused(f"{credential} is overprivileged: {','.join(extra)}")


class HttpTransport:
    def __init__(self, sensitive_values: list[str] | None = None):
        self.sensitive_values = list(sensitive_values or [])
        self.attempts: list[dict[str, str]] = []

    def _record(
        self,
        *,
        url: str,
        jmap_method: str,
        http_status: str,
        error_type: str,
        description: Any,
    ) -> None:
        self.attempts.append(
            {
                "endpoint_path": endpoint_path(url),
                "http_status": http_status,
                "jmap_method": jmap_method,
                "error_type": error_type,
                "description": sanitize_diagnostic(
                    description, self.sensitive_values
                ),
            }
        )

    def _refuse(
        self,
        *,
        url: str,
        jmap_method: str,
        http_status: str,
        error_type: str,
        description: Any,
    ) -> None:
        self._record(
            url=url,
            jmap_method=jmap_method,
            http_status=http_status,
            error_type=error_type,
            description=description,
        )
        attempt = self.attempts[-1]
        raise Refused(
            "Stalwart request failed: "
            f"ENDPOINT_PATH={attempt['endpoint_path']} "
            f"HTTP_STATUS={attempt['http_status']} "
            f"JMAP_METHOD={attempt['jmap_method']} "
            f"JMAP_ERROR_TYPE={attempt['error_type']} "
            f"DESCRIPTION={attempt['description']}"
        )

    def json(
        self,
        url: str,
        authorization: str,
        *,
        payload: dict[str, Any] | None = None,
        jmap_method: str | None = None,
    ) -> dict[str, Any]:
        method_name = jmap_method or ("GET " + endpoint_path(url) if payload is None else "JMAP")
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
            with request.urlopen(message, timeout=15) as response:
                value = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            raw = exc.read(8192)
            try:
                problem = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                problem = {}
            error_type = problem.get("type") if isinstance(problem, dict) else None
            description = (
                problem.get("description") or problem.get("detail")
                if isinstance(problem, dict)
                else None
            ) or exc.reason or "HTTP request failed"
            self._refuse(
                url=url,
                jmap_method=method_name,
                http_status=str(exc.code),
                error_type=str(error_type or "httpError"),
                description=description,
            )
        except (error.URLError, TimeoutError, OSError) as exc:
            self._refuse(
                url=url,
                jmap_method=method_name,
                http_status="unavailable",
                error_type="transportError",
                description=f"request did not reach Stalwart ({type(exc).__name__})",
            )
        except json.JSONDecodeError:
            self._refuse(
                url=url,
                jmap_method=method_name,
                http_status="200",
                error_type="notJson",
                description="Stalwart returned malformed JSON",
            )
        if not isinstance(value, dict):
            self._refuse(
                url=url,
                jmap_method=method_name,
                http_status="200",
                error_type="invalidResponse",
                description="Stalwart returned an invalid JSON response",
            )
        for item in value.get("methodResponses") or []:
            if isinstance(item, list) and len(item) >= 2 and item[0] == "error":
                details = item[1] if isinstance(item[1], dict) else {}
                self._refuse(
                    url=url,
                    jmap_method=method_name,
                    http_status="200",
                    error_type=str(details.get("type") or "jmapError"),
                    description=details.get("description") or "JMAP method failed",
                )
        self._record(
            url=url,
            jmap_method=method_name,
            http_status="200",
            error_type="none",
            description="request succeeded",
        )
        return value


def method_result(
    response: dict[str, Any], method: str, *, transport: HttpTransport | None = None
) -> dict[str, Any]:
    for item in response.get("methodResponses") or []:
        if (
            isinstance(item, list)
            and len(item) >= 2
            and item[0] == method
            and isinstance(item[1], dict)
        ):
            return item[1]
    if transport and transport.attempts:
        attempt = transport.attempts[-1]
        raise Refused(
            "Stalwart JMAP response missing method: "
            f"ENDPOINT_PATH={attempt['endpoint_path']} "
            f"HTTP_STATUS={attempt['http_status']} "
            f"JMAP_METHOD={method} "
            "JMAP_ERROR_TYPE=missingMethod "
            f"DESCRIPTION={sanitize_diagnostic('method response was not returned', transport.sensitive_values)}"
        )
    raise Refused(f"Stalwart did not authorize {method}")


def discover_jmap_api_url(
    credentials: dict[str, str],
    transport: HttpTransport,
    *,
    base_url: str,
) -> str:
    if base_url != EXPECTED_URL:
        raise Refused(f"credential validation must remain local at {EXPECTED_URL}")
    management_auth = f"Bearer {credentials['STALWART_API_KEY']}"
    response = transport.json(
        f"{base_url}/jmap/session",
        management_auth,
        jmap_method="GET /jmap/session",
    )
    advertised = response.get("apiUrl")
    if not isinstance(advertised, str) or not advertised:
        raise Refused(
            "Stalwart JMAP session failed: ENDPOINT_PATH=/jmap/session "
            "HTTP_STATUS=200 JMAP_METHOD=GET /jmap/session "
            "JMAP_ERROR_TYPE=malformedJmapSession "
            "DESCRIPTION=apiUrl was missing or invalid"
        )
    try:
        return resolve_jmap_api_url(base_url, advertised)
    except Refused as exc:
        raise Refused(
            "Stalwart JMAP session failed: ENDPOINT_PATH=/jmap/session "
            "HTTP_STATUS=200 JMAP_METHOD=GET /jmap/session "
            "JMAP_ERROR_TYPE=malformedJmapSession "
            "DESCRIPTION=apiUrl was not a safe absolute URL"
        ) from exc


def validate_live(
    credentials: dict[str, str],
    account_id: str,
    transport: HttpTransport,
    *,
    base_url: str = EXPECTED_URL,
) -> int:
    if base_url != EXPECTED_URL:
        raise Refused(f"credential validation must remain local at {EXPECTED_URL}")
    if not account_id:
        raise Refused("--account-id is required")
    resolved_account_id = lookup_account_id(credentials, transport, base_url=base_url)
    if resolved_account_id != account_id:
        raise Refused(f"accountId does not belong to {EXPECTED_ADDRESS}")
    mail_auth = basic_mail_authorization(credentials["STALWART_JMAP_SERVICE_TOKEN"])
    mail_account = transport.json(
        f"{base_url}/api/account",
        mail_auth,
        jmap_method="GET /api/account",
    )
    require_exact_permissions(mail_account.get("permissions"), MAIL_PERMISSIONS, "mail credential")
    jmap_url = discover_jmap_api_url(credentials, transport, base_url=base_url)
    return validate_mail_access(
        credentials["STALWART_JMAP_SERVICE_TOKEN"],
        account_id,
        transport,
        base_url=base_url,
        jmap_url=jmap_url,
        account_already_validated=True,
    )


def validate_mail_access(
    service_token: str,
    account_id: str,
    transport: HttpTransport,
    *,
    base_url: str,
    jmap_url: str,
    account_already_validated: bool = False,
) -> int:
    """Prove the gateway service credential is read-only and has zero submissions."""
    if base_url != EXPECTED_URL or jmap_url != f"{EXPECTED_URL}/jmap/":
        raise Refused("mail credential validation must remain at local /jmap/")
    if not account_id:
        raise Refused("mail credential validation requires an accountId")
    mail_auth = basic_mail_authorization(service_token)
    if not account_already_validated:
        mail_account = transport.json(
            f"{base_url}/api/account",
            mail_auth,
            jmap_method="GET /api/account",
        )
        require_exact_permissions(
            mail_account.get("permissions"), MAIL_PERMISSIONS, "mail credential"
        )
    mail_response = transport.json(
        jmap_url,
        mail_auth,
        payload={
            "using": [
                "urn:ietf:params:jmap:core",
                "urn:ietf:params:jmap:mail",
                "urn:ietf:params:jmap:submission",
            ],
            "methodCalls": [
                ["Mailbox/get", {"accountId": account_id}, "mailboxes"],
                ["Identity/get", {"accountId": account_id}, "identities"],
            ],
        },
        jmap_method="Mailbox/get+Identity/get",
    )
    if not (method_result(mail_response, "Mailbox/get", transport=transport).get("list") or []):
        raise Refused("mail credential cannot read the gateway-test mailbox")
    identities = method_result(mail_response, "Identity/get", transport=transport).get("list") or []
    if not any(
        isinstance(identity, dict)
        and str(identity.get("email") or "").lower() == EXPECTED_ADDRESS
        for identity in identities
    ):
        raise Refused(f"mail credential does not own {EXPECTED_ADDRESS}")
    submission_response = transport.json(
        jmap_url,
        mail_auth,
        payload={
            "using": [
                "urn:ietf:params:jmap:core",
                "urn:ietf:params:jmap:submission",
            ],
            "methodCalls": [
                [
                    "EmailSubmission/query",
                    {"accountId": account_id, "limit": 100},
                    "submissions",
                ]
            ],
        },
        jmap_method="EmailSubmission/query",
    )
    submission_result = method_result(
        submission_response,
        "EmailSubmission/query",
        transport=transport,
    )
    submission_ids = submission_result.get("ids")
    submission_total = submission_result.get("total")
    if (
        not isinstance(submission_ids, list)
        or any(not isinstance(item, str) for item in submission_ids)
        or not isinstance(submission_total, int)
        or isinstance(submission_total, bool)
        or submission_total != len(submission_ids)
    ):
        raise Refused("EmailSubmission inventory is malformed or incomplete")
    if submission_total != 0:
        raise Refused("EmailSubmission inventory is not zero")
    return submission_total


def lookup_account_id(
    credentials: dict[str, str],
    transport: HttpTransport,
    *,
    base_url: str = EXPECTED_URL,
) -> str:
    if base_url != EXPECTED_URL:
        raise Refused(f"credential validation must remain local at {EXPECTED_URL}")
    management_auth = f"Bearer {credentials['STALWART_API_KEY']}"
    management_account = transport.json(
        f"{base_url}/api/account",
        management_auth,
        jmap_method="GET /api/account",
    )
    require_exact_permissions(
        management_account.get("permissions"), MANAGEMENT_PERMISSIONS, "management key"
    )
    jmap_url = discover_jmap_api_url(credentials, transport, base_url=base_url)
    domain_response = transport.json(
        jmap_url,
        management_auth,
        payload={
            "using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"],
            "methodCalls": [["x:Domain/query", {"filter": {"name": EXPECTED_DOMAIN}, "limit": 2}, "domain"]],
        },
        jmap_method="x:Domain/query",
    )
    domain_ids = method_result(
        domain_response, "x:Domain/query", transport=transport
    ).get("ids") or []
    if len(domain_ids) != 1:
        raise Refused(f"management key did not resolve exactly one {EXPECTED_DOMAIN} domain")
    management_response = transport.json(
        jmap_url,
        management_auth,
        payload={
            "using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"],
            "methodCalls": [
                [
                    "x:Account/query",
                    {
                        "filter": {"name": "gateway-test", "domainId": domain_ids[0]},
                        "limit": 2,
                    },
                    "account",
                ],
                ["x:MtaRoute/get", {}, "routes"],
                [
                    "x:MtaOutboundStrategy/get",
                    {"ids": ["singleton"]},
                    "strategy",
                ],
            ],
        },
        jmap_method="x:Account/query+x:MtaRoute/get+x:MtaOutboundStrategy/get",
    )
    account_ids = method_result(
        management_response, "x:Account/query", transport=transport
    ).get("ids") or []
    method_result(management_response, "x:MtaRoute/get", transport=transport)
    method_result(
        management_response, "x:MtaOutboundStrategy/get", transport=transport
    )
    if len(account_ids) != 1:
        raise Refused(f"management key did not resolve exactly one {EXPECTED_ADDRESS} account")
    return str(account_ids[0])


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument("--secret-file", type=Path, required=True)
    value.add_argument("--account-id")
    value.add_argument("--lookup-account-id", action="store_true")
    value.add_argument("--url", default=EXPECTED_URL)
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    credentials = read_credentials(args.secret_file.resolve())
    transport = HttpTransport(list(credentials.values()))
    try:
        if args.lookup_account_id:
            if args.account_id:
                raise Refused("--lookup-account-id and --account-id are mutually exclusive")
            account_id = lookup_account_id(credentials, transport, base_url=args.url)
            print(f"ACCOUNT_ID={account_id}")
            print("SECRET_VALUES_PRINTED=NONE")
            return 0
        if not args.account_id:
            raise Refused("--account-id is required")
        submission_count = validate_live(
            credentials, args.account_id, transport, base_url=args.url
        )
    finally:
        credentials.clear()
        transport.sensitive_values.clear()
    print("STALWART_CERTIFICATION_CREDENTIALS=PASS")
    print(f"ACCOUNT_ADDRESS={EXPECTED_ADDRESS}")
    print(f"ACCOUNT_ID={args.account_id}")
    print("MANAGEMENT_PERMISSIONS=LEAST_PRIVILEGE")
    print("MAILBOX_ACCESS=PASS")
    print(f"EMAIL_SUBMISSION_COUNT={submission_count}")
    print("SECRET_VALUES_PRINTED=NONE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Refused as exc:
        print(f"Stalwart credential validation refused: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
