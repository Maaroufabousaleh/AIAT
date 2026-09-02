"""Validate exact technical runtime/CLI pins or explicit unavailability.

The checker is deliberately independent of OSS licence/restriction metadata.
It verifies only that production image inputs are exact and that host- or
operator-supplied capabilities are labelled unavailable until their identity
is supplied.  It performs no network access, image build, or installation.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

MAS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PINS = MAS_ROOT / "docs" / "provenance" / "operator_pins.yaml"
CHECK_SCHEMA = "aiat.operator-pin-contract.v1"
CONCRETE_VERSION_RE = re.compile(r"^(?:v)?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def _load(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("operator_pins.yaml must contain a mapping")
    return value


def check(*, pins_path: Path = DEFAULT_PINS, repository_root: Path = MAS_ROOT) -> dict[str, Any]:
    errors: list[str] = []
    try:
        raw = _load(pins_path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return {
            "schema_version": CHECK_SCHEMA,
            "status": "fail",
            "pin_count": 0,
            "locked_count": 0,
            "unavailable_count": 0,
            "errors": [f"cannot load operator pin manifest: {type(exc).__name__}: {exc}"],
            "policy": {"programme_scope": "personal-internal-only", "licence_metadata_is_gate": False},
        }

    if raw.get("schema_version") != CHECK_SCHEMA:
        errors.append(f"schema_version must be {CHECK_SCHEMA!r}")
    if raw.get("programme_scope") != "personal-internal-only":
        errors.append("programme_scope must be personal-internal-only")
    policy = raw.get("policy") if isinstance(raw.get("policy"), dict) else {}
    if policy.get("exact_pin_required") is not True:
        errors.append("policy.exact_pin_required must be true")
    if policy.get("unavailable_is_explicit") is not True:
        errors.append("policy.unavailable_is_explicit must be true")
    if policy.get("licence_metadata_is_gate") is not False:
        errors.append("policy.licence_metadata_is_gate must be false")

    pins = raw.get("pins")
    if not isinstance(pins, list) or not pins:
        errors.append("pins must be a non-empty list")
        pins = []
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(pins):
        if not isinstance(row, dict):
            errors.append(f"pins[{index}] must be a mapping")
            continue
        pin_id = str(row.get("id") or "").strip()
        status = str(row.get("status") or "").strip().lower()
        if not pin_id or pin_id in seen:
            errors.append(f"pins[{index}] has a missing or duplicate id")
            continue
        seen.add(pin_id)
        if status not in {"locked", "unavailable"}:
            errors.append(f"{pin_id}: status must be locked or unavailable")
        version = row.get("version")
        if status == "locked":
            if not isinstance(version, str) or not CONCRETE_VERSION_RE.fullmatch(version):
                errors.append(f"{pin_id}: locked pins require a concrete semver version")
        else:
            if not str(row.get("reason") or "").strip():
                errors.append(f"{pin_id}: unavailable pins require a reason")
        assertions = row.get("assertions") or []
        if not isinstance(assertions, list):
            errors.append(f"{pin_id}: assertions must be a list")
            assertions = []
        if status == "locked" and not assertions:
            errors.append(f"{pin_id}: locked pins require at least one source assertion")
        checked_assertions = 0
        for assertion in assertions:
            if not isinstance(assertion, dict):
                errors.append(f"{pin_id}: each assertion must be a mapping")
                continue
            relative = str(assertion.get("file") or "").strip()
            needle = str(assertion.get("contains") or "")
            if not relative or not needle:
                errors.append(f"{pin_id}: assertions require file and contains")
                continue
            path = (repository_root / relative).resolve()
            if repository_root not in path.parents:
                errors.append(f"{pin_id}: assertion path escapes repository: {relative}")
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except OSError as exc:
                errors.append(f"{pin_id}: cannot read assertion file {relative}: {type(exc).__name__}")
                continue
            if needle not in content:
                errors.append(f"{pin_id}: {relative} is missing the pinned declaration {needle!r}")
                continue
            checked_assertions += 1
        rows.append(
            {
                "id": pin_id,
                "kind": str(row.get("kind") or ""),
                "version": version,
                "status": status,
                "scope": str(row.get("scope") or ""),
                "assertion_count": len(assertions),
                "checked_assertion_count": checked_assertions,
                **({"reason": str(row.get("reason") or "")} if status == "unavailable" else {}),
            }
        )

    locked_count = sum(row["status"] == "locked" for row in rows)
    unavailable_count = sum(row["status"] == "unavailable" for row in rows)
    return {
        "schema_version": CHECK_SCHEMA,
        "status": "pass" if not errors else "fail",
        "pin_count": len(rows),
        "locked_count": locked_count,
        "unavailable_count": unavailable_count,
        "pins": rows,
        "errors": sorted(errors),
        "policy": {
            "programme_scope": "personal-internal-only",
            "licence_metadata_is_gate": False,
        },
        "scope": "exact source declarations and explicit host/operator unavailability; no installation or network probe",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pins", type=Path, default=DEFAULT_PINS)
    parser.add_argument("--json", action="store_true", help="emit the machine-readable report")
    args = parser.parse_args(argv)
    report = check(pins_path=args.pins)
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(
            f"operator-pins: {report['status']} — pins={report['pin_count']} "
            f"locked={report['locked_count']} unavailable={report['unavailable_count']}"
        )
        for error in report["errors"]:
            print(f"operator-pins: {error}", file=sys.stderr)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
