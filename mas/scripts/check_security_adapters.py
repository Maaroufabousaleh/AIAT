"""Validate the default bounded security-scanner adapter contract.

This check proves that the Semgrep, SkillSpector, and TruffleHog compatibility
aliases resolve to the canonical ``security.scan`` tool and that the default
security worker advertises the same scanner set. It does not run a scanner,
reach a network, dispatch a worker, or inspect licence metadata as a gate.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from mas_tools_sdk.manifest import resolve_tool_name

MAS_ROOT = Path(__file__).resolve().parents[1]
CHECK_SCHEMA = "aiat.security-adapter-check.v1"
EXPECTED_ALIASES = ("semgrep", "skillspector", "trufflehog")


def build_report() -> dict[str, Any]:
    errors: list[str] = []
    aliases: dict[str, str | None] = {}
    for alias in EXPECTED_ALIASES:
        aliases[alias] = resolve_tool_name(alias)
        if aliases[alias] != "security.scan":
            errors.append(f"{alias} must resolve to security.scan")

    worker_path = MAS_ROOT / "workers" / "security_evaluator.yaml"
    try:
        worker = yaml.safe_load(worker_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        worker = {}
        errors.append(f"cannot read security evaluator manifest: {type(exc).__name__}")

    adapter_config = ((worker.get("runtime") or {}).get("adapter_config") or {}) if isinstance(worker, dict) else {}
    default_tools = list(adapter_config.get("default_tools") or []) if isinstance(adapter_config, dict) else []
    available_tools = list(adapter_config.get("available_tools") or []) if isinstance(adapter_config, dict) else []
    for scanner in ("semgrep", "skillspector"):
        if scanner not in default_tools:
            errors.append(f"security evaluator default_tools is missing {scanner}")
    if "trufflehog" not in available_tools:
        errors.append("security evaluator available_tools is missing trufflehog")

    env_example = MAS_ROOT.parent / ".env.example"
    try:
        env_text = env_example.read_text(encoding="utf-8")
    except OSError as exc:
        env_text = ""
        errors.append(f"cannot read .env.example: {type(exc).__name__}")
    if "TOOL_SKILLSPECTOR_COMMAND" not in env_text:
        errors.append(".env.example must document TOOL_SKILLSPECTOR_COMMAND")

    return {
        "schema_version": CHECK_SCHEMA,
        "status": "fail" if errors else "pass",
        "aliases": aliases,
        "canonical_tool": "security.scan",
        "default_scanners": default_tools,
        "available_scanners": available_tools,
        "sandbox_boundary": "shared_tool_sandbox_adapter",
        "errors": errors,
        "mutation_performed": False,
        "worker_dispatch_performed": False,
        "live_execution_status": "not_checked",
        "licence_metadata_is_gate": False,
        "scope": "static alias, manifest, and configuration contract",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    args = parser.parse_args(argv)
    report = build_report()
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"security-adapter-check: {report['status']} errors={len(report['errors'])}")
        for error in report["errors"]:
            print(f"security-adapter-check: {error}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
