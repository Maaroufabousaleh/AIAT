"""Check the optional runtime compatibility lock and local preflight.

The lock contract is static evidence.  A missing package or incompatible MCP
version is reported as ``activation_status: blocked`` while the checker itself
passes when the declaration is well formed.  This keeps an optional runtime
from becoming a false release failure or a falsely certified activation.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.metadata
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from mas_core.worker_registry.maf_compatibility import (
    MAF_COMPATIBILITY_SCHEMA,
    evaluate_microsoft_agent_framework_compatibility,
)

CHECK_SCHEMA = "aiat.runtime-compatibility-check.v1"
ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "docs" / "provenance" / "runtime_compatibility.yaml"
TOOL_SERVICE_PYPROJECT = ROOT / "apps" / "tool-service" / "pyproject.toml"


def _workspace_mcp_specifier() -> str | None:
    text = TOOL_SERVICE_PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r"\"mcp([^\"]*)\"", text)
    return f"mcp{match.group(1)}" if match else None


def _contract_errors(lock: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if lock.get("schema_version") != "aiat.runtime-compatibility.v1":
        errors.append("runtime compatibility schema_version is invalid")
    policy = lock.get("policy")
    if not isinstance(policy, dict) or policy.get("licence_metadata_is_gate") is not False:
        errors.append("licence metadata must be explicitly non-gating")
    runtimes = lock.get("runtimes")
    if not isinstance(runtimes, list) or len(runtimes) != 1:
        errors.append("exactly one MAF runtime lock entry is required")
        return errors
    entry = runtimes[0]
    if not isinstance(entry, dict):
        return ["MAF runtime lock entry must be an object"]
    required = {
        "id": "microsoft_agent_framework",
        "distribution": "agent-framework",
        "import": "agent_framework",
        "locked_version": "1.13.0",
    }
    for key, expected in required.items():
        if entry.get(key) != expected:
            errors.append(f"MAF lock {key} must be {expected!r}")
    mcp = entry.get("mcp")
    if not isinstance(mcp, dict) or mcp.get("version_specifier") != ">=1.27,<2":
        errors.append("MAF lock MCP specifier must be >=1.27,<2")
    if entry.get("status") not in {"locked-pending-install", "locked-certified-isolated"}:
        errors.append("MAF lock status must record pending or isolated-certified state")
    profile = entry.get("optional_profile")
    if not isinstance(profile, dict):
        errors.append("MAF lock optional_profile must be an object")
    else:
        if profile.get("requirements_file") != "mas/infra/runtime/maf/requirements.txt":
            errors.append("MAF optional profile requirements file is invalid")
        if profile.get("certification_evidence") != "mas/docs/provenance/maf_runtime_certification.json":
            errors.append("MAF optional profile certification evidence path is invalid")
        locked_versions = profile.get("locked_versions")
        if not isinstance(locked_versions, dict) or locked_versions.get("agent-framework") != "1.13.0" or locked_versions.get("mcp") != "1.29.0":
            errors.append("MAF optional profile versions must be agent-framework 1.13.0 and MCP 1.29.0")
        if profile.get("certification_status") not in {"pass", "pending"}:
            errors.append("MAF optional profile certification status must be pass or pending")
    return errors


def build_report() -> dict[str, Any]:
    errors: list[str] = []
    try:
        lock = yaml.safe_load(LOCK_PATH.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        lock = {}
        errors.append(f"unable to load runtime compatibility lock: {type(exc).__name__}")
    if not isinstance(lock, dict):
        lock = {}
        errors.append("runtime compatibility lock must be an object")
    errors.extend(_contract_errors(lock))

    package_version = None
    mcp_version = None
    with contextlib.suppress(importlib.metadata.PackageNotFoundError):
        package_version = importlib.metadata.version("agent-framework")
    with contextlib.suppress(importlib.metadata.PackageNotFoundError):
        mcp_version = importlib.metadata.version("mcp")
    preflight = evaluate_microsoft_agent_framework_compatibility(
        package_version=package_version,
        mcp_version=mcp_version,
    )
    workspace_mcp_specifier = _workspace_mcp_specifier()
    expected_workspace_specifier = "mcp==1.23.3"
    if workspace_mcp_specifier != expected_workspace_specifier:
        errors.append(
            "workspace tool-service MCP declaration changed; refresh the compatibility lock evidence"
        )
    return {
        "schema_version": CHECK_SCHEMA,
        "compatibility_schema": MAF_COMPATIBILITY_SCHEMA,
        "status": "pass" if not errors else "fail",
        "lock_contract": "pass" if not errors else "fail",
        "activation_status": "ready" if preflight.ready else "blocked",
        "activation_blockers": list(preflight.blockers),
        "package_import_available": importlib.util.find_spec("agent_framework") is not None,
        "package_version": package_version,
        "mcp_version": mcp_version,
        "workspace_mcp_specifier": workspace_mcp_specifier,
        "required_mcp_specifier": ">=1.27,<2",
        "mutation_performed": False,
        "network_access_performed": False,
        "licence_metadata_is_gate": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)
    report = build_report()
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(
            f"runtime compatibility: {report['status']} "
            f"(activation {report['activation_status']})"
        )
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
