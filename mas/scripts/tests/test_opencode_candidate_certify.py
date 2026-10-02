"""Regression tests for reproducible OpenCode candidate evidence."""

from __future__ import annotations

import importlib.util
import io
import json
import lzma
import subprocess
import tarfile
from pathlib import Path

import pytest

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


def test_scanner_sanitization_removes_trufflehog_raw_secret_fields():
    module = _module()
    detection = {
        "DetectorName": "AWS",
        "Raw": "AKIA" + "A" * 16,
        "RawV2": "token-value-that-must-not-persist",
        "ExtraData": {"candidate": "nested-secret-value"},
        "SourceMetadata": {"Data": {"Filesystem": {"file": "src/example.ts"}}},
    }

    sanitized, complete = module._sanitize_scanner_output("trufflehog", json.dumps(detection))
    row = json.loads(sanitized)

    assert complete is True
    assert row["Raw"] == "[REDACTED]"
    assert row["RawV2"] == "[REDACTED]"
    assert row["ExtraData"] == "[REDACTED]"
    assert row["SourceMetadata"]["Data"]["Filesystem"]["file"] == "src/example.ts"
    assert "AKIA" + "A" * 16 not in sanitized
    assert "token-value-that-must-not-persist" not in sanitized


def test_semgrep_sanitization_omits_source_context():
    module = _module()
    source_line = 'password = "private-value"'
    raw = json.dumps({"results": [{"extra": {"lines": source_line, "severity": "ERROR"}}], "errors": []})

    sanitized, complete = module._sanitize_scanner_output("semgrep", raw)

    assert complete is True
    assert source_line not in sanitized
    assert '"lines": "[REDACTED_SOURCE_CONTEXT]"' in sanitized


def test_skillspector_sanitization_redacts_freeform_finding_evidence():
    module = _module()
    private_context = "untrusted source evidence that must not persist"
    raw = json.dumps(
        {
            "issues": [
                {
                    "severity": "HIGH",
                    "code_snippet": private_context,
                    "evidence": private_context,
                    "finding": private_context,
                    "message": private_context,
                }
            ]
        }
    )

    sanitized, complete = module._sanitize_scanner_output("skillspector", raw)
    record = json.loads(sanitized)["issues"][0]

    assert complete is True
    assert record["severity"] == "HIGH"
    assert all(record[key] == "[REDACTED]" for key in ("code_snippet", "evidence", "finding", "message"))
    assert private_context not in sanitized


def test_malformed_scanner_output_is_replaced_instead_of_retained():
    module = _module()
    malformed = '{"raw":"' + ("secret-value-" * 8)

    sanitized, complete = module._sanitize_scanner_output("skillspector", malformed)

    assert complete is False
    assert "secret-value-" not in sanitized
    assert json.loads(sanitized)["aiat_sanitization"] == "invalid scanner output omitted"


def test_candidate_source_archive_is_retained_with_matching_hash(tmp_path):
    module = _module()
    repository = tmp_path / "upstream"
    repository.mkdir()
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
    (repository / "source.txt").write_text("pinned source\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "source.txt"], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=AIAT test",
            "-c",
            "user.email=aiat-test@example.invalid",
            "commit",
            "-m",
            "fixture",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "-C", str(repository), "tag", "v1.2.3"], check=True, capture_output=True)
    snapshot = tmp_path / "evidence" / "source-snapshot.tar.xz"

    metadata, extracted = module._prepare_source(
        str(repository),
        "1.2.3",
        tmp_path / "work",
        snapshot,
    )

    assert metadata["snapshot_retained"] is True
    assert metadata["snapshot_path"] == "source-snapshot.tar.xz"
    archive_bytes = lzma.decompress(snapshot.read_bytes())
    assert module._sha256_bytes(archive_bytes) == metadata["archive_sha256"]
    assert module._sha256_file(snapshot) == metadata["snapshot_sha256"]
    assert (extracted / "source.txt").read_text(encoding="utf-8") == "pinned source\n"


def test_source_archive_rejects_symlink_path_traversal(tmp_path):
    module = _module()
    archive_bytes = io.BytesIO()
    with tarfile.open(fileobj=archive_bytes, mode="w") as archive:
        link = tarfile.TarInfo("candidate/escape")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../outside"
        archive.addfile(link)

        payload = b"must stay inside the extraction root"
        member = tarfile.TarInfo("candidate/escape/payload.txt")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    destination = tmp_path / "source"
    with pytest.raises(RuntimeError, match="unsafe link"):
        module._extract_source_archive(archive_bytes.getvalue(), destination)

    assert not (tmp_path / "outside" / "payload.txt").exists()


