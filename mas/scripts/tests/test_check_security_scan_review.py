"""Tests for the security-finding review register contract."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

SCRIPT = Path(__file__).resolve().parents[1] / "check_security_scan_review.py"
SCAN = SCRIPT.parents[1] / "docs" / "provenance" / "security_scan_evidence.yaml"
REVIEW = SCRIPT.parents[1] / "docs" / "provenance" / "security_scan_review.yaml"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_security_scan_review", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_current_review_register_covers_open_scan_without_waiving_gate() -> None:
    module = _load_module()
    report = module.inspect(SCAN, REVIEW)

    assert report["status"] == "pass", report["errors"]
    assert report["scan_count"] == 9
    assert report["active_scan_count"] == 5
    assert report["historical_scan_count"] == 4
    assert report["finding_count"] == 507
    assert "historical_finding_count" not in report
    assert report["engine_warning_count"] == 54
    assert report["review_required_count"] == 4
    assert report["technical_gate_status"] == "blocked"
    assert report["licence_metadata_is_gate"] is False
    assert report["reviews"][0]["status"] == "open"
    assert report["latest_reproduction"]["finding_count"] == 507
    assert report["latest_reproduction"]["scanner_error_count"] == 54
    assert report["latest_reproduction"]["technical_gate_status"] == "blocked"


def test_zero_finding_scanner_has_explicit_passed_review() -> None:
    module = _load_module()
    scan_document = yaml.safe_load(SCAN.read_text(encoding="utf-8"))
    review_document = yaml.safe_load(REVIEW.read_text(encoding="utf-8"))
    scan = next(row for row in scan_document["scans"] if row["scanner"] == "skillspector" and row.get("remediation_input") is not False)
    review = next(row for row in review_document["reviews"] if row["scan_ref"].endswith("#" + scan["id"]))

    assert scan["status"] == "passed"
    assert scan["finding_count"] == 0
    assert scan["rule_counts"] == {}
    assert review["status"] == "resolved"
    assert review["gate_status"] == "passed"
    assert review["finding_disposition"] == "no_findings"
    assert review["rule_groups"] == []
    assert module.inspect(SCAN, REVIEW)["status"] == "pass"


def test_unreproducible_historical_scan_rejects_scalar_counts(tmp_path: Path) -> None:
    module = _load_module()
    scans = yaml.safe_load(SCAN.read_text(encoding="utf-8"))
    historical = next(row for row in scans["scans"] if row.get("historical_only") is True)
    historical["finding_count"] = 316
    scan_path = tmp_path / "security-scan-evidence.yaml"
    scan_path.write_text(yaml.safe_dump(scans, sort_keys=False), encoding="utf-8")

    report = module.inspect(scan_path, REVIEW)

    assert report["status"] == "fail"
    assert any(
        "unreproducible historical evidence must not supply finding_count" in error
        for error in report["errors"]
    )


def test_review_register_requires_latest_reproduction(tmp_path: Path) -> None:
    module = _load_module()
    review = yaml.safe_load(REVIEW.read_text(encoding="utf-8"))
    review.pop("latest_reproduction", None)
    review_path = tmp_path / "review.yaml"
    review_path.write_text(yaml.safe_dump(review, sort_keys=False), encoding="utf-8")

    report = module.inspect(SCAN, review_path)

    assert report["status"] == "fail"
    assert any("latest_reproduction" in error for error in report["errors"])


def test_review_register_rejects_unassigned_rule(tmp_path: Path) -> None:
    module = _load_module()
    review = yaml.safe_load(REVIEW.read_text(encoding="utf-8"))
    review["reviews"][0]["rule_groups"][0]["rules"] = []
    review_path = tmp_path / "review.yaml"
    review_path.write_text(yaml.safe_dump(review, sort_keys=False), encoding="utf-8")

    report = module.inspect(SCAN, review_path)

    assert report["status"] == "fail"
    assert any("rules must be a non-empty list" in error for error in report["errors"])


def test_review_register_rejects_finding_count_drift(tmp_path: Path) -> None:
    module = _load_module()
    review = yaml.safe_load(REVIEW.read_text(encoding="utf-8"))
    review["reviews"][0]["rule_groups"][0]["finding_count"] = 260
    review_path = tmp_path / "review.yaml"
    review_path.write_text(yaml.safe_dump(review, sort_keys=False), encoding="utf-8")

    report = module.inspect(SCAN, review_path)

    assert report["status"] == "fail"
    assert any("disagrees with mapped rules" in error for error in report["errors"])
