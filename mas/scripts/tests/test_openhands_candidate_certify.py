"""Deterministic tests for the OpenHands certification report semantics."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _module():
    script = Path(__file__).resolve().parents[1] / "openhands_candidate_certify.py"
    spec = importlib.util.spec_from_file_location("openhands_candidate_certify", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_certification_keeps_scanner_errors_distinct_from_findings(monkeypatch, tmp_path: Path) -> None:
    certify_module = _module()
    def fake_prepare(_repository: str, _version: str, root: Path):
        source = root / "scan-source"
        source.mkdir()
        return (
            {
                "repository": "https://github.com/OpenHands/software-agent-sdk.git",
                "release": "1.43.0",
                "tag": "v1.43.0",
                "commit": certify_module.EXPECTED_SOURCE_COMMIT,
                "archive_sha256": "a" * 64,
            },
            source,
        )

    monkeypatch.setattr(certify_module, "_prepare_source", fake_prepare)
    monkeypatch.setattr(certify_module, "_current_git_revision", lambda: "b" * 40)
    monkeypatch.setattr(certify_module, "_tool_version", lambda name, output: {"name": name, "available": True, "version": "pinned"})
    monkeypatch.setattr(
        certify_module,
        "_run_scanner",
        lambda name, command, source, output_dir: {
            "name": name,
            "status": "blocked" if name == "semgrep" else "pass",
            "finding_count": 2 if name == "semgrep" else 0,
            "severity_counts": {"INFO": 2} if name == "semgrep" else {},
            "scanner_error_count": 1 if name == "semgrep" else 0,
            "scanner_errors": [{"failure_class": certify_module.SCANNER_COVERAGE_INCOMPLETE, "count": 1}]
            if name == "semgrep"
            else [],
            "failure_class": certify_module.SCANNER_COVERAGE_INCOMPLETE if name == "semgrep" else None,
            "failure_classes": [certify_module.SCANNER_COVERAGE_INCOMPLETE] if name == "semgrep" else [],
            "raw_output_retained": True,
        },
    )
    monkeypatch.setattr(certify_module, "_run_sbom", lambda source, output: {"status": "pass"})
    monkeypatch.setattr(certify_module, "_run_image_sbom", lambda image, output: {"status": "pass"})
    monkeypatch.setattr(certify_module, "_image_probe", lambda image: {"status": "pass"})
    monkeypatch.setattr(certify_module, "_run_boundary", lambda command, output: {"status": "pass"})
    monkeypatch.setattr(certify_module, "_load_json", lambda path: {"status": "pass", "failure_classes": []})
    monkeypatch.setattr(certify_module, "_agent_server_probe", lambda base, key, commit: {"status": "pass"})
    monkeypatch.setattr(certify_module, "_container_probe", lambda name: {"status": "pass", "runtime": "runsc"})
    monkeypatch.setattr(certify_module, "_cleanup_container", lambda name: {"status": "pass", "remaining_containers": 0})

    report = certify_module.certify(
        repository=certify_module.DEFAULT_REPOSITORY,
        version=certify_module.DEFAULT_VERSION,
        image_ref=certify_module.DEFAULT_IMAGE,
        output_dir=tmp_path,
        boundary_command=["true"],
        tooling_manifest_path=tmp_path / "tooling.json",
    )
    assert report["status"] == "blocked"
    assert report["security_findings_interpretable"] is False
    assert certify_module.SCANNER_COVERAGE_INCOMPLETE in report["failure_classes"]
    assert certify_module.SECURITY_FINDING in report["failure_classes"]
    persisted = json.loads((tmp_path / "candidate-certification.json").read_text())
    assert persisted["runtime_applicability"]["raw_hits_are_not_exploitability_verdicts"] is True
