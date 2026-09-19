"""Validate the dependency-free OpenCode/OpenHands benchmark run plan.

This checker validates the fixed corpus and its governed run plan. It does not
start either coding runtime, call a model, access a provider, or declare a
winner. A passing report means the benchmark is ready to run, not that it ran.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

SCHEMA = "aiat.coding-runtime-benchmark-readiness.v1"
CORPUS_SCHEMA = "aiat.coding-runtime-benchmark-corpus.v1"
EXPECTED_COUNTS = {
    "bounded_bug_fix": 10,
    "feature_addition": 10,
    "failing_test_repair": 6,
    "behavior_preserving_refactor": 6,
    "repository_analysis_documentation": 4,
    "adversarial_tool_policy_sandbox": 4,
}
REQUIRED_OBSERVATIONS = (
    "task_success",
    "tests_passed",
    "correctness",
    "diff_size",
    "diff_quality",
    "wall_time_ms",
    "model_tokens",
    "approximate_model_cost",
    "tool_calls",
    "pause",
    "interrupt",
    "recovery",
    "timeout",
    "security_gate",
    "integration_complexity",
    "cleanup",
)


def corpus_path() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "coding-runtime-benchmark" / "corpus.json"


def _error(errors: list[str], message: str) -> None:
    if message not in errors:
        errors.append(message)


def validate_corpus(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema_version") != CORPUS_SCHEMA:
        _error(errors, "schema_version_mismatch")
    if payload.get("status") != "READY_NOT_RUN":
        _error(errors, "corpus_must_remain_ready_not_run")

    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        _error(errors, "tasks_missing")
        tasks = []
    if payload.get("task_count") != len(tasks):
        _error(errors, "task_count_mismatch")
    if len(tasks) != sum(EXPECTED_COUNTS.values()):
        _error(errors, "task_count_must_be_40")

    counts = {category: 0 for category in EXPECTED_COUNTS}
    ids: set[str] = set()
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            _error(errors, f"task_not_object:{index}")
            continue
        task_id = task.get("id")
        category = task.get("category")
        if not isinstance(task_id, str) or not task_id:
            _error(errors, f"task_id_missing:{index}")
        elif task_id in ids:
            _error(errors, f"duplicate_task_id:{task_id}")
        else:
            ids.add(task_id)
        if category not in EXPECTED_COUNTS:
            _error(errors, f"unknown_category:{task_id or index}")
        else:
            counts[category] += 1
        for field in ("title", "prompt", "acceptance"):
            value = task.get(field)
            if not value or (isinstance(value, list) and not all(isinstance(item, str) and item for item in value)):
                _error(errors, f"task_field_invalid:{task_id or index}:{field}")
        if task.get("external_side_effects") is not None:
            _error(errors, f"task_must_not_override_effect_policy:{task_id or index}")

    if counts != EXPECTED_COUNTS:
        _error(errors, "category_counts_mismatch")
    if payload.get("categories") != EXPECTED_COUNTS:
        _error(errors, "declared_category_counts_mismatch")

    policy = payload.get("execution_policy")
    if not isinstance(policy, dict):
        _error(errors, "execution_policy_missing")
    else:
        expected_policy = {
            "workspace_mode": "isolated_copy",
            "external_side_effects": "forbidden",
            "network": "deny",
            "credentials": "none",
            "same_task_and_budget_required": True,
            "same_provider_and_model_required": True,
            "payloads_retained": False,
        }
        for key, expected in expected_policy.items():
            if policy.get(key) != expected:
                _error(errors, f"execution_policy_mismatch:{key}")

    observations = payload.get("required_observations")
    if observations != list(REQUIRED_OBSERVATIONS):
        _error(errors, "required_observations_mismatch")

    run_plan = payload.get("run_plan")
    if not isinstance(run_plan, dict):
        _error(errors, "run_plan_missing")
    else:
        if run_plan.get("candidates") != ["opencode", "openhands"]:
            _error(errors, "candidate_order_or_set_mismatch")
        if run_plan.get("repetitions_per_candidate") != 2:
            _error(errors, "repetition_count_mismatch")
        if run_plan.get("runs_per_candidate") != 80:
            _error(errors, "runs_per_candidate_mismatch")
        if run_plan.get("total_runs") != 160:
            _error(errors, "total_run_count_mismatch")
        if run_plan.get("winner_requires_complete_governed_evidence") is not True:
            _error(errors, "winner_gate_missing")

    return {
        "schema_version": SCHEMA,
        "status": "PASS" if not errors else "FAILED_CERTIFICATION_IMPLEMENTATION",
        "errors": errors,
        "corpus_id": payload.get("corpus_id"),
        "task_count": len(tasks),
        "category_counts": counts,
        "execution_status": "NOT_RUN",
        "benchmark_runs": 0,
        "decision": None,
        "payloads_retained": False,
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
    }


def validate_default_corpus() -> dict[str, Any]:
    try:
        payload = json.loads(corpus_path().read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    return validate_corpus(payload if isinstance(payload, dict) else {})


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
        print(f"coding-runtime-benchmark: {report['status']}")
        if report["errors"]:
            print("  errors: " + ", ".join(report["errors"]))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
