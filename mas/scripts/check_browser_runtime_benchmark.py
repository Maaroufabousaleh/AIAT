"""Validate the dependency-free Playwright/browser-agent benchmark plan.

The checker validates the fixed workflow corpus and safety policy only. It
does not launch a browser, install Stagehand, use credentials, access a
provider, or perform an external browser mutation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

SCHEMA = "aiat.browser-runtime-benchmark-readiness.v1"
CORPUS_SCHEMA = "aiat.browser-runtime-benchmark-corpus.v1"
EXPECTED_COUNTS = {
    "form_fill_submit": 4,
    "login_session_continuation": 3,
    "navigation_and_tabs": 3,
    "structured_extraction": 4,
    "downloads_and_uploads": 3,
    "dynamic_selector_changes": 4,
    "iframe_and_shadow_dom": 3,
    "post_action_verification": 3,
    "changed_dom_fixtures": 3,
}
REQUIRED_METRICS = (
    "workflow_success",
    "post_action_verification",
    "dynamic_recovery",
    "duplicate_effects",
    "credential_policy_violations",
    "cross_origin_violations",
    "median_latency_ms",
    "p95_latency_ms",
    "model_calls",
    "approximate_model_cost",
    "human_interventions",
    "cleanup",
    "evidence_completeness",
)


def corpus_path() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "browser-runtime-benchmark" / "corpus.json"


def _add(errors: list[str], message: str) -> None:
    if message not in errors:
        errors.append(message)


def validate_corpus(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema_version") != CORPUS_SCHEMA:
        _add(errors, "schema_version_mismatch")
    if payload.get("status") != "READY_NOT_RUN":
        _add(errors, "corpus_must_remain_ready_not_run")
    workflows = payload.get("workflows")
    if not isinstance(workflows, list):
        _add(errors, "workflows_missing")
        workflows = []
    if payload.get("workflow_count") != len(workflows):
        _add(errors, "workflow_count_mismatch")
    if len(workflows) != sum(EXPECTED_COUNTS.values()):
        _add(errors, "workflow_count_must_be_30")

    counts = {category: 0 for category in EXPECTED_COUNTS}
    ids: set[str] = set()
    for index, workflow in enumerate(workflows):
        if not isinstance(workflow, dict):
            _add(errors, f"workflow_not_object:{index}")
            continue
        workflow_id = workflow.get("id")
        category = workflow.get("category")
        if not isinstance(workflow_id, str) or not workflow_id:
            _add(errors, f"workflow_id_missing:{index}")
        elif workflow_id in ids:
            _add(errors, f"duplicate_workflow_id:{workflow_id}")
        else:
            ids.add(workflow_id)
        if category not in EXPECTED_COUNTS:
            _add(errors, f"unknown_category:{workflow_id or index}")
        else:
            counts[category] += 1
        for field in ("title", "description", "acceptance"):
            value = workflow.get(field)
            if not value or (isinstance(value, list) and not all(isinstance(item, str) and item for item in value)):
                _add(errors, f"workflow_field_invalid:{workflow_id or index}:{field}")
    if counts != EXPECTED_COUNTS:
        _add(errors, "category_counts_mismatch")
    if payload.get("categories") != EXPECTED_COUNTS:
        _add(errors, "declared_category_counts_mismatch")

    expected_policy = {
        "workspace": "disposable_local_fixture",
        "network": "deny_except_explicit_fixture",
        "credentials": "none_for_corpus",
        "governed_identity_required_for_persistent_profiles": True,
        "consequential_external_effects": "forbidden",
        "shadow_effects": "read_only_or_disposable_fixture_only",
        "duplicate_submissions": "forbidden",
        "cross_origin_access": "forbidden",
        "payloads_retained": False,
        "production_activation": False,
        "stagehand_dependency_added": False,
    }
    policy = payload.get("execution_policy")
    if not isinstance(policy, dict):
        _add(errors, "execution_policy_missing")
    else:
        for key, expected in expected_policy.items():
            if policy.get(key) != expected:
                _add(errors, f"execution_policy_mismatch:{key}")

    if payload.get("required_metrics") != list(REQUIRED_METRICS):
        _add(errors, "required_metrics_mismatch")
    run_plan = payload.get("run_plan")
    if not isinstance(run_plan, dict):
        _add(errors, "run_plan_missing")
    else:
        if run_plan.get("candidates") != ["playwright", "agent_assisted_fallback"]:
            _add(errors, "candidate_order_or_set_mismatch")
        if run_plan.get("repetitions_per_workflow") != 3:
            _add(errors, "repetition_count_mismatch")
        if run_plan.get("runs_per_candidate") != 90:
            _add(errors, "runs_per_candidate_mismatch")
        if run_plan.get("total_runs") != 180:
            _add(errors, "total_run_count_mismatch")
        if run_plan.get("fallback_only_on_deterministic_failure") is not True:
            _add(errors, "fallback_policy_missing")
        if run_plan.get("winner_requires_complete_governed_evidence") is not True:
            _add(errors, "winner_gate_missing")

    candidates = payload.get("candidates")
    if not isinstance(candidates, dict):
        _add(errors, "candidates_missing")
    else:
        playwright = candidates.get("playwright")
        fallback = candidates.get("agent_assisted_fallback")
        if not isinstance(playwright, dict) or playwright.get("status") != "CURRENT_DEFAULT_UNCHANGED":
            _add(errors, "playwright_default_changed")
        if not isinstance(fallback, dict) or fallback.get("status") != "EXPERIMENTAL_NOT_ENABLED":
            _add(errors, "agentic_fallback_activated")

    return {
        "schema_version": SCHEMA,
        "status": "PASS" if not errors else "FAILED_CERTIFICATION_IMPLEMENTATION",
        "errors": errors,
        "corpus_id": payload.get("corpus_id"),
        "workflow_count": len(workflows),
        "category_counts": counts,
        "execution_status": "NOT_RUN",
        "benchmark_runs": 0,
        "decision": None,
        "stagehand_dependency_added": False,
        "playwright_default_changed": False,
        "payloads_retained": False,
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=corpus_path())
    parser.add_argument("--json", action="store_true", help="emit the full machine-readable report")
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.corpus.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    report = validate_corpus(payload if isinstance(payload, dict) else {})
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"browser-runtime-benchmark: {report['status']}")
        if report["errors"]:
            print("  errors: " + ", ".join(report["errors"]))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
