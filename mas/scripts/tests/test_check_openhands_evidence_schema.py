"""Tests for fail-closed OpenHands evidence-tree validation."""

from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_openhands_evidence_schema.py"
SPEC = importlib.util.spec_from_file_location("check_openhands_evidence_schema", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _gate_report(status: str = "BLOCKED_MISSING_OPERATOR_SECRET") -> dict:
    gates = {
        gate_id: {"gate_id": gate_id, "status": "NOT_RUN"}
        for gate_id in MODULE.GATE_IDS
    }
    return {
        "schema_version": MODULE.GATE_SCHEMA,
        "status": status,
        "candidate_sha": "a" * 40,
        "openhands_source_commit": "b" * 40,
        "openhands_image_digest": "sha256:" + "c" * 64,
        "payloads_retained": False,
        "gates": gates,
        "evaluation": {
            "mandatory_gate_count": len(MODULE.GATE_IDS),
            "all_required_gates_passed": status == "PASSED",
        },
    }


def _write_tree(tmp_path: Path, report: dict) -> Path:
    root = tmp_path / "evidence"
    path = root / "certification" / "gate-evaluation.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(report), encoding="utf-8")
    (root / "provider").mkdir()
    (root / "provider" / "preflight.json").write_text(
        json.dumps({"status": "BLOCKED_MISSING_OPERATOR_SECRET", "secret_present": False}),
        encoding="utf-8",
    )
    return root


def test_expected_provider_block_is_valid_evidence() -> None:
    report = MODULE.validate(_write_tree(Path(tempfile.mkdtemp()), _gate_report()))
    assert report["status"] == "PASS", report["errors"]
    assert report["final_certification_status"] == "BLOCKED_MISSING_OPERATOR_SECRET"


def test_gateway_provenance_block_is_an_explicit_allowed_status() -> None:
    report = MODULE.validate(_write_tree(Path(tempfile.mkdtemp()), _gate_report("BLOCKED_GATEWAY_PROVENANCE")))
    assert report["status"] == "PASS", report["errors"]
    assert report["final_certification_status"] == "BLOCKED_GATEWAY_PROVENANCE"


def test_certification_authorization_block_is_an_explicit_allowed_status() -> None:
    report = MODULE.validate(
        _write_tree(Path(tempfile.mkdtemp()), _gate_report("BLOCKED_CERTIFICATION_AUTHORIZATION"))
    )
    assert report["status"] == "PASS", report["errors"]
    assert report["final_certification_status"] == "BLOCKED_CERTIFICATION_AUTHORIZATION"


def test_precise_failed_gate_status_is_valid_fail_closed_evidence() -> None:
    report = MODULE.validate(_write_tree(Path(tempfile.mkdtemp()), _gate_report("FAILED_FILE_MODIFICATIONS")))
    assert report["status"] == "PASS", report["errors"]
    assert report["final_certification_status"] == "FAILED_FILE_MODIFICATIONS"


def test_true_sensitive_retention_flag_fails_closed(tmp_path: Path) -> None:
    root = _write_tree(tmp_path, _gate_report())
    (root / "provider" / "unsafe.json").write_text(json.dumps({"raw_response_retained": True}), encoding="utf-8")
    report = MODULE.validate(root)
    assert report["status"] == "FAILED_CERTIFICATION_IMPLEMENTATION"
    assert any(item.startswith("sensitive_retention_flag:") for item in report["errors"])


def test_openhands_secret_and_payload_retention_aliases_fail_closed(tmp_path: Path) -> None:
    root = _write_tree(tmp_path, _gate_report())
    (root / "provider" / "unsafe-aliases.json").write_text(
        json.dumps(
            {
                "credentials_retained": True,
                "gateway_key_retained": True,
                "raw_logs_retained": True,
                "response_payloads_retained": True,
            }
        ),
        encoding="utf-8",
    )
    report = MODULE.validate(root)
    assert report["status"] == "FAILED_CERTIFICATION_IMPLEMENTATION"
    assert {
        "sensitive_retention_flag:provider/unsafe-aliases.json:credentials_retained",
        "sensitive_retention_flag:provider/unsafe-aliases.json:gateway_key_retained",
        "sensitive_retention_flag:provider/unsafe-aliases.json:raw_logs_retained",
        "sensitive_retention_flag:provider/unsafe-aliases.json:response_payloads_retained",
    }.issubset(set(report["errors"]))


def test_passed_status_requires_all_gates_passed(tmp_path: Path) -> None:
    report = MODULE.validate(_write_tree(tmp_path, _gate_report("PASSED")))
    assert report["status"] == "FAILED_CERTIFICATION_IMPLEMENTATION"
    assert "passed_status_contains_non_pass_gate" in report["errors"]


def test_invalid_json_is_reported_without_retaining_contents(tmp_path: Path) -> None:
    root = _write_tree(tmp_path, _gate_report())
    (root / "broken.json").write_text("not-json", encoding="utf-8")
    report = MODULE.validate(root)
    assert report["status"] == "FAILED_CERTIFICATION_IMPLEMENTATION"
    assert any(item.startswith("json_invalid:broken.json") for item in report["errors"])
    assert "not-json" not in json.dumps(report, sort_keys=True)


def test_trufflehog_json_lines_are_valid_scanner_evidence(tmp_path: Path) -> None:
    root = _write_tree(tmp_path, _gate_report())
    (root / "certification" / "trufflehog.json").write_text(
        '{"Verified":false}\n{"Verified":false}\n',
        encoding="utf-8",
    )
    report = MODULE.validate(root)
    assert report["status"] == "PASS", report["errors"]


def test_malformed_trufflehog_json_line_fails_closed_without_echoing_payload(tmp_path: Path) -> None:
    root = _write_tree(tmp_path, _gate_report())
    sentinel = "not-a-secret-payload"
    (root / "certification" / "trufflehog.json").write_text(
        '{"Verified":false}\n' + sentinel + "\n",
        encoding="utf-8",
    )
    report = MODULE.validate(root)
    assert report["status"] == "FAILED_CERTIFICATION_IMPLEMENTATION"
    assert any(item.startswith("jsonl_invalid:certification/trufflehog.json") for item in report["errors"])
    assert sentinel not in json.dumps(report, sort_keys=True)


def test_historical_workflow_failure_record_is_scalar_and_fail_closed() -> None:
    path = (
        Path(__file__).resolve().parents[3]
        / "mas/docs/provenance/openhands-candidate/2026-08-22-v1.43.0/github-run-32645055499-failure.json"
    )
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["workflow"]["classification"] == "FAILED_CERTIFICATION_IMPLEMENTATION"
    assert record["workflow"]["failure_reason"] == "MALFORMED_WORKFLOW_HEREDOC"
    assert record["workflow"]["live_gate_wave"] == "NOT_RUN"
    assert record["image_pulls"] == {
        "openhands": {
            "status": "PASS",
            "image": record["image_pulls"]["openhands"]["image"],
        },
        "litellm": {
            "status": "PASS",
            "image": record["image_pulls"]["litellm"]["image"],
        },
        "omniroute": {
            "status": "PASS",
            "image": record["image_pulls"]["omniroute"]["image"],
        },
    }
    assert record["artifact"]["raw_logs_retained"] is False
    assert record["artifact"]["credentials_retained"] is False
    assert record["artifact"]["payloads_retained"] is False


def test_second_historical_gateway_provenance_failure_is_scalar_and_precise() -> None:
    path = (
        Path(__file__).resolve().parents[3]
        / "mas/docs/provenance/openhands-candidate/2026-08-22-v1.43.0/github-run-32648660093-failure.json"
    )
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["workflow"]["classification"] == "FAILED_CERTIFICATION_IMPLEMENTATION"
    assert record["workflow"]["failure_reason"] == "LITELLM_RELEASE_TAG_RESOLUTION_FAILED"
    assert record["workflow"]["live_gate_wave"] == "NOT_RUN"
    assert record["provider"]["configuration_status"] == "PASS"
    assert record["image_platform_verification"]["status"] == "PASS"
    assert record["gateway_pin_verifier"]["status"] == "PASS"
    assert record["artifact"]["payloads_retained"] is False
    assert record["evidence_boundary"]["provider_failure"] is False
    assert record["evidence_boundary"]["zero_residue"] is True
