"""Tests for the default externally sourced steward lifecycle fixture."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from mas_core.worker_registry.steward import (
    CompatibilityMatrix,
    ExternalProvenance,
    ExternalWorkerSteward,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_compatibility_matrix_is_recorded_as_steward_owned_evidence() -> None:
    steward = ExternalWorkerSteward(
        worker_id="matrix-worker",
        provenance=ExternalProvenance(
            canonical_source_repository="https://github.com/example/worker",
            exact_release="1.0.0",
            transport_type="process",
            security_scan_status="passed",
        ),
    )
    matrix = CompatibilityMatrix(
        runtime_version="1.0.0",
        adapter_version="1.0.0",
        contract_version="aiat.adapter.v1",
        fixtures=("worker_contract",),
        passed=True,
    )

    recorded = steward.record_compatibility_matrix(matrix)

    assert recorded is not matrix
    assert recorded == steward.compatibility_matrices[0]
    assert recorded.passed is True


def test_external_default_workers_cover_steward_candidate_matrix_and_rollback_contract() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_worker_steward_contract.py", "--json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.worker-steward-contract-check.v1"
    assert report["status"] == "pass"
    assert report["external_worker_count"] == 2
    assert all(row["status"] == "pass" for row in report["rows"])
    assert all(
        row["real_security_scan_status"] == "findings_review_required"
        for row in report["rows"]
    )
    assert report["boundary"]["live_canary"] == "not_checked"
    assert report["policy"]["licence_metadata_is_gate"] is False
