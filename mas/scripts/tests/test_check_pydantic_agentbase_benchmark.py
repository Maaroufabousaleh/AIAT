"""Tests for the dependency-free Pydantic AI/AgentBase benchmark contract."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _module():
    script = Path(__file__).resolve().parents[1] / "check_pydantic_agentbase_benchmark.py"
    spec = importlib.util.spec_from_file_location("check_pydantic_agentbase_benchmark", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _record() -> dict:
    return json.loads(_module().corpus_path().read_text(encoding="utf-8"))


def test_fixed_corpus_is_ready_not_run_without_activation() -> None:
    module = _module()
    report = module.validate_corpus(_record())

    assert report["status"] == "PASS"
    assert report["case_count"] == 48
    assert report["execution_status"] == "NOT_RUN"
    assert report["benchmark_runs"] == 0
    assert report["decision"] is None
    assert report["pydantic_ai_dependency_added"] is False
    assert report["production_default_changed"] is False


def test_fixed_corpus_has_the_approved_case_distribution() -> None:
    module = _module()
    report = module.validate_corpus(_record())

    assert report["category_counts"] == {
        "structured_no_tools": 12,
        "tool_selection_structured_output": 12,
        "multiturn_project_context": 8,
        "policy_denial": 6,
        "budget_model_constraints": 4,
        "checkpoint_restart_resume": 6,
    }


def test_corpus_rejects_activation_or_unsafe_policy() -> None:
    module = _module()
    record = _record()
    record["execution_policy"]["dependency_added"] = True
    record["candidates"]["pydantic_ai"]["status"] = "ACTIVE"

    report = module.validate_corpus(record)

    assert report["status"] == "FAILED_CERTIFICATION_IMPLEMENTATION"
    assert "execution_policy_mismatch:dependency_added" in report["errors"]
    assert "pydantic_ai_activation_claimed" in report["errors"]


def test_corpus_rejects_duplicate_case_or_wrong_run_count() -> None:
    module = _module()
    record = _record()
    record["cases"][1]["id"] = record["cases"][0]["id"]
    record["run_plan"]["total_runs"] = 1

    report = module.validate_corpus(record)

    assert report["status"] == "FAILED_CERTIFICATION_IMPLEMENTATION"
    assert "duplicate_case_id:structured-01" in report["errors"]
    assert "total_run_count_mismatch" in report["errors"]
