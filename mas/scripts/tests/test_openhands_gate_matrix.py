"""Fail-closed tests for the canonical OpenHands gate matrix."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load(name: str):
    path = Path(__file__).resolve().parents[1] / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_all_mandatory_gates_are_present_and_not_run_is_not_pass() -> None:
    module = _load("openhands_certification_gates")
    gates = module.initial_gate_map()
    result = module.evaluate_gate_map(gates)
    assert len(module.GATE_IDS) == 20
    assert result["status"] == "BLOCKED_INCOMPLETE_MANDATORY_GATES"
    assert result["not_run_gate_count"] == 20
    assert result["all_required_gates_passed"] is False


def test_missing_provider_secret_is_explicit_and_does_not_skip_gates() -> None:
    module = _load("openhands_certification_gates")
    result = module.evaluate_gate_map(module.initial_gate_map(), blocker_status="BLOCKED_MISSING_OPERATOR_SECRET")
    assert result["status"] == "BLOCKED_MISSING_OPERATOR_SECRET"
    assert result["not_run_gate_count"] == 20
    assert result["all_required_gates_passed"] is False


def test_only_a_complete_pass_map_can_pass() -> None:
    module = _load("openhands_certification_gates")
    gates = module.initial_gate_map()
    for row in gates.values():
        row["status"] = "PASS"
    result = module.evaluate_gate_map(gates)
    assert result["status"] == "PASSED"
    assert result["passed_gate_count"] == 20


def test_unknown_or_invalid_gate_fails_implementation() -> None:
    module = _load("openhands_certification_gates")
    gates = module.initial_gate_map()
    gates.pop("resume")
    gates["unexpected"] = {"status": "PASS"}
    result = module.evaluate_gate_map(gates)
    assert result["status"] == "FAILED_CERTIFICATION_IMPLEMENTATION"
    assert result["missing_gates"] == ["resume"]
    assert result["unknown_gates"] == ["unexpected"]
