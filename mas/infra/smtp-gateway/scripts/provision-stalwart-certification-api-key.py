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
            with request.urlopen(message, timeout=20) as response:
                value = json.loads(response.read().decode("utf-8"))
        except (error.HTTPError, error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise Refused("Stalwart rejected the local provisioning request") from exc
        if not isinstance(value, dict):
            raise Refused("Stalwart returned an invalid provisioning response")
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


def require_create_permission(account: dict[str, Any]) -> None:
    permissions = account.get("permissions")
    if not isinstance(permissions, list) or "sysApiKeyCreate" not in permissions:
        raise Refused(
            "authenticated account lacks sysApiKeyCreate; its role/password was not modified"
        )


def extract_created_secret(response: dict[str, Any]) -> str:
    for item in response.get("methodResponses") or []:
        if isinstance(item, list) and len(item) >= 2 and item[0] == "x:ApiKey/set":
            result = item[1] if isinstance(item[1], dict) else {}
            if result.get("notCreated"):
                raise Refused("Stalwart refused to create the standalone ApiKey object")
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
) -> None:
    descriptor = reserve_output(output)
    completed = False
    try:
        account = transport.json(f"{LOCAL_URL}/api/account", admin_authorization)
        require_create_permission(account)
        response = transport.json(
            f"{LOCAL_URL}/api",
            admin_authorization,
            payload=api_key_payload(expires_at),
        )
        api_key = extract_created_secret(response)
        mailbox_basic = base64.b64encode(
            f"{GATEWAY_ACCOUNT}:{app_password}".encode()
        ).decode()
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
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.url != LOCAL_URL:
        raise Refused(f"provisioning must remain local at {LOCAL_URL}")
    if not 1 <= args.expires_in_hours <= 168:
        raise Refused("--expires-in-hours must be between 1 and 168")
    admin_name = input("Existing Stalwart administrator address: ").strip()
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
    try:
        provision(
            transport=HttpTransport(),
            admin_authorization=f"Basic {admin_basic}",
            app_password=app_password,
            output=args.output.resolve(),
            expires_at=expires_at,
        )
    finally:
        admin_password = ""
        app_password = ""
        admin_basic = ""
    print("STALWART_API_KEY_PROVISIONING=PASS")
    print(f"PROTECTED_CREDENTIAL_FILE={args.output.resolve()}")
    print(f"API_KEY_EXPIRES_AT={expires_at}")
    print("API_KEY_SECRET_PRINTED=NONE")
    print("ADMIN_ACCOUNT_MUTATION=NONE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Refused as exc:
        print(f"Stalwart ApiKey provisioning refused: {exc}", file=sys.stderr)
        raise SystemExit(1)
