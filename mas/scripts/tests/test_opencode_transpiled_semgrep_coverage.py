"""Tests for exact-source TypeScript coverage through Bun transpilation."""

from __future__ import annotations

import importlib.util
import io
import json
import tarfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_opencode_transpiled_semgrep_coverage.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("opencode_transpiled_semgrep_coverage", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_candidate_snapshot_reference_is_relative_to_certification_file(tmp_path: Path) -> None:
    module = _load_module()
    repository = tmp_path / "repo"
    candidate_dir = repository / "candidate"
    candidate_dir.mkdir(parents=True)
    snapshot = candidate_dir / "source-snapshot.tar.xz"
    snapshot.write_bytes(b"exact source archive")
    candidate = candidate_dir / "candidate-certification.json"
    candidate.write_text(
        json.dumps(
            {
                "source": {
                    "snapshot_path": snapshot.name,
                    "snapshot_sha256": module._sha256(snapshot),
                    "commit": "a" * 40,
                }
            }
        ),
        encoding="utf-8",
    )
    module.REPO_ROOT = repository

    observed, metadata = module._candidate_snapshot(candidate)

    assert observed == snapshot.resolve()
    assert metadata["snapshot_sha256"] == module._sha256(snapshot)


def test_extract_exact_files_writes_only_allowlisted_source(tmp_path: Path) -> None:
    module = _load_module()
    relative = "packages/core/src/util/effect-flock.ts"
    module.SOURCE_PATHS = (relative,)
    archive_path = tmp_path / "candidate.tar.xz"
    content = b"export const lock = true;\n"
    with tarfile.open(archive_path, mode="w:xz") as archive:
        member = tarfile.TarInfo(relative)
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))

    destination = tmp_path / "source"
    module._extract_exact_files(archive_path, destination)

    assert (destination / relative).read_bytes() == content


def test_extract_exact_files_rejects_parent_traversal(tmp_path: Path) -> None:
    module = _load_module()
    module.SOURCE_PATHS = ("../escape.ts",)
    archive_path = tmp_path / "candidate.tar.xz"
    content = b"unsafe"
    with tarfile.open(archive_path, mode="w:xz") as archive:
        member = tarfile.TarInfo("../escape.ts")
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))

    try:
        module._extract_exact_files(archive_path, tmp_path / "source")
    except ValueError as exc:
        assert "unsafe or non-file" in str(exc)
    else:
        raise AssertionError("archive parent traversal was accepted")
    assert not (tmp_path / "escape.ts").exists()


def test_source_map_maps_generated_line_and_column_to_candidate_source() -> None:
    module = _load_module()
    source_map = {
        "sources": ["../../source/packages/core/src/util/effect-flock.ts"],
        "mappings": "AAAA;AACA",
    }

    mapped = module._source_map_location(
        source_map,
        generated_line=2,
        generated_column=1,
    )

    assert mapped == ("packages/core/src/util/effect-flock.ts", 2, 1)


def test_summary_keeps_findings_open_and_records_mapped_locations() -> None:
    module = _load_module()
    source_path = "packages/core/src/util/effect-flock.ts"
    report = {
        "version": module.EXPECTED_SEMGREP_VERSION,
        "paths": {"scanned": ["/tmp/compiled/effect-flock.js"]},
        "results": [
            {
                "check_id": "path-traversal",
                "path": "/tmp/compiled/effect-flock.js",
                "start": {"line": 2, "col": 1},
                "end": {"line": 2, "col": 4},
                "extra": {"severity": "WARNING"},
            }
        ],
        "errors": [
            {"path": "/tmp/compiled/server.js", "message": "parser limitation"}
        ],
    }

    summary = module._summarize(
        report,
        source_metadata={"commit": "a" * 40},
        source_maps={
            "effect-flock.js": {
                "sources": [f"../../source/{source_path}"],
                "mappings": "AAAA;AACA",
            }
        },
        semgrep_exit_code=0,
    )

    assert summary["status"] == "blocked"
    assert summary["semgrep_exit_code"] == 0
    assert summary["transpiled_semgrep_finding_count"] == 1
    assert summary["deduplicated_rule_location_count"] == 1
    assert summary["finding_source_locations"] == [
        {
            "rule_id": "path-traversal",
            "generated_file": "effect-flock.js",
            "generated_line": 2,
            "generated_column": 1,
            "source_path": source_path,
            "source_line": 2,
            "source_column": 1,
            "source_map_applied": True,
        }
    ]
    assert summary["runtime_reachable_parser_error_paths"] == []
