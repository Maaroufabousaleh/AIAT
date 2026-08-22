"""Run the reproducible OpenHands v1.43.0 supply-chain certification wave.

This command deliberately does not activate an adapter or infer that scanner
hits are exploitable vulnerabilities.  It retains sanitized scanner/SBOM
outputs, separates tooling/parser/coverage failures from findings, and leaves
runtime applicability for the deployed-image cross-check.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

try:  # Script execution from ``mas/scripts``.
    from opencode_candidate_certify import (
        AIAT_BOUNDARY_FAILURE,
        SBOM_FAILURE,
        SCANNER_EXECUTION_FAILURE,
        SECURITY_FINDING,
        TOOL_INSTALLATION_FAILURE,
        _current_git_revision,
        _load_json,
        _parse_json_output,
        _prepare_source,
        _redacted_text,
        _run,
        _run_boundary,
        _run_sbom,
        _run_scanner,
        _sha256_file,
        _tool_version,
        _write_json,
    )
except ImportError:  # pragma: no cover - package/module invocation fallback
    from scripts.opencode_candidate_certify import (  # type: ignore[no-redef]
        AIAT_BOUNDARY_FAILURE,
        SBOM_FAILURE,
        SCANNER_EXECUTION_FAILURE,
        SECURITY_FINDING,
        TOOL_INSTALLATION_FAILURE,
        _current_git_revision,
        _load_json,
        _parse_json_output,
        _prepare_source,
        _redacted_text,
        _run,
        _run_boundary,
        _run_sbom,
        _run_scanner,
        _sha256_file,
        _tool_version,
        _write_json,
    )


SCHEMA = "aiat.openhands-candidate-certification.v1"
SCANNER_COVERAGE_INCOMPLETE = "SCANNER_COVERAGE_INCOMPLETE"
DEFAULT_REPOSITORY = "https://github.com/OpenHands/software-agent-sdk.git"
DEFAULT_VERSION = "v1.43.0"
DEFAULT_IMAGE = "ghcr.io/openhands/agent-server:1.43.0-python@sha256:36f847d1dfbbbdce90052437b06a3c6e76b8a54683228182eaf73085f03fcd97"
EXPECTED_SOURCE_COMMIT = "4c1237f391fe394e9f67505fe3a0bd2d81f84188"
EXPECTED_IMAGE_DIGEST = "sha256:36f847d1dfbbbdce90052437b06a3c6e76b8a54683228182eaf73085f03fcd97"


def _run_image_sbom(image_ref: str, output_dir: Path) -> dict[str, Any]:
    """Generate an image SBOM from the locally pulled immutable image."""

    executable = shutil.which("syft")
    path = output_dir / "image-sbom.cdx.json"
    stdout_path = output_dir / "syft-image.stdout.log"
    stderr_path = output_dir / "syft-image.stderr.log"
    invocation = [executable or "syft", f"docker:{image_ref}", "-o", f"cyclonedx-json={path}"]
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
            [executable, f"docker:{image_ref}", "-o", f"cyclonedx-json={path}"],
            capture_output=True,
            text=True,
            timeout=1800,
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
            "scanner_error": "syft_image_failed_or_missing_output",
            "invocation": invocation,
            "stdout_path": stdout_path.name,
            "stderr_path": stderr_path.name,
            "path": path.name,
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    if not isinstance(payload, dict):
        return {
            "status": "blocked",
            "available": True,
            "exit_status": result.returncode,
            "failure_class": SBOM_FAILURE,
            "scanner_error": "image_sbom_shape_invalid",
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
        "component_count": len(payload.get("components") or []) if isinstance(payload.get("components"), list) else 0,
        "credentials_persisted": False,
    }


def _image_probe(image_ref: str) -> dict[str, Any]:
    if "@sha256:" not in image_ref:
        return {"status": "blocked", "reason": "image_not_digest_pinned"}
    result = _run(["docker", "image", "inspect", image_ref, "--format", "{{json .RepoDigests}}"], timeout=30)
    if result.returncode != 0:
        return {"status": "blocked", "reason": "image_not_pulled_or_docker_unavailable"}
    try:
        digests = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return {"status": "blocked", "reason": "docker_image_metadata_invalid"}
    normalized = sorted(str(value) for value in digests) if isinstance(digests, list) else []
    expected = image_ref.rsplit("@", 1)[1]
    return {
        "status": "pass",
        "expected_digest": expected,
        "repo_digests": normalized,
    }


def _agent_server_probe(
    base_url: str | None,
    session_api_key: str | None,
    expected_commit: str,
    expected_version: str | None = None,
) -> dict[str, Any]:
    """Probe only scalar Agent Server health/readiness/build metadata."""

    if not base_url or not session_api_key:
        return {"status": "blocked", "reason": "agent_server_endpoint_or_session_key_missing"}
    checks: dict[str, Any] = {}
    for name, path in (("health", "/health"), ("readiness", "/ready"), ("server_info", "/server_info")):
        request = urllib.request.Request(
            base_url.rstrip("/") + path,
            headers={"X-Session-API-Key": session_api_key, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                body = response.read()
                payload = json.loads(body.decode("utf-8")) if body else {}
                checks[name] = {"status": "pass", "http_status": response.status}
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            checks[name] = {"status": "blocked", "error_type": type(exc).__name__}
            continue
        if name == "readiness" and isinstance(payload, dict):
            checks[name]["reported_ready"] = str(payload.get("status", "")).lower() in {"ready", "healthy"}
            if not checks[name]["reported_ready"]:
                checks[name]["status"] = "blocked"
        if name == "server_info" and isinstance(payload, dict):
            versions = payload.get("versions") or payload.get("packages") or {}
            server_version = payload.get("version")
            if not server_version and isinstance(versions, dict):
                server_version = versions.get("openhands-agent-server") or versions.get("server_version")
            build_sha = (
                payload.get("build_git_sha")
                or payload.get("build_sha")
                or payload.get("git_sha")
                or payload.get("commit_sha")
            )
            checks[name]["server_version"] = str(server_version).removeprefix("v") if server_version else None
            checks[name]["server_version_matches"] = (
                bool(checks[name]["server_version"])
                and (expected_version is None or checks[name]["server_version"] == expected_version.removeprefix("v"))
            )
            checks[name]["build_sha_matches"] = bool(build_sha) and str(build_sha) == expected_commit
            checks[name]["provenance_proven"] = checks[name]["server_version_matches"] and checks[name]["build_sha_matches"]
            if not checks[name]["provenance_proven"]:
                checks[name]["status"] = "blocked"
    status = "pass" if all(row.get("status") == "pass" for row in checks.values()) else "blocked"
    return {"status": status, "checks": checks, "payloads_retained": False}


def _container_probe(container_name: str | None) -> dict[str, Any]:
    if not container_name:
        return {"status": "blocked", "reason": "container_name_missing"}
    runtime = _run(["docker", "inspect", "--format", "{{.HostConfig.Runtime}}", container_name], timeout=30)
    value = (runtime.stdout or "").strip()
    return {
        "status": "pass" if runtime.returncode == 0 and value == "runsc" else "blocked",
        "runtime": value or None,
        "runtime_probe_returncode": runtime.returncode,
    }


def _cleanup_container(container_name: str | None) -> dict[str, Any]:
    if not container_name:
        return {"status": "blocked", "reason": "container_name_missing"}
    removed = _run(["docker", "rm", "-f", container_name], timeout=30)
    remaining = _run(["docker", "ps", "-aq", "--filter", f"name=^{container_name}$"], timeout=30)
    residue = len([line for line in (remaining.stdout or "").splitlines() if line.strip()]) if remaining.returncode == 0 else None
    return {
        "status": "pass" if residue == 0 else "blocked",
        "remove_returncode": removed.returncode,
        "remaining_containers": residue,
    }


def _normalize_scanner_row(row: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    """Add OpenHands-specific parser/coverage taxonomy to shared scanner output."""

    if row.get("name") != "semgrep" or not row.get("scanner_error_count"):
        return row
    path = output_dir / str(row.get("raw_json_path") or "semgrep.json")
    value, parse_error = _parse_json_output(path)
    errors = value.get("errors") if isinstance(value, dict) else None
    details: list[dict[str, Any]] = []
    execution = 0
    coverage = 0
    if isinstance(errors, list):
        for error in errors:
            if not isinstance(error, dict):
                coverage += 1
                continue
            code = str(error.get("code") or "unknown")
            error_type = error.get("type")
            normalized_type = str(error_type[0] if isinstance(error_type, list) and error_type else error_type or "unknown")
            failure = SCANNER_EXECUTION_FAILURE if code == "2" or normalized_type == "Internal matching error" else SCANNER_COVERAGE_INCOMPLETE
            if failure == SCANNER_EXECUTION_FAILURE:
                execution += 1
            else:
                coverage += 1
            details.append({"failure_class": failure, "error_type": normalized_type})
    if parse_error:
        execution += 1
        details.append({"failure_class": SCANNER_EXECUTION_FAILURE, "error_type": "semgrep_json_invalid"})
    classes = sorted({str(item["failure_class"]) for item in details})
    row["failure_classes"] = classes
    row["scanner_errors"] = details or row.get("scanner_errors", [])
    row["coverage_error_count"] = coverage
    row["execution_error_count"] = execution
    row["failure_class"] = SCANNER_EXECUTION_FAILURE if execution else SCANNER_COVERAGE_INCOMPLETE
    return row


def certify(
    *,
    repository: str,
    version: str,
    image_ref: str,
    output_dir: Path,
    boundary_command: list[str] | None = None,
    tooling_manifest_path: Path | None = None,
    agent_server_url: str | None = None,
    session_api_key: str | None = None,
    container_name: str | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    blockers: list[str] = []
    source_metadata: dict[str, Any] | None = None
    scanner_rows: list[dict[str, Any]] = []
    source_sbom: dict[str, Any] = {"status": "not_run"}
    image_sbom: dict[str, Any] = {"status": "not_run"}
    boundary: dict[str, Any] = {"status": "not_run"}
    tooling = _load_json(tooling_manifest_path)
    tool_versions: dict[str, Any] = {}
    aiat_candidate_commit = _current_git_revision()
    expected_aiat_candidate = os.getenv("AIAT_CANDIDATE_SHA", "").strip().lower() or None
    if aiat_candidate_commit is None:
        blockers.append("aiat_candidate_commit_unavailable")
    elif expected_aiat_candidate and aiat_candidate_commit != expected_aiat_candidate:
        blockers.append("aiat_candidate_commit_mismatch")

    with tempfile.TemporaryDirectory(prefix="aiat-openhands-candidate-") as temporary:
        try:
            source_metadata, source = _prepare_source(repository, version, Path(temporary))
            if source_metadata.get("commit") != EXPECTED_SOURCE_COMMIT:
                blockers.append("upstream_tag_commit_mismatch")
        except Exception as exc:
            blockers.append(f"source_preparation:{type(exc).__name__}")
            source = None
        if source is not None:
            _write_json(output_dir / "source-manifest.json", source_metadata)
            for name in ("semgrep", "trufflehog", "skillspector", "syft"):
                tool_versions[name] = _tool_version(name, output_dir)
            scanner_rows = [
                _run_scanner("semgrep", ["semgrep", "--config", "auto", "--json", "--no-git-ignore", str(source)], source=source, output_dir=output_dir),
                _run_scanner("trufflehog", ["trufflehog", "filesystem", "--json", "--no-update", str(source)], source=source, output_dir=output_dir),
                _run_scanner("skillspector", ["skillspector", "scan", str(source), "--no-llm", "--format", "json"], source=source, output_dir=output_dir),
            ]
            scanner_rows = [_normalize_scanner_row(row, output_dir) for row in scanner_rows]
            source_sbom = _run_sbom(source, output_dir)
            image_sbom = _run_image_sbom(image_ref, output_dir)
            boundary = _run_boundary(boundary_command, output_dir)
        else:
            blockers.append("source_not_available")

    if not re.search(r"@sha256:[0-9a-fA-F]{64}$", image_ref):
        blockers.append("candidate_image_not_digest_pinned")
    if image_ref.rsplit("@", 1)[-1] != EXPECTED_IMAGE_DIGEST:
        blockers.append("candidate_image_digest_mismatch")
    image_probe = _image_probe(image_ref)
    if image_probe.get("status") != "pass":
        blockers.append("candidate_image_not_verified_locally")
    container_probe = _container_probe(container_name)
    if container_probe.get("status") != "pass":
        blockers.append("agent_server_not_running_under_runsc")
    agent_server = _agent_server_probe(agent_server_url, session_api_key, EXPECTED_SOURCE_COMMIT, version)
    if agent_server.get("status") != "pass":
        checks = agent_server.get("checks") if isinstance(agent_server.get("checks"), dict) else {}
        if any(checks.get(name, {}).get("status") != "pass" for name in ("health", "readiness")):
            blockers.append("agent_server_health_or_readiness_failed")
        if checks.get("server_info", {}).get("provenance_proven") is not True:
            blockers.append("agent_server_provenance_unproven")

    scanner_errors = sum(int(row.get("scanner_error_count") or 0) for row in scanner_rows)
    findings = sum(int(row.get("finding_count") or 0) for row in scanner_rows)
    severity_counts: Counter[str] = Counter()
    for row in scanner_rows:
        severity_counts.update({str(key).upper(): int(value) for key, value in (row.get("severity_counts") or {}).items()})

    failure_classes: set[str] = set()
    for row in scanner_rows:
        if row.get("failure_class"):
            failure_classes.add(str(row["failure_class"]))
        failure_classes.update(str(item) for item in row.get("failure_classes", []) if item)
        if row.get("status") != "pass":
            if row.get("failure_class") == TOOL_INSTALLATION_FAILURE:
                blockers.append(f"{row.get('name')}_tool_installation_failed")
            if SCANNER_EXECUTION_FAILURE in (row.get("failure_classes") or []):
                blockers.append(f"{row.get('name')}_execution_failed")
            if SCANNER_COVERAGE_INCOMPLETE in (row.get("failure_classes") or []):
                blockers.append(f"{row.get('name')}_coverage_incomplete")
    failure_classes.update(str(item) for item in tooling.get("failure_classes", []) if item)
    if tooling.get("status") != "pass":
        blockers.append("scanner_tool_provisioning_incomplete")
    for sbom in (source_sbom, image_sbom):
        if sbom.get("status") != "pass":
            blockers.append("sbom_not_generated")
            if sbom.get("failure_class"):
                failure_classes.add(str(sbom["failure_class"]))
    if boundary.get("status") != "pass":
        blockers.append("aiat_boundary_regression_not_passed")
        failure_classes.add(AIAT_BOUNDARY_FAILURE)
    if findings:
        failure_classes.add(SECURITY_FINDING)

    security_interpretable = not scanner_errors and all(row.get("status") == "pass" for row in scanner_rows)
    if blockers:
        decision = "blocked"
    elif findings:
        decision = "findings_review_required"
    else:
        decision = "passed"
    cleanup = _cleanup_container(container_name)
    if cleanup.get("status") != "pass":
        blockers.append("agent_server_container_residue")
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
        "candidate_image_digest": image_ref.rsplit("@", 1)[1] if "@" in image_ref else None,
        "source": source_metadata or {"status": "not_prepared", "clone_retained": False},
        "image_probe": image_probe,
        "agent_server": agent_server,
        "container_runtime": container_probe,
        "cleanup": cleanup,
        "scanners": scanner_rows,
        "tool_versions": tool_versions,
        "tooling_provisioning": tooling,
        "scanner_errors": scanner_errors,
        "raw_findings_count": findings,
        "findings_by_severity": dict(sorted(severity_counts.items())),
        "security_findings_interpretable": security_interpretable,
        "runtime_applicability": {
            "status": "pending_deployed_image_cross_check",
            "raw_hits_are_not_exploitability_verdicts": True,
            "source_only_and_image_reachable_classification_required": True,
        },
        "source_sbom": source_sbom,
        "image_sbom": image_sbom,
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
        "scope": "fresh pinned OpenHands source/image/SBOM/scanner candidate certification; no activation or release decision",
    }
    _write_json(output_dir / "candidate-certification.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.getenv("OPENHANDS_CANDIDATE_REPOSITORY", DEFAULT_REPOSITORY))
    parser.add_argument("--version", default=os.getenv("OPENHANDS_CANDIDATE_VERSION", DEFAULT_VERSION))
    parser.add_argument("--image-ref", default=os.getenv("OPENHANDS_CANDIDATE_IMAGE_REF", DEFAULT_IMAGE))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--boundary-command")
    parser.add_argument("--tooling-manifest", type=Path)
    parser.add_argument("--agent-server-url")
    parser.add_argument("--session-api-key")
    parser.add_argument("--container-name")
    args = parser.parse_args(argv)
    import shlex

    report = certify(
        repository=args.repository,
        version=args.version,
        image_ref=args.image_ref,
        output_dir=args.output,
        boundary_command=shlex.split(args.boundary_command) if args.boundary_command else None,
        tooling_manifest_path=args.tooling_manifest,
        agent_server_url=args.agent_server_url,
        session_api_key=args.session_api_key,
        container_name=args.container_name,
    )
    print(
        json.dumps(
            {
                key: report[key]
                for key in ("status", "candidate_commit", "candidate_image_digest", "scanner_errors", "raw_findings_count", "findings_by_severity", "failure_classes", "blockers")
            },
            sort_keys=True,
            indent=2,
        )
    )
    return 0 if report["status"] == "passed" else 2 if report["status"] == "blocked" else 1


if __name__ == "__main__":
    raise SystemExit(main())
