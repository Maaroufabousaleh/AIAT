#!/usr/bin/env python3
"""Revoke the exact temporary Stalwart route-lifecycle API key."""

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
    args = parser.parse_args(argv)
    if args.url != CREDENTIALS.LOCAL_URL:
        raise CREDENTIALS.Refused(f"revocation must remain local at {CREDENTIALS.LOCAL_URL}")
    metadata = args.metadata_file or CREDENTIALS.default_metadata_path(args.secret_file)
    CREDENTIALS.validate_local_files(args.secret_file.resolve(), metadata.resolve())
    password = getpass.getpass("Existing permanent Stalwart administrator password: ")
    if not password:
        raise CREDENTIALS.Refused("administrator credential is required")
    diagnostic = CREDENTIALS.PROVISIONING.DiagnosticState()
    diagnostic.sensitive_values.append(password)
    try:
        CREDENTIALS.revoke(
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
    print("ROUTE_LIFECYCLE_REVOCATION=PASS")
    print("SERVER_SIDE_REVOCATION=PASS")
    print("LOCAL_SECRET_REMOVED=PASS")
    print("ROUTE_LIFECYCLE_SECRET_PRINTED=NONE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CREDENTIALS.Refused, CREDENTIALS.PROVISIONING.Refused):
        print("Stalwart route credential revocation refused")
        raise SystemExit(1) from None
