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


def test_gateway_auth_boundary_failure_gets_model_gateway_blocker(tmp_path: Path) -> None:
    module = _load("check_openhands_gate_matrix")
    (tmp_path / "gateway" / "omniroute").mkdir(parents=True)
    (tmp_path / "gateway" / "omniroute" / "auth.json").write_text(
        json.dumps(
            {
                "status": "BLOCKED",
                "failure_class": "MODEL_GATEWAY_TRANSPORT_FAILURE",
                "raw_response_retained": False,
                "credentials_retained": False,
            }
        ),
        encoding="utf-8",
    )
    report = module.evaluate(evidence_root=tmp_path, provider_status="PASS")
    assert report["status"] == "BLOCKED_MODEL_GATEWAY"
    assert report["evidence_blocker_status"] == "BLOCKED_MODEL_GATEWAY"


def test_gateway_provenance_failure_gets_a_distinct_blocker_class(tmp_path: Path) -> None:
    module = _load("check_openhands_gate_matrix")
    (tmp_path / "gateway").mkdir()
    (tmp_path / "gateway" / "version-verification.json").write_text(
        json.dumps(
            {
                "status": "FAILED_CERTIFICATION_IMPLEMENTATION",
                "failure_class": "LITELLM_RELEASE_TAG_RESOLUTION_FAILED",
                "source_or_payload_retained": False,
            }
        ),
        encoding="utf-8",
    )
    report = module.evaluate(evidence_root=tmp_path, provider_status="PASS")
    assert report["status"] == "BLOCKED_GATEWAY_PROVENANCE"
    assert report["evidence_blocker_status"] == "BLOCKED_GATEWAY_PROVENANCE"


