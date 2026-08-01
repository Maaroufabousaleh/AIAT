#!/usr/bin/env python3
"""Provision the temporary least-privilege Stalwart route key."""

from __future__ import annotations

import argparse
import getpass
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "stalwart_route_lifecycle_credentials",
    Path(__file__).with_name("stalwart_route_lifecycle_credentials.py"),
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("route credential helper is unavailable")
CREDENTIALS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CREDENTIALS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("/etc/aiat/stalwart-route-lifecycle.env"))
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--expires-in-hours", type=int, default=4)
    parser.add_argument("--url", default=CREDENTIALS.LOCAL_URL)
    parser.add_argument(
        "--administrator-address",
        default=CREDENTIALS.PERMANENT_ADMINISTRATOR_ADDRESS,
    )
    parser.add_argument("--container-name", default=CREDENTIALS.STALWART_CONTAINER)
    args = parser.parse_args(argv)
    if args.url != CREDENTIALS.LOCAL_URL:
        raise CREDENTIALS.Refused(f"provisioning must remain local at {CREDENTIALS.LOCAL_URL}")
    if not 1 <= args.expires_in_hours <= 24:
        raise CREDENTIALS.Refused("--expires-in-hours must be between 1 and 24")
    if args.administrator_address != CREDENTIALS.PERMANENT_ADMINISTRATOR_ADDRESS:
        raise CREDENTIALS.Refused("only the permanent directory administrator may own this key")
    metadata = args.metadata or CREDENTIALS.default_metadata_path(args.output)
    diagnostic = CREDENTIALS.PROVISIONING.DiagnosticState()
    server_image = CREDENTIALS.PROVISIONING.inspect_running_image(args.container_name)
    password = getpass.getpass("Existing permanent Stalwart administrator password: ")
    if not password:
        raise CREDENTIALS.Refused("administrator credential is required")
    expires_at = (
        datetime.now(UTC) + timedelta(hours=args.expires_in_hours)
    ).isoformat(timespec="seconds").replace("+00:00", "Z")
    diagnostic.sensitive_values.append(password)
    try:
        CREDENTIALS.provision(
            base_url=args.url,
            administrator_address=args.administrator_address,
            administrator_password=password,
            output=args.output.resolve(),
            metadata_file=metadata.resolve(),
            expires_at=expires_at,
            server_image=server_image,
            diagnostic=diagnostic,
        )
    finally:
        password = ""
        diagnostic.sensitive_values.clear()
    CREDENTIALS.print_result("provision", args.output.resolve(), metadata.resolve())
    print("ROUTE_LIFECYCLE_PERMISSIONS=" + ",".join(CREDENTIALS.ROUTE_KEY_PERMISSIONS))
    print("ADMIN_ACCOUNT_MUTATION=NONE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CREDENTIALS.Refused, CREDENTIALS.PROVISIONING.Refused):
        print("Stalwart route credential provisioning refused")
        raise SystemExit(1) from None
