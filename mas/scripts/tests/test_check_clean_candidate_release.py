"""Tests for clean-candidate static evidence validation."""

from __future__ import annotations

import importlib.util
import json
import pathlib


def _module():
    script = pathlib.Path(__file__).resolve().parents[1] / "check_clean_candidate_release.py"
    spec = importlib.util.spec_from_file_location("check_clean_candidate_release", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_current_clean_candidate_certificate_passes() -> None:
    report = _module().validate()
    assert report["status"] == "PASS"
    assert report["candidate_revision_matches"] is True
    assert report["clean_clone"] is True
    assert report["static_ledger"]["checks_passed"] == 63
    assert report["static_ledger"]["release_decision"] == "NO-RELEASE"
    assert report["current_checkout_clean"] is False


def test_stale_candidate_certificate_is_blocked(tmp_path: pathlib.Path) -> None:
    module = _module()
    evidence = json.loads(module.DEFAULT_EVIDENCE.read_text(encoding="utf-8"))
    evidence["candidate_sha"] = "0" * 40
    path = tmp_path / "stale.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")

    report = module.validate(evidence_path=path)

    assert report["status"] == "BLOCKED"
    assert report["candidate_revision_matches"] is False
    assert any("does not match" in error for error in report["errors"])


def test_payload_retention_is_blocked(tmp_path: pathlib.Path) -> None:
    module = _module()
    evidence = json.loads(module.DEFAULT_EVIDENCE.read_text(encoding="utf-8"))
    evidence["payloads_retained"] = True
    path = tmp_path / "unsafe.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")

    report = module.validate(evidence_path=path)

    assert report["status"] == "BLOCKED"
    assert any("retention boundary" in error for error in report["errors"])
