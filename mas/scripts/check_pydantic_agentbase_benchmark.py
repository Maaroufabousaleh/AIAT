"""Validate the dependency-free Pydantic AI versus AgentBase run plan.

This checker validates the fixed benchmark corpus only. It never imports
Pydantic AI, calls a model/provider, changes the AgentBase default, or declares
an adoption decision.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

SCHEMA = "aiat.pydantic-agentbase-benchmark-readiness.v1"
CORPUS_SCHEMA = "aiat.pydantic-agentbase-benchmark-corpus.v1"
EXPECTED_COUNTS = {
    "structured_no_tools": 12,
    "tool_selection_structured_output": 12,
    "multiturn_project_context": 8,
    "policy_denial": 6,
    "budget_model_constraints": 4,
    "checkpoint_restart_resume": 6,
}
REQUIRED_METRICS = (
    "schema_acceptance",
    "task_quality",
    "tool_selection",
    "prohibited_action_rejection",
    "model_attribution",
    "token_count",
    "approximate_cost",
    "median_latency_ms",
    "p95_latency_ms",
    "human_interventions",
    "restart_recovery",
    "maintainable_implementation_loc",
    "generic_mechanics_loc",
    "policy_violations",
)


def corpus_path() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "pydantic-agentbase-benchmark" / "corpus.json"


def _add(errors: list[str], message: str) -> None:
    if message not in errors:
        errors.append(message)


def validate_corpus(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema_version") != CORPUS_SCHEMA:
        _add(errors, "schema_version_mismatch")
    if payload.get("status") != "READY_NOT_RUN":
        _add(errors, "corpus_must_remain_ready_not_run")

    cases = payload.get("cases")
    if not isinstance(cases, list):
        _add(errors, "cases_missing")
        cases = []
    if payload.get("case_count") != len(cases):
        _add(errors, "case_count_mismatch")
    if len(cases) != sum(EXPECTED_COUNTS.values()):
        _add(errors, "case_count_must_be_48")

    counts = {category: 0 for category in EXPECTED_COUNTS}
    ids: set[str] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            _add(errors, f"case_not_object:{index}")
            continue
        case_id = case.get("id")
        category = case.get("category")
        if not isinstance(case_id, str) or not case_id:
            _add(errors, f"case_id_missing:{index}")
        elif case_id in ids:
            _add(errors, f"duplicate_case_id:{case_id}")
        else:
            ids.add(case_id)
        if category not in EXPECTED_COUNTS:
            _add(errors, f"unknown_category:{case_id or index}")
        else:
            counts[category] += 1
        for field in ("title", "prompt", "acceptance"):
            value = case.get(field)
            if not value or (isinstance(value, list) and not all(isinstance(item, str) and item for item in value)):
                _add(errors, f"case_field_invalid:{case_id or index}:{field}")
    if counts != EXPECTED_COUNTS:
        _add(errors, "category_counts_mismatch")
    if payload.get("categories") != EXPECTED_COUNTS:
        _add(errors, "declared_category_counts_mismatch")

    expected_policy = {
        "same_model_profile": True,
        "same_exact_model_id": True,
        "same_tool_schemas": True,
        "same_tool_grants": True,
        "same_project_context": True,
        "network": "deny",
        "credentials": "none",
        "privileged_effects": "forbidden",
        "payloads_retained": False,
        "production_activation": False,
        "dependency_added": False,
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
        if run_plan.get("candidates") != ["agentbase", "pydantic_ai"]:
            _add(errors, "candidate_order_or_set_mismatch")
        if run_plan.get("repetitions_per_case") != 3:
            _add(errors, "repetition_count_mismatch")
        if run_plan.get("runs_per_candidate") != 144:
            _add(errors, "runs_per_candidate_mismatch")
        if run_plan.get("total_runs") != 288:
            _add(errors, "total_run_count_mismatch")
        if run_plan.get("winner_requires_complete_governed_evidence") is not True:
            _add(errors, "winner_gate_missing")

    candidates = payload.get("candidates")
    if not isinstance(candidates, dict):
        _add(errors, "candidates_missing")
    else:
        for name in ("agentbase", "pydantic_ai"):
            candidate = candidates.get(name)
            if not isinstance(candidate, dict) or not candidate.get("implementation"):
                _add(errors, f"candidate_invalid:{name}")
        if isinstance(candidates.get("agentbase"), dict) and candidates["agentbase"].get("status") != "CURRENT_DEFAULT_UNCHANGED":
            _add(errors, "agentbase_default_changed")
        if isinstance(candidates.get("pydantic_ai"), dict) and candidates["pydantic_ai"].get("status") != "NOT_ADOPTED":
            _add(errors, "pydantic_ai_activation_claimed")

    return {
        "schema_version": SCHEMA,
        "status": "PASS" if not errors else "FAILED_CERTIFICATION_IMPLEMENTATION",
        "errors": errors,
        "corpus_id": payload.get("corpus_id"),
        "case_count": len(cases),
        "category_counts": counts,
        "execution_status": "NOT_RUN",
        "benchmark_runs": 0,
        "decision": None,
        "pydantic_ai_dependency_added": False,
        "production_default_changed": False,
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
        print(f"pydantic-agentbase-benchmark: {report['status']}")
        if report["errors"]:
            print("  errors: " + ", ".join(report["errors"]))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
