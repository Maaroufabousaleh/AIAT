"""Read-only steward/candidate certification readiness contract tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from mas_core.worker_registry.worker_steward_readiness import (
    evaluate_worker_steward_readiness,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKER_ID = "00000000-0000-4000-8000-000000000201"
CANDIDATE_ID = "00000000-0000-4000-8000-000000000202"


def _inputs() -> dict[str, Any]:
    return {
        "worker": {
            "id": WORKER_ID,
            "source_repo": "https://github.com/example/worker",
            "version_pin": "1.2.3",
        },
        "steward": {
            "worker_id": WORKER_ID,
            "status": "READY",
            "provenance": {
                "canonical_source_repository": "https://github.com/example/worker",
                "exact_release": "1.2.3",
                "transport_type": "process",
                "security_scan_status": "passed",
                "license_id": "AGPL-3.0-only",
                "redistribution_status": "notice-only",
            },
        },
        "candidate": {
            "candidate_id": CANDIDATE_ID,
            "worker_id": WORKER_ID,
            "intake_status": "CERTIFYING",
            "bundle": {
                "bundle_id": "00000000-0000-4000-8000-000000000203",
                "documentation_refs": ["00000000-0000-4000-8000-000000000204"],
                "verified_capabilities": {"capabilities": {"read_only": True}},
            },
            "adapter": {
                "adapter_id": "00000000-0000-4000-8000-000000000205",
                "version": "adapter-1.0.0",
                "content_hash": "adapter-hash",
            },
        },
        "worker_id": WORKER_ID,
        "candidate_id": CANDIDATE_ID,
    }


def test_complete_candidate_snapshot_is_ready_without_license_gate() -> None:
    report = evaluate_worker_steward_readiness(**_inputs())
    assert report["status"] == "pass"
    assert report["licence_metadata_is_gate"] is False
    assert report["checks"]["compatibility_matrix"]["status"] == "not_checked"
    assert report["blockers"] == []


def test_steward_and_candidate_technical_evidence_fail_closed() -> None:
    inputs = _inputs()
    steward = dict(inputs["steward"])
    steward["status"] = "PROVISIONING"
    steward["provenance"] = {
        "canonical_source_repository": "https://github.com/example/worker",
        "exact_release": "1.2.3",
        "security_scan_status": "findings_review_required",
        "license_id": "AGPL-3.0-only",
    }
    inputs["steward"] = steward
    candidate = dict(inputs["candidate"])
    candidate["intake_status"] = "SOURCE_REVIEW"
    candidate["bundle"] = {"bundle_id": "bundle-1", "documentation_refs": []}
    candidate["adapter"] = {"adapter_id": "adapter-1", "version": "1.0.0"}
    inputs["candidate"] = candidate
    report = evaluate_worker_steward_readiness(**inputs)
    codes = {item["code"] for item in report["blockers"]}
    assert report["status"] == "blocked"
    assert "steward_not_ready" in codes
    assert "security_scan_not_passed" in codes
    assert "candidate_stage_not_certifiable" in codes
    assert "documentation_snapshot_missing" in codes
    assert "capability_snapshot_missing" in codes
    assert "runtime_adapter_hash_missing" in codes
    assert not any("license" in code or "licence" in code for code in codes)


def test_approved_candidate_requires_passed_certification_record() -> None:
    inputs = _inputs()
    candidate = dict(inputs["candidate"])
    candidate["intake_status"] = "APPROVED"
    inputs["candidate"] = candidate
    report = evaluate_worker_steward_readiness(**inputs)
    assert report["status"] == "blocked"
    assert {item["code"] for item in report["blockers"]} == {"candidate_certification_missing"}

    candidate["evidence"] = {"certification": {"passed": True}}
    report = evaluate_worker_steward_readiness(**inputs)
    assert report["status"] == "pass"


def test_failed_supplied_compatibility_matrix_blocks_but_license_does_not() -> None:
    report = evaluate_worker_steward_readiness(
        **_inputs(),
        compatibility_matrices=[{"adapter_version": "adapter-1.0.0", "passed": False}],
    )
    assert report["status"] == "blocked"
    assert {item["code"] for item in report["blockers"]} == {"compatibility_matrix_failed"}


def test_live_mode_requires_explicit_candidate_and_never_mutates() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/check_worker_steward_readiness.py",
            "--live",
            "--json",
            "--url",
            "http://localhost:8000",
            "--api-key",
            "test-only-not-secret",
            "--worker-id",
            WORKER_ID,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 2
    assert report["status"] == "blocked"
    assert report["no_mutation"] is True
    assert "candidate-id" in report["reason"]


def test_fixture_cli_is_secret_safe() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_worker_steward_readiness.py", "--json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.worker-steward-readiness-check.v1"
    assert report["status"] == "pass"
    assert report["licence_metadata_is_gate"] is False
    assert "api_key" not in result.stdout
