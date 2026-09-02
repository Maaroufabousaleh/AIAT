"""Tests for the not-yet-run neutral worker comparison record."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _module():
    script = Path(__file__).resolve().parents[1] / "check_openhands_worker_comparison.py"
    spec = importlib.util.spec_from_file_location("check_openhands_worker_comparison", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_canonical_record_is_not_run_and_has_no_winner() -> None:
    module = _module()
    record = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "docs/provenance/openhands-candidate/2026-08-22-v1.43.0/comparison-matrix.json"
        ).read_text(encoding="utf-8")
    )
    report = module.validate(record)
    assert report["status"] == "PASS"
    assert report["comparison_status"] == "NOT_RUN"
    assert report["decision"] is None


def test_comparison_cannot_accept_a_candidate_without_metrics() -> None:
    module = _module()
    record = {
        "schema_version": module.SCHEMA,
        "status": "PASS",
        "decision": "OPENHANDS",
        "candidates": {},
        "metrics": {},
        "payloads_retained": False,
    }
    report = module.validate(record)
    assert report["status"] == "FAILED_CERTIFICATION_IMPLEMENTATION"
    assert "comparison_must_remain_not_run" in report["errors"]
    assert "comparison_decision_must_be_empty" in report["errors"]
