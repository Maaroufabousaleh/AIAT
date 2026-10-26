"""First-party worker identity must not be treated as third-party metadata."""

from __future__ import annotations

from pathlib import Path

import yaml
from check_worker_reconciliation import reconcile

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = REPO_ROOT / "mas/workers/aiat_deterministic_file_worker.yaml"


def _reconcile_candidate(tmp_path: Path, *, mutate=None) -> dict:
    raw = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    if mutate is not None:
        mutate(raw)
    workers_dir = tmp_path / "workers"
    workers_dir.mkdir()
    (workers_dir / "aiat_deterministic_file_worker.yaml").write_text(
        yaml.safe_dump(raw, sort_keys=False), encoding="utf-8"
    )
    company_path = tmp_path / "company.yaml"
    company_path.write_text(
        yaml.safe_dump({"departments": [], "worker_assignments": []}),
        encoding="utf-8",
    )
    return reconcile(workers_dir=workers_dir, company_manifest=company_path)


def test_canonical_aiat_candidate_reconciles_without_third_party_catalogue_entry(tmp_path) -> None:
    report = _reconcile_candidate(tmp_path)

    assert report["errors"] == []
    assert report["status"] == "pass"
    assert {
        "worker": "aiat_deterministic_file_worker",
        "evidence": "security_scan_status",
        "status": "pending",
    } in report["pending_evidence"]


def test_first_party_oci_digest_must_match_the_runtime_reference(tmp_path) -> None:
    report = _reconcile_candidate(
        tmp_path,
        mutate=lambda manifest: manifest["runtime"]["adapter_config"].update(
            image="aiat-deterministic-file-worker@sha256:" + "0" * 64
        ),
    )

    assert any(
        "first-party OCI image reference disagrees" in error
        for error in report["errors"]
    )


def test_first_party_source_repository_must_be_canonical_https(tmp_path) -> None:
    report = _reconcile_candidate(
        tmp_path,
        mutate=lambda manifest: manifest["metadata"].update(
            source_repo="http://github.com/Maaroufabousaleh/AIAT"
        ),
    )

    assert any(
        "AIAT first-party source_repo must be the canonical HTTPS AIAT repository" in error
        for error in report["errors"]
    )
