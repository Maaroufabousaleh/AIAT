"""Reconcile the documented default-worker table with checked-in manifests.

This is a deterministic declaration/implementation check for the personal
AIAT instance.  It proves that the default worker slots named by the target
programme still point at the intended department, runtime boundary, and
adapter-specific stack.  It deliberately does not claim package availability,
security scans, sandbox smoke, canary, live-run, rollback, or provider
certification.  Licence/restriction values remain provenance metadata only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from mas_core.protocols.worker_manifest import WorkerManifest
from mas_core.worker_registry.runtime_catalog import RUNTIME_CATALOG

MAS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKERS_DIR = MAS_ROOT / "workers"
CHECK_SCHEMA = "aiat.default-worker-bindings.v1"


# Keep this table intentionally close to the corrected default-worker table in
# AIAT_TARGET_PROGRAMME.md.  The values are implementation intent, not a
# licence allow-list and not a live certification result.
EXPECTED_BINDINGS: dict[str, dict[str, Any]] = {
    "financial_analyst": {
        "department": "office_cfo",
        "runtime_tier": "langgraph",
        "isolation_mode": "langgraph",
        "transport": "process",
        "capability_prefix": "finance.",
        "config": {"preferred_runtime": "langgraph"},
    },
    "tech_analyst": {
        "department": "office_cio",
        "runtime_tier": "langgraph",
        "isolation_mode": "langgraph",
        "transport": "process",
        "capability_prefix": "technology.",
        "config_contains": {"supported_frameworks": ["langgraph", "microsoft_agent_framework"]},
    },
    "hr_analyst": {
        "department": "office_chrm",
        "runtime_tier": "langgraph",
        "isolation_mode": "langgraph",
        "transport": "process",
        "capability_prefix": "workforce.",
        "config": {"preferred_runtime": "langgraph"},
    },
    "security_analyst": {
        "department": "office_cso",
        "runtime_tier": "external",
        "isolation_mode": "wrapper",
        "transport": "process",
        "capability_prefix": "security.",
        "required_tools": ["security.scan"],
        "config_contains": {"preferred_tools": ["semgrep", "skillspector"]},
    },
    "sprint_planner": {
        "department": "office_cto",
        "runtime_tier": "langgraph",
        "isolation_mode": "langgraph",
        "transport": "process",
        "capability_prefix": "sprint.",
        "config": {"preferred_runtime": "langgraph", "default_planning_adapter": "ccpm"},
    },
    "kpi_analyst": {
        "department": "office_cto",
        "runtime_tier": "langgraph",
        "isolation_mode": "langgraph",
        "transport": "process",
        "capability_prefix": "kpi.",
        "config": {"preferred_runtime": "langgraph"},
    },
    "requirements_writer": {
        "department": "dept_production",
        "runtime_tier": "langgraph",
        "isolation_mode": "langgraph",
        "transport": "process",
        "capability_prefix": "docs.requirements_",
        "config": {"preferred_runtime": "langgraph"},
        "config_contains": {"document_stack": ["docling", "github-spec-kit"]},
    },
    "planner": {
        "department": "dept_production",
        "runtime_tier": "langgraph",
        "isolation_mode": "langgraph",
        "transport": "process",
        "capability_prefix": "production.plan_",
        "config": {"default_planning_adapter": "ccpm"},
        "config_contains": {"available_issue_adapters": ["github_issues", "plane", "openproject"]},
    },
    "cost_estimator": {
        "department": "dept_production",
        "runtime_tier": "langgraph",
        "isolation_mode": "langgraph",
        "transport": "process",
        "capability_prefix": "production.cost_",
        "config": {"preferred_runtime": "langgraph"},
    },
    "system_architect": {
        "department": "dept_system",
        "runtime_tier": "crewai",
        "isolation_mode": "crewai",
        "transport": "process",
        "capability_prefix": "system.architecture_",
        "config": {"preferred_runtime": "crewai"},
        "config_contains": {"diagram_stack": ["mermaid"]},
    },
    "solution_designer": {
        "department": "dept_system",
        "runtime_tier": "langgraph",
        "isolation_mode": "langgraph",
        "transport": "process",
        "capability_prefix": "system.solution_",
        "config": {"preferred_runtime": "langgraph"},
        "config_contains": {"supported_frameworks": ["langgraph", "microsoft_agent_framework"], "integration_adapters": ["mcp", "github", "web"]},
    },
    "tech_writer": {
        "department": "dept_system",
        "runtime_tier": "langgraph",
        "isolation_mode": "langgraph",
        "transport": "process",
        "capability_prefix": "docs.technical_",
        "config": {"preferred_runtime": "langgraph"},
        "config_contains": {"document_stack": ["docling", "mermaid"]},
    },
    "tester": {
        "department": "dept_qa",
        "runtime_tier": "external",
        "isolation_mode": "opencode",
        "transport": "opencode",
        "adapter_entrypoint": "OpenCodeAdapter",
        "capability_prefix": "qa.test_",
        "config_contains": {"coding_stack": ["opencode", "playwright", "pytest"], "optional_external_adapters": ["openhands-core"]},
    },
    "devops_eng": {
        "department": "dept_devops",
        "runtime_tier": "external",
        "isolation_mode": "wrapper",
        "transport": "process",
        "capability_prefix": "devops.",
        "config": {"default_iac": "opentofu", "default_ci": "github_actions"},
        "config_contains": {"available_adapters": ["opentofu", "github_actions", "ansible"]},
    },
    "sre_agent": {
        "department": "dept_devops",
        "runtime_tier": "external",
        "isolation_mode": "wrapper",
        "transport": "process",
        "capability_prefix": "sre.",
        "config_contains": {"monitoring_stack": ["litellm-ui", "omniroute-analytics", "playwright-api-checks"]},
        "config": {"optional_metrics": "prometheus-compatible"},
    },
}


def _load_manifest(path: Path) -> WorkerManifest:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name}: expected a YAML mapping")
    return WorkerManifest.model_validate(raw)


def _department(manifest: WorkerManifest) -> str | None:
    return next(
        (tag for tag in manifest.metadata.tags if tag.startswith(("exec_", "office_", "dept_"))),
        None,
    )


def _config_values(config: dict[str, Any], key: str) -> list[str]:
    value = config.get(key)
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value]
    return [str(value)] if value is not None else []


def _check_contains(actual: dict[str, Any], expected: dict[str, list[str]], *, worker_id: str, errors: list[str]) -> None:
    for key, required_values in expected.items():
        observed = set(_config_values(actual, key))
        missing = [value for value in required_values if value not in observed]
        if missing:
            errors.append(f"{worker_id}: runtime.adapter_config.{key} missing {missing}")


def reconcile(*, workers_dir: Path = DEFAULT_WORKERS_DIR) -> dict[str, Any]:
    errors: list[str] = []
    rows: list[dict[str, Any]] = []
    manifests: dict[str, WorkerManifest] = {}
    for path in sorted(workers_dir.glob("*.yaml")):
        try:
            manifest = _load_manifest(path)
        except (OSError, ValueError, yaml.YAMLError, ValidationError) as exc:
            errors.append(str(exc))
            continue
        manifests[manifest.metadata.id] = manifest

    for worker_id, expected in EXPECTED_BINDINGS.items():
        manifest = manifests.get(worker_id)
        if manifest is None:
            errors.append(f"{worker_id}: documented default worker is missing from checked-in manifests")
            continue
        config = dict(manifest.runtime.adapter_config or {})
        observed_tools = {
            str(tool)
            for capability in manifest.capabilities
            for tool in capability.required_tools
        }
        row_errors: list[str] = []
        checks = {
            "department": _department(manifest) == expected["department"],
            "runtime_tier": manifest.runtime_tier == expected["runtime_tier"],
            "isolation_mode": manifest.integration.isolation_mode == expected["isolation_mode"],
            "transport": manifest.runtime.transport == expected["transport"],
            "capability_prefix": any(
                capability.name.startswith(expected["capability_prefix"])
                for capability in manifest.capabilities
            ),
        }
        runtime_definition = RUNTIME_CATALOG.get(manifest.runtime_tier)
        checks["runtime_catalogue_pair"] = bool(
            runtime_definition
            and manifest.runtime.transport in runtime_definition.supported_transports
            and manifest.integration.isolation_mode in runtime_definition.supported_isolation_modes
        )
        expected_entrypoint = str(expected.get("adapter_entrypoint") or "WorkerAgent")
        checks["adapter_entrypoint"] = bool(
            config.get("entrypoint") == expected_entrypoint
            and manifest.integration.adapter_entrypoint == expected_entrypoint
        )
        for key, passed in checks.items():
            if not passed:
                observed = {
                    "department": _department(manifest),
                    "runtime_tier": manifest.runtime_tier,
                    "isolation_mode": manifest.integration.isolation_mode,
                    "transport": manifest.runtime.transport,
                    "runtime_catalogue_pair": {
                        "runtime_tier": manifest.runtime_tier,
                        "transport": manifest.runtime.transport,
                        "isolation_mode": manifest.integration.isolation_mode,
                    },
                    "adapter_entrypoint": {
                        "runtime": config.get("entrypoint"),
                        "integration": manifest.integration.adapter_entrypoint,
                    },
                }.get(key, expected["capability_prefix"])
                expected_value = (
                    "runtime catalogue supports declared transport/isolation"
                    if key == "runtime_catalogue_pair"
                    else expected_entrypoint
                    if key == "adapter_entrypoint"
                    else expected.get(key) or expected["capability_prefix"]
                )
                row_errors.append(f"{key}: expected {expected_value!r}, observed {observed!r}")
        for key, value in (expected.get("config") or {}).items():
            if config.get(key) != value:
                row_errors.append(f"runtime.adapter_config.{key}: expected {value!r}, observed {config.get(key)!r}")
        _check_contains(config, expected.get("config_contains") or {}, worker_id=worker_id, errors=row_errors)
        for required_tool in expected.get("required_tools") or []:
            if required_tool not in observed_tools:
                row_errors.append(f"capabilities.required_tools: missing {required_tool!r}")
        rows.append(
            {
                "worker_id": worker_id,
                "department": _department(manifest),
                "runtime_tier": manifest.runtime_tier,
                "transport": manifest.runtime.transport,
                "isolation_mode": manifest.integration.isolation_mode,
                "runtime_catalogue_pair": checks["runtime_catalogue_pair"],
                "adapter_entrypoint": config.get("entrypoint"),
                "capability_names": sorted(capability.name for capability in manifest.capabilities),
                "certification_status": manifest.certification_status,
                "security_scan_status": str((manifest.source_provenance or {}).get("security_scan_status") or "not_recorded"),
                "status": "pass" if not row_errors else "fail",
                "errors": row_errors,
            }
        )
        errors.extend(f"{worker_id}: {error}" for error in row_errors)

    return {
        "schema_version": CHECK_SCHEMA,
        "programme_scope": "personal-internal-only",
        "licence_metadata_is_gate": False,
        "checked_worker_count": len(rows),
        "expected_worker_count": len(EXPECTED_BINDINGS),
        "workers": rows,
        "errors": sorted(errors),
        "status": "pass" if not errors else "fail",
        "scope": "static documented-default binding reconciliation; no runtime, storage, scanner, or deployment mutation",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers-dir", type=Path, default=DEFAULT_WORKERS_DIR)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--live", action="store_true", help="explicitly report the live certification boundary")
    args = parser.parse_args(argv)

    report = reconcile(workers_dir=args.workers_dir)
    if args.live:
        report["live"] = {
            "status": "blocked",
            "reason": "live adapter, sandbox, canary, and rollback certification requires an operator-selected environment",
        }
        report["status"] = "fail" if report["errors"] else "blocked"
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print(
            f"default-worker-bindings: {report['status']} "
            f"workers={report['checked_worker_count']}/{report['expected_worker_count']} "
            f"errors={len(report['errors'])}"
        )
        for error in report["errors"]:
            print(f"default-worker-bindings: error: {error}", file=sys.stderr)
    if args.live:
        return 1 if report["errors"] else 2
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
