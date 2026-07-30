#!/usr/bin/env python3
"""Create the protected two-line Stalwart certification credential file."""

from __future__ import annotations

import argparse
import base64
import getpass
import os
from pathlib import Path
import sys


ADDRESS = "gateway-test@agents.aiat.ca"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/etc/aiat/resend-certification.env"),
    )
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("run as root so the credential file is root-owned")
    output = args.output.resolve()
    if output.exists():
        raise RuntimeError(f"refusing to overwrite {output}")
    api_key = getpass.getpass("Stalwart least-privilege API key: ")
    app_password = getpass.getpass(f"Application password for {ADDRESS}: ")
    if not api_key or not app_password or any(character.isspace() for character in app_password):
        raise RuntimeError("credentials are missing or malformed")
    basic = base64.b64encode(f"{ADDRESS}:{app_password}".encode()).decode()
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(f"STALWART_API_KEY={api_key}\n")
            handle.write(f"STALWART_JMAP_SERVICE_TOKEN=Basic {basic}\n")
    finally:
        api_key = ""
        app_password = ""
        basic = ""
    print(f"CREATED={output}")
    print("OWNER=root")
    print("MODE=0600")
    print("SECRET_VALUES_PRINTED=NONE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"credential file creation refused: {exc}", file=sys.stderr)
        raise SystemExit(1)
