"""Deterministic tests for the OpenHands certification report semantics."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


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
    def fake_run_scanner(name, command, source, output_dir):
        if name == "semgrep":
            (output_dir / "semgrep.json").write_text(
                json.dumps({"results": [{"extra": {"severity": "INFO"}}], "errors": [{"code": "1", "type": "parse"}]}),
                encoding="utf-8",
            )
        return {
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
            "raw_json_path": f"{name}.json",
        }
    monkeypatch.setattr(certify_module, "_run_scanner", fake_run_scanner)
    monkeypatch.setattr(certify_module, "_run_sbom", lambda source, output: {"status": "pass"})
    monkeypatch.setattr(certify_module, "_run_image_sbom", lambda image, output: {"status": "pass"})
    monkeypatch.setattr(certify_module, "_image_probe", lambda image: {"status": "pass"})
    monkeypatch.setattr(certify_module, "_run_boundary", lambda command, output: {"status": "pass"})
    monkeypatch.setattr(certify_module, "_load_json", lambda path: {"status": "pass", "failure_classes": []})
    monkeypatch.setattr(certify_module, "_agent_server_probe", lambda base, key, commit, version: {"status": "pass"})
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


def test_image_cross_check_retains_only_path_classification(monkeypatch) -> None:
    certify_module = _module()
    monkeypatch.setattr(
        certify_module,
        "_run",
        lambda command, **_: SimpleNamespace(
            returncode=0,
            stdout="openhands-agent-server/openhands/agent_server/api.py\t/agent-server/openhands-agent-server/openhands/agent_server/api.py\n",
            stderr="",
        ),
    )
    result = certify_module._image_cross_check(
        "candidate-container",
        [
            {
                "name": "skillspector",
                "applicability": {
                    "security_sensitive_paths": [
                        "openhands-agent-server/openhands/agent_server/api.py",
                        ".github/workflows/server.yml",
                    ]
                },
            }
        ],
    )
    assert result["status"] == "pass"
    assert result["classification_counts"]["IMAGE_PRESENT_REACHABLE"] == 1
    assert result["classification_counts"]["SOURCE_ONLY"] == 1
    assert any(row["path_class"] == "ci_release" for row in result["paths"])
    assert result["payloads_retained"] is False


def test_failed_image_sbom_does_not_leave_invalid_json_artifact(monkeypatch, tmp_path: Path) -> None:
    certify_module = _module()
    monkeypatch.setattr(certify_module.shutil, "which", lambda name: "/usr/bin/syft")

    def fake_run(command, **_kwargs):
        output_argument = next(argument for argument in command if str(argument).startswith("cyclonedx-json="))
        Path(str(output_argument).split("=", 1)[1]).write_text("", encoding="utf-8")
        return SimpleNamespace(returncode=1, stdout="", stderr="no space left on device")

    monkeypatch.setattr(certify_module.subprocess, "run", fake_run)
    report = certify_module._run_image_sbom("example/image@sha256:" + "a" * 64, tmp_path)
    assert report["status"] == "blocked"
    assert report["failure_class"] == certify_module.SBOM_FAILURE
    assert not (tmp_path / "image-sbom.cdx.json").exists()


def test_cleanup_failure_cannot_leave_a_passed_certification(monkeypatch, tmp_path: Path) -> None:
    certify_module = _module()

    def fake_prepare(_repository: str, _version: str, root: Path):
        source = root / "scan-source"
        source.mkdir()
        return ({"commit": certify_module.EXPECTED_SOURCE_COMMIT}, source)

    monkeypatch.setattr(certify_module, "_prepare_source", fake_prepare)
    monkeypatch.setattr(certify_module, "_current_git_revision", lambda: "b" * 40)
    monkeypatch.setattr(certify_module, "_tool_version", lambda name, output: {"name": name, "available": True})
    monkeypatch.setattr(
        certify_module,
        "_run_scanner",
        lambda name, command, source, output_dir: {
            "name": name,
            "status": "pass",
            "finding_count": 0,
            "severity_counts": {},
            "scanner_error_count": 0,
            "scanner_errors": [],
            "failure_classes": [],
            "raw_output_retained": True,
            "raw_json_path": f"{name}.json",
        },
    )
    monkeypatch.setattr(certify_module, "_run_sbom", lambda source, output: {"status": "pass"})
    monkeypatch.setattr(certify_module, "_run_image_sbom", lambda image, output: {"status": "pass"})
    monkeypatch.setattr(certify_module, "_image_probe", lambda image: {"status": "pass"})
    monkeypatch.setattr(certify_module, "_run_boundary", lambda command, output: {"status": "pass"})
    monkeypatch.setattr(certify_module, "_load_json", lambda path: {"status": "pass", "failure_classes": []})
    monkeypatch.setattr(certify_module, "_agent_server_probe", lambda base, key, commit, version: {"status": "pass"})
    monkeypatch.setattr(certify_module, "_container_probe", lambda name: {"status": "pass", "runtime": "runsc"})
    monkeypatch.setattr(certify_module, "_image_cross_check", lambda name, rows: {"status": "pass"})
    monkeypatch.setattr(certify_module, "_cleanup_container", lambda name: {"status": "blocked", "remaining_containers": 1})

    report = certify_module.certify(
        repository=certify_module.DEFAULT_REPOSITORY,
        version=certify_module.DEFAULT_VERSION,
        image_ref=certify_module.DEFAULT_IMAGE,
        output_dir=tmp_path,
        boundary_command=["true"],
        tooling_manifest_path=tmp_path / "tooling.json",
    )
    assert report["status"] == "blocked"
    assert "agent_server_container_residue" in report["blockers"]
