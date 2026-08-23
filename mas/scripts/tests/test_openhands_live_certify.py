"""Fail-closed status semantics for the OpenHands live wrapper."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _module():
    script = Path(__file__).resolve().parents[1] / "openhands_live_certify.py"
    spec = importlib.util.spec_from_file_location("openhands_live_certify", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_partial_live_wave_cannot_report_pass() -> None:
    module = _module()
    statuses = module._status_map("NOT_RUN")
    statuses["coding_task"] = "PASS"
    statuses["zero_residue"] = "PASS"
    assert module._final_status(statuses, []) == "BLOCKED_INCOMPLETE_MANDATORY_GATES"


def test_live_wrapper_uses_narrow_blocker_classes() -> None:
    module = _module()
    statuses = module._status_map("NOT_RUN")
    assert module._final_status(statuses, ["operator_configuration_missing:GROQ_API_KEY"]) == "BLOCKED_OPERATOR_CONFIGURATION"
    assert module._final_status(statuses, ["readiness:health"]) == "BLOCKED_RUNTIME_STARTUP"
    statuses["coding_task"] = "FAILED_MODEL_EXECUTION"
    assert module._final_status(statuses, ["live_coding_task_failed"]) == "FAILED_MODEL_EXECUTION"
    statuses["coding_task"] = "PASS"
    statuses["zero_residue"] = "FAILED_CLEANUP"
    assert module._final_status(statuses, ["run_scoped_mcp_grant_residue"]) == "BLOCKED_CLEANUP"
