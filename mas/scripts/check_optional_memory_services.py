"""Validate the disabled-by-default optional memory/workflow service contract.

This checker is a static, payload-free readiness contract for Letta, Qdrant,
and Temporal candidates.  It does not install, contact, enable, select, or
mutate an optional service.  AIAT remains the authority for state, scope,
retention, deletion, and rollback; licence metadata is informational only.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

CHECK_SCHEMA = "aiat.optional-memory-services-check.v1"
CATALOGUE_SCHEMA = "aiat.optional-memory-services.v1"
MAS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOGUE = MAS_ROOT / "docs" / "provenance" / "optional_memory_services.yaml"
EXPECTED_IDS = ("letta", "qdrant", "temporal")
REQUIRED_TOP_LEVEL = {
    "id",
    "display_name",
    "category",
    "status",
    "default_enabled",
    "integration_mode",
    "adapter",
    "provenance",
    "steward",
    "authority_boundary",
    "data_boundary",
    "measurable_value",
    "conformance",
    "outage_recovery",
    "backup_restore",
    "removal",
}
REQUIRED_SECTIONS = {
    "provenance": {"source_kind", "version_status", "metadata_ref", "licence_metadata_ref"},
    "steward": {"id", "owner", "runtime_tier", "sandbox_profile", "network_mode"},
    "authority_boundary": {
        "state_owner",
        "retention_owner",
        "deletion_owner",
        "project_scope_required",
        "canonical_write_path",
    },
    "data_boundary": {"input_scope", "output_scope", "credentials", "raw_payload_retention"},
    "measurable_value": {"baseline", "metrics", "threshold", "budget_owner", "stop_condition"},
    "conformance": {"required_checks"},
    "outage_recovery": {"failure_classification", "degraded_mode", "recovery_check"},
    "backup_restore": {"backup_scope", "restore_check", "cleanup_check"},
    "removal": {"disable_action", "data_delete_action", "rollback_check"},
}


def _non_empty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _load(path: Path) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return {}, [f"catalogue could not be loaded: {type(exc).__name__}"]
    if not isinstance(raw, dict):
        return {}, ["catalogue must be a YAML object"]
    return raw, errors


def _validate(catalogue: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    errors: list[str] = []
    if catalogue.get("schema_version") != CATALOGUE_SCHEMA:
        errors.append("catalogue schema_version is invalid")
    if catalogue.get("programme_scope") != "personal-internal-only":
        errors.append("catalogue programme_scope must be personal-internal-only")

    policy = catalogue.get("policy")
    if not isinstance(policy, dict):
        errors.append("catalogue policy must be an object")
    else:
        if policy.get("default_enabled") is not False:
            errors.append("optional services must be disabled by default")
        if policy.get("state_authority") != "aiat":
            errors.append("AIAT must remain the optional-service state authority")
        if policy.get("project_scope_required") is not True:
            errors.append("optional services must require project scope")
        if policy.get("licence_metadata_is_gate") is not False:
            errors.append("licence metadata must be explicitly non-gating")

    services = catalogue.get("services")
    if not isinstance(services, list):
        return errors + ["catalogue services must be a list"], []
    ids = [item.get("id") for item in services if isinstance(item, dict)]
    if tuple(ids) != EXPECTED_IDS:
        errors.append(f"catalogue service order/IDs must be {EXPECTED_IDS!r}")

    rows: list[dict[str, Any]] = []
    for index, service in enumerate(services):
        prefix = f"services[{index}]"
        if not isinstance(service, dict):
            errors.append(f"{prefix} must be an object")
            continue
        missing = sorted(REQUIRED_TOP_LEVEL - set(service))
        errors.extend(f"{prefix} missing {key}" for key in missing)
        if service.get("status") != "candidate":
            errors.append(f"{prefix}.status must be candidate")
        if service.get("default_enabled") is not False:
            errors.append(f"{prefix}.default_enabled must be false")
        for section, required in REQUIRED_SECTIONS.items():
            value = service.get(section)
            if not isinstance(value, dict):
                errors.append(f"{prefix}.{section} must be an object")
                continue
            errors.extend(f"{prefix}.{section} missing {key}" for key in sorted(required - set(value)))
            if section == "provenance":
                for key in ("metadata_ref", "licence_metadata_ref"):
                    if not _non_empty(value.get(key)):
                        errors.append(f"{prefix}.{section}.{key} must be a non-empty metadata path")
            elif section == "steward":
                if value.get("owner") != "aiat":
                    errors.append(f"{prefix}.steward.owner must be aiat")
            elif section == "authority_boundary":
                for key in ("state_owner", "retention_owner", "deletion_owner"):
                    if value.get(key) != "aiat":
                        errors.append(f"{prefix}.{section}.{key} must be aiat")
                if value.get("project_scope_required") is not True:
                    errors.append(f"{prefix}.{section}.project_scope_required must be true")
            elif section == "data_boundary":
                if value.get("raw_payload_retention") != "forbidden":
                    errors.append(f"{prefix}.{section}.raw_payload_retention must be forbidden")
            elif section == "measurable_value":
                metrics = value.get("metrics")
                if not isinstance(metrics, list) or not metrics or not all(_non_empty(item) for item in metrics):
                    errors.append(f"{prefix}.{section}.metrics must be a non-empty string list")
            elif section == "conformance":
                checks = value.get("required_checks")
                if not isinstance(checks, list) or not checks or not all(_non_empty(item) for item in checks):
                    errors.append(f"{prefix}.{section}.required_checks must be a non-empty string list")
        rows.append(
            {
                "id": service.get("id"),
                "category": service.get("category"),
                "status": service.get("status"),
                "default_enabled": service.get("default_enabled"),
                "integration_mode": service.get("integration_mode"),
                "steward_id": (service.get("steward") or {}).get("id"),
                "required_conformance_count": len((service.get("conformance") or {}).get("required_checks") or []),
                "measurable_metric_count": len((service.get("measurable_value") or {}).get("metrics") or []),
                "authority_owner": (service.get("authority_boundary") or {}).get("state_owner"),
                "raw_payload_retention": (service.get("data_boundary") or {}).get("raw_payload_retention"),
                "removal_defined": all(
                    _non_empty((service.get("removal") or {}).get(key))
                    for key in ("disable_action", "data_delete_action", "rollback_check")
                ),
            }
        )
    return errors, rows


def build_report(*, catalogue_path: Path = DEFAULT_CATALOGUE, live: bool = False) -> dict[str, Any]:
    catalogue, load_errors = _load(catalogue_path)
    errors, rows = _validate(catalogue)
    errors = load_errors + errors
    report: dict[str, Any] = {
        "schema_version": CHECK_SCHEMA,
        "catalogue_schema_version": CATALOGUE_SCHEMA,
        "mode": "live" if live else "static",
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "services": rows,
        "service_count": len(rows),
        "enabled_service_count": sum(row.get("default_enabled") is True for row in rows),
        "mutation_performed": False,
        "network_access_performed": False,
        "licence_metadata_is_gate": False,
        "scope": "optional Letta/Qdrant/Temporal value, authority, boundary, recovery, and removal contract",
    }
    if live and not errors:
        report.update(
            status="blocked",
            reason="operator-selected optional service endpoints and certified sandboxes are required; no live service was contacted",
            live_scope="readiness contract only; no installation, activation, selection, or mutation",
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--live", action="store_true", help="require explicit live service configuration")
    parser.add_argument("--catalogue", type=Path, default=DEFAULT_CATALOGUE)
    args = parser.parse_args(argv)
    report = build_report(catalogue_path=args.catalogue, live=args.live)
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"optional memory services: {report['status']}")
    return 2 if report["status"] == "blocked" else (0 if report["status"] == "pass" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
