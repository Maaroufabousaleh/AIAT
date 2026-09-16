"""Regression coverage for OpenCode candidate scanner triage."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "opencode_candidate_triage.py"


def _module():
    spec = importlib.util.spec_from_file_location("opencode_candidate_triage", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_semgrep_errors_keep_runtime_coverage_visible() -> None:
    module = _module()
    rows, counts = module._semgrep_error_rows(
        {
            "errors": [
                {
                    "code": 3,
                    "path": "/tmp/scan-source/packages/core/src/server.ts",
                    "type": ["PartialParsing", [{"start": {"line": 12}}]],
                },
                {
                    "code": 3,
                    "path": "/tmp/scan-source/.github/workflows/check.yml",
                    "type": ["PartialParsing", []],
                },
            ]
        }
    )

    assert rows[0]["path"] == "packages/core/src/server.ts"
    assert rows[0]["runtime_security_relevant"] is True
    assert rows[0]["disposition"] == "coverage_gap_requires_alternate_scan"
    assert rows[1]["runtime_security_relevant"] is False
    assert counts["source syntax genuinely unsupported"] == 1
    assert counts["embedded GitHub-expression/YAML parsing limitation"] == 1


def test_skillspector_classifies_reachable_runtime_findings_as_actionable() -> None:
    module = _module()
    rows, applicability, rules = module._skillspector_rows(
        {
            "issues": [
                {
                    "id": "E1",
                    "severity": "HIGH",
                    "location": {"file": "packages/opencode/src/server/index.ts"},
                    "evidence": {"local_only": True},
                },
                {
                    "id": "E1",
                    "severity": "HIGH",
                    "location": {"file": "packages/web/src/content/docs/security.md"},
                },
            ]
        }
    )

    assert rows[0]["applicability"] == module.RUNTIME_APPLICABLE
    assert rows[0]["aiat_serve_reachable"] is True
    assert rows[0]["actionable"] is True
    assert rows[1]["applicability"] == "NON_RUNTIME"
    assert rows[1]["actionable"] is False
    assert applicability[module.RUNTIME_APPLICABLE] == 1
    assert applicability["NON_RUNTIME"] == 1
    assert rules["E1"] == 2


def test_trufflehog_rows_do_not_copy_secret_material(tmp_path) -> None:
    module = _module()
    path = tmp_path / "trufflehog.json"
    path.write_text(
        json.dumps(
            {
                "DetectorName": "Generic",
                "Verified": False,
                "Raw": "must-not-be-retained",
                "SourceMetadata": {
                    "Data": {"Filesystem": {"file": "/tmp/scan-source/tests/fixture.py"}}
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    rows, counts = module._trufflehog_rows(path)

    assert rows[0]["path"] == "tests/fixture.py"
    assert rows[0]["classification"] == "TEST_FIXTURE"
    assert rows[0]["raw_secret_material_retained"] is False
    assert "must-not-be-retained" not in json.dumps(rows)
    assert counts["TEST_FIXTURE"] == 1


def test_trufflehog_malformed_jsonl_is_bounded_scanner_failure(tmp_path) -> None:
    module = _module()
    path = tmp_path / "trufflehog.json"
    path.write_text('{"DetectorName":"Generic"}\n{"truncated"\n', encoding="utf-8")

    rows, counts = module._trufflehog_rows(path)

    assert len(rows) == 2
    malformed = rows[1]
    assert malformed["classification"] == module.SCANNER_EXECUTION_FAILURE
    assert malformed["path"] == "unknown"
    assert malformed["raw_secret_material_retained"] is False
    assert counts[module.SCANNER_EXECUTION_FAILURE] == 1


def test_trufflehog_invalid_encoding_is_bounded_scanner_failure(tmp_path) -> None:
    module = _module()
    path = tmp_path / "trufflehog.json"
    path.write_bytes(b'{"DetectorName":"Generic"}\n\xff\n')

    rows, counts = module._trufflehog_rows(path)

    assert len(rows) == 1
    assert rows[0]["classification"] == module.SCANNER_EXECUTION_FAILURE
    assert rows[0]["error_type"] == "UnicodeDecodeError"
    assert rows[0]["raw_secret_material_retained"] is False
    assert counts[module.SCANNER_EXECUTION_FAILURE] == 1


def test_image_observation_requires_matching_digest_before_container_probe(monkeypatch) -> None:
    module = _module()
    calls: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = json.dumps(
            {
                "RepoDigests": ["ghcr.io/anomalyco/opencode@sha256:actual"],
                "Id": "sha256:image",
                "RootFS": {"Layers": ["layer"]},
                "Config": {"Entrypoint": ["opencode"]},
            }
        )

    def run(argv, **_kwargs):
        calls.append(list(argv))
        return Result()

    monkeypatch.setattr(module.subprocess, "run", run)
    result = module._docker_image_observation(
        "ghcr.io/anomalyco/opencode:1.18.21@sha256:requested"
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "docker_image_digest_mismatch"
    assert result["digest_match"] is False
    assert len(calls) == 1


def test_image_observation_reports_verified_digest_and_bounded_probe(monkeypatch) -> None:
    module = _module()
    calls: list[list[str]] = []

    class InspectResult:
        returncode = 0
        stdout = json.dumps(
            {
                "RepoDigests": ["ghcr.io/anomalyco/opencode@sha256:requested"],
                "Id": "sha256:image",
                "RootFS": {"Layers": ["layer"]},
                "Config": {"Entrypoint": ["opencode"]},
            }
        )

    class RunResult:
        returncode = 0
        stdout = "1.18.21\n"

    def run(argv, **_kwargs):
        calls.append(list(argv))
        return InspectResult() if argv[1:3] == ["image", "inspect"] else RunResult()

    monkeypatch.setattr(module.subprocess, "run", run)
    result = module._docker_image_observation(
        "ghcr.io/anomalyco/opencode:1.18.21@sha256:requested"
    )

    assert result["status"] == "pass"
    assert result["digest_match"] is True
    assert result["application_version"] == "1.18.21"
    assert result["credentials_or_payloads_retained"] is False
    assert len(calls) == 2
    probe = calls[1]
    assert probe[0:3] == ["docker", "run", "--rm"]
    assert probe[probe.index("--network") + 1] == "none"
    assert probe[probe.index("--cap-drop") + 1] == "ALL"
    assert "--read-only" in probe
    assert probe[probe.index("--pids-limit") + 1] == "64"
    assert probe[probe.index("--memory") + 1] == "256m"
    assert probe[probe.index("--cpus") + 1] == "0.5"


def test_certification_decision_is_derived_from_current_evidence() -> None:
    module = _module()

    assert module._certification_decision(
        coverage_incomplete=False,
        runtime_review_required=False,
        verified_secrets=0,
        image_status="pass",
        sbom_status="pass",
        raw_findings=0,
    ) == "PASSED"
    assert module._certification_decision(
        coverage_incomplete=True,
        runtime_review_required=False,
        verified_secrets=0,
        image_status="pass",
        sbom_status="pass",
        raw_findings=0,
    ) == "BLOCKED_SCANNER_COVERAGE_INCOMPLETE"
    assert module._certification_decision(
        coverage_incomplete=False,
        runtime_review_required=True,
        verified_secrets=0,
        image_status="pass",
        sbom_status="pass",
        raw_findings=0,
    ) == "BLOCKED_RUNTIME_REVIEW_REQUIRED"
    assert module._certification_decision(
        coverage_incomplete=True,
        runtime_review_required=True,
        verified_secrets=0,
        image_status="pass",
        sbom_status="pass",
        raw_findings=0,
    ) == "BLOCKED_SCANNER_COVERAGE_INCOMPLETE_AND_RUNTIME_REVIEW_REQUIRED"


def test_triage_rejects_changed_certified_scanner_artifact(tmp_path, monkeypatch) -> None:
    module = _module()
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    outputs = {
        "semgrep": '{"results": [], "errors": []}\n',
        "trufflehog": "",
        "skillspector": '{"issues": []}\n',
    }
    scanner_rows = []
    for name, content in outputs.items():
        path = artifact_dir / f"{name}.json"
        path.write_text(content, encoding="utf-8")
        scanner_rows.append(
            {
                "name": name,
                "raw_json_path": path.name,
                "raw_json_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "status": "pass",
            }
        )
    (artifact_dir / "semgrep.json").write_text('{"results": [{"changed": true}]}\n', encoding="utf-8")
    report_path = tmp_path / "candidate-certification.json"
    report_path.write_text(
        json.dumps(
            {
                "scanners": scanner_rows,
                "candidate_image_ref": "candidate@sha256:" + "a" * 64,
                "sbom": {"status": "pass"},
                "raw_findings_count": 0,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "_docker_image_observation", lambda _image: {"status": "pass"})

    with pytest.raises(ValueError, match="hash mismatch"):
        module.triage(
            report_path=report_path,
            artifact_dir=artifact_dir,
            source_dir=source_dir,
            output_path=tmp_path / "triage.json",
        )


def test_triage_derives_findings_instead_of_trusting_report_count(tmp_path, monkeypatch) -> None:
    module = _module()
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    outputs = {
        "semgrep": '{"results": [], "errors": []}\n',
        "trufflehog": "",
        "skillspector": '{"issues": []}\n',
    }
    scanner_rows = []
    for name, content in outputs.items():
        path = artifact_dir / f"{name}.json"
        path.write_text(content, encoding="utf-8")
        scanner_rows.append(
            {
                "name": name,
                "raw_json_path": path.name,
                "raw_json_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "status": "pass",
            }
        )
    report_path = tmp_path / "candidate-certification.json"
    report_path.write_text(
        json.dumps(
            {
                "scanners": scanner_rows,
                "candidate_image_ref": "candidate@sha256:" + "a" * 64,
                "sbom": {"status": "pass"},
                "raw_findings_count": 999,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "_docker_image_observation", lambda _image: {"status": "pass"})

    result = module.triage(
        report_path=report_path,
        artifact_dir=artifact_dir,
        source_dir=source_dir,
        output_path=tmp_path / "triage.json",
    )

    assert result["decision"]["CERTIFICATION_DECISION"] == "PASSED"
    assert result["decision"]["RAW_FINDINGS_DERIVED"] == 0
    assert result["decision"]["RAW_FINDINGS_REPORTED"] == 999
    assert result["decision"]["RAW_FINDINGS_REPORT_MATCH"] is False
    assert result["raw_evidence"]["semgrep"]["unchanged"] is True
    assert result["raw_evidence"]["semgrep"]["certified_sha256"] == result["raw_evidence"]["semgrep"]["sha256"]


def test_triage_blocks_unclassified_nonpassing_scanner_manifest(tmp_path, monkeypatch) -> None:
    module = _module()
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    outputs = {
        "semgrep": '{"results": [], "errors": []}\n',
        "trufflehog": "",
        "skillspector": '{"issues": []}\n',
    }
    scanner_rows = []
    for name, content in outputs.items():
        path = artifact_dir / f"{name}.json"
        path.write_text(content, encoding="utf-8")
        scanner_rows.append(
            {
                "name": name,
                "raw_json_path": path.name,
                "raw_json_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "status": "blocked" if name == "semgrep" else "pass",
            }
        )
    report_path = tmp_path / "candidate-certification.json"
    report_path.write_text(
        json.dumps(
            {
                "scanners": scanner_rows,
                "candidate_image_ref": "candidate@sha256:" + "a" * 64,
                "sbom": {"status": "pass"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "_docker_image_observation", lambda _image: {"status": "pass"})

    result = module.triage(
        report_path=report_path,
        artifact_dir=artifact_dir,
        source_dir=source_dir,
        output_path=tmp_path / "triage.json",
    )

    assert result["decision"]["CERTIFICATION_DECISION"] == "BLOCKED_SCANNER_COVERAGE_INCOMPLETE"
