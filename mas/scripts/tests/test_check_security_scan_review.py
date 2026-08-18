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
    assert report["scan_count"] == 1
    assert report["finding_count"] == 316
    assert report["engine_warning_count"] == 54
    assert report["review_required_count"] == 1
    assert report["technical_gate_status"] == "blocked"
    assert report["licence_metadata_is_gate"] is False
    assert report["reviews"][0]["status"] == "open"


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
