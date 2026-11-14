#!/usr/bin/env python3
"""Fail-closed validation of retained worker and Redis image CI artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


def _read_json(root: Path, name: str) -> dict[str, Any]:
    path = root / name
    if not path.is_file():
        raise ValueError(f"required evidence file is missing: {name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"evidence file is not valid JSON: {name}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"evidence file must contain a JSON object: {name}")
    return value


def _verify_archive(root: Path, archive_name: str, expected: str) -> None:
    archive = root / archive_name
    if not archive.is_file():
        raise ValueError(f"candidate image archive is missing: {archive_name}")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("candidate archive SHA-256 is missing or malformed")
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError("candidate image archive SHA-256 does not match its identity record")


def _verify_source_revision_file(root: Path, source_sha: str) -> None:
    path = root / "source-commit.txt"
    if not path.is_file():
        raise ValueError("candidate source-commit.txt is missing")
    try:
        recorded = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError("candidate source-commit.txt cannot be read") from exc
    if recorded != source_sha:
        raise ValueError("candidate source-commit.txt does not match the selected release revision")


def _verify_sbom(root: Path) -> None:
    sbom = _read_json(root, "image-sbom.cdx.json")
    if sbom.get("bomFormat") != "CycloneDX":
        raise ValueError("candidate SBOM is not a CycloneDX document")
    components = sbom.get("components")
    if not isinstance(components, list) or not components:
        raise ValueError("candidate CycloneDX SBOM has no components")


def verify_evidence(kind: str, root: Path, source_sha: str) -> dict[str, Any]:
    """Validate evidence fields needed before promoting a candidate to GHCR."""
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("source revision must be a full lowercase Git commit SHA")

    if kind == "worker":
        summary = _read_json(root, "candidate-summary.json")
        candidate = summary.get("candidate")
        candidate_identity = _read_json(root, "candidate-image.json")
        if summary.get("schema_version") != "aiat.worker.alpine-candidate-summary.v1":
            raise ValueError("worker candidate summary schema is unsupported")
        if summary.get("status") != "pass" or not isinstance(candidate, dict):
            raise ValueError("worker candidate CI did not pass")
        if candidate_identity != candidate:
            raise ValueError("worker summary and standalone image identity records differ")
        if candidate.get("schema_version") != "aiat.worker.alpine-image-candidate.v1":
            raise ValueError("worker candidate identity schema is unsupported")
        if candidate.get("source_revision") != source_sha:
            raise ValueError("worker image source revision does not match this release run")
        if candidate.get("status") != "build_identity_verified":
            raise ValueError("worker image identity is not verified")
        if candidate.get("registry_published") is not False or summary.get("registry_published") is not False:
            raise ValueError("worker evidence unexpectedly claims a registry publication")
        if candidate.get("governed_worker_pin_changed") is not False or summary.get("governed_pin_changed") is not False:
            raise ValueError("worker evidence unexpectedly changes the governed image pin")
        if candidate.get("github_workflow_attestation_subject") != "candidate.oci.tar":
            raise ValueError("worker archive provenance subject is not the expected OCI archive")
        for field in ("oci_index_digest", "platform_manifest_digest", "config_digest"):
            if not isinstance(candidate.get(field), str) or not _SHA256.fullmatch(candidate[field]):
                raise ValueError(f"worker candidate {field} is missing or malformed")
        gates = summary.get("gates")
        if not isinstance(gates, dict) or not gates or any(value != "success" for value in gates.values()):
            raise ValueError("one or more worker candidate gates did not pass")
        vulnerabilities = summary.get("vulnerabilities")
        if not isinstance(vulnerabilities, dict):
            raise ValueError("worker vulnerability summary is missing")
        for category in ("image", "source_and_recipe"):
            report = vulnerabilities.get(category)
            if not isinstance(report, dict) or report.get("status") != "present":
                raise ValueError(f"worker {category} vulnerability report is unavailable")
            if report.get("critical") != 0 or report.get("high") != 0:
                raise ValueError(f"worker {category} report contains high or critical findings")
        if summary.get("sbom_present") is not True or summary.get("github_attestation_bundles_present") is not True:
            raise ValueError("worker SBOM or archive attestation bundles are missing")
        _verify_sbom(root)
        _verify_source_revision_file(root, source_sha)
        _read_json(root, "attestation-references.json")
        _verify_archive(root, "candidate.oci.tar", candidate.get("oci_archive_sha256", ""))
        return {
            "kind": kind,
            "source_sha": source_sha,
            "platform_manifest_digest": candidate["platform_manifest_digest"],
            "config_digest": candidate["config_digest"],
            "archive_sha256": candidate["oci_archive_sha256"],
            "status": "verified_candidate_evidence",
        }

    if kind == "redis":
        identity = _read_json(root, "candidate-image-identity.json")
        scans = _read_json(root, "trivy-summary.json")
        if identity.get("schema_version") != "aiat.redis-acl-init-candidate.v1":
            raise ValueError("Redis candidate identity schema is unsupported")
        if identity.get("source_commit") != source_sha:
            raise ValueError("Redis image source revision does not match this release run")
        if identity.get("registry_reference") is not None:
            raise ValueError("Redis evidence unexpectedly claims an immutable registry reference")
        if identity.get("platform") != "linux/amd64":
            raise ValueError("Redis candidate platform differs from the certified target")
        if not isinstance(identity.get("docker_image_config_digest"), str) or not _SHA256.fullmatch(identity["docker_image_config_digest"]):
            raise ValueError("Redis candidate image configuration digest is missing or malformed")
        if scans.get("schema_version") != "aiat.trivy-evidence-summary.v1" or scans.get("status") != "complete":
            raise ValueError("Redis vulnerability scan summary is incomplete")
        reports = scans.get("reports")
        if not isinstance(reports, dict):
            raise ValueError("Redis vulnerability reports are missing")
        for category in ("redis_initializer_image", "redis_initializer_dependencies"):
            report = reports.get(category)
            if not isinstance(report, dict) or report.get("status") != "read":
                raise ValueError(f"Redis {category} scan report is unavailable")
            if report.get("high_critical_count") != 0:
                raise ValueError(f"Redis {category} report contains high or critical findings")
        sbom = scans.get("sbom")
        if not isinstance(sbom, dict) or sbom.get("status") != "read" or not isinstance(sbom.get("component_count"), int) or sbom["component_count"] < 1:
            raise ValueError("Redis candidate SBOM is missing or empty")
        _verify_sbom(root)
        _verify_source_revision_file(root, source_sha)
        _read_json(root, "attestation-references.json")
        _verify_archive(root, "candidate-image.tar", identity.get("archive_sha256", ""))
        return {
            "kind": kind,
            "source_sha": source_sha,
            "config_digest": identity["docker_image_config_digest"],
            "archive_sha256": identity["archive_sha256"],
            "status": "verified_candidate_evidence",
        }

    raise ValueError("kind must be worker or redis")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("worker", "redis"), required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    try:
        result = verify_evidence(args.kind, args.root, args.source_sha)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
