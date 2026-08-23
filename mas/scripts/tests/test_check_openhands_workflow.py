"""Static validation tests for the manual OpenHands workflow."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _workflow() -> str:
    return (Path(__file__).resolve().parents[2] / ".." / ".github" / "workflows" / "openhands-candidate-certification.yml").read_text(encoding="utf-8")


def _module():
    script = Path(__file__).resolve().parents[1] / "check_openhands_workflow.py"
    spec = importlib.util.spec_from_file_location("check_openhands_workflow", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_current_workflow_is_manual_pinned_and_fail_closed() -> None:
    report = _module().validate(_workflow())
    assert report["status"] == "PASS", report["errors"]
    assert report["manual_only"] is True
    assert report["candidate_sha_bound"] is True
    assert report["exact_image_pins"] is True
    assert "GITHUB_STEP_SUMMARY" in _workflow()


def test_automatic_trigger_and_static_profile_are_rejected() -> None:
    text = _workflow().replace("workflow_dispatch:", "push:\n    branches: [main]\n  workflow_dispatch:", 1)
    text += "\n          OPENHANDS_AGENT_PROFILE_ID: ${{ vars.OPENHANDS_AGENT_PROFILE_ID }}\n"
    report = _module().validate(text)
    assert report["status"] == "FAILED_CERTIFICATION_IMPLEMENTATION"
    assert any(item.startswith("automatic_trigger_present:push") for item in report["errors"])
    assert "static_profile_uuid_input_present" in report["errors"]


def test_missing_provider_gate_or_cleanup_readback_is_rejected() -> None:
    text = _workflow()
    report = _module().validate(text.replace("steps.provider_preflight.outputs.ready == 'true'", "steps.other.outputs.ready == 'true'"))
    assert "provider_gate_missing_before_expensive_stages" in report["errors"]

    report = _module().validate(text.replace('"verified_absent"', '"removed"'))
    assert "cleanup_absence_readback_missing" in report["errors"]

    report = _module().validate(text.replace("preflight-wrapper.json", "preflight.json"))
    assert "provider_preflight_execution_failure_class_missing" in report["errors"]
