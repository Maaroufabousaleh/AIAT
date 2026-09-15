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


def test_semgrep_error_classes_separate_internal_execution_from_coverage():
    module = _module()
    classes, details = module._semgrep_error_classes(
        {
            "errors": [
                {"code": 2, "type": "Internal matching error"},
                {"code": 3, "type": ["PartialParsing", []]},
            ]
        }
    )
    assert classes == [module.SCANNER_COVERAGE_INCOMPLETE, module.SCANNER_EXECUTION_FAILURE]
    assert {row["failure_class"] for row in details} == {
        module.SCANNER_COVERAGE_INCOMPLETE,
        module.SCANNER_EXECUTION_FAILURE,
    }


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


def test_semgrep_nonzero_empty_exit_is_recorded_in_failure_classes(tmp_path, monkeypatch):
    module = _module()
    source = tmp_path / "source"
    source.mkdir()
    output_dir = tmp_path / "evidence"
    output_dir.mkdir()

    class Result:
        returncode = 2
        stdout = json.dumps({"results": [], "errors": []})
        stderr = "scanner stopped"

    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/semgrep")
    monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs: Result())

    row = module._run_scanner(
        "semgrep",
        ["semgrep", "--json", str(source)],
        source=source,
        output_dir=output_dir,
    )

    assert row["status"] == "blocked"
    assert row["failure_class"] == module.SCANNER_EXECUTION_FAILURE
    assert module.SCANNER_EXECUTION_FAILURE in row["failure_classes"]


def test_unavailable_scanner_is_recorded_in_failure_classes(tmp_path, monkeypatch):
    module = _module()
    source = tmp_path / "source"
    source.mkdir()
    output_dir = tmp_path / "evidence"
    output_dir.mkdir()

    monkeypatch.setattr(module.shutil, "which", lambda _name: None)

    row = module._run_scanner(
        "semgrep",
        ["semgrep", "--json", str(source)],
        source=source,
        output_dir=output_dir,
    )

    assert row["status"] == "blocked"
    assert row["failure_class"] == module.TOOL_INSTALLATION_FAILURE
    assert row["failure_classes"] == [module.TOOL_INSTALLATION_FAILURE]


def test_scanner_process_exception_is_recorded_in_failure_classes(tmp_path, monkeypatch):
    module = _module()
    source = tmp_path / "source"
    source.mkdir()
    output_dir = tmp_path / "evidence"
    output_dir.mkdir()

    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/semgrep")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("scanner unavailable")),
    )

    row = module._run_scanner(
        "semgrep",
        ["semgrep", "--json", str(source)],
        source=source,
        output_dir=output_dir,
    )

    assert row["status"] == "blocked"
    assert row["failure_class"] == module.SCANNER_EXECUTION_FAILURE
    assert row["failure_classes"] == [module.SCANNER_EXECUTION_FAILURE]


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
