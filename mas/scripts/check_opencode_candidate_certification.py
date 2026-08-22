"""Validate the retained OpenCode candidate certification package.

This is a structural evidence check.  It reports the candidate's technical
gate separately from the package's own status so a blocked fresh scan remains
visible without being mistaken for a passing OpenCode activation.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = REPO_ROOT / "mas" / "docs" / "provenance" / "opencode-candidate" / "2026-08-21-v1.18.21" / "candidate-certification.json"
SCHEMA = "aiat.opencode-candidate-certification.v1"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")
FAILURE_CLASSES = {
    "TOOL_INSTALLATION_FAILURE",
    "SCANNER_EXECUTION_FAILURE",
    "SECURITY_FINDING",
    "SBOM_FAILURE",
    "AIAT_BOUNDARY_FAILURE",
}


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
