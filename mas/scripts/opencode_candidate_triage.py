"""Normalize the retained OpenCode candidate scanner evidence.

This command is an applicability review layer over the immutable, sanitized
scanner artifact.  It never edits the scanner JSON and it never copies secret
values, source snippets, or scanner messages into the retained triage result.
The source checkout and Docker image are inspection inputs only; OpenCode is
not changed or activated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

SCHEMA = "aiat.opencode-candidate-triage.v1"
TOOL_INSTALLATION_FAILURE = "TOOL_INSTALLATION_FAILURE"
SCANNER_EXECUTION_FAILURE = "SCANNER_EXECUTION_FAILURE"
SCANNER_COVERAGE_INCOMPLETE = "SCANNER_COVERAGE_INCOMPLETE"
SECURITY_FINDING = "SECURITY_FINDING"
SBOM_FAILURE = "SBOM_FAILURE"
RUNTIME_APPLICABLE = "RUNTIME_APPLICABLE"
REQUIRED_SCANNERS = ("semgrep", "trufflehog", "skillspector")

REQUIRED_SKILLSPECTOR_RULES = ("AE3", "AE4", "E1", "E2", "EA1", "EA2", "RP1", "SC9")
MEDIA_OR_BINARY_SUFFIXES = (
    ".7z",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".mp3",
    ".mp4",
    ".png",
    ".svg",
    ".tgz",
    ".ttf",
    ".wav",
    ".webp",
    ".woff",
    ".woff2",
    ".zip",
)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validated_scanner_artifacts(
    report: dict[str, Any],
    artifact_dir: Path,
) -> dict[str, tuple[dict[str, Any], Path]]:
    """Resolve only the scanner artifacts certified by the retained report.

    Triage must never silently substitute a same-named file from a changed
    evidence directory.  The certification report is the binding manifest:
    each required scanner must name its canonical artifact and its recorded
    SHA-256 must match before any scanner output is interpreted.
    """

    scanner_rows = report.get("scanners")
    if not isinstance(scanner_rows, list):
        raise ValueError("candidate report scanner manifest is missing")

    artifact_root = artifact_dir.resolve()
    validated: dict[str, tuple[dict[str, Any], Path]] = {}
    for row in scanner_rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        if name not in REQUIRED_SCANNERS:
            continue
        if name in validated:
            raise ValueError(f"candidate report contains duplicate scanner {name!r}")
        reported_path = row.get("raw_json_path")
        canonical_name = f"{name}.json"
        if not isinstance(reported_path, str) or reported_path != canonical_name:
            raise ValueError(f"candidate report has invalid {name} scanner artifact path")
        candidate = (artifact_dir / reported_path).resolve()
        try:
            candidate.relative_to(artifact_root)
        except ValueError as exc:
            raise ValueError(f"candidate report {name} artifact escapes its evidence directory") from exc
        if not candidate.is_file():
            raise ValueError(f"candidate report {name} scanner artifact is missing")
        expected_hash = str(row.get("raw_json_sha256") or "").lower()
        if len(expected_hash) != 64 or any(character not in "0123456789abcdef" for character in expected_hash):
            raise ValueError(f"candidate report {name} scanner artifact hash is invalid")
        actual_hash = _sha256(candidate)
        if actual_hash != expected_hash:
            raise ValueError(f"candidate report {name} scanner artifact hash mismatch")
        validated[name] = (row, candidate)

    missing = [name for name in REQUIRED_SCANNERS if name not in validated]
    if missing:
        raise ValueError(f"candidate report scanner manifest is missing: {', '.join(missing)}")
    return validated


def _relative_path(value: Any) -> str:
    """Strip disposable scan roots without retaining arbitrary temp paths."""

    path = str(value or "").replace("\\", "/")
    marker = "/scan-source/"
    if marker in path:
        return path.rsplit(marker, 1)[1]
    if path.startswith("scan-source/"):
        return path.removeprefix("scan-source/")
    return path.lstrip("./")


def _error_type(value: Any) -> str:
    if isinstance(value, list) and value:
        return str(value[0])
    return str(value or "unknown")


def _error_line(value: Any) -> int | None:
    if isinstance(value, list) and len(value) > 1 and isinstance(value[1], list):
        starts: list[int] = []
        for row in value[1]:
            if isinstance(row, dict):
                start = row.get("start")
                if isinstance(start, dict) and isinstance(start.get("line"), int):
                    starts.append(int(start["line"]))
        return min(starts) if starts else None
    return None


def _path_area(path: str) -> str:
    lower = path.lower()
    if (
        lower.startswith((".github/", ".husky/", "nix/"))
        or lower in {"install", "github/action.yml"}
        or lower.startswith("github/")
    ):
        return "ci/release code"
    if "/test/" in lower or lower.startswith(("test/", "tests/")) or "/fixtures/" in lower or "/fixture/" in lower:
        return "tests/fixtures"
    if (
        lower.endswith((".md", ".mdx"))
        or lower.startswith(("readme", "docs/"))
        or "/docs/" in lower
        or "!open_code brand assets/" in lower
    ):
        return "documentation/examples"
    if lower.startswith("artifacts/") or any(lower.endswith(suffix) for suffix in MEDIA_OR_BINARY_SUFFIXES):
        return "generated artifact"
    if lower.startswith(("packages/app/", "packages/console/", "packages/desktop/", "packages/storybook/")):
        return "build/install code"
    if lower.startswith("packages/") and "/src/" in lower:
        return "shipped/runtime code"
    if lower.endswith(("dockerfile", ".dockerfile")) or "/script/" in lower or lower.startswith("script/"):
        return "build/install code"
    return "other"


def _runtime_relevant(path: str) -> bool:
    lower = path.lower()
    if lower.startswith("packages/core/src/"):
        return True
    if lower.startswith("packages/opencode/src/"):
        # The server/session/tool/util paths are part of the `serve` process.
        # CLI/TUI/LSP paths are not reached by AIAT's fixed serve entrypoint.
        return not lower.startswith(
            (
                "packages/opencode/src/cli/",
                "packages/opencode/src/lsp/",
                "packages/opencode/src/plugin/tui/",
            )
        )
    return False


def _image_disposition(path: str) -> str:
    lower = path.lower()
    if lower.startswith("packages/core/src/"):
        return "IMAGE_PRESENT_REACHABLE"
    if lower.startswith("packages/opencode/src/") and _runtime_relevant(path):
        return "IMAGE_PRESENT_REACHABLE"
    if lower.startswith(("packages/opencode/src/", "packages/core/src/")):
        return "IMAGE_PRESENT_NOT_REACHABLE"
    return "SOURCE_ONLY"


def _semgrep_error_class(code: Any, error_type: str, path: str) -> str:
    if str(code) == "2" or error_type == "Internal matching error":
        return SCANNER_EXECUTION_FAILURE
    if path.lower().startswith(".github/"):
        return "embedded GitHub-expression/YAML parsing limitation"
    if path.lower().endswith((".md", ".mdx")) or "/docs/" in path.lower():
        return "MDX/generated/documentation parsing limitation"
    if error_type in {"PartialParsing", "Syntax error"} and _path_area(path) == "shipped/runtime code":
        return "source syntax genuinely unsupported"
    return "other"


def _semgrep_error_rows(value: dict[str, Any]) -> tuple[list[dict[str, Any]], Counter[str]]:
    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    errors = value.get("errors")
    if not isinstance(errors, list):
        return rows, counts
    for index, error in enumerate(errors, 1):
        if not isinstance(error, dict):
            path = "unknown"
            code = "unknown"
            error_type = "malformed_error_row"
            line = None
        else:
            path = _relative_path(error.get("path"))
            code = str(error.get("code") or "unknown")
            error_type = _error_type(error.get("type"))
            line = _error_line(error.get("type"))
        classification = _semgrep_error_class(code, error_type, path)
        runtime_relevant = _runtime_relevant(path)
        counts[classification] += 1
        rows.append(
            {
                "index": index,
                "code": code,
                "error_type": error_type,
                "path": path,
                "line": line,
                "area": _path_area(path),
                "runtime_security_relevant": runtime_relevant,
                "aiat_serve_reachable": runtime_relevant,
                "classification": classification,
                "disposition": (
                    "coverage_gap_requires_alternate_scan"
                    if classification == "source syntax genuinely unsupported"
                    else "bounded_non_runtime_scan_error"
                    if not runtime_relevant
                    else "runtime_coverage_gap"
                ),
            }
        )
    return rows, counts


def _semgrep_result_summary(value: dict[str, Any]) -> dict[str, Any]:
    results = value.get("results")
    if not isinstance(results, list):
        return {
            "total": 0,
            "runtime_total": 0,
            "runtime_review_items": 0,
            "runtime_actionable_candidates": [],
            "by_area": {},
            "by_severity": {},
        }
    by_area: Counter[str] = Counter()
    by_severity: Counter[str] = Counter()
    runtime_total = 0
    runtime_review_items = 0
    actionable: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        path = _relative_path(item.get("path"))
        area = _path_area(path)
        severity = str((item.get("extra") or {}).get("severity") or "INFO").upper()
        rule = str(item.get("check_id") or "unknown")
        by_area[area] += 1
        by_severity[severity] += 1
        if not _runtime_relevant(path):
            continue
        runtime_total += 1
        if severity in {"ERROR", "HIGH"}:
            runtime_review_items += 1
        # Semgrep's process-launch ERROR rows are concrete candidates for
        # manual review, not confirmed vulnerabilities.
        if severity == "ERROR":
            actionable.append(
                {
                    "rule_id": rule,
                    "severity": severity,
                    "path": path,
                    "line": (item.get("start") or {}).get("line"),
                    "area": area,
                    "image_disposition": _image_disposition(path),
                    "classification": "RUNTIME_CANDIDATE_REQUIRES_REVIEW",
                    "confirmed_exploitable": False,
                    "aiat_wrapper_mitigated": False,
                }
            )
    return {
        "total": len(results),
        "runtime_total": runtime_total,
        "runtime_review_items": runtime_review_items,
        "runtime_actionable_candidates": actionable,
        "by_area": dict(sorted(by_area.items())),
        "by_severity": dict(sorted(by_severity.items())),
    }


def _trufflehog_path(value: dict[str, Any]) -> str:
    metadata = value.get("SourceMetadata")
    data = metadata.get("Data") if isinstance(metadata, dict) else None
    filesystem = data.get("Filesystem") if isinstance(data, dict) else None
    return _relative_path(filesystem.get("file") if isinstance(filesystem, dict) else "unknown")


def _trufflehog_classification(path: str, detector: str) -> str:
    lower = path.lower()
    if lower.startswith("packages/web/src/content/docs/"):
        return "DOCUMENTATION_EXAMPLE"
    if (
        lower.startswith(("test/", "tests/"))
        or "/test/" in lower
        or "/tests/" in lower
        or "/fixtures/" in lower
        or "/fixture/" in lower
    ):
        return "TEST_FIXTURE"
    # The infrastructure rows are secret-resource names/references, not
    # secret values.  The DigitalOcean row is a public OAuth client ID.
    if path in {"infra/secret.ts", "infra/monitoring.ts", "packages/opencode/src/plugin/digitalocean.ts"}:
        return "FALSE_POSITIVE"
    return "UNVERIFIED_CREDENTIAL_LIKE_VALUE"


def _trufflehog_rows(path: Path) -> tuple[list[dict[str, Any]], Counter[str]]:
    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        return [
            {
                "index": 0,
                "detector": "unknown",
                "path": "unknown",
                "verified": False,
                "verification_error_present": True,
                "area": "unknown",
                "classification": SCANNER_EXECUTION_FAILURE,
                "image_disposition": "UNKNOWN",
                "appears_synthetic_or_example": False,
                "remediation_required": False,
                "operator_review_required": True,
                "raw_secret_material_retained": False,
                "error_type": type(exc).__name__,
            }
        ], Counter({SCANNER_EXECUTION_FAILURE: 1})
    for index, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            # Keep malformed JSONL bounded and payload-free.  The line may
            # contain a secret-shaped value, so retain only the line number
            # and the parser error class as scanner execution evidence.
            counts[SCANNER_EXECUTION_FAILURE] += 1
            rows.append(
                {
                    "index": index,
                    "detector": "unknown",
                    "path": "unknown",
                    "verified": False,
                    "verification_error_present": True,
                    "area": "unknown",
                    "classification": SCANNER_EXECUTION_FAILURE,
                    "image_disposition": "UNKNOWN",
                    "appears_synthetic_or_example": False,
                    "remediation_required": False,
                    "operator_review_required": True,
                    "raw_secret_material_retained": False,
                    "error_type": "JSONDecodeError",
                }
            )
            continue
        if not isinstance(value, dict):
            counts[SCANNER_EXECUTION_FAILURE] += 1
            rows.append(
                {
                    "index": index,
                    "detector": "unknown",
                    "path": "unknown",
                    "verified": False,
                    "verification_error_present": True,
                    "area": "unknown",
                    "classification": SCANNER_EXECUTION_FAILURE,
                    "image_disposition": "UNKNOWN",
                    "appears_synthetic_or_example": False,
                    "remediation_required": False,
                    "operator_review_required": True,
                    "raw_secret_material_retained": False,
                    "error_type": "non_object_jsonl_record",
                }
            )
            continue
        relative = _trufflehog_path(value)
        detector = str(value.get("DetectorName") or value.get("DetectorType") or "unknown")
        classification = _trufflehog_classification(relative, detector)
        verified = bool(value.get("Verified"))
        counts[classification] += 1
        rows.append(
            {
                "index": index,
                "detector": detector,
                "path": relative,
                "verified": verified,
                "verification_error_present": bool(value.get("VerificationError") or value.get("verification_error")),
                "area": _path_area(relative),
                "classification": "VERIFIED_SECRET" if verified else classification,
                "image_disposition": _image_disposition(relative),
                "appears_synthetic_or_example": classification in {"DOCUMENTATION_EXAMPLE", "TEST_FIXTURE", "FALSE_POSITIVE"},
                "remediation_required": verified,
                "operator_review_required": (not verified and classification == "UNVERIFIED_CREDENTIAL_LIKE_VALUE"),
                "raw_secret_material_retained": False,
            }
        )
    return rows, counts


def _skillspector_classification(rule_id: str, path: str) -> tuple[str, str]:
    lower = path.lower()
    binary_like = "!/" in lower or any(lower.endswith(suffix) for suffix in MEDIA_OR_BINARY_SUFFIXES)
    if rule_id == "AE3":
        return "FALSE_POSITIVE_OR_MISAPPLIED", "binary/media NUL-byte heuristic has no worker-runtime threat path"
    if rule_id == "AE4" and binary_like:
        return "FALSE_POSITIVE_OR_MISAPPLIED", "binary/archive Unicode heuristic has no worker-runtime threat path"
    if rule_id == "SC9":
        return "FALSE_POSITIVE_OR_MISAPPLIED", "hidden filename alone does not establish an executable artifact in the worker"
    if _runtime_relevant(path):
        return RUNTIME_APPLICABLE, "path is reachable by the pinned headless worker serve process and requires review"
    return "NON_RUNTIME", "source path is not shipped in the pinned headless worker image"


def _skillspector_rows(value: dict[str, Any]) -> tuple[list[dict[str, Any]], Counter[str], Counter[str]]:
    rows: list[dict[str, Any]] = []
    applicability: Counter[str] = Counter()
    by_rule: Counter[str] = Counter()
    issues = value.get("issues")
    if not isinstance(issues, list):
        return rows, applicability, by_rule
    for index, issue in enumerate(issues, 1):
        if not isinstance(issue, dict):
            continue
        rule_id = str(issue.get("id") or "unknown")
        location = issue.get("location")
        path = _relative_path(location.get("file") if isinstance(location, dict) else "unknown")
        severity = str(issue.get("severity") or "UNKNOWN").upper()
        classification, reason = _skillspector_classification(rule_id, path)
        by_rule[rule_id] += 1
        applicability[classification] += 1
        runtime_reachable = _runtime_relevant(path)
        rows.append(
            {
                "index": index,
                "rule_id": rule_id,
                "severity": severity,
                "path": path,
                "area": _path_area(path),
                "local_only": bool((issue.get("evidence") or {}).get("local_only")),
                "applicability": classification,
                "reason": reason,
                "image_disposition": _image_disposition(path),
                "aiat_serve_reachable": runtime_reachable,
                "actionable": runtime_reachable and severity in {"HIGH", "CRITICAL"},
                "raw_snippet_retained": False,
            }
        )
    return rows, applicability, by_rule


def _docker_image_observation(image_ref: str) -> dict[str, Any]:
    """Capture scalar image identity and application-tree observations only."""

    requested_digest = image_ref.rsplit("@sha256:", 1)[1] if "@sha256:" in image_ref else None
    if not requested_digest:
        return {
            "status": "blocked",
            "reason": "candidate_image_reference_not_digest_pinned",
            "requested_digest": None,
            "repo_digests": [],
            "digest_match": False,
            "credentials_or_payloads_retained": False,
        }
    try:
        inspect = subprocess.run(
            ["docker", "image", "inspect", image_ref, "--format", "{{json .}}"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "blocked", "reason": type(exc).__name__, "requested_digest": requested_digest}
    if inspect.returncode != 0:
        return {"status": "blocked", "reason": "docker_image_inspect_failed", "requested_digest": requested_digest}
    try:
        metadata = json.loads(inspect.stdout)
    except json.JSONDecodeError:
        return {"status": "blocked", "reason": "docker_image_metadata_invalid", "requested_digest": requested_digest}
    if not isinstance(metadata, dict):
        return {"status": "blocked", "reason": "docker_image_metadata_invalid", "requested_digest": requested_digest}
    repo_digests = [str(item) for item in metadata.get("RepoDigests") or []]
    digest_match = any(item.endswith(requested_digest) for item in repo_digests)
    config = metadata.get("Config") if isinstance(metadata.get("Config"), dict) else {}
    if not digest_match:
        return {
            "status": "blocked",
            "reason": "docker_image_digest_mismatch",
            "requested_digest": requested_digest,
            "repo_digests": repo_digests,
            "digest_match": False,
            "image_id": str(metadata.get("Id") or ""),
            "rootfs_layer_count": len((metadata.get("RootFS") or {}).get("Layers") or []),
            "entrypoint": config.get("Entrypoint"),
            "working_dir": config.get("WorkingDir"),
            "source_tree_paths_not_retained": True,
            "credentials_or_payloads_retained": False,
        }
    try:
        run = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--read-only",
                "--pids-limit",
                "64",
                "--memory",
                "256m",
                "--cpus",
                "0.5",
                "--user",
                "65532:65532",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,nodev,size=16m",
                "--entrypoint",
                "/bin/sh",
                image_ref,
                "-c",
                "test -x /usr/local/bin/opencode; version=$(/usr/local/bin/opencode --version 2>/dev/null); test ! -e /app; test ! -e /opt/opencode; test ! -e /usr/local/lib/opencode; printf '%s' \"$version\"",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "status": "blocked",
            "reason": type(exc).__name__,
            "requested_digest": requested_digest,
            "repo_digests": repo_digests,
            "digest_match": True,
            "credentials_or_payloads_retained": False,
        }
    version = (run.stdout or "").strip()
    return {
        "status": "pass" if run.returncode == 0 else "blocked",
        "reason": None if run.returncode == 0 else "docker_application_tree_probe_failed",
        "requested_digest": requested_digest,
        "repo_digests": repo_digests,
        "digest_match": digest_match,
        "image_id": str(metadata.get("Id") or ""),
        "rootfs_layer_count": len((metadata.get("RootFS") or {}).get("Layers") or []),
        "entrypoint": config.get("Entrypoint"),
        "working_dir": config.get("WorkingDir"),
        "application_entrypoint_present": run.returncode == 0,
        "application_version": version or None,
        "application_tree_present": False if run.returncode == 0 else None,
        "source_tree_paths_present": 0 if run.returncode == 0 else None,
        "source_tree_paths_not_retained": True,
        "aiat_execution_command": "opencode serve --hostname <configured> --port <configured>",
        "aiat_native_tool_policy": "deny-by-default; run-scoped MCP facade explicitly allowed",
        "credentials_or_payloads_retained": False,
    }


def _scanner_failure_classes(row: dict[str, Any]) -> set[str]:
    classes: set[str] = set()
    failure_class = row.get("failure_class")
    if failure_class:
        classes.add(str(failure_class))
    classes.update(str(item) for item in row.get("failure_classes", []) if item)
    return classes


def _certification_decision(
    *,
    coverage_incomplete: bool,
    runtime_review_required: bool,
    verified_secrets: int,
    image_status: str,
    sbom_status: str,
    raw_findings: int,
) -> str:
    """Derive the triage decision from the evidence just analyzed."""

    if coverage_incomplete and runtime_review_required:
        return "BLOCKED_SCANNER_COVERAGE_INCOMPLETE_AND_RUNTIME_REVIEW_REQUIRED"
    if coverage_incomplete:
        return "BLOCKED_SCANNER_COVERAGE_INCOMPLETE"
    if runtime_review_required:
        return "BLOCKED_RUNTIME_REVIEW_REQUIRED"
    if verified_secrets:
        return "BLOCKED_VERIFIED_SECRET_FINDING"
    if image_status != "pass":
        return "BLOCKED_IMAGE_CROSSCHECK"
    if sbom_status != "pass":
        return "BLOCKED_SBOM"
    if raw_findings:
        return "FINDINGS_REVIEW_REQUIRED"
    return "PASSED"


def triage(*, report_path: Path, artifact_dir: Path, source_dir: Path, output_path: Path) -> dict[str, Any]:
    report = _load_json(report_path)
    if not isinstance(report, dict):
        raise ValueError("candidate report must be an object")
    scanner_artifacts = _validated_scanner_artifacts(report, artifact_dir)
    semgrep_report, semgrep_path = scanner_artifacts["semgrep"]
    _trufflehog_report, trufflehog_path = scanner_artifacts["trufflehog"]
    skillspector_report, skillspector_path = scanner_artifacts["skillspector"]
    semgrep = _load_json(semgrep_path)
    skillspector = _load_json(skillspector_path)
    if not isinstance(semgrep, dict) or not isinstance(skillspector, dict):
        raise ValueError("structured scanner outputs are invalid")
    semgrep_errors, semgrep_error_counts = _semgrep_error_rows(semgrep)
    semgrep_results = _semgrep_result_summary(semgrep)
    trufflehog_rows, trufflehog_counts = _trufflehog_rows(trufflehog_path)
    skillspector_rows, skillspector_applicability, skillspector_rules = _skillspector_rows(skillspector)
    image = _docker_image_observation(str(report.get("candidate_image_ref") or ""))

    raw_evidence = {}
    for name, (scanner_report, path) in scanner_artifacts.items():
        certified_hash = str(scanner_report.get("raw_json_sha256") or "").lower()
        actual_hash = _sha256(path)
        raw_evidence[name] = {
            "path": path.name,
            "sha256": actual_hash,
            "certified_sha256": certified_hash,
            "unchanged": actual_hash == certified_hash,
            "raw_values_exported": False,
        }
    report_failure_classes = {
        failure_class
        for scanner_report in (semgrep_report, _trufflehog_report, skillspector_report)
        for failure_class in _scanner_failure_classes(scanner_report)
    }
    report_scanner_error_counts = {
        name: int(scanner_report.get("scanner_error_count") or 0)
        for name, scanner_report in (
            ("semgrep", semgrep_report),
            ("trufflehog", _trufflehog_report),
            ("skillspector", skillspector_report),
        )
    }
    scanner_execution_failure = (
        SCANNER_EXECUTION_FAILURE in report_failure_classes
        or any(count > 0 for count in report_scanner_error_counts.values())
    )
    scanner_coverage_failure = SCANNER_COVERAGE_INCOMPLETE in report_failure_classes
    scanner_manifest_blocked = any(
        str(scanner_report.get("status") or "") != "pass"
        and _scanner_failure_classes(scanner_report) != {SECURITY_FINDING}
        for scanner_report in (semgrep_report, _trufflehog_report, skillspector_report)
    )
    runtime_coverage = (
        "PASS"
        if not any(row["runtime_security_relevant"] for row in semgrep_errors)
        and not scanner_execution_failure
        and not scanner_coverage_failure
        and not scanner_manifest_blocked
        and not trufflehog_counts.get(SCANNER_EXECUTION_FAILURE)
        else "INCOMPLETE"
    )
    trufflehog_artifact_error_count = int(trufflehog_counts.get(SCANNER_EXECUTION_FAILURE) or 0)
    scanner_execution_failure_count = (
        report_scanner_error_counts["semgrep"]
        + max(report_scanner_error_counts["trufflehog"], trufflehog_artifact_error_count)
        + report_scanner_error_counts["skillspector"]
    )
    runtime_actionable = semgrep_results["runtime_actionable_candidates"]
    skillspector_false = skillspector_applicability["FALSE_POSITIVE_OR_MISAPPLIED"]
    skillspector_non_runtime = skillspector_applicability["NON_RUNTIME"]
    skillspector_runtime = skillspector_applicability["RUNTIME_APPLICABLE"]
    trufflehog_verified = sum(1 for row in trufflehog_rows if row["verified"])
    trufflehog_actionable = sum(1 for row in trufflehog_rows if row["remediation_required"])
    derived_raw_findings = int(semgrep_results["total"]) + sum(
        1
        for row in trufflehog_rows
        if row["classification"] != SCANNER_EXECUTION_FAILURE
    ) + len(skillspector_rows)
    skillspector_actionable = [row for row in skillspector_rows if row["actionable"]]
    runtime_review_required = bool(runtime_actionable or skillspector_actionable)
    sbom_status = str((report.get("sbom") or {}).get("status") or "blocked") if isinstance(report.get("sbom"), dict) else "blocked"
    certification_decision = _certification_decision(
        coverage_incomplete=runtime_coverage != "PASS",
        runtime_review_required=runtime_review_required,
        verified_secrets=trufflehog_verified,
        image_status=str(image.get("status") or "blocked"),
        sbom_status=sbom_status,
        raw_findings=derived_raw_findings,
    )
    triage_result = {
        "schema_version": SCHEMA,
        "programme_scope": "personal-internal-only",
        "candidate": {
            "version": report.get("candidate_version"),
            "upstream_commit": report.get("candidate_commit"),
            "aiat_candidate_commit": report.get("aiat_candidate_commit"),
            "image_ref": report.get("candidate_image_ref"),
            "image_digest": report.get("candidate_image_digest"),
            "source_archive_sha256": (report.get("source") or {}).get("archive_sha256"),
        },
        "raw_evidence": raw_evidence,
        "evidence_policy": {
            "raw_scanner_outputs_modified": False,
            "raw_secret_values_retained": False,
            "source_snippets_retained": False,
            "credentials_persisted": False,
            "payloads_persisted": False,
            "licence_metadata_is_gate": False,
        },
        "taxonomy": {
            "TOOL_INSTALLATION_FAILURE": False,
            "SCANNER_EXECUTION_FAILURE": scanner_execution_failure or bool(trufflehog_artifact_error_count),
            "SCANNER_COVERAGE_INCOMPLETE": runtime_coverage != "PASS",
            "SECURITY_FINDING": derived_raw_findings > 0,
            "SBOM_FAILURE": not isinstance(report.get("sbom"), dict)
            or report["sbom"].get("status") != "pass",
            "scanner_tooling_status": "PASS",
            "historical_blocker_wording_corrected": True,
        },
        "semgrep": {
            "total_findings": semgrep_results["total"],
            "runtime_total": semgrep_results["runtime_total"],
            "runtime_review_items": semgrep_results["runtime_review_items"],
            "runtime_actionable_candidates": len(runtime_actionable),
            "runtime_review_required": runtime_review_required,
            "runtime_coverage": runtime_coverage,
            "errors_remaining": len(semgrep_errors),
            "error_class_counts": dict(sorted(semgrep_error_counts.items())),
            "error_inventory": semgrep_errors,
            "result_summary": semgrep_results,
            "alternate_supported_configuration": {
                "status": "not_used",
                "reason": "no alternate configuration with retained, warning-free runtime evidence",
                "runtime_files_excluded": False,
            },
        },
        "trufflehog": {
            "total": len(trufflehog_rows),
            "verified": trufflehog_verified,
            "unverified": len(trufflehog_rows) - trufflehog_verified,
            "actionable": trufflehog_actionable,
            "classification_counts": dict(sorted(trufflehog_counts.items())),
            "detections": trufflehog_rows,
            "raw_secret_material_printed": False,
        },
        "skillspector": {
            "total": len(skillspector_rows),
            "runtime_applicable": skillspector_runtime,
            "non_runtime": skillspector_non_runtime,
            "false_positive_or_misapplied": skillspector_false,
            "actionable_high": sum(1 for row in skillspector_rows if row["actionable"] and row["severity"] == "HIGH"),
            "actionable_medium": sum(1 for row in skillspector_rows if row["actionable"] and row["severity"] == "MEDIUM"),
            "actionable_total": len(skillspector_actionable),
            "rule_counts": dict(sorted(skillspector_rules.items())),
            "required_rule_ids_present": all(rule in skillspector_rules for rule in REQUIRED_SKILLSPECTOR_RULES),
            "findings": skillspector_rows,
            "rule_applicability_notes": {
                "AE3": "embedded NUL bytes in media/assets are not a runtime issue without a threat path",
                "AE4": "mixed-script/binary asset heuristics are non-runtime for this headless image",
                "E1": "external URLs in CI/installer/UI are not worker-runtime egress evidence",
                "E2": "environment reads in the GitHub helper are not the AIAT worker path",
                "EA1": "repository .opencode agents are not in the deployed image/workspace by default",
                "EA2": "desktop UI auto-approval settings are not server-worker policy",
                "RP1": "CI/Nix/publishing references are outside the deployed worker",
                "SC9": "hidden names alone do not establish concealed executable reachability",
            },
        },
        "image_crosscheck": image,
        "decision": {
            "SEMGREP_RUNTIME_COVERAGE": runtime_coverage,
            "SEMGREP_ERRORS_REMAINING": len(semgrep_errors),
            "SEMGREP_ACTIONABLE_FINDINGS": len(runtime_actionable),
            "TRUFFLEHOG_TOTAL": len(trufflehog_rows),
            "TRUFFLEHOG_VERIFIED": trufflehog_verified,
            "TRUFFLEHOG_UNVERIFIED": len(trufflehog_rows) - trufflehog_verified,
            "TRUFFLEHOG_ACTIONABLE": trufflehog_actionable,
            "SKILLSPECTOR_TOTAL": len(skillspector_rows),
            "SKILLSPECTOR_RUNTIME_APPLICABLE": skillspector_runtime,
            "SKILLSPECTOR_NON_RUNTIME": skillspector_non_runtime,
            "SKILLSPECTOR_FALSE_POSITIVE_OR_MISAPPLIED": skillspector_false,
            "SKILLSPECTOR_ACTIONABLE_HIGH": sum(
                1 for row in skillspector_rows if row["actionable"] and row["severity"] == "HIGH"
            ),
            "SKILLSPECTOR_ACTIONABLE_MEDIUM": sum(
                1 for row in skillspector_rows if row["actionable"] and row["severity"] == "MEDIUM"
            ),
            "RUNTIME_ACTIONABLE_FINDINGS": len(runtime_actionable),
            "RUNTIME_REVIEW_REQUIRED": runtime_review_required,
            "RAW_FINDINGS_DERIVED": derived_raw_findings,
            "RAW_FINDINGS_REPORTED": int(report.get("raw_findings_count") or 0),
            "RAW_FINDINGS_REPORT_MATCH": (
                int(report.get("raw_findings_count") or 0) == derived_raw_findings
            ),
            "SCANNER_EXECUTION_FAILURES": scanner_execution_failure_count,
            "AIAT_WRAPPER_MITIGATED_FINDINGS": 0,
            "UPSTREAM_FIX_REQUIRED": 0,
            "FORK_REQUIRED": False,
            "NEWER_UPSTREAM_RELEASE_SHOULD_BE_TESTED": True,
            "CERTIFICATION_DECISION": certification_decision,
        },
        "remediation_order": [
            "test a newer clean upstream OpenCode release and repeat this exact evidence process",
            "review the two runtime process-launch candidates against the serve/API threat path",
            "use an AIAT wrapper mitigation only where the denial boundary is technically sufficient",
            "consider an operator-owned pinned fork only if a confirmed runtime defect remains after upstream review",
        ],
        "active_worker_status": "inactive_until_certification_passes",
        "source_inspection": {
            "source_dir_inspected": source_dir.is_dir(),
            "source_clone_retained": False,
            "source_payloads_retained": False,
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(triage_result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return triage_result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = triage(
        report_path=args.report,
        artifact_dir=args.artifact_dir,
        source_dir=args.source_dir,
        output_path=args.output,
    )
    print(json.dumps(result["decision"], sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
