"""Validate bounded tool-service image budgets and optional local measurements.

The static invocation validates the checked-in budget contract.  Supplying an
image reference performs a read-only local Docker size check; optional measured
compressed/startup/memory values can be supplied by a release probe.  Missing
Docker or missing measurements are reported as ``blocked`` rather than being
silently treated as a pass.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

BUDGET_PATH = Path(__file__).resolve().parents[1] / "infra" / "docker" / "image-budgets.yaml"
SCHEMA_VERSION = "aiat.image-budget-check.v1"
_POSITIVE_FIELDS = {
    "max_compressed_bytes",
    "max_uncompressed_bytes",
    "max_startup_seconds",
    "max_memory_bytes",
}


def _load_budgets() -> dict[str, dict[str, Any]]:
    raw = yaml.safe_load(BUDGET_PATH.read_text(encoding="utf-8")) or {}
    budgets = raw.get("budgets") or {}
    if not isinstance(budgets, dict) or not budgets:
        raise ValueError("image-budgets.yaml must define at least one budget")
    required = {"profile", "image_ref_env", *_POSITIVE_FIELDS}
    for name, budget in budgets.items():
        if not isinstance(budget, dict) or not required.issubset(budget):
            raise ValueError(f"{name}: incomplete image budget")
        if not isinstance(budget["profile"], str) or not budget["profile"].strip():
            raise ValueError(f"{name}: profile must be non-empty text")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*_IMAGE_REF", str(budget["image_ref_env"])):
            raise ValueError(f"{name}: image_ref_env must be an *_IMAGE_REF variable")
        for field in _POSITIVE_FIELDS:
            if int(budget[field]) <= 0:
                raise ValueError(f"{name}: {field} must be positive")
    return budgets


def _docker_size(image_ref: str) -> int:
    if shutil.which("docker") is None:
        raise RuntimeError("docker CLI is unavailable")
    result = subprocess.run(
        ["docker", "image", "inspect", image_ref, "--format", "{{.Size}}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(result.stdout.strip())


def _report(
    *,
    budget_name: str,
    budget: dict[str, Any],
    image_ref: str | None,
    uncompressed_bytes: int | None,
    compressed_bytes: int | None,
    startup_seconds: float | None,
    memory_bytes: int | None,
    status: str,
    errors: list[str],
    reason: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "live" if image_ref else "static",
        "scope": "checked-in budget contract plus optional local measurements",
        "budget": budget_name,
        "profile": budget["profile"],
        "image_ref": image_ref,
        "measurements": {
            "uncompressed_bytes": uncompressed_bytes,
            "compressed_bytes": compressed_bytes,
            "startup_seconds": startup_seconds,
            "memory_bytes": memory_bytes,
        },
        "limits": {
            key.removeprefix("max_"): budget[key]
            for key in _POSITIVE_FIELDS
        },
        "status": status,
        "errors": errors,
        "reason": reason,
    }


def evaluate(
    *,
    budget_name: str = "tool-service-core",
    image_ref: str | None = None,
    compressed_bytes: int | None = None,
    startup_seconds: float | None = None,
    memory_bytes: int | None = None,
    size_reader=_docker_size,
) -> tuple[dict[str, Any], int]:
    try:
        budgets = _load_budgets()
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        report = {
            "schema_version": SCHEMA_VERSION,
            "mode": "static",
            "status": "fail",
            "errors": [str(exc)],
        }
        return report, 1
    budget = budgets.get(budget_name)
    if budget is None:
        report = {
            "schema_version": SCHEMA_VERSION,
            "mode": "static",
            "status": "fail",
            "errors": [f"unknown budget {budget_name!r}"],
        }
        return report, 1

    errors: list[str] = []
    uncompressed_bytes: int | None = None
    if image_ref:
        try:
            uncompressed_bytes = int(size_reader(image_ref))
        except (RuntimeError, OSError, subprocess.CalledProcessError, ValueError) as exc:
            report = _report(
                budget_name=budget_name,
                budget=budget,
                image_ref=image_ref,
                uncompressed_bytes=None,
                compressed_bytes=compressed_bytes,
                startup_seconds=startup_seconds,
                memory_bytes=memory_bytes,
                status="blocked",
                errors=["local image size measurement unavailable"],
                reason=type(exc).__name__,
            )
            return report, 2

    measurements = {
        "uncompressed_bytes": (uncompressed_bytes, int(budget["max_uncompressed_bytes"])),
        "compressed_bytes": (compressed_bytes, int(budget["max_compressed_bytes"])),
        "startup_seconds": (startup_seconds, float(budget["max_startup_seconds"])),
        "memory_bytes": (memory_bytes, int(budget["max_memory_bytes"])),
    }
    for name, (value, limit) in measurements.items():
        if value is not None and value > limit:
            errors.append(f"{name}={value} exceeds limit {limit}")
        if value is not None and value < 0:
            errors.append(f"{name} must not be negative")
    status = "fail" if errors else "pass"
    reason = "all supplied measurements are within the checked-in budget"
    report = _report(
        budget_name=budget_name,
        budget=budget,
        image_ref=image_ref,
        uncompressed_bytes=uncompressed_bytes,
        compressed_bytes=compressed_bytes,
        startup_seconds=startup_seconds,
        memory_bytes=memory_bytes,
        status=status,
        errors=errors,
        reason=reason,
    )
    return report, 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-ref", help="Inspect one local image by Docker reference")
    parser.add_argument("--budget", default="tool-service-core", help="Budget key from image-budgets.yaml")
    parser.add_argument("--compressed-bytes", type=int, help="Optional measured compressed archive size")
    parser.add_argument("--startup-seconds", type=float, help="Optional measured health-startup duration")
    parser.add_argument("--memory-bytes", type=int, help="Optional measured steady-state memory")
    parser.add_argument("--json", action="store_true", help="Emit a machine-readable report")
    args = parser.parse_args(argv)
    report, exit_code = evaluate(
        budget_name=args.budget,
        image_ref=args.image_ref,
        compressed_bytes=args.compressed_bytes,
        startup_seconds=args.startup_seconds,
        memory_bytes=args.memory_bytes,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    elif report.get("status") == "pass":
        print(f"image-budget: {report.get('budget', args.budget)} PASS")
    else:
        print(f"image-budget: {report.get('reason', 'blocked')}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