def test_sbom_evidence_records_the_executed_syft_arguments(tmp_path, monkeypatch):
    module = _module()
    source = tmp_path / "source"
    source.mkdir()
    output_dir = tmp_path / "evidence"
    output_dir.mkdir()
    invocations = []

    class Result:
        returncode = 0
        stdout = "syft 1.51.0"
        stderr = ""

    def fake_run(command, **kwargs):
        invocations.append(command)
        if "--version" in command:
            return Result()
        output = Path(command[command.index("-o") + 1].split("=", 1)[1])
        output.write_text(json.dumps({"components": [{"name": "fixture"}]}), encoding="utf-8")
        result = Result()
        result.stdout = ""
        return result

    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/syft")
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    evidence = module._run_sbom(source, output_dir)

    assert invocations[0][invocations[0].index("-o") + 1].startswith("cyclonedx-json=")
    assert evidence["invocation"] == ["/usr/bin/syft", "dir:<source>", "-o", "cyclonedx-json=<sbom>"]
    assert evidence["working_directory"] == "<source>"
    assert evidence["path"] == "sbom.cdx.json"


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


def test_semgrep_rule_counts_are_stable_and_match_findings():
    module = _module()
    counts = module._semgrep_rule_counts(
        {"results": [{"check_id": "rule.path"}, {"check_id": "rule.path"}, {"check_id": "rule.exec"}]}
    )

    assert counts == {"rule.exec": 1, "rule.path": 2}


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


def test_recursive_skillspector_summary_flattens_per_skill_findings(tmp_path):
    module = _module()
    path = tmp_path / "skillspector-recursive.json"
    path.write_text(
        json.dumps(
            {
                "execution_successful": True,
                "skills": [
                    {"execution_successful": True, "issues": [{"id": "E1", "severity": "HIGH"}]},
                    {"execution_successful": True, "issues": [{"id": "SC2", "severity": "MEDIUM"}]},
                ],
            }
        ),
        encoding="utf-8",
    )

    count, severities, errors, shape = module._generic_summary(path, "skillspector")

    assert (count, severities, errors, shape) == (
        2,
        {"HIGH": 1, "MEDIUM": 1},
        0,
        None,
    )
    assert module._generic_rule_counts(path) == {"E1": 1, "SC2": 1}


def test_skillspector_scope_requires_bundled_skill_resources(tmp_path):
    module = _module()
    assert module._has_bundled_skill_resources(tmp_path) is False
    skill = tmp_path / ".opencode" / "skills" / "example" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# bounded fixture\n", encoding="utf-8")
    assert module._has_bundled_skill_resources(tmp_path) is True


def test_generic_rule_counts_use_scalar_skill_rule_ids(tmp_path):
    module = _module()
    path = tmp_path / "skillspector-rules.json"
    path.write_text('{"issues":[{"id":"TM2"},{"id":"TM2"},{"id":"SC2"}]}', encoding="utf-8")

    assert module._generic_rule_counts(path) == {"SC2": 1, "TM2": 2}


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


def test_scanner_retains_only_sanitized_output_and_hashes_that_artifact(tmp_path, monkeypatch):
    module = _module()
    source = tmp_path / "source"
    source.mkdir()
    output_dir = tmp_path / "evidence"
    output_dir.mkdir()
    detector_value = "github_pat_" + "a" * 30

    class Result:
        pass

    result = Result()
    result.returncode = 1
    result.stdout = json.dumps({"issues": [{"severity": "HIGH", "Raw": detector_value}]})
    result.stderr = f"scanner diagnostic {detector_value}"

    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/skillspector")
    monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs: result)

    row = module._run_scanner(
        "skillspector",
        ["skillspector", "scan", str(source)],
        source=source,
        output_dir=output_dir,
    )

    retained = (output_dir / "skillspector.json").read_text(encoding="utf-8")
    stderr = (output_dir / "skillspector.stderr.log").read_text(encoding="utf-8")
    assert row["raw_output_retained"] is True
    assert row["raw_output_sanitized"] is True
    assert row["raw_json_sha256"] == module._sha256_file(output_dir / "skillspector.json")
    assert detector_value not in retained
    assert detector_value not in stderr
    assert "stderr omitted for secret safety" in stderr


def test_recursive_skillspector_reads_cli_output_file_and_redacts_paths(tmp_path, monkeypatch):
    module = _module()
    source = tmp_path / "source"
    source.mkdir()
    output_dir = tmp_path / "evidence"
    output_dir.mkdir()
    raw_output = output_dir / "skillspector-cli-output.json"

    class Result:
        returncode = 0
        stdout = "recursive scan completed"
        stderr = ""

    def fake_run(command, **_kwargs):
        raw_output.write_text(
            json.dumps(
                {
                    "execution_successful": True,
                    "skills": [
                        {
                            "path": "example",
                            "execution_successful": True,
                            "issues": [{"id": "E1", "severity": "HIGH", "evidence": "private context"}],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return Result()

    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/skillspector")
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    row = module._run_scanner(
        "skillspector",
        [
            "skillspector",
            "scan",
            ".opencode/skills",
            "--recursive",
            "--no-llm",
            "--format",
            "json",
            "--output",
            str(raw_output),
        ],
        source=source,
        output_dir=output_dir,
    )

    retained = (output_dir / "skillspector.json").read_text(encoding="utf-8")
    assert row["status"] == "pass"
    assert row["finding_count"] == 1
    assert row["rule_counts"] == {"E1": 1}
    assert "<bundled-skill-resources>" in row["invocation"]
    assert "<skillspector-output>" in row["invocation"]
    assert "private context" not in retained
    assert raw_output.exists() is False


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
