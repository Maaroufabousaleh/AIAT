"""Tests for the dependency-free browser benchmark contract."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _module():
    script = Path(__file__).resolve().parents[1] / "check_browser_runtime_benchmark.py"
    spec = importlib.util.spec_from_file_location("check_browser_runtime_benchmark", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _record() -> dict:
    return json.loads(_module().corpus_path().read_text(encoding="utf-8"))


def test_fixed_corpus_is_ready_not_run_and_keeps_playwright_default() -> None:
    module = _module()
    report = module.validate_corpus(_record())

    assert report["status"] == "PASS"
    assert report["workflow_count"] == 30
    assert report["execution_status"] == "NOT_RUN"
    assert report["benchmark_runs"] == 0
    assert report["decision"] is None
    assert report["stagehand_dependency_added"] is False
    assert report["playwright_default_changed"] is False


def test_fixed_corpus_has_the_approved_workflow_distribution() -> None:
    module = _module()
    report = module.validate_corpus(_record())

    assert report["category_counts"] == {
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


def test_corpus_rejects_fallback_activation_or_external_effects() -> None:
    module = _module()
    record = _record()
    record["execution_policy"]["consequential_external_effects"] = "allowed"
    record["candidates"]["agent_assisted_fallback"]["status"] = "ACTIVE"

    report = module.validate_corpus(record)

    assert report["status"] == "FAILED_CERTIFICATION_IMPLEMENTATION"
    assert "execution_policy_mismatch:consequential_external_effects" in report["errors"]
    assert "agentic_fallback_activated" in report["errors"]


def test_corpus_rejects_duplicate_workflow_or_wrong_fallback_plan() -> None:
    module = _module()
    record = _record()
    record["workflows"][1]["id"] = record["workflows"][0]["id"]
    record["run_plan"]["fallback_only_on_deterministic_failure"] = False

    report = module.validate_corpus(record)

    assert report["status"] == "FAILED_CERTIFICATION_IMPLEMENTATION"
    assert "duplicate_workflow_id:form-01" in report["errors"]
    assert "fallback_policy_missing" in report["errors"]
