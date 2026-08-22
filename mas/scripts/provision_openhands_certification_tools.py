"""Provision the pinned scanner set for the OpenHands candidate wave.

The installer is shared with the prior OpenCode certification only at the
tool-version/source level; this wrapper writes an OpenHands-specific evidence
schema and never changes the OpenCode certification code or evidence.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from provision_opencode_certification_tools import provision_tools

SCHEMA = "aiat.openhands-tool-provisioning.v1"


def provision(*, output_dir: Path, install_dir: Path, python_executable: str) -> dict:
    report = provision_tools(
        output_dir=output_dir,
        install_dir=install_dir,
        python_executable=python_executable,
    )
    report = {
        **report,
        "schema_version": SCHEMA,
        "candidate": "OpenHands Software Agent SDK + Agent Server v1.43.0",
        "tooling_source": "AIAT pinned scanner set; no OpenCode code or findings reused",
    }
    (output_dir / "tooling-provisioning.json").write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--install-dir", type=Path, required=True)
    parser.add_argument("--python", default="python3")
    args = parser.parse_args(argv)
    report = provision(
        output_dir=args.output,
        install_dir=args.install_dir,
        python_executable=args.python,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "required_tools": report["required_tools"],
                "failure_classes": report["failure_classes"],
            },
            sort_keys=True,
            indent=2,
        )
    )
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
