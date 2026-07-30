#!/usr/bin/env python3
"""Read-only validation of least-privilege Stalwart v0.16 credentials."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any
from urllib import error, request


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
    def json(
        self,
        url: str,
        authorization: str,
        *,
        payload: dict[str, Any] | None = None,
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
            with request.urlopen(message, timeout=15) as response:
                value = json.loads(response.read().decode("utf-8"))
        except (error.HTTPError, error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise Refused("Stalwart rejected a certification credential or request") from exc
        if not isinstance(value, dict):
            raise Refused("Stalwart returned an invalid JSON response")
        return value


def method_result(response: dict[str, Any], method: str) -> dict[str, Any]:
    for item in response.get("methodResponses") or []:
        if isinstance(item, list) and len(item) >= 2 and item[0] == method:
            if isinstance(item[1], dict):
                return item[1]
    raise Refused(f"Stalwart did not authorize {method}")


def validate_live(
    credentials: dict[str, str],
    account_id: str,
    transport: HttpTransport,
    *,
    base_url: str = EXPECTED_URL,
) -> None:
    if base_url != EXPECTED_URL:
        raise Refused(f"credential validation must remain local at {EXPECTED_URL}")
    if not account_id:
        raise Refused("--account-id is required")
    resolved_account_id = lookup_account_id(credentials, transport, base_url=base_url)
    if resolved_account_id != account_id:
        raise Refused(f"accountId does not belong to {EXPECTED_ADDRESS}")
    mail_auth = basic_mail_authorization(credentials["STALWART_JMAP_SERVICE_TOKEN"])


    mail_account = transport.json(f"{base_url}/api/account", mail_auth)
    require_exact_permissions(mail_account.get("permissions"), MAIL_PERMISSIONS, "mail credential")
    mail_response = transport.json(
        f"{base_url}/jmap",
        mail_auth,
        payload={
            "using": ["urn:ietf:params:jmap:core", "urn:ietf:params:jmap:mail"],
            "methodCalls": [
                ["Mailbox/get", {"accountId": account_id}, "mailboxes"],
                ["Identity/get", {"accountId": account_id}, "identities"],
            ],
        },
    )
    if not (method_result(mail_response, "Mailbox/get").get("list") or []):
        raise Refused("mail credential cannot read the gateway-test mailbox")
    identities = method_result(mail_response, "Identity/get").get("list") or []
    if not any(
        isinstance(identity, dict)
        and str(identity.get("email") or "").lower() == EXPECTED_ADDRESS
        for identity in identities
    ):
        raise Refused(f"mail credential does not own {EXPECTED_ADDRESS}")


def lookup_account_id(
    credentials: dict[str, str],
    transport: HttpTransport,
    *,
    base_url: str = EXPECTED_URL,
) -> str:
    if base_url != EXPECTED_URL:
        raise Refused(f"credential validation must remain local at {EXPECTED_URL}")
    management_auth = f"Bearer {credentials['STALWART_API_KEY']}"
    management_account = transport.json(f"{base_url}/api/account", management_auth)
    require_exact_permissions(
        management_account.get("permissions"), MANAGEMENT_PERMISSIONS, "management key"
    )
    domain_response = transport.json(
        f"{base_url}/api",
        management_auth,
        payload={
            "using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"],
            "methodCalls": [["x:Domain/query", {"filter": {"name": EXPECTED_DOMAIN}, "limit": 2}, "domain"]],
        },
    )
    domain_ids = method_result(domain_response, "x:Domain/query").get("ids") or []
    if len(domain_ids) != 1:
        raise Refused(f"management key did not resolve exactly one {EXPECTED_DOMAIN} domain")
    management_response = transport.json(
        f"{base_url}/api",
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
    )
    account_ids = method_result(management_response, "x:Account/query").get("ids") or []
    method_result(management_response, "x:MtaRoute/get")
    method_result(management_response, "x:MtaOutboundStrategy/get")
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
    try:
        if args.lookup_account_id:
            if args.account_id:
                raise Refused("--lookup-account-id and --account-id are mutually exclusive")
            account_id = lookup_account_id(credentials, HttpTransport(), base_url=args.url)
            print(f"ACCOUNT_ID={account_id}")
            print("SECRET_VALUES_PRINTED=NONE")
            return 0
        if not args.account_id:
            raise Refused("--account-id is required")
        validate_live(credentials, args.account_id, HttpTransport(), base_url=args.url)
    finally:
        credentials.clear()
    print("STALWART_CERTIFICATION_CREDENTIALS=PASS")
    print(f"ACCOUNT_ADDRESS={EXPECTED_ADDRESS}")
    print(f"ACCOUNT_ID={args.account_id}")
    print("MANAGEMENT_PERMISSIONS=LEAST_PRIVILEGE")
    print("MAILBOX_ACCESS=PASS")
    print("SECRET_VALUES_PRINTED=NONE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Refused as exc:
        print(f"Stalwart credential validation refused: {exc}", file=sys.stderr)
        raise SystemExit(1)
