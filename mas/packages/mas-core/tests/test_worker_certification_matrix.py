"""Deterministic default-worker certification matrix regression tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml  # type: ignore[import-untyped]

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "generate_worker_certification_matrix.py"
ARTIFACT = REPO_ROOT / "docs" / "provenance" / "worker_certification_matrix.yaml"
WORKERS = REPO_ROOT / "workers"


def test_checked_in_matrix_is_deterministic_and_covers_all_worker_manifests() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    matrix = yaml.safe_load(ARTIFACT.read_text(encoding="utf-8"))
    assert matrix["schema_version"] == "aiat.worker-certification-matrix.v1"
    assert matrix["programme_scope"] == "personal-internal-only"
    assert matrix["license_handling"] == "metadata-only"
    rows = matrix["workers"]
    assert isinstance(rows, list)
    assert len(rows) == 39
    worker_ids = [row["worker_id"] for row in rows]
    assert worker_ids == sorted(worker_ids)
    assert len(worker_ids) == len(set(worker_ids))
    assert set(worker_ids) == {path.stem for path in WORKERS.glob("*.yaml")}
    assert all("license" not in row and "licence" not in row for row in rows)


def test_matrix_separates_technical_evidence_from_live_certification() -> None:
    matrix = yaml.safe_load(ARTIFACT.read_text(encoding="utf-8"))
    rows = matrix["workers"]

    assert all(row["evidence_state"] in {
        "pending_live_certification",
        "pending_security_evidence",
        "declared_certified_live_retest_required",
        "blocked",
    } for row in rows)
    coding = next(row for row in rows if row["worker_id"] == "coding_worker")
    tester = next(row for row in rows if row["worker_id"] == "tester")
    assert coding["security_scan_status"] == "findings_review_required"
    assert tester["security_scan_status"] == "findings_review_required"
    assert coding["evidence_state"] == "pending_security_evidence"
    assert tester["evidence_state"] == "pending_security_evidence"


def test_matrix_cli_json_is_secret_safe() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "api_key" not in json.dumps({"stdout": result.stdout, "stderr": result.stderr})
