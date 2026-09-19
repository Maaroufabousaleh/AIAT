"""Tests for the dependency-free coding-runtime benchmark contract."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _module():
    script = Path(__file__).resolve().parents[1] / "check_coding_runtime_benchmark.py"
    spec = importlib.util.spec_from_file_location("check_coding_runtime_benchmark", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _record() -> dict:
    return json.loads(_module().corpus_path().read_text(encoding="utf-8"))


def test_fixed_corpus_is_ready_not_run_and_has_required_shape() -> None:
    module = _module()
    report = module.validate_corpus(_record())

    assert report["status"] == "PASS"
    assert report["task_count"] == 40
    assert report["execution_status"] == "NOT_RUN"
    assert report["benchmark_runs"] == 0
    assert report["decision"] is None
    assert report["external_network_access_performed"] is False
    assert report["external_provider_mutation_performed"] is False


def test_corpus_categories_match_the_approved_40_task_plan() -> None:
    module = _module()
    report = module.validate_corpus(_record())

    assert report["category_counts"] == {
        "bounded_bug_fix": 10,
        "feature_addition": 10,
        "failing_test_repair": 6,
        "behavior_preserving_refactor": 6,
        "repository_analysis_documentation": 4,
        "adversarial_tool_policy_sandbox": 4,
    }


def test_corpus_rejects_external_effects_and_wrong_run_plan() -> None:
    module = _module()
    record = _record()
    record["execution_policy"]["network"] = "allow"
    record["run_plan"]["total_runs"] = 1

    report = module.validate_corpus(record)

    assert report["status"] == "FAILED_CERTIFICATION_IMPLEMENTATION"
    assert "execution_policy_mismatch:network" in report["errors"]
    assert "total_run_count_mismatch" in report["errors"]


def test_corpus_rejects_duplicate_or_missing_tasks() -> None:
    module = _module()
    record = _record()
    record["tasks"][1]["id"] = record["tasks"][0]["id"]
    record["tasks"][2].pop("prompt")

    report = module.validate_corpus(record)

    assert report["status"] == "FAILED_CERTIFICATION_IMPLEMENTATION"
    assert "duplicate_task_id:bug-01" in report["errors"]
    assert "task_field_invalid:bug-03:prompt" in report["errors"]
