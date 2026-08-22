"""Reproduce a pinned OpenCode candidate security/supply-chain certification.

The historical OpenCode scan in the release register is intentionally not
used as remediation input: its source clone and raw findings were not
retained.  This command starts a new candidate boundary by resolving one
exact release tag, cloning that commit into a disposable directory, hashing a
source archive, generating an SBOM, and running the configured scanners.

Scanner output is retained in the requested evidence directory only after
secret-shaped fields and values are redacted.  The source clone is temporary;
the immutable repository/commit/archive hash is the retained provenance
reference.  A non-zero scanner exit caused by findings is not itself a scanner
execution error when structured findings were emitted.  Missing tools,
malformed output, and infrastructure errors remain separate blockers.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

SCHEMA = "aiat.opencode-candidate-certification.v1"
DEFAULT_REPOSITORY = "https://github.com/anomalyco/opencode.git"
DEFAULT_VERSION = "1.18.21"
DEFAULT_IMAGE = "ghcr.io/anomalyco/opencode:1.18.21@sha256:56c82ee8b5ead35406a83102ad1960030b7ab58dcd591e3ab5f44c2b5e0170cb"
SENSITIVE_KEY = re.compile(
    r"(?i)(?:password|secret|token|authorization|cookie|private[_-]?key|api[_-]?key|credential|raw[_-]?secret)"
)
SENSITIVE_VALUE = re.compile(
    r"(?i)(?:bearer\s+[a-z0-9._~+/=-]{12,}|gh[pousr]_[a-z0-9]{20,}|sk-[a-z0-9]{20,})"
)
SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "WARNING", "INFO", "ERROR")
TOOL_INSTALLATION_FAILURE = "TOOL_INSTALLATION_FAILURE"
SCANNER_EXECUTION_FAILURE = "SCANNER_EXECUTION_FAILURE"
SECURITY_FINDING = "SECURITY_FINDING"
SBOM_FAILURE = "SBOM_FAILURE"
AIAT_BOUNDARY_FAILURE = "AIAT_BOUNDARY_FAILURE"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _current_git_revision() -> str | None:
    try:
        result = _run(["git", "rev-parse", "HEAD"], cwd=Path.cwd(), timeout=30.0)
    except (OSError, subprocess.SubprocessError):
        return None
    revision = (result.stdout or "").strip().lower()
    return revision if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", revision) else None


def _extract_source_archive(archive_bytes: bytes, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
        members = archive.getmembers()
        for member in members:
            target = (destination / member.name).resolve()
            if target != root and root not in target.parents:
                raise RuntimeError("source archive contains an unsafe path")
        archive.extractall(destination)


def _redact(value: Any, *, key: str = "") -> Any:
    if SENSITIVE_KEY.search(key):
        # Preserve boolean/integer evidence flags such as
        # ``credentials_persisted: false`` while redacting secret values.
        if isinstance(value, (str, bytes, bytearray, dict, list, tuple)):
            return "[REDACTED]"
        return value
    if isinstance(value, dict):
        return {str(k): _redact(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item, key=key) for item in value]
    if isinstance(value, str):
        return SENSITIVE_VALUE.sub("[REDACTED]", value)
    return value


def _redacted_text(value: str) -> str:
    return SENSITIVE_VALUE.sub("[REDACTED]", value)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_redact(value), sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout: float = 600.0,
    stdout: int | Any = subprocess.PIPE,
    stderr: int | Any = subprocess.PIPE,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=stdout == subprocess.PIPE and stderr == subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )


def _resolve_tag(repository: str, version: str) -> str:
    tag = version if version.startswith("v") else f"v{version}"
    result = _run(["git", "ls-remote", repository, f"refs/tags/{tag}"])
    if result.returncode != 0:
        raise RuntimeError("git tag resolution failed")
    rows = [line.split() for line in (result.stdout or "").splitlines() if line.split()]
    for row in rows:
        if len(row) >= 2 and row[1] == f"refs/tags/{tag}":
            commit = row[0].lower()
            if re.fullmatch(r"[0-9a-f]{40}", commit):
                return commit
    raise RuntimeError(f"release tag {tag!r} was not found")


def _prepare_source(repository: str, version: str, root: Path) -> dict[str, Any]:
    commit = _resolve_tag(repository, version)
    source = root / "source"
    clone = _run(["git", "clone", "--filter=blob:none", "--no-checkout", repository, str(source)], timeout=900.0)
    if clone.returncode != 0:
        raise RuntimeError("source clone failed")
    checkout = _run(["git", "checkout", "--detach", commit], cwd=source, timeout=120.0)
    if checkout.returncode != 0:
        raise RuntimeError("exact candidate checkout failed")
    actual = (_run(["git", "rev-parse", "HEAD"], cwd=source, timeout=30.0).stdout or "").strip().lower()
    if actual != commit:
        raise RuntimeError("checked-out candidate commit did not match the resolved tag")
    try:
        archive = subprocess.run(
            ["git", "archive", "--format=tar", "HEAD"],
            cwd=source,
            capture_output=True,
            text=False,
            timeout=300.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"source archive hash failed: {type(exc).__name__}") from exc
    if archive.returncode != 0:
        raise RuntimeError("source archive hash failed")
    archive_bytes = archive.stdout or b""
    scan_source = root / "scan-source"
    _extract_source_archive(archive_bytes, scan_source)
    files = _run(["git", "ls-files", "-z"], cwd=source, timeout=60.0)
    file_count = len([item for item in (files.stdout or "").split("\0") if item])
    return {
        "repository": repository,
        "release": version.lstrip("v"),
        "tag": version if version.startswith("v") else f"v{version}",
        "commit": commit,
        "commit_url": f"{repository.removesuffix('.git')}/commit/{commit}",
        "archive_sha256": _sha256_bytes(archive_bytes),
        "file_count": file_count,
        "clone_retained": False,
        "scan_tree_git_metadata_excluded": True,
        "immutable_provenance_ref": True,
    }, scan_source


def _tool_version(name: str, output_dir: Path) -> dict[str, Any]:
    path = shutil.which(name)
    if not path:
        return {"name": name, "available": False, "version": None}
    try:
        result = _run([path, "--version"], timeout=30.0)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"name": name, "available": False, "error_type": type(exc).__name__}
    version = _redacted_text((result.stdout or result.stderr or "").strip().splitlines()[0] if (result.stdout or result.stderr) else "")
    (output_dir / f"{name}-version.txt").write_text(version + "\n", encoding="utf-8")
    return {"name": name, "available": result.returncode == 0, "version": version or None}


def _parse_json_output(path: Path) -> tuple[Any | None, str | None]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, type(exc).__name__
    return value, None


def _load_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {"status": "not_supplied", "failure_classes": []}
    value, error = _parse_json_output(path)
    if error or not isinstance(value, dict):
        return {
            "status": "blocked",
            "failure_classes": [TOOL_INSTALLATION_FAILURE],
            "error_type": "tooling_manifest_invalid",
        }
    return value


def _semgrep_summary(value: Any) -> tuple[int, Counter[str], int, str | None]:
    if not isinstance(value, dict) or not isinstance(value.get("results"), list):
        return 0, Counter(), 0, "semgrep_output_shape_invalid"
    results = value["results"]
    severities = Counter(str((item.get("extra") or {}).get("severity", "INFO")).upper() for item in results if isinstance(item, dict))
    errors = value.get("errors")
    error_count = len(errors) if isinstance(errors, list) else 0
    return len(results), severities, error_count, None


def _trufflehog_summary(path: Path) -> tuple[int, Counter[str], int, str | None]:
    findings = 0
    severities: Counter[str] = Counter()
    malformed = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        return 0, severities, 1, type(exc).__name__
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if isinstance(value, dict):
            findings += 1
            severities[str(value.get("severity") or "HIGH").upper()] += 1
    return findings, severities, malformed, "trufflehog_json_line_invalid" if malformed else None


def _generic_summary(path: Path, scanner: str) -> tuple[int, Counter[str], int, str | None]:
    value, error = _parse_json_output(path)
    if error:
        return 0, Counter(), 1, f"{scanner}_json_invalid"
    if isinstance(value, list):
        findings = len(value)
    elif isinstance(value, dict) and isinstance(value.get("findings"), list):
        findings = len(value["findings"])
    elif isinstance(value, dict) and isinstance(value.get("issues"), list):
        issue_rows = [item for item in value["issues"] if isinstance(item, dict)]
        severities = Counter(str(item.get("severity") or "HIGH").upper() for item in issue_rows)
        return len(issue_rows), severities, 0, None
    elif isinstance(value, dict) and value.get("status") in {"ok", "pass", "passed"}:
        findings = 0
    else:
        return 0, Counter(), 1, f"{scanner}_output_shape_invalid"
    return findings, Counter(), 0, None


def _run_scanner(
    scanner: str,
    command: list[str],
    *,
    source: Path,
    output_dir: Path,
) -> dict[str, Any]:
    stdout_path = output_dir / f"{scanner}.json"
    stderr_path = output_dir / f"{scanner}.stderr.log"
    executable = shutil.which(command[0])
    if executable is None:
        return {
            "name": scanner,
            "status": "blocked",
            "available": False,
            "finding_count": 0,
            "scanner_error_count": 1,
            "scanner_errors": ["executable_unavailable"],
            "failure_class": TOOL_INSTALLATION_FAILURE,
            "raw_output_retained": False,
        }
    actual_command = [executable, *command[1:]]
    try:
        result = subprocess.run(
            actual_command,
            cwd=source,
            capture_output=True,
            text=True,
            timeout=1800.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "name": scanner,
            "status": "blocked",
            "available": True,
            "finding_count": 0,
            "scanner_error_count": 1,
            "scanner_errors": [type(exc).__name__],
            "failure_class": SCANNER_EXECUTION_FAILURE,
            "raw_output_retained": False,
        }
    stdout_text = result.stdout or ""
    stderr_text = result.stderr or ""
    # Scanner output is evidence, but TruffleHog and source snippets can carry
    # secret-shaped values.  Persist the structured output only after redaction.
    stdout_path.write_text(_redacted_text(stdout_text), encoding="utf-8")
    stderr_path.write_text(_redacted_text(stderr_text), encoding="utf-8")
    value, parse_error = _parse_json_output(stdout_path) if scanner != "trufflehog" else (None, None)
    if scanner == "semgrep":
        finding_count, severities, scanner_error_count, shape_error = _semgrep_summary(value)
        scanner_errors = ([shape_error] if shape_error else [])
    elif scanner == "trufflehog":
        finding_count, severities, scanner_error_count, shape_error = _trufflehog_summary(stdout_path)
        scanner_errors = ([shape_error] if shape_error else [])
    else:
        finding_count, severities, scanner_error_count, shape_error = _generic_summary(stdout_path, scanner)
        scanner_errors = ([shape_error] if shape_error else [])
    if parse_error and scanner != "trufflehog":
        scanner_error_count += 1
        scanner_errors.append(f"{scanner}_json_invalid")
    # Semgrep/TruffleHog use non-zero exits to indicate findings.  A non-zero
    # exit with no structured findings is an execution error instead.
    if result.returncode != 0 and finding_count == 0 and scanner_error_count == 0:
        scanner_error_count = 1
        scanner_errors.append(f"exit_{result.returncode}_without_structured_findings")
    failure_class = SCANNER_EXECUTION_FAILURE if scanner_error_count else (SECURITY_FINDING if finding_count else None)
    return {
        "name": scanner,
        "status": "pass" if scanner_error_count == 0 else "blocked",
        "available": True,
        "invocation": ["<source>" if argument == str(source) else argument for argument in actual_command],
        "exit_status": result.returncode,
        "finding_count": finding_count,
        "severity_counts": dict(sorted(severities.items())),
        "scanner_error_count": scanner_error_count,
        "scanner_errors": scanner_errors,
        "failure_class": failure_class,
        "raw_json_path": str(stdout_path.name),
        "stderr_log_path": str(stderr_path.name),
        "raw_output_retained": True,
        "raw_output_sanitized": True,
        "raw_json_sha256": _sha256_file(stdout_path),
    }


def _run_sbom(source: Path, output_dir: Path) -> dict[str, Any]:
    executable = shutil.which("syft")
    path = (output_dir / "sbom.cdx.json").resolve()
    stdout_path = output_dir / "syft.stdout.log"
    stderr_path = output_dir / "syft.stderr.log"
    invocation = [executable or "syft", f"dir:{source}", f"cyclonedx-json={path}"]
    if executable is None:
        return {
            "status": "blocked",
            "available": False,
            "failure_class": SBOM_FAILURE,
            "scanner_error": "syft_unavailable",
            "invocation": invocation,
            "path": path.name,
        }
    try:
        result = subprocess.run(
            [executable, f"dir:{source}", "-o", f"cyclonedx-json={path}"],
            cwd=source,
            capture_output=True,
            text=True,
            timeout=1800.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "status": "blocked",
            "available": True,
            "failure_class": SBOM_FAILURE,
            "scanner_error": type(exc).__name__,
            "invocation": invocation,
            "path": path.name,
        }
    stdout_path.write_text(_redacted_text(result.stdout or ""), encoding="utf-8")
    stderr_path.write_text(_redacted_text(result.stderr or ""), encoding="utf-8")
    if result.returncode != 0 or not path.is_file():
        return {
            "status": "blocked",
            "available": True,
            "exit_status": result.returncode,
            "failure_class": SBOM_FAILURE,
            "scanner_error": "syft_failed_or_missing_output",
            "invocation": invocation,
            "stdout_path": stdout_path.name,
            "stderr_path": stderr_path.name,
            "path": path.name,
        }
    value, error = _parse_json_output(path)
    if error or not isinstance(value, dict):
        return {
            "status": "blocked",
            "available": True,
            "failure_class": SBOM_FAILURE,
            "scanner_error": "sbom_shape_invalid",
            "invocation": invocation,
            "stdout_path": stdout_path.name,
            "stderr_path": stderr_path.name,
            "path": path.name,
        }
    return {
        "status": "pass",
        "available": True,
        "exit_status": result.returncode,
        "version": _tool_version("syft", output_dir).get("version"),
        "invocation": invocation,
        "stdout_path": stdout_path.name,
        "stderr_path": stderr_path.name,
        "path": path.name,
        "sha256": _sha256_file(path),
        "component_count": len(value.get("components") or []) if isinstance(value.get("components"), list) else 0,
        "credentials_persisted": False,
    }


def _run_boundary(command: list[str] | None, output_dir: Path) -> dict[str, Any]:
    if not command:
        return {"status": "blocked", "reason": "AIAT boundary regression command was not supplied"}
    stdout_path = output_dir / "aiat-boundary.stdout.log"
    stderr_path = output_dir / "aiat-boundary.stderr.log"
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=1800.0, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "blocked", "reason": type(exc).__name__}
    stdout_path.write_text(_redacted_text(result.stdout or ""), encoding="utf-8")
    stderr_path.write_text(_redacted_text(result.stderr or ""), encoding="utf-8")
    return {
        "status": "pass" if result.returncode == 0 else "fail",
        "returncode": result.returncode,
        "stdout_path": stdout_path.name,
        "stderr_path": stderr_path.name,
        "logs_sanitized": True,
    }


def certify(
    *,
    repository: str,
    version: str,
    image_ref: str,
    output_dir: Path,
    boundary_command: list[str] | None = None,
    tooling_manifest_path: Path | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    blockers: list[str] = []
    source_metadata: dict[str, Any] | None = None
    scanner_rows: list[dict[str, Any]] = []
    sbom: dict[str, Any] = {"status": "not_run"}
    boundary: dict[str, Any] = {"status": "not_run"}
    tooling = _load_json(tooling_manifest_path)
    tool_versions: dict[str, Any] = {}
    aiat_candidate_commit = _current_git_revision()
    expected_aiat_candidate = os.getenv("AIAT_CANDIDATE_SHA", "").strip().lower() or None
    if aiat_candidate_commit is None:
        blockers.append("aiat_candidate_commit_unavailable")
    elif expected_aiat_candidate and aiat_candidate_commit != expected_aiat_candidate:
        blockers.append("aiat_candidate_commit_mismatch")
    with tempfile.TemporaryDirectory(prefix="aiat-opencode-candidate-") as temporary:
        try:
            source_metadata, source = _prepare_source(repository, version, Path(temporary))
        except Exception as exc:
            blockers.append(f"source_preparation:{type(exc).__name__}")
            source = None
        if source is not None:
            _write_json(output_dir / "source-manifest.json", source_metadata)
            for name in ("semgrep", "trufflehog", "skillspector", "syft"):
                tool_versions[name] = _tool_version(name, output_dir)
            scanner_rows.extend(
                [
                    _run_scanner("semgrep", ["semgrep", "--config", "auto", "--json", "--no-git-ignore", str(source)], source=source, output_dir=output_dir),
                    _run_scanner("trufflehog", ["trufflehog", "filesystem", "--json", "--no-update", str(source)], source=source, output_dir=output_dir),
                    _run_scanner("skillspector", ["skillspector", "scan", str(source), "--no-llm", "--format", "json"], source=source, output_dir=output_dir),
                ]
            )
            sbom = _run_sbom(source, output_dir)
            boundary = _run_boundary(boundary_command, output_dir)
        else:
            blockers.append("source_not_available")

    if not re.search(r"@sha256:[0-9a-fA-F]{64}$", image_ref):
        blockers.append("candidate image reference is not digest pinned")
    image_digest = image_ref.rsplit("@sha256:", 1)[1] if "@sha256:" in image_ref else None
    scanner_errors = sum(int(row.get("scanner_error_count") or 0) for row in scanner_rows)
    findings = sum(int(row.get("finding_count") or 0) for row in scanner_rows)
    severity_counts: Counter[str] = Counter()
    for row in scanner_rows:
        severity_counts.update({str(k).upper(): int(v) for k, v in (row.get("severity_counts") or {}).items()})
    if any(row.get("status") != "pass" for row in scanner_rows):
        blockers.append("one_or_more_scanners_unavailable_or_failed")
    if sbom.get("status") != "pass":
        blockers.append("sbom_not_generated")
    if tooling.get("status") == "blocked":
        blockers.append("tool_provisioning_failed")
    if boundary.get("status") != "pass":
        blockers.append("aiat_boundary_regression_not_passed")
    failure_classes = {
        str(row["failure_class"])
        for row in scanner_rows
        if row.get("failure_class")
    }
    failure_classes.update(
        str(item)
        for item in tooling.get("failure_classes", [])
        if item
    )
    if sbom.get("failure_class"):
        failure_classes.add(str(sbom["failure_class"]))
    if boundary.get("status") != "pass":
        failure_classes.add(AIAT_BOUNDARY_FAILURE)
    if findings:
        failure_classes.add(SECURITY_FINDING)
    if blockers:
        decision = "blocked"
    elif findings:
        decision = "findings_review_required"
    else:
        decision = "passed"
    report = {
        "schema_version": SCHEMA,
        "programme_scope": "personal-internal-only",
        "status": decision,
        "aiat_candidate_commit": aiat_candidate_commit,
        "upstream_repository": repository,
        "candidate_version": version.lstrip("v"),
        "candidate_tag": version if version.startswith("v") else f"v{version}",
        "candidate_commit": (source_metadata or {}).get("commit"),
        "candidate_image_ref": image_ref,
        "candidate_image_digest": image_digest,
        "source": source_metadata or {"status": "not_prepared", "clone_retained": False},
        "scanners": scanner_rows,
        "tool_versions": tool_versions,
        "tooling_provisioning": tooling,
        "scanner_errors": scanner_errors,
        "raw_findings_count": findings,
        "upstream_findings": findings,
        "security_findings_interpretable": not any(row.get("status") != "pass" for row in scanner_rows),
        "aiat_local_findings": 0 if boundary.get("status") == "pass" else None,
        "findings_by_severity": dict(sorted(severity_counts.items())),
        "sbom": sbom,
        "aiat_local_boundary": boundary,
        "failure_classes": sorted(failure_classes),
        "remediation_required": bool(findings or scanner_errors or blockers),
        "fork_required": False,
        "active_worker_status": "inactive_until_certification_passes",
        "blockers": sorted(set(blockers)),
        "evidence_policy": {
            "source_clone_retained": False,
            "immutable_provenance_retained": bool(source_metadata),
            "raw_scanner_outputs_retained_sanitized": bool(scanner_rows)
            and all(row.get("raw_output_retained") is True for row in scanner_rows),
            "credentials_persisted": False,
            "payloads_persisted": False,
            "licence_metadata_is_gate": False,
        },
        "scope": "fresh pinned OpenCode source/SBOM/scanner candidate certification; no activation or release decision",
    }
    _write_json(output_dir / "candidate-certification.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.getenv("OPENCODE_CANDIDATE_REPOSITORY", DEFAULT_REPOSITORY))
    parser.add_argument("--version", default=os.getenv("OPENCODE_CANDIDATE_VERSION", DEFAULT_VERSION))
    parser.add_argument("--image-ref", default=os.getenv("OPENCODE_CANDIDATE_IMAGE_REF", DEFAULT_IMAGE))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--boundary-command",
        help="quoted command for AIAT wrapper/grant/sandbox regression tests",
    )
    parser.add_argument(
        "--tooling-manifest",
        type=Path,
        help="structured provisioning result produced before scanning",
    )
    args = parser.parse_args(argv)
    boundary = shlex.split(args.boundary_command) if args.boundary_command else None
    report = certify(
        repository=args.repository,
        version=args.version,
        image_ref=args.image_ref,
        output_dir=args.output,
        boundary_command=boundary,
        tooling_manifest_path=args.tooling_manifest,
    )
    print(json.dumps({key: report[key] for key in ("status", "candidate_version", "candidate_commit", "scanner_errors", "raw_findings_count", "findings_by_severity", "blockers")}, sort_keys=True, indent=2))
    return 0 if report["status"] == "passed" else 2 if report["status"] == "blocked" else 1


if __name__ == "__main__":
    raise SystemExit(main())
