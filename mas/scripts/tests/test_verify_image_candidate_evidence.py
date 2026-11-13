from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

import pytest

from scripts.verify_image_candidate_evidence import verify_evidence

if TYPE_CHECKING:
    from pathlib import Path

SOURCE_SHA = "1d3e4fcb0f759eeb139ea167cdf7bfc5d104c575"


def _json(root: Path, name: str, value: object) -> None:
    (root / name).write_text(json.dumps(value), encoding="utf-8")


def _source(root: Path) -> None:
    (root / "source-commit.txt").write_text(SOURCE_SHA + "\n", encoding="utf-8")


def _archive(root: Path, name: str) -> str:
    path = root / name
    path.write_bytes(b"disposable candidate archive")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _worker(root: Path) -> None:
    archive_sha = _archive(root, "candidate.oci.tar")
    digest = "sha256:" + "a" * 64
    candidate = {
        "schema_version": "aiat.worker.alpine-image-candidate.v1",
        "source_revision": SOURCE_SHA,
        "status": "build_identity_verified",
        "registry_published": False,
        "governed_worker_pin_changed": False,
        "github_workflow_attestation_subject": "candidate.oci.tar",
        "oci_index_digest": digest,
        "platform_manifest_digest": digest,
        "config_digest": digest,
        "oci_archive_sha256": archive_sha,
    }
    _json(root, "candidate-image.json", candidate)
    _source(root)
    _json(root, "candidate-summary.json", {
        "schema_version": "aiat.worker.alpine-candidate-summary.v1",
        "status": "pass",
        "candidate": candidate,
        "gates": {"scan": "success", "runsc": "success"},
        "vulnerabilities": {
            "image": {"status": "present", "critical": 0, "high": 0},
            "source_and_recipe": {"status": "present", "critical": 0, "high": 0},
        },
        "sbom_present": True,
        "github_attestation_bundles_present": True,
        "registry_published": False,
        "governed_pin_changed": False,
    })
    _json(root, "image-sbom.cdx.json", {"bomFormat": "CycloneDX", "components": [{"name": "worker"}]})
    _json(root, "attestation-references.json", {"provenance": {}, "sbom": {}})


def _redis(root: Path) -> None:
    archive_sha = _archive(root, "candidate-image.tar")
    _json(root, "candidate-image-identity.json", {
        "schema_version": "aiat.redis-acl-init-candidate.v1",
        "source_commit": SOURCE_SHA,
        "docker_image_config_digest": "sha256:" + "b" * 64,
        "platform": "linux/amd64",
        "archive_sha256": archive_sha,
        "registry_reference": None,
    })
    _source(root)
    _json(root, "trivy-summary.json", {
        "schema_version": "aiat.trivy-evidence-summary.v1",
        "status": "complete",
        "reports": {
            "redis_initializer_image": {"status": "read", "high_critical_count": 0},
            "redis_initializer_dependencies": {"status": "read", "high_critical_count": 0},
        },
        "sbom": {"status": "read", "component_count": 2},
    })
    _json(root, "image-sbom.cdx.json", {"bomFormat": "CycloneDX", "components": [{"name": "redis-init"}]})
    _json(root, "attestation-references.json", {"provenance": {}, "sbom": {}})


def test_worker_candidate_evidence_is_accepted_when_every_gate_passes(tmp_path: Path) -> None:
    _worker(tmp_path)

    result = verify_evidence("worker", tmp_path, SOURCE_SHA)

    assert result["status"] == "verified_candidate_evidence"
    assert result["source_sha"] == SOURCE_SHA


def test_worker_candidate_rejects_any_high_severity_finding(tmp_path: Path) -> None:
    _worker(tmp_path)
    summary = json.loads((tmp_path / "candidate-summary.json").read_text(encoding="utf-8"))
    summary["vulnerabilities"]["image"]["high"] = 1
    _json(tmp_path, "candidate-summary.json", summary)

    with pytest.raises(ValueError, match="high or critical"):
        verify_evidence("worker", tmp_path, SOURCE_SHA)


def test_worker_candidate_rejects_a_modified_archive(tmp_path: Path) -> None:
    _worker(tmp_path)
    (tmp_path / "candidate.oci.tar").write_bytes(b"changed")

    with pytest.raises(ValueError, match="archive SHA-256"):
        verify_evidence("worker", tmp_path, SOURCE_SHA)


def test_worker_candidate_rejects_mismatched_summary_and_identity(tmp_path: Path) -> None:
    _worker(tmp_path)
    identity = json.loads((tmp_path / "candidate-image.json").read_text(encoding="utf-8"))
    identity["oci_index_digest"] = "sha256:" + "c" * 64
    _json(tmp_path, "candidate-image.json", identity)

    with pytest.raises(ValueError, match="identity records differ"):
        verify_evidence("worker", tmp_path, SOURCE_SHA)


def test_redis_candidate_evidence_is_accepted_when_scans_and_sbom_pass(tmp_path: Path) -> None:
    _redis(tmp_path)

    result = verify_evidence("redis", tmp_path, SOURCE_SHA)

    assert result["status"] == "verified_candidate_evidence"
    assert result["config_digest"] == "sha256:" + "b" * 64


def test_redis_candidate_rejects_a_source_revision_mismatch(tmp_path: Path) -> None:
    _redis(tmp_path)

    with pytest.raises(ValueError, match="source revision"):
        verify_evidence("redis", tmp_path, "2" * 40)


def test_redis_candidate_rejects_incomplete_vulnerability_scans(tmp_path: Path) -> None:
    _redis(tmp_path)
    summary = json.loads((tmp_path / "trivy-summary.json").read_text(encoding="utf-8"))
    summary["reports"]["redis_initializer_image"]["high_critical_count"] = 1
    _json(tmp_path, "trivy-summary.json", summary)

    with pytest.raises(ValueError, match="high or critical"):
        verify_evidence("redis", tmp_path, SOURCE_SHA)
