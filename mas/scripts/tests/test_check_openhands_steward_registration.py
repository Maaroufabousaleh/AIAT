"""Tests for the read-only OpenHands steward-registration preflight."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _module():
    script = Path(__file__).resolve().parents[1] / "check_openhands_steward_registration.py"
    spec = importlib.util.spec_from_file_location("check_openhands_steward_registration", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _module()
DEFAULT_IMAGE_DIGEST = MODULE.DEFAULT_IMAGE_DIGEST
DEFAULT_SOURCE_COMMIT = MODULE.DEFAULT_SOURCE_COMMIT
WORKER_ID = MODULE.WORKER_ID
validate = MODULE.validate

GATES_SPEC = importlib.util.spec_from_file_location(
    "openhands_certification_gates",
    Path(__file__).resolve().parents[1] / "openhands_certification_gates.py",
)
assert GATES_SPEC and GATES_SPEC.loader
GATES = importlib.util.module_from_spec(GATES_SPEC)
GATES_SPEC.loader.exec_module(GATES)
GATE_IDS = GATES.GATE_IDS
initial_gate_map = GATES.initial_gate_map


def _evidence(candidate_sha: str = "a" * 40) -> dict:
    gates = initial_gate_map()
    for row in gates.values():
        row["status"] = "PASS"
    return {
        "schema_version": "aiat.openhands-certification-gate-evaluation.v1",
        "status": "PASSED",
        "candidate_sha": candidate_sha,
        "openhands_source_commit": DEFAULT_SOURCE_COMMIT,
        "openhands_image_digest": DEFAULT_IMAGE_DIGEST,
        "payloads_retained": False,
        "gates": gates,
        "evaluation": {
            "all_required_gates_passed": True,
            "mandatory_gate_count": len(GATE_IDS),
            "passed_gate_count": len(GATE_IDS),
            "not_run_gate_count": 0,
            "blocked_gate_count": 0,
            "failed_gate_count": 0,
        },
    }


def test_completed_exact_evidence_is_ready_without_mutating_steward() -> None:
    report = validate(
        _evidence(),
        candidate_sha="a" * 40,
        candidate_record={"candidate_id": "candidate-1", "worker_id": WORKER_ID, "intake_status": "CERTIFYING"},
    )
    assert report["status"] == "READY_FOR_STEWARD_REGISTRATION"
    assert report["registration_mutated"] is False
    assert report["activation"]["worker_status"] == "INACTIVE"
    assert report["activation"]["activation_approval_required"] is True


def test_wrong_candidate_pin_is_blocked() -> None:
    report = validate(_evidence("a" * 40), candidate_sha="b" * 40)
    assert report["status"] == "BLOCKED_STEWARD_REGISTRATION"
    assert "candidate_sha_mismatch" in report["blockers"]


def test_wrong_image_pin_is_blocked() -> None:
    report = validate(
        _evidence(),
        candidate_sha="a" * 40,
        image_digest="sha256:" + "0" * 64,
    )
    assert report["status"] == "BLOCKED_STEWARD_REGISTRATION"
    assert "image_digest_mismatch" in report["blockers"]


def test_incomplete_gate_evidence_is_blocked() -> None:
    evidence = _evidence()
    evidence["gates"]["timeout"]["status"] = "NOT_RUN"
    evidence["status"] = "BLOCKED_INCOMPLETE_MANDATORY_GATES"
    evidence["evaluation"].update(
        {
            "all_required_gates_passed": False,
            "passed_gate_count": len(GATE_IDS) - 1,
            "not_run_gate_count": 1,
        }
    )
    report = validate(evidence, candidate_sha="a" * 40)
    assert report["status"] == "BLOCKED_STEWARD_REGISTRATION"
    assert "mandatory_gates_not_passed" in report["blockers"]
    assert "mandatory_gate_not_run" in report["blockers"]


def test_non_certifying_candidate_cannot_be_registered() -> None:
    report = validate(
        _evidence(),
        candidate_sha="a" * 40,
        candidate_record={"candidate_id": "candidate-1", "worker_id": WORKER_ID, "intake_status": "APPROVED"},
    )
    assert report["status"] == "BLOCKED_STEWARD_REGISTRATION"
    assert "candidate_not_certifying" in report["blockers"]
