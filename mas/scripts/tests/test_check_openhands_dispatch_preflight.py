"""Non-secret tests for the morning OpenHands dispatch preflight."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _module():
    script = Path(__file__).resolve().parents[1] / "check_openhands_dispatch_preflight.py"
    spec = importlib.util.spec_from_file_location("check_openhands_dispatch_preflight", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preflight_requires_secret_variables_and_explicit_sha() -> None:
    module = _module()
    root = Path(__file__).resolve().parents[2]
    workflow = (root.parent / ".github" / "workflows" / "openhands-candidate-certification.yml").read_text(encoding="utf-8")
    manifest = (root / "docs/provenance/openhands-candidate/2026-08-22-v1.43.0/worker-manifest.yaml").read_text(encoding="utf-8")
    sha = "a" * 40
    ready = module.evaluate_static(
        workflow_text=workflow,
        manifest_text=manifest,
        actual_sha=sha,
        requested_sha=sha,
        secret_names={"GROQ_API_KEY"},
        variable_values={"OPENHANDS_MODEL_ID": module.EXPECTED_MODEL, "OPENHANDS_MCP_SETTINGS_KEY": module.EXPECTED_MCP_KEY},
        local_tests_passed=True,
    )
    assert ready["ready_to_dispatch"] is True
    blocked = module.evaluate_static(
        workflow_text=workflow,
        manifest_text=manifest,
        actual_sha=sha,
        requested_sha=None,
        secret_names=set(),
        variable_values={},
        local_tests_passed=True,
    )
    assert blocked["ready_to_dispatch"] is False
    assert blocked["github_secret_presence"] == "NO"
    assert "GROQ_API_KEY" not in blocked.get("dispatch_command", "")
