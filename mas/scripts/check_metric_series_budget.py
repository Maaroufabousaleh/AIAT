"""Check AIAT Prometheus series budgets without exposing metric payloads.

The default profile exercises the bounded project-state metric with a synthetic
large population and checks the in-process AIAT registry. ``--live`` fetches
the orchestrator ``/metrics`` exposition, counts only ``mas_*`` families, and
applies the same total/family budgets. Missing API configuration or an
unreachable endpoint is ``blocked`` (exit 2), never a pass.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx
from prometheus_client.parser import text_string_to_metric_families

from mas_core.observability.metrics import (
    METRIC_FAMILY_SERIES_BUDGETS,
    METRIC_SERIES_BUDGET,
    metric_declared_label_inventory,
    metric_label_inventory,
    metric_label_policy_inventory,
    metric_series_budget_status,
    reconcile_project_state_metrics,
)

CHECK_SCHEMA = "aiat.metric-series-budget-check.v1"
REQUIRED_FAMILIES = frozenset(
    {"mas_project_state", "mas_review_circuit_open", "mas_infra_lead_time"}
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--live", action="store_true", help="fetch an orchestrator metrics scrape")
    parser.add_argument(
        "--url",
        default=os.getenv("AIAT_ORCHESTRATOR_URL", os.getenv("ORCHESTRATOR_API_URL", "")),
        help="orchestrator base URL (or AIAT_ORCHESTRATOR_URL/ORCHESTRATOR_API_URL)",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("AIAT_OPERATOR_API_KEY", os.getenv("AIAT_API_KEY", "")),
        help="optional operator key; never included in the report",
    )
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--synthetic-projects", type=int, default=10_000)
    return parser


def _status(
    *,
    family_counts: dict[str, int],
    labels: dict[str, set[str]],
    declared_labels: dict[str, tuple[str, ...]],
    label_policies: dict[str, dict[str, dict[str, str]]],
) -> tuple[bool, list[str]]:
    total = sum(family_counts.values())
    violations: list[str] = []
    missing_families = sorted(REQUIRED_FAMILIES - set(family_counts))
    if missing_families:
        violations.append("required AIAT metric families are missing: " + ", ".join(missing_families))
    if total > METRIC_SERIES_BUDGET:
        violations.append(f"total custom series {total} exceeds budget {METRIC_SERIES_BUDGET}")
    for name, budget in METRIC_FAMILY_SERIES_BUDGETS.items():
        count = family_counts.get(name, 0)
        if count > budget:
            violations.append(f"{name} has {count} series; budget is {budget}")
    for name, values in labels.items():
        policies = label_policies.get(name)
        if policies is None:
            violations.append(f"{name} has no declared AIAT label policy")
            policies = {}
        if "project_id" in values:
            violations.append(f"{name} exposes an unbounded project_id label")
        for label in sorted(values):
            policy = policies.get(label)
            if policy is None:
                violations.append(f"{name}.{label} has no declared label policy")
            elif policy.get("classification") != "bounded":
                violations.append(
                    f"{name}.{label} is classified {policy.get('classification', 'unknown')}, not bounded"
                )
    for name, declared in declared_labels.items():
        policies = label_policies.get(name)
        if policies is None:
            violations.append(f"{name} has no declared AIAT label policy")
            continue
        for label in declared:
            if label not in policies:
                violations.append(f"{name}.{label} has no policy for its declared label")
        for label in policies:
            if label not in declared:
                violations.append(f"{name}.{label} is in policy but not declared by the collector")
    for name, policies in label_policies.items():
        for label, policy in policies.items():
            if policy.get("classification") != "bounded":
                violations.append(
                    f"{name}.{label} is classified {policy.get('classification', 'unknown')}, not bounded"
                )
    return not violations, violations


def _fixture(synthetic_projects: int) -> dict[str, Any]:
    count = max(1, min(int(synthetic_projects), 1_000_000))
    states = [
        state
        for index in range(count)
        for state in ("INIT", "IN_PROGRESS", "FAILED")
        if index % 3 == 0 or state != "FAILED"
    ]
    try:
        reconcile_project_state_metrics(states)
        registry_status = metric_series_budget_status()
        labels = metric_label_inventory()
        family_counts = {
            str(name): int(value) for name, value in (registry_status.get("family_counts") or {}).items()
        }
        label_sets = {str(name): set(values) for name, values in labels.items()}
        declared_labels = metric_declared_label_inventory()
        label_policies = metric_label_policy_inventory()
        passed, violations = _status(
            family_counts=family_counts,
            labels=label_sets,
            declared_labels=declared_labels,
            label_policies=label_policies,
        )
        passed = passed and bool(registry_status.get("passed"))
        return {
            "schema_version": CHECK_SCHEMA,
            "mode": "fixture",
            "status": "pass" if passed else "fail",
            "synthetic_project_count": count,
            "total": sum(family_counts.values()),
            "family_counts": family_counts,
            "budget": METRIC_SERIES_BUDGET,
            "family_budgets": dict(METRIC_FAMILY_SERIES_BUDGETS),
            "label_inventory": {name: sorted(values) for name, values in label_sets.items()},
            "declared_label_inventory": {
                name: list(values) for name, values in declared_labels.items()
            },
            "label_policies": label_policies,
            "violations": violations,
            "project_id_label_present": any("project_id" in values for values in label_sets.values()),
            "scope": "deterministic in-process registry and synthetic bounded project population",
        }
    finally:
        reconcile_project_state_metrics([])


def _normalize_scrape_family_name(name: str) -> str:
    """Fold Prometheus client's synthetic histogram ``_created`` family.

    The Python client exposes a timestamp sample named ``<histogram>_created``
    without a separate ``# TYPE`` declaration.  It is part of the same
    bounded histogram family for this check, not an undeclared AIAT metric.
    Keep the normalization narrow so a real metric whose name merely ends in
    ``_created`` is still rejected unless its base collector is declared.
    """

    suffix = "_created"
    if name.endswith(suffix):
        base = name[: -len(suffix)]
        declared_families = set(METRIC_FAMILY_SERIES_BUDGETS) | set(
            metric_declared_label_inventory()
        )
        if base in declared_families:
            return base
    return name


def _parse_scrape(body: str) -> tuple[dict[str, int], dict[str, set[str]]]:
    family_counts: dict[str, int] = {}
    labels: dict[str, set[str]] = {}
    for family in text_string_to_metric_families(body):
        name = _normalize_scrape_family_name(str(family.name))
        if not name.startswith("mas_"):
            continue
        samples = list(family.samples)
        family_counts[name] = family_counts.get(name, 0) + len(samples)
        label_names = labels.setdefault(name, set())
        for sample in samples:
            label_names.update(str(key) for key in sample.labels)
    return family_counts, labels


def _blocked(reason: str, *, url_configured: bool = False) -> dict[str, Any]:
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "live",
        "status": "blocked",
        "reason": reason,
        "url_configured": url_configured,
        "scope": "orchestrator Prometheus exposition; AIAT-owned mas_* families only",
    }


def _live(*, url: str, api_key: str, timeout: float) -> dict[str, Any]:
    if not url.strip():
        return _blocked("missing live configuration: orchestrator URL")
    headers = {"X-API-Key": api_key} if api_key.strip() else {}
    try:
        response = httpx.get(f"{url.rstrip('/')}/metrics", headers=headers, timeout=timeout)
        response.raise_for_status()
        family_counts, labels = _parse_scrape(response.text)
    except (httpx.HTTPError, ValueError):
        return _blocked("orchestrator metrics endpoint unavailable", url_configured=True)
    label_policies = metric_label_policy_inventory()
    declared_labels = metric_declared_label_inventory()
    passed, violations = _status(
        family_counts=family_counts,
        labels=labels,
        declared_labels=declared_labels,
        label_policies=label_policies,
    )
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "live",
        "status": "pass" if passed else "fail",
        "url_configured": True,
        "total": sum(family_counts.values()),
        "family_counts": family_counts,
        "budget": METRIC_SERIES_BUDGET,
        "family_budgets": dict(METRIC_FAMILY_SERIES_BUDGETS),
        "label_inventory": {name: sorted(values) for name, values in labels.items()},
        "declared_label_inventory": {
            name: list(values) for name, values in declared_labels.items()
        },
        "label_policies": label_policies,
        "violations": violations,
        "project_id_label_present": any("project_id" in values for values in labels.values()),
        "scope": "orchestrator Prometheus exposition; AIAT-owned mas_* families only",
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = (
        _live(url=args.url, api_key=args.api_key, timeout=args.timeout)
        if args.live
        else _fixture(args.synthetic_projects)
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"metric-series-budget: {report['status']} — {report.get('scope', report.get('reason', ''))}")
    if report["status"] == "blocked":
        return 2
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
