"""Fail-closed tests for the canonical OpenHands gate matrix."""

from __future__ import annotations

import importlib.util
import json
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
    assert all(
        {
            "pass_criteria",
            "fail_criteria",
            "evidence_schema",
            "cleanup_behavior",
            "timeout_seconds",
            "provider_required",
        }
        <= set(row)
        for row in module.GATE_DEFINITIONS
    )
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


def test_evidence_derivation_only_promotes_explicit_live_statuses(tmp_path: Path) -> None:
    module = _load("check_openhands_gate_matrix")
    (tmp_path / "live").mkdir()
    (tmp_path / "live" / "live-certification.json").write_text(
        json.dumps(
            {
                "gates": {
                    "coding_task": "PASS",
                    "file_modifications": "PASS",
                    "test_execution": "PASS",
                    "artifact_capture": "PASS",
                    "pause": "NOT_RUN",
                    "forced_failure": "PASS",
                },
                "task": {"expected_changed_paths": ["slugger/core.py"]},
            }
        ),
        encoding="utf-8",
    )
    gates = module.derive_gate_rows(tmp_path)
    assert gates["real_coding_task"]["status"] == "PASS"
    assert gates["file_modifications"]["status"] == "PASS"
    assert gates["test_execution"]["status"] == "PASS"
    assert gates["artifact_capture"]["status"] == "PASS"
    assert gates["graceful_pause"]["status"] == "NOT_RUN"
    assert gates["resume"]["status"] == "NOT_RUN"
    assert gates["forced_failure"]["status"] == "PASS"


def test_coding_success_does_not_infer_tests_or_artifacts(tmp_path: Path) -> None:
    module = _load("check_openhands_gate_matrix")
    (tmp_path / "live").mkdir()
    (tmp_path / "live" / "live-certification.json").write_text(
        json.dumps({"gates": {"coding_task": "PASS"}, "task": {"expected_changed_paths": ["slugger/core.py"]}}),
        encoding="utf-8",
    )
    gates = module.derive_gate_rows(tmp_path)
    assert gates["real_coding_task"]["status"] == "PASS"
    assert gates["test_execution"]["status"] == "NOT_RUN"
    assert gates["artifact_capture"]["status"] == "NOT_RUN"


def test_scalar_gateway_failure_gets_a_narrow_blocker_class(tmp_path: Path) -> None:
    module = _load("check_openhands_gate_matrix")
    (tmp_path / "gateway").mkdir()
    (tmp_path / "gateway" / "route-probe.json").write_text(
        json.dumps({"status": "BLOCKED", "failure_class": "MODEL_GATEWAY_AUTH_FAILURE"}),
        encoding="utf-8",
    )
    report = module.evaluate(evidence_root=tmp_path, provider_status="PASS")
    assert report["status"] == "BLOCKED_MODEL_GATEWAY"
    assert report["evidence_blocker_status"] == "BLOCKED_MODEL_GATEWAY"
    assert report["evaluation"]["all_required_gates_passed"] is False


def test_candidate_preflight_failure_is_not_reported_as_provider_failure(tmp_path: Path) -> None:
    module = _load("check_openhands_gate_matrix")
    report = module.evaluate(
        evidence_root=tmp_path,
        provider_status="PASS",
        configuration_status="BLOCKED_STATIC_CONFIGURATION",
    )
    assert report["status"] == "BLOCKED_STATIC_CONFIGURATION"
    assert report["provider_configuration_status"] == "PASS"
    assert report["configuration_status"] == "BLOCKED_STATIC_CONFIGURATION"


def test_incomplete_gateway_topology_is_infrastructure_failure(tmp_path: Path) -> None:
    module = _load("check_openhands_gate_matrix")
    (tmp_path / "gateway").mkdir()
    (tmp_path / "gateway" / "network-topology.json").write_text(
        json.dumps(
            {
                "topology_status": "BLOCKED",
                "expected_container_count": 4,
                "observed_container_count": 3,
            }
        ),
        encoding="utf-8",
    )
    report = module.evaluate(evidence_root=tmp_path, provider_status="PASS")
    assert report["status"] == "FAILED_INFRASTRUCTURE"
    assert report["evidence_blocker_status"] == "FAILED_INFRASTRUCTURE"
