#!/usr/bin/env python3
"""Fail when a configured mail-edge secret appears in evidence artifacts.

Only variable names are reported. Secret values are read and compared inside
this process; they are never passed in subprocess arguments or printed.
"""

from __future__ import annotations

import sys
from pathlib import Path

SECRET_NAMES = (
    "IDENTITY_DATABASE_PASSWORD",
    "IDENTITY_SERVICE_SECRET",
    "IDENTITY_CONTENT_ENCRYPTION_KEY",
    "STALWART_API_KEY",
    "STALWART_JMAP_SERVICE_TOKEN",
    "STALWART_RECOVERY_ADMIN",
    "RESEND_API_KEY",
)


def _dotenv_values(path: Path) -> dict[str, bytes]:
    values: dict[str, bytes] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if name in SECRET_NAMES and value:
            values[name] = value.encode()
    return values


def main() -> int:
    if len(sys.argv) < 3:
        print(
            "usage: validate-secret-evidence.py ENV_FILE EVIDENCE_FILE [...]",
            file=sys.stderr,
        )
        return 2
    env_path = Path(sys.argv[1])
    evidence_paths = [Path(value) for value in sys.argv[2:]]
    if not env_path.is_file():
        print("mail-edge environment file is not readable", file=sys.stderr)
        return 2
    missing_evidence = [path for path in evidence_paths if not path.is_file()]
    if missing_evidence:
        print("one or more evidence files are not readable", file=sys.stderr)
        return 2

    secrets = _dotenv_values(env_path)
    if not secrets:
        print("no configured mail-edge secrets were available to scan", file=sys.stderr)
        return 2

    matched_names: set[str] = set()
    for evidence_path in evidence_paths:
        evidence = evidence_path.read_bytes()
        for name, value in secrets.items():
            if value in evidence:
                matched_names.add(name)

    if matched_names:
        for name in sorted(matched_names):
            print(f"secret value detected for {name}", file=sys.stderr)
        return 1
    print(
        f"no configured secret values found in {len(evidence_paths)} evidence file(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
