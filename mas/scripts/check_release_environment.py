"""Capture a secret-safe, reproducibility-oriented AIAT environment manifest.

The report identifies the source revision, relevant lock/configuration hashes,
available tool identities, and only the presence/absence of selected
environment inputs. It never prints environment values, credentials, image
references, or deployment payloads. The manifest digest excludes the capture
timestamp so repeated reads of the same source/tool/input state are
deterministic. This is evidence for a release run, not a release decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAS_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "aiat.release-environment.v1"

# These files define the inputs that can change a reproducibility claim. The
# list is intentionally explicit; the checker must fail loudly when a tracked
# release input expected by the programme disappears.
TRACKED_INPUTS = (
    "../.env.example",
    "pyproject.toml",
    "uv.lock",
    "infra/compose/docker-compose.yml",
    "infra/docker/Dockerfile.orchestrator-api",
    "infra/docker/Dockerfile.tool-service",
    "docs/provenance/release_ledger.yaml",
    "docs/provenance/production_images.yaml",
    "docs/provenance/operator_pins.yaml",
    "docs/provenance/security_scan_evidence.yaml",
    "docs/provenance/worker_certification_matrix.yaml",
    "workers/coding_worker.yaml",
    "workers/tester.yaml",
)

# Presence is useful for diagnosing a blocked run; values are never included.
ENVIRONMENT_INPUTS = (
    "AIAT_ORCHESTRATOR_URL",
    "ORCHESTRATOR_API_URL",
    "AIAT_OPERATOR_API_KEY",
    "AIAT_API_KEY",
    "AIAT_CEO_API_KEY",
    "AIAT_WORKER_API_KEY",
    "AIAT_COMPANY_TIMEZONE",
    "AIAT_IMAGE_REF",
    "ORCHESTRATOR_IMAGE_REF",
    "TOOL_SERVICE_IMAGE_REF",
)

TOOL_COMMANDS: dict[str, tuple[str, ...]] = {
    "python": (sys.executable, "--version"),
    "uv": ("uv", "--version"),
    "docker": ("docker", "--version"),
    "node": ("node", "--version"),
    "npm": ("npm", "--version"),
    "runsc": ("runsc", "--version"),
}


def _run(command: tuple[str, ...], *, timeout: float = 5.0) -> tuple[int, str]:
    try:
        result = subprocess.run(
            list(command),
            cwd=MAS_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 127, ""
    output = (result.stdout or result.stderr or "").strip()
    return result.returncode, output[:300]


def _git_metadata() -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    revision_code, revision = _run(("git", "rev-parse", "HEAD"))
    branch_code, branch = _run(("git", "branch", "--show-current"))
    try:
        status_result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=MAS_ROOT,
            capture_output=True,
            text=True,
            timeout=15.0,
            check=False,
        )
        status_code = status_result.returncode
        status_lines = (status_result.stdout or "").splitlines()
    except (OSError, subprocess.SubprocessError):
        status_code = 127
        status_lines = []
    if revision_code != 0 or not revision:
        errors.append("git revision is unavailable")
    if status_code != 0:
        errors.append("git working-tree status is unavailable")
    return (
        {
            "revision": revision or None,
            "branch": branch or None if branch_code == 0 else None,
            "working_tree_clean": status_code == 0 and not bool(status_lines),
            "changed_path_count": len(status_lines),
        },
        errors,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tracked_file_inventory() -> tuple[list[dict[str, Any]], list[str]]:
    inventory: list[dict[str, Any]] = []
    errors: list[str] = []
    for relative in TRACKED_INPUTS:
        path = MAS_ROOT / relative
        if not path.is_file():
            inventory.append({"path": relative, "present": False})
            errors.append(f"required release input is missing: {relative}")
            continue
        inventory.append(
            {
                "path": relative,
                "present": True,
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return inventory, errors


def _tool_inventory() -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for name, command in TOOL_COMMANDS.items():
        executable = shutil.which(command[0]) if not Path(command[0]).is_absolute() else command[0]
        if not executable:
            inventory.append({"name": name, "available": False})
            continue
        returncode, version = _run(command)
        inventory.append(
            {
                "name": name,
                "available": returncode == 0,
                "executable_present": True,
                "version": version if returncode == 0 else None,
            }
        )
    return inventory


def _environment_presence() -> list[dict[str, Any]]:
    return [
        {"name": name, "configured": bool(os.environ.get(name, "").strip())}
        for name in ENVIRONMENT_INPUTS
    ]


def _digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def build_report(*, require_clean: bool = False) -> dict[str, Any]:
    git, git_errors = _git_metadata()
    files, file_errors = _tracked_file_inventory()
    errors = [*git_errors, *file_errors]
    if require_clean and not git["working_tree_clean"]:
        errors.append("working tree is dirty")
    tools = _tool_inventory()
    environment_presence = _environment_presence()

    stable_payload: dict[str, Any] = {
        "schema_version": SCHEMA,
        "programme_scope": "personal-internal-only",
        "git": git,
        "tracked_inputs": files,
        "tools": tools,
        "environment_presence": environment_presence,
        "host": {
            "python": platform.python_version(),
            "platform": platform.platform(aliased=True),
            "machine": platform.machine(),
        },
    }
    return {
        **stable_payload,
        "manifest_digest": _digest(stable_payload),
        "tracked_input_count": len(files),
        "available_tool_count": sum(bool(item.get("available")) for item in tools),
        "configured_environment_input_count": sum(
            bool(item.get("configured")) for item in environment_presence
        ),
        "captured_at": datetime.now(tz=UTC).isoformat(),
        "status": "fail" if errors else "pass",
        "errors": errors,
        "scope": "secret-safe source/tool/environment identity; no deployment mutation or credential output",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument(
        "--require-clean",
        action="store_true",
        help="fail when the git working tree is dirty",
    )
    args = parser.parse_args(argv)
    report = build_report(require_clean=args.require_clean)
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(
            f"release-environment: {report['status']} — "
            f"digest={report['manifest_digest']}"
        )
        for error in report["errors"]:
            print(f"  - {error}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