def test_baseline_failure_remains_blocking_when_auto_route_evidence_passes(tmp_path: Path) -> None:
    module = _load("check_openhands_gate_matrix")
    (tmp_path / "gateway").mkdir()
    (tmp_path / "gateway" / "provider-baseline.json").write_text(
        json.dumps(
            {
                "status": "BLOCKED",
                "failure_class": "PROVIDER_SERVER_ERROR",
                "failure_http_status": 502,
                "raw_response_retained": False,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "gateway" / "auto-routing.json").write_text(
        json.dumps({"status": "PASS", "route": {"routing_mode": "omniroute_auto_coding"}}),
        encoding="utf-8",
    )
    report = module.evaluate(evidence_root=tmp_path, provider_status="PASS")
    assert report["status"] == "BLOCKED_PROVIDER"
    assert report["evidence_blocker_status"] == "BLOCKED_PROVIDER"
    assert report["evaluation"]["all_required_gates_passed"] is False


def test_omniroute_startup_failure_gets_runtime_blocker_class(tmp_path: Path) -> None:
    module = _load("check_openhands_gate_matrix")
    (tmp_path / "gateway").mkdir()
    (tmp_path / "gateway" / "provider-provisioning.json").write_text(
        json.dumps({"status": "BLOCKED", "failure_class": "OMNIROUTE_HEALTH_FAILURE"}),
        encoding="utf-8",
    )
    report = module.evaluate(evidence_root=tmp_path, provider_status="PASS")
    assert report["status"] == "BLOCKED_RUNTIME_STARTUP"
    assert report["evidence_blocker_status"] == "BLOCKED_RUNTIME_STARTUP"


def test_omniroute_readiness_subclasses_remain_runtime_blockers(tmp_path: Path) -> None:
    module = _load("check_openhands_gate_matrix")
    (tmp_path / "gateway").mkdir()
    for failure_class in (
        "OMNIROUTE_HEALTH_AUTH_CONTRACT_FAILURE",
        "OMNIROUTE_APPLICATION_HEALTH_FAILURE",
        "OMNIROUTE_HEALTH_TIMEOUT",
    ):
        (tmp_path / "gateway" / "omniroute").mkdir(exist_ok=True)
        (tmp_path / "gateway" / "omniroute" / "health.json").write_text(
            json.dumps({"health_status": "BLOCKED", "failure_class": failure_class}),
            encoding="utf-8",
        )
        report = module.evaluate(evidence_root=tmp_path, provider_status="PASS")
        assert report["status"] == "BLOCKED_RUNTIME_STARTUP"


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


def test_certification_authorization_failure_keeps_its_narrow_blocker(tmp_path: Path) -> None:
    module = _load("check_openhands_gate_matrix")
    (tmp_path / "live").mkdir()
    (tmp_path / "live" / "live-certification.json").write_text(
        json.dumps(
            {
                "status": "BLOCKED_CERTIFICATION_AUTHORIZATION",
                "worker_activation": "INACTIVE",
                "gates": {},
            }
        ),
        encoding="utf-8",
    )
    report = module.evaluate(evidence_root=tmp_path, provider_status="PASS")
    assert report["status"] == "BLOCKED_CERTIFICATION_AUTHORIZATION"
    assert report["evidence_blocker_status"] == "BLOCKED_CERTIFICATION_AUTHORIZATION"


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


def test_network_creation_failure_is_infrastructure_failure(tmp_path: Path) -> None:
    module = _load("check_openhands_gate_matrix")
    (tmp_path / "gateway").mkdir()
    (tmp_path / "gateway" / "network-startup.json").write_text(
        json.dumps(
            {
                "status": "BLOCKED",
                "failure_class": "FAILED_INFRASTRUCTURE",
                "reason": "docker_network_create_failed",
            }
        ),
        encoding="utf-8",
    )
    report = module.evaluate(evidence_root=tmp_path, provider_status="PASS")
    assert report["status"] == "FAILED_INFRASTRUCTURE"
    assert report["evidence_blocker_status"] == "FAILED_INFRASTRUCTURE"


def test_agent_and_tool_startup_failures_keep_narrow_blockers(tmp_path: Path) -> None:
    module = _load("check_openhands_gate_matrix")
    (tmp_path / "startup").mkdir()
    startup_path = tmp_path / "startup" / "startup.json"
    startup_path.write_text(
        json.dumps({"health_status": "BLOCKED", "failure_class": "OPENHANDS_AGENT_SERVER_STARTUP_FAILURE"}),
        encoding="utf-8",
    )
    report = module.evaluate(evidence_root=tmp_path, provider_status="PASS")
    assert report["status"] == "BLOCKED_GVISOR"
    startup_path.unlink()
    (tmp_path / "bridge").mkdir()
    (tmp_path / "bridge" / "startup.json").write_text(
        json.dumps({"health_status": "BLOCKED", "failure_class": "TOOL_SERVICE_STARTUP_FAILURE"}),
        encoding="utf-8",
    )
    report = module.evaluate(evidence_root=tmp_path, provider_status="PASS")
    assert report["status"] == "BLOCKED_TOOL_BRIDGE"


def test_runsc_gateway_probe_failure_gets_model_gateway_blocker(tmp_path: Path) -> None:
    module = _load("check_openhands_gate_matrix")
    (tmp_path / "gateway").mkdir()
    (tmp_path / "gateway" / "runsc-to-litellm.json").write_text(
        json.dumps({"status": "BLOCKED"}),
        encoding="utf-8",
    )
    report = module.evaluate(evidence_root=tmp_path, provider_status="PASS")
    assert report["status"] == "BLOCKED_MODEL_GATEWAY"


def test_scanner_coverage_failure_gets_a_narrow_blocker_class(tmp_path: Path) -> None:
    module = _load("check_openhands_gate_matrix")
    (tmp_path / "certification").mkdir()
    (tmp_path / "certification" / "candidate-certification.json").write_text(
        json.dumps(
            {
                "status": "blocked",
                "scanner_errors": 4,
                "failure_classes": ["SCANNER_COVERAGE_INCOMPLETE"],
                "security_findings_interpretable": False,
            }
        ),
        encoding="utf-8",
    )
    report = module.evaluate(evidence_root=tmp_path, provider_status="PASS")
    assert report["status"] == "BLOCKED_SCANNER_COVERAGE"
    assert report["evidence_blocker_status"] == "BLOCKED_SCANNER_COVERAGE"


def test_review_only_findings_get_security_triage_blocker(tmp_path: Path) -> None:
    module = _load("check_openhands_gate_matrix")
    (tmp_path / "certification").mkdir()
    (tmp_path / "certification" / "candidate-certification.json").write_text(
        json.dumps(
            {
                "status": "findings_review_required",
                "scanner_errors": 0,
                "raw_findings_count": 76,
                "security_findings_interpretable": True,
            }
        ),
        encoding="utf-8",
    )
    report = module.evaluate(evidence_root=tmp_path, provider_status="PASS")
    assert report["status"] == "BLOCKED_SECURITY_TRIAGE"
    assert report["evidence_blocker_status"] == "BLOCKED_SECURITY_TRIAGE"
