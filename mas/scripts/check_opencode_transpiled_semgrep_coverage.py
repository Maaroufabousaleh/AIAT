"""Run Semgrep on exact-source TypeScript after a pinned Bun transpilation.

This is a bounded compensating security scan for exact TypeScript paths that
Semgrep's parser reports as partial or unsupported. Bun only parses/transpiles
the files; candidate code is never executed. The resulting JavaScript is then
scanned with the same pinned Semgrep executable and ruleset used for the
candidate scan. Findings and parser errors remain gates; this checker does not
waive or disposition them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CANDIDATE = (
    REPO_ROOT
    / "mas/docs/provenance/opencode-candidate/2026-10-02-v1.18.21-r4/candidate-certification.json"
)
SCHEMA = "aiat.opencode-transpiled-semgrep-coverage.v1"
BUN_VERSION = "1.3.14"
EXPECTED_SEMGREP_VERSION = "1.168.0"
SOURCE_PATHS = (
    "packages/opencode/src/lsp/server.ts",
    "packages/core/src/effect/keyed-mutex.ts",
    "packages/core/src/util/effect-flock.ts",
    "packages/opencode/src/server/routes/instance/httpapi/public.ts",
    "packages/plugin/src/tui.ts",
    "packages/tui/src/plugin/slots.tsx",
)
RUNTIME_REACHABLE_PATHS = frozenset(
    {
        "packages/core/src/effect/keyed-mutex.ts",
        "packages/core/src/util/effect-flock.ts",
        "packages/opencode/src/server/routes/instance/httpapi/public.ts",
    }
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _candidate_snapshot(candidate_path: Path) -> tuple[Path, dict[str, Any]]:
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    source = candidate.get("source")
    if not isinstance(source, dict):
        raise ValueError("candidate source provenance is missing")
    snapshot_ref = str(source.get("snapshot_path") or "").strip()
    snapshot_sha256 = str(source.get("snapshot_sha256") or "").strip()
    commit = str(source.get("commit") or "").strip()
    if not snapshot_ref or len(snapshot_sha256) != 64 or not commit:
        raise ValueError("candidate source snapshot identity is incomplete")
    snapshot = (candidate_path.parent / snapshot_ref).resolve()
    if not _within(snapshot, REPO_ROOT) or not snapshot.is_file():
        raise ValueError("candidate source snapshot is unavailable inside the repository")
    observed = _sha256(snapshot)
    if observed != snapshot_sha256:
        raise ValueError("candidate source snapshot hash does not match the certification")
    return snapshot, {"commit": commit, "snapshot_ref": snapshot_ref, "snapshot_sha256": observed}


def _extract_exact_files(snapshot: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(snapshot, mode="r:xz") as archive:
        for relative in SOURCE_PATHS:
            member = archive.getmember(relative)
            member_path = PurePosixPath(member.name)
            if member_path.is_absolute() or ".." in member_path.parts or not member.isfile():
                raise ValueError("candidate archive contains an unsafe or non-file coverage entry")
            target = destination.joinpath(*member_path.parts)
            if not _within(target, destination):
                raise ValueError("candidate archive path escaped its temporary source directory")
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("candidate archive source member could not be read")
            target.write_bytes(source.read())


def _run(command: list[str], *, cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _normalise_paths(value: Any, *, temp_root: Path, compiled_root: Path) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _normalise_paths(item, temp_root=temp_root, compiled_root=compiled_root)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalise_paths(item, temp_root=temp_root, compiled_root=compiled_root) for item in value]
    if isinstance(value, str):
        text = value.replace(str(compiled_root), "<transpiled-source>")
        return text.replace(str(temp_root), "<temporary-candidate>")
    return value


def _decode_source_map(mappings: str) -> list[list[tuple[int, int, int, int]]]:
    """Decode source/line/column mappings while preserving map-wide deltas."""
    base64_values = {
        **{chr(ord("A") + index): index for index in range(26)},
        **{chr(ord("a") + index): index + 26 for index in range(26)},
        **{str(index): index + 52 for index in range(10)},
        "+": 62,
        "/": 63,
    }
    mapped_lines: list[list[tuple[int, int, int, int]]] = []
    previous_source = 0
    previous_original_line = 0
    previous_original_column = 0
    for encoded_line in mappings.split(";"):
        segments: list[tuple[int, int, int, int]] = []
        generated_column = 0
        for encoded_segment in encoded_line.split(","):
            if not encoded_segment:
                continue
            values: list[int] = []
            value = 0
            shift = 0
            for character in encoded_segment:
                digit = base64_values.get(character)
                if digit is None:
                    raise ValueError("source map contains invalid base64 VLQ data")
                continuation = digit & 32
                value |= (digit & 31) << shift
                if continuation:
                    shift += 5
                    continue
                negative = value & 1
                decoded = value >> 1
                values.append(-decoded if negative else decoded)
                value = 0
                shift = 0
            if shift or value:
                raise ValueError("source map contains an incomplete base64 VLQ segment")
            if len(values) == 1:
                generated_column += values[0]
                continue
            if len(values) not in (4, 5):
                raise ValueError("source map contains an invalid segment width")
            generated_column += values[0]
            previous_source += values[1]
            previous_original_line += values[2]
            previous_original_column += values[3]
            segments.append(
                (
                    generated_column,
                    previous_source,
                    previous_original_line,
                    previous_original_column,
                )
            )
        mapped_lines.append(segments)
    return mapped_lines


def _source_map_location(
    source_map: dict[str, Any], *, generated_line: int, generated_column: int
) -> tuple[str, int, int] | None:
    """Map a one-based generated position to a retained candidate source path."""

    sources = source_map.get("sources")
    mappings = source_map.get("mappings")
    if not isinstance(sources, list) or not isinstance(mappings, str) or generated_line < 1:
        return None
    mapped_lines = _decode_source_map(mappings)
    if generated_line > len(mapped_lines):
        return None
    matching = [
        segment
        for segment in mapped_lines[generated_line - 1]
        if segment[0] <= max(0, generated_column - 1)
    ]
    if not matching:
        return None
    _column, source_index, original_line, original_column = matching[-1]
    if source_index < 0 or source_index >= len(sources):
        return None
    source = str(sources[source_index]).replace("\\", "/")
    candidate_path = next(
        (known for known in SOURCE_PATHS if source.endswith(known)),
        None,
    )
    if candidate_path is None:
        return None
    return candidate_path, original_line + 1, original_column + 1


def _summarize(
    report: dict[str, Any],
    *,
    source_metadata: dict[str, str],
    source_maps: dict[str, dict[str, Any]],
    semgrep_exit_code: int,
) -> dict[str, Any]:
    results = report.get("results") if isinstance(report.get("results"), list) else []
    errors = report.get("errors") if isinstance(report.get("errors"), list) else []
    rules: Counter[str] = Counter()
    severities: Counter[str] = Counter()
    locations: set[tuple[str, str, int | None]] = set()
    findings_by_source_path: Counter[str] = Counter()
    finding_locations: list[dict[str, Any]] = []
    for row in results:
        if not isinstance(row, dict):
            continue
        rule = str(row.get("check_id") or "unknown_rule")
        extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
        start = row.get("start") if isinstance(row.get("start"), dict) else {}
        compiled_path = Path(str(row.get("path") or "")).name
        source_path = next(
            (source for source in SOURCE_PATHS if Path(source).stem + ".js" == compiled_path),
            "unmapped-transpiled-output",
        )
        line = start.get("line") if isinstance(start.get("line"), int) else None
        rules[rule] += 1
        severity = str(extra.get("severity") or "UNKNOWN").upper()
        severities[severity] += 1
        findings_by_source_path[source_path] += 1
        generated_column = start.get("col") if isinstance(start.get("col"), int) else 1
        source_location = None
        source_map = source_maps.get(compiled_path)
        if source_map is not None and line is not None:
            source_location = _source_map_location(
                source_map,
                generated_line=line,
                generated_column=generated_column,
            )
        if source_location is not None:
            source_path, source_line, source_column = source_location
        else:
            source_line, source_column = line, generated_column
        locations.add((rule, source_path, source_line))
        finding_locations.append(
            {
                "rule_id": rule,
                "generated_file": compiled_path,
                "generated_line": line,
                "generated_column": generated_column,
                "source_path": source_path,
                "source_line": source_line,
                "source_column": source_column,
                "source_map_applied": source_location is not None,
            }
        )
    error_paths: list[str] = []
    for row in errors:
        if isinstance(row, dict):
            path = Path(str(row.get("path") or "")).name
            source_path = next(
                (source for source in SOURCE_PATHS if Path(source).stem + ".js" == path),
                "unmapped-transpiled-output",
            )
            error_paths.append(source_path)
    runtime_errors = sorted(set(error_paths) & RUNTIME_REACHABLE_PATHS)
    status = "blocked" if errors else "findings_review_required" if results else "pass"
    return {
        "schema_version": SCHEMA,
        "checked_at": datetime.now(UTC).isoformat(),
        "status": status,
        "upstream_source": source_metadata,
        "bun_version": BUN_VERSION,
        "semgrep_version": str(report.get("version") or "unknown"),
        "semgrep_exit_code": semgrep_exit_code,
        "scanned_transpiled_file_count": len(report.get("paths", {}).get("scanned", []))
        if isinstance(report.get("paths"), dict)
        else 0,
        "transpiled_semgrep_finding_count": len(results),
        "deduplicated_rule_location_count": len(locations),
        "rule_counts": dict(sorted(rules.items())),
        "severity_counts": dict(sorted(severities.items())),
        "findings_by_source_path": dict(sorted(findings_by_source_path.items())),
        "finding_source_locations": finding_locations,
        "scanner_error_count": len(errors),
        "scanner_error_source_paths": sorted(set(error_paths)),
        "runtime_reachable_parser_error_paths": runtime_errors,
        "runtime_reachable_paths": sorted(RUNTIME_REACHABLE_PATHS),
        "candidate_source_executed": False,
        "dependency_install_lifecycle_executed": False,
        "external_module_resolution": False,
        "payloads_retained": False,
        "secret_values_retained": False,
        "scope": "Semgrep security scan of Bun-transpiled executable code from six exact TypeScript paths; findings remain open",
    }


def run_coverage(
    *,
    candidate_path: Path,
    output_dir: Path,
    semgrep_executable: str | None = None,
) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    if not _within(output_dir, REPO_ROOT):
        raise ValueError("evidence output directory must remain inside the repository")
    semgrep = shutil.which(semgrep_executable or "semgrep")
    if not semgrep:
        raise FileNotFoundError("pinned Semgrep executable is unavailable")
    snapshot, source_metadata = _candidate_snapshot(candidate_path.resolve())
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / "semgrep-transpiled-typescript.json"
    summary_path = output_dir / "semgrep-transpiled-typescript-summary.json"

    with tempfile.TemporaryDirectory(prefix="aiat-opencode-transpiled-coverage-") as temporary:
        temp_root = Path(temporary)
        source_root = temp_root / "source"
        compiled_root = temp_root / "compiled"
        _extract_exact_files(snapshot, source_root)

        npm = shutil.which("npm")
        if not npm:
            raise FileNotFoundError("npm executable is unavailable for the pinned Bun parser")
        bun_version_command = [
            npm,
            "exec",
            "--ignore-scripts",
            "--yes",
            f"--package=bun@{BUN_VERSION}",
            "--",
            "bun",
            "--version",
        ]
        bun_version_result = _run(bun_version_command, cwd=temp_root, timeout=240)
        bun_version = (bun_version_result.stdout or "").strip().splitlines()[-1:] or [""]
        if bun_version_result.returncode != 0 or bun_version[0] != BUN_VERSION:
            raise RuntimeError("pinned Bun version verification failed")

        output_paths = [source_root / relative for relative in SOURCE_PATHS]
        build_command = [
            npm,
            "exec",
            "--ignore-scripts",
            "--yes",
            f"--package=bun@{BUN_VERSION}",
            "--",
            "bun",
            "build",
            *(str(path) for path in output_paths),
            "--outdir",
            str(compiled_root),
            "--external",
            "*",
            "--target",
            "bun",
            "--sourcemap=external",
        ]
        build_result = _run(build_command, cwd=temp_root, timeout=240)
        compiled_files = sorted(compiled_root.rglob("*.js")) if compiled_root.exists() else []
        if build_result.returncode != 0 or len(compiled_files) != len(SOURCE_PATHS):
            raise RuntimeError("pinned Bun transpilation did not emit the exact expected file set")
        source_maps: dict[str, dict[str, Any]] = {}
        for compiled_file in compiled_files:
            source_map_path = compiled_file.with_suffix(compiled_file.suffix + ".map")
            if not source_map_path.is_file():
                raise RuntimeError("pinned Bun transpilation omitted an expected source map")
            try:
                source_map = json.loads(source_map_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError("pinned Bun emitted an invalid source map") from exc
            if not isinstance(source_map, dict):
                raise RuntimeError("pinned Bun emitted an invalid source map shape")
            source_maps[compiled_file.name] = source_map

        semgrep_version_result = _run([semgrep, "--version"], cwd=temp_root, timeout=30)
        semgrep_version = (semgrep_version_result.stdout or "").strip().splitlines()[-1:] or [""]
        if semgrep_version_result.returncode != 0 or semgrep_version[0] != EXPECTED_SEMGREP_VERSION:
            raise RuntimeError("Semgrep version does not match the retained candidate tool pin")

        semgrep_command = [
            semgrep,
            "--config",
            "auto",
            "--json",
            "--no-git-ignore",
            *(str(path) for path in compiled_files),
        ]
        scan_result = _run(semgrep_command, cwd=temp_root, timeout=240)
        try:
            semgrep_report = json.loads(scan_result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Semgrep returned invalid JSON for transpiled TypeScript") from exc
        if not isinstance(semgrep_report, dict):
            raise RuntimeError("Semgrep returned an invalid report shape")

        semgrep_report = _normalise_paths(
            semgrep_report,
            temp_root=temp_root,
            compiled_root=compiled_root,
        )
        # Reuse the candidate certifier's sanitizer: source snippets and
        # metavariables are redacted while scanner structure is preserved.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from opencode_candidate_certify import _sanitize_scanner_output

        safe_raw, sanitized = _sanitize_scanner_output(
            "semgrep", json.dumps(semgrep_report, sort_keys=True)
        )
        if not sanitized:
            raise RuntimeError("Semgrep evidence sanitization failed closed")
        raw_path.write_text(safe_raw, encoding="utf-8")
        summary = _summarize(
            semgrep_report,
            source_metadata=source_metadata,
            source_maps=source_maps,
            semgrep_exit_code=scan_result.returncode,
        )
        summary.update(
            {
                "raw_result_path": str(raw_path.relative_to(REPO_ROOT).as_posix()),
                "raw_result_sha256": _sha256(raw_path),
                "raw_result_bytes": raw_path.stat().st_size,
                "emitted_file_sha256": {
                    file.name: _sha256(file) for file in compiled_files
                },
                "build_invocation": [
                    "npm",
                    "exec",
                    "--ignore-scripts",
                    "--yes",
                    f"--package=bun@{BUN_VERSION}",
                    "--",
                    "bun",
                    "build",
                    *[f"<candidate-source>/{relative}" for relative in SOURCE_PATHS],
                    "--outdir",
                    "<temporary-output>",
                    "--external",
                    "*",
                    "--target",
                    "bun",
                    "--sourcemap=external",
                ],
                "semgrep_invocation": [
                    "semgrep",
                    "--config",
                    "auto",
                    "--json",
                    "--no-git-ignore",
                    *[f"<transpiled-source>/{file.name}" for file in compiled_files],
                ],
                "scanner_output_sanitized": True,
            }
        )
        summary_path.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--semgrep", help="explicit pinned Semgrep executable")
    args = parser.parse_args(argv)
    try:
        report = run_coverage(
            candidate_path=args.candidate,
            output_dir=args.output_dir,
            semgrep_executable=args.semgrep,
        )
    except (OSError, ValueError, RuntimeError, tarfile.TarError, subprocess.SubprocessError) as exc:
        report = {
            "schema_version": SCHEMA,
            "status": "blocked",
            "error_type": type(exc).__name__,
            "candidate_source_executed": False,
            "payloads_retained": False,
            "secret_values_retained": False,
        }
        print(json.dumps(report, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "status": report["status"],
                "finding_count": report["transpiled_semgrep_finding_count"],
                "scanner_error_count": report["scanner_error_count"],
                "runtime_reachable_parser_error_paths": report["runtime_reachable_parser_error_paths"],
                "raw_result_sha256": report["raw_result_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
