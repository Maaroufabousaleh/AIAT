"""Validate the reproducible default worker-runtime install profile.

This is a source/lock/Dockerfile contract check.  It proves that the
production orchestrator image requests the same default runtimes declared in
``pyproject.toml`` and represented in ``uv.lock``.  It does not import the
runtimes, certify a worker, or evaluate licence/restriction metadata; those
remain separate runtime, security, sandbox, canary, and live-run evidence.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

from mas_core.worker_registry.runtime_catalog import RUNTIME_CATALOG

MAS_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = MAS_ROOT / "apps" / "orchestrator-api" / "pyproject.toml"
DOCKERFILE_PATH = MAS_ROOT / "infra" / "docker" / "Dockerfile.orchestrator-api"
LOCK_PATH = MAS_ROOT / "uv.lock"
CHECK_SCHEMA = "aiat.runtime-install-profile-check.v1"

DEFAULT_PROFILE = {
    "langgraph": ">=0.2,<1.0",
    "crewai": "==1.6.1",
}
_DEPENDENCY_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)")


def _normalise_name(value: str) -> str:
    return value.replace("_", "-").lower()


def _dependency_name(value: str) -> str | None:
    match = _DEPENDENCY_NAME_RE.match(value.strip())
    return _normalise_name(match.group(1)) if match else None


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        value = tomllib.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a TOML mapping")
    return value


def _lock_packages(lock: dict[str, Any]) -> dict[str, dict[str, Any]]:
    packages = lock.get("package") or []
    if not isinstance(packages, list):
        raise ValueError("uv.lock package entries must be a list")
    result: dict[str, dict[str, Any]] = {}
    for row in packages:
        if isinstance(row, dict) and isinstance(row.get("name"), str):
            result[_normalise_name(row["name"])] = row
    return result


def _profile_contract(pyproject: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    errors: list[str] = []
    optional = ((pyproject.get("project") or {}).get("optional-dependencies") or {})
    declared = optional.get("runtime-default")
    if not isinstance(declared, list):
        return {}, ["orchestrator-api must declare project.optional-dependencies.runtime-default"]
    parsed: dict[str, str] = {}
    for dependency in declared:
        if not isinstance(dependency, str):
            errors.append("runtime-default entries must be dependency strings")
            continue
        name = _dependency_name(dependency)
        if name:
            parsed[name] = dependency
    for name, specifier in DEFAULT_PROFILE.items():
        if name not in parsed:
            errors.append(f"runtime-default is missing {name}")
        elif parsed[name] != f"{name}{specifier}":
            errors.append(
                f"runtime-default {name} must match the declared specifier {specifier}"
            )
        definition = RUNTIME_CATALOG.get(name)
        if definition is None or name not in {_normalise_name(item) for item in definition.required_imports}:
            errors.append(f"runtime catalogue does not require the {name} import")
    return parsed, errors


def build_report() -> dict[str, Any]:
    errors: list[str] = []
    try:
        pyproject = _read_toml(PYPROJECT_PATH)
        lock = _read_toml(LOCK_PATH)
        dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        return {
            "schema_version": CHECK_SCHEMA,
            "status": "fail",
            "errors": [f"cannot read install profile inputs: {type(exc).__name__}"],
            "scope": "source, lock, runtime-catalogue, and production Dockerfile contract",
            "policy": {"programme_scope": "personal-internal-only", "licence_metadata_is_gate": False},
        }

    declared, errors_from_profile = _profile_contract(pyproject)
    errors.extend(errors_from_profile)
    install_command = 'pip install "/tmp/orchestrator-api[runtime-default]"'
    dockerfile_has_profile = install_command in dockerfile
    if not dockerfile_has_profile:
        errors.append("production orchestrator Dockerfile does not install runtime-default")

    try:
        packages = _lock_packages(lock)
    except ValueError as exc:
        packages = {}
        errors.append(str(exc))
    locked_versions: dict[str, str | None] = {}
    for name in DEFAULT_PROFILE:
        package = packages.get(name)
        if package is None:
            errors.append(f"uv.lock is missing {name}")
            locked_versions[name] = None
        else:
            locked_versions[name] = str(package.get("version") or "") or None

    orchestrator = packages.get("orchestrator-api") or {}
    metadata = orchestrator.get("metadata") if isinstance(orchestrator, dict) else {}
    requires_dist = metadata.get("requires-dist") if isinstance(metadata, dict) else []
    lock_profile: dict[str, str] = {}
    for row in requires_dist if isinstance(requires_dist, list) else []:
        if not isinstance(row, dict) or row.get("marker") != "extra == 'runtime-default'":
            continue
        name = _normalise_name(str(row.get("name") or ""))
        specifier = str(row.get("specifier") or "")
        if name:
            lock_profile[name] = specifier
    for name, specifier in DEFAULT_PROFILE.items():
        if lock_profile.get(name) != specifier:
            errors.append(f"uv.lock runtime-default metadata does not preserve {name}{specifier}")

    return {
        "schema_version": CHECK_SCHEMA,
        "status": "fail" if errors else "pass",
        "profile": "runtime-default",
        "declared_dependencies": declared,
        "locked_versions": locked_versions,
        "lock_profile": lock_profile,
        "dockerfile_installs_profile": dockerfile_has_profile,
        "runtime_catalogue": {
            name: {
                "required_imports": list(RUNTIME_CATALOG[name].required_imports),
                "optional": RUNTIME_CATALOG[name].optional,
            }
            for name in DEFAULT_PROFILE
            if name in RUNTIME_CATALOG
        },
        "errors": errors,
        "certification_boundary": {
            "source_declaration": "checked",
            "lock_resolution": "checked",
            "production_image_install_command": "checked",
            "runtime_imports": "not_checked",
            "security_scan": "not_checked",
            "sandbox": "not_checked",
            "canary": "not_checked",
            "live_worker_run": "not_checked",
            "rollback": "not_checked",
        },
        "policy": {
            "programme_scope": "personal-internal-only",
            "licence_metadata_is_gate": False,
        },
        "scope": "source, lock, runtime-catalogue, and production Dockerfile contract",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the machine-readable report")
    args = parser.parse_args(argv)
    report = build_report()
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(
            f"runtime-install-profile: {report['status']} — "
            f"profile={report.get('profile', 'runtime-default')} "
            f"errors={len(report.get('errors', []))}"
        )
        for error in report.get("errors", []):
            print(f"runtime-install-profile: {error}", file=sys.stderr)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
