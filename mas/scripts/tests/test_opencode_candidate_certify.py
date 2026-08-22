"""Regression tests for reproducible OpenCode candidate evidence."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "opencode_candidate_certify.py"


def _module():
    spec = importlib.util.spec_from_file_location("opencode_candidate_certify", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_redaction_removes_secret_shaped_fields_and_values():
    module = _module()
    value = module._redact({"token": "super-secret", "message": "Bearer abcdefghijklmnop", "safe": "value"})
    assert value == {"token": "[REDACTED]", "message": "[REDACTED]", "safe": "value"}


def test_semgrep_summary_separates_findings_from_engine_errors():
    module = _module()
    finding_count, severities, errors, shape = module._semgrep_summary(
        {
            "results": [
                {"extra": {"severity": "ERROR"}},
                {"extra": {"severity": "WARNING"}},
            ],
            "errors": [{"message": "engine warning"}],
        }
    )
    assert finding_count == 2
    assert severities == {"ERROR": 1, "WARNING": 1}
    assert errors == 1
    assert shape is None


def test_generic_summary_reads_skillspector_issues_and_severity(tmp_path):
    module = _module()
    path = tmp_path / "skillspector-fixture.json"
    path.write_text(
        '{"issues":[{"severity":"HIGH"},{"severity":"CRITICAL"}],"execution_successful":true}',
        encoding="utf-8",
    )
    finding_count, severities, errors, shape = module._generic_summary(path, "skillspector")
    assert finding_count == 2
    assert severities == {"CRITICAL": 1, "HIGH": 1}
    assert errors == 0
    assert shape is None


def test_candidate_report_requires_digest_image_sbom_scanners_and_boundary(tmp_path, monkeypatch):
    module = _module()
    source = tmp_path / "source"
    source.mkdir()

    monkeypatch.setattr(module, "_prepare_source", lambda *_args: ({"commit": "a" * 40}, source))
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    report = module.certify(
        repository="https://github.com/anomalyco/opencode.git",
        version="1.18.21",
        image_ref="ghcr.io/anomalyco/opencode:1.18.21",
        output_dir=tmp_path / "evidence",
    )

    assert report["status"] == "blocked"
    assert report["active_worker_status"] == "inactive_until_certification_passes"
    assert len(report["aiat_candidate_commit"]) == 40
    assert "candidate image reference is not digest pinned" in report["blockers"]
    assert report["evidence_policy"]["credentials_persisted"] is False
    persisted = json.loads((tmp_path / "evidence" / "candidate-certification.json").read_text())
    assert persisted["source"]["commit"] == "a" * 40
    assert persisted["evidence_policy"]["credentials_persisted"] is False
