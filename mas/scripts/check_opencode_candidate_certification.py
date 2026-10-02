"""Validate the retained OpenCode candidate certification package.

This is a structural evidence check.  It reports the candidate's technical
gate separately from the package's own status so a blocked fresh scan remains
visible without being mistaken for a passing OpenCode activation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import lzma
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = REPO_ROOT / "mas" / "docs" / "provenance" / "opencode-candidate" / "2026-10-02-v1.18.21" / "candidate-certification.json"
SCHEMA = "aiat.opencode-candidate-certification.v1"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")
FAILURE_CLASSES = {
    "TOOL_INSTALLATION_FAILURE",
    "SCANNER_EXECUTION_FAILURE",
    "SCANNER_COVERAGE_INCOMPLETE",
    "SECURITY_FINDING",
    "SBOM_FAILURE",
    "AIAT_BOUNDARY_FAILURE",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_xz_payload(path: Path) -> str:
    digest = hashlib.sha256()
    with lzma.open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect(path: Path = DEFAULT_PATH) -> dict[str, Any]:
    errors: list[str] = []
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {
            "schema_version": "aiat.opencode-candidate-certification-review.v1",
            "mode": "static",
            "status": "fail",
            "errors": [f"candidate evidence could not be loaded: {type(exc).__name__}"],
            "licence_metadata_is_gate": False,
        }
    if not isinstance(report, dict):
        errors.append("candidate evidence must be a JSON object")
        report = {}
    if report.get("schema_version") != SCHEMA:
        errors.append("candidate evidence schema is invalid")
    if report.get("programme_scope") != "personal-internal-only":
        errors.append("candidate evidence must remain personal-internal-only")
    commit = str(report.get("candidate_commit") or "")
    if not COMMIT_RE.fullmatch(commit):
        errors.append("candidate commit must be a full SHA")
    aiat_commit = str(report.get("aiat_candidate_commit") or "")
    if not COMMIT_RE.fullmatch(aiat_commit):
        errors.append("AIAT candidate checkout commit must be a full SHA")
    image = str(report.get("candidate_image_ref") or "")
    if not DIGEST_RE.search(image):
        errors.append("candidate image must be digest pinned")
    image_digest = str(report.get("candidate_image_digest") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", image_digest) or not image.endswith(image_digest):
        errors.append("candidate image digest field must match the immutable image reference")
    if report.get("status") not in {"blocked", "findings_review_required", "passed"}:
        errors.append("candidate technical status is invalid")
    source = report.get("source")
    if (
        not isinstance(source, dict)
        or source.get("clone_retained") is not False
        or source.get("immutable_provenance_ref") is not True
        or source.get("scan_tree_git_metadata_excluded") is not True
    ):
        errors.append("source provenance must retain an immutable reference, exclude clone metadata, and discard the clone")
    if not isinstance(source, dict) or source.get("snapshot_retained") is not True:
        errors.append("exact candidate source snapshot must be retained")
    else:
        snapshot_name = source.get("snapshot_path")
        snapshot_path = path.parent / str(snapshot_name or "")
        if not snapshot_name or Path(str(snapshot_name)).name != str(snapshot_name) or not snapshot_path.is_file():
            errors.append("candidate source snapshot path is missing or unsafe")
        else:
            try:
                if _sha256(snapshot_path) != source.get("snapshot_sha256"):
                    errors.append("candidate source snapshot digest does not match")
                if source.get("snapshot_encoding") != "xz" or _sha256_xz_payload(snapshot_path) != source.get("archive_sha256"):
                    errors.append("candidate source archive digest does not match its retained snapshot")
            except (OSError, EOFError, lzma.LZMAError):
                errors.append("candidate source snapshot could not be verified")
    scan_scope = report.get("source_scan_scope")
    if not isinstance(scan_scope, dict):
        errors.append("candidate source scan scope must be explicit")
    else:
        if scan_scope.get("semgrep_path_exclusions") != []:
            errors.append("candidate Semgrep scan must not exclude source paths")
        exclusions = scan_scope.get("semgrep_rule_exclusions")
        expected_exclusions = {
            "yaml.github-actions.security.curl-eval.curl-eval",
            "yaml.github-actions.security.gha-curl-pipe-shell.gha-curl-pipe-shell",
        }
        exclusion_ids = (
            [str(row.get("rule_id")) for row in exclusions]
            if isinstance(exclusions, list) and all(isinstance(row, dict) for row in exclusions)
            else None
        )
        if exclusion_ids not in ([], sorted(expected_exclusions)):
            errors.append("Semgrep rule exclusions must match the reviewed CI matcher failures")
        elif isinstance(exclusions, list) and any(
            row.get("compensating_scanners") != ["actionlint", "zizmor"]
            or row.get("disposition") != "review_required; does not imply equivalent rule coverage"
            for row in exclusions
        ):
            errors.append("Semgrep rule exclusions must retain their compensating review boundary")
        semgrep_row = next(
            (row for row in report.get("scanners", []) if isinstance(row, dict) and row.get("name") == "semgrep"),
            None,
        )
        invocation = semgrep_row.get("invocation") if isinstance(semgrep_row, dict) else None
        invoked_exclusions = (
            [str(invocation[index + 1]) for index, value in enumerate(invocation[:-1]) if value == "--exclude-rule"]
            if isinstance(invocation, list)
            else None
        )
        if exclusion_ids is not None and invoked_exclusions != exclusion_ids:
            errors.append("Semgrep exclusion metadata must match the retained invocation")
    evidence_policy = report.get("evidence_policy")
    if not isinstance(evidence_policy, dict):
        errors.append("evidence policy is missing")
    else:
        if evidence_policy.get("credentials_persisted") is not False:
            errors.append("candidate evidence must not persist credentials")
        if evidence_policy.get("payloads_persisted") is not False:
            errors.append("candidate evidence must not persist payloads")
        if evidence_policy.get("licence_metadata_is_gate") is not False:
            errors.append("licence metadata must remain non-gating")
    scanners = report.get("scanners")
    if not isinstance(scanners, list) or {str(row.get("name")) for row in scanners if isinstance(row, dict)} != {"semgrep", "trufflehog", "skillspector"}:
        errors.append("candidate evidence must enumerate Semgrep, TruffleHog, and SkillSpector")
    elif isinstance(scanners, list):
        tool_versions = report.get("tool_versions") if isinstance(report.get("tool_versions"), dict) else {}
        for row in scanners:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "scanner")
            if not isinstance(row.get("invocation"), list) or not row.get("invocation"):
                errors.append(f"{name} invocation is missing")
            version = tool_versions.get(name)
            if not isinstance(version, dict) or version.get("available") is not True or not str(version.get("version") or "").strip():
                errors.append(f"{name} exact scanner version is missing")
            rule_counts = row.get("rule_counts")
            if not isinstance(rule_counts, dict):
                errors.append(f"{name} normalized rule counts are missing")
            else:
                parsed_counts: list[int] = []
                for rule, count in rule_counts.items():
                    try:
                        parsed = int(count)
                    except (TypeError, ValueError):
                        parsed = -1
                    if not str(rule).strip() or isinstance(count, bool) or parsed < 0:
                        errors.append(f"{name} normalized rule counts contain an invalid row")
                    else:
                        parsed_counts.append(parsed)
                if sum(parsed_counts) != int(row.get("finding_count") or 0):
                    errors.append(f"{name} normalized rule counts do not match its finding count")
            retained = row.get("raw_output_retained") is True
            if retained:
                artifact_name = str(row.get("raw_json_path") or "")
                artifact = path.parent / artifact_name
                if Path(artifact_name).name != artifact_name or not artifact.is_file():
                    errors.append(f"{name} sanitized raw result is missing or unsafe")
                else:
                    try:
                        if row.get("raw_output_sanitized") is not True or _sha256(artifact) != row.get("raw_json_sha256"):
                            errors.append(f"{name} sanitized raw result digest or status does not match")
                    except OSError:
                        errors.append(f"{name} sanitized raw result could not be verified")
            elif int(row.get("finding_count") or 0) > 0 or row.get("status") == "pass":
                errors.append(f"{name} findings or passing status require a retained sanitized raw result")
    failure_classes = report.get("failure_classes")
    if not isinstance(failure_classes, list) or not set(failure_classes).issubset(FAILURE_CLASSES):
        errors.append("candidate evidence contains an unknown failure classification")
    if report.get("status") == "blocked" and report.get("security_findings_interpretable") is True:
        errors.append("blocked tooling must not mark security findings interpretable")
    if not isinstance(report.get("tool_versions"), dict):
        errors.append("candidate evidence must retain tool version probes")
    if not isinstance(report.get("tooling_provisioning"), dict):
        errors.append("candidate evidence must retain tooling provisioning evidence")
    if not isinstance(report.get("sbom"), dict):
        errors.append("candidate evidence must retain SBOM status")
    else:
        sbom = report["sbom"]
        if sbom.get("status") == "pass":
            sbom_name = str(sbom.get("path") or "")
            sbom_path = path.parent / sbom_name
            if Path(sbom_name).name != sbom_name or not sbom_path.is_file():
                errors.append("passing candidate SBOM path is missing or unsafe")
            else:
                try:
                    if _sha256(sbom_path) != sbom.get("sha256"):
                        errors.append("candidate SBOM digest does not match the retained artifact")
                except OSError:
                    errors.append("candidate SBOM could not be verified")
            if not isinstance(sbom.get("invocation"), list) or not sbom.get("invocation"):
                errors.append("passing candidate SBOM invocation is missing")
            syft = report.get("tool_versions", {}).get("syft") if isinstance(report.get("tool_versions"), dict) else None
            if not isinstance(syft, dict) or syft.get("available") is not True or not str(syft.get("version") or "").strip():
                errors.append("passing candidate SBOM must retain the exact Syft version")
    technical_gate = "passed" if report.get("status") == "passed" else "blocked"
    return {
        "schema_version": "aiat.opencode-candidate-certification-review.v1",
        "mode": "static",
        "status": "fail" if errors else "pass",
        "errors": errors,
        "candidate_status": report.get("status"),
        "technical_gate_status": technical_gate,
        "candidate_version": report.get("candidate_version"),
        "candidate_commit": commit or None,
        "aiat_candidate_commit": aiat_commit or None,
        "candidate_image_digest": image_digest or None,
        "scanner_count": len(scanners) if isinstance(scanners, list) else 0,
        "scanner_error_count": report.get("scanner_errors"),
        "finding_count": report.get("raw_findings_count"),
        "active_worker_status": report.get("active_worker_status"),
        "licence_metadata_is_gate": False,
        "scope": "structural candidate evidence validation; technical OpenCode activation remains blocked until the fresh scan passes",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = inspect(args.path)
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"opencode-candidate-certification: {report['status'].upper()}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
