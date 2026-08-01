#!/usr/bin/env python3
"""Validate the temporary Stalwart route key without a destructive probe."""

from __future__ import annotations

import argparse
import getpass
import importlib.util
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
    parser.add_argument("--secret-file", type=Path, default=Path("/etc/aiat/stalwart-route-lifecycle.env"))
    parser.add_argument("--metadata-file", type=Path)
    parser.add_argument("--url", default=CREDENTIALS.LOCAL_URL)
    parser.add_argument(
        "--administrator-address",
        default=CREDENTIALS.PERMANENT_ADMINISTRATOR_ADDRESS,
    )
    parser.add_argument("--local-only", action="store_true")
    args = parser.parse_args(argv)
    if args.url != CREDENTIALS.LOCAL_URL:
        raise CREDENTIALS.Refused(f"validation must remain local at {CREDENTIALS.LOCAL_URL}")
    metadata = args.metadata_file or CREDENTIALS.default_metadata_path(args.secret_file)
    CREDENTIALS.validate_local_files(
        args.secret_file.resolve(),
        metadata.resolve(),
        allow_current_user=args.local_only,
    )
    if args.local_only:
        print("ROUTE_LIFECYCLE_LOCAL_FILE_VALIDATION=PASS")
        print("ROUTE_LIFECYCLE_SECRET_PRINTED=NONE")
        return 0
    password = getpass.getpass("Existing permanent Stalwart administrator password: ")
    if not password:
        raise CREDENTIALS.Refused("administrator credential is required")
    diagnostic = CREDENTIALS.PROVISIONING.DiagnosticState()
    diagnostic.sensitive_values.append(password)
    try:
        CREDENTIALS.validate(
            base_url=args.url,
            administrator_address=args.administrator_address,
            administrator_password=password,
            secret_file=args.secret_file.resolve(),
            metadata_file=metadata.resolve(),
            diagnostic=diagnostic,
        )
    finally:
        password = ""
        diagnostic.sensitive_values.clear()
    print("ROUTE_LIFECYCLE_CREDENTIAL=PASS")
    print("ROUTE_KEY_AUTHENTICATION=PASS")
    print("ROUTE_GET_AUTHORIZATION=PASS")
    print("STRATEGY_GET_AUTHORIZATION=PASS")
    print("DESTRUCTIVE_PERMISSION_PROBE=NOT_PERFORMED")
    print("ROUTE_LIFECYCLE_SECRET_PRINTED=NONE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CREDENTIALS.Refused, CREDENTIALS.PROVISIONING.Refused):
        print("Stalwart route credential validation refused")
        raise SystemExit(1) from None
