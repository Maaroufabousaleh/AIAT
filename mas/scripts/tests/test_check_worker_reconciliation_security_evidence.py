"""Regression tests for worker reconciliation's retained scan evidence schema."""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from pathlib import Path

from check_worker_reconciliation import _security_evidence_index


def _write_evidence(tmp_path: Path, *, scanned_file_count_status: str, scanner_status: str) -> Path:
    path = tmp_path / "security-scan-evidence.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "policy": {
                    "security_scan_is_technical_gate": True,
                    "licence_metadata_is_gate": False,
                },
                "scans": [
                    {
                        "id": "same-source-carry-forward",
                        "scanner": "semgrep",
                        "scanner_version": "1.168.0",
                        "source_repo": "https://example.invalid/upstream",
                        "source_revision": "v1.18.21",
                        "target_workers": ["coding_worker"],
                        "status": "findings_review_required",
                        "raw_findings_retained": False,
                        "normalized_result_retained": True,
                        "source_snapshot_retained": True,
                        "scanned_file_count": None,
                        "scanned_file_count_status": scanned_file_count_status,
                        "source_snapshot_file_count": 6520,
                        "finding_count": 7,
                        "engine_warning_count": 0,
                        "scanner_status": scanner_status,
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def test_reconciliation_accepts_scanner_file_count_not_repeated_for_carry_forward(tmp_path) -> None:
    path = _write_evidence(
        tmp_path,
        scanned_file_count_status="not_repeated_in_current_scan",
        scanner_status="prior_exact_source_scan_findings_carried_forward",
    )
    errors: list[str] = []

    index = _security_evidence_index(path, errors)

    assert errors == []
    assert index["same-source-carry-forward"]["finding_count"] == 7


def test_reconciliation_does_not_accept_unqualified_not_repeated_scan_count(tmp_path) -> None:
    path = _write_evidence(
        tmp_path,
        scanned_file_count_status="not_repeated_in_current_scan",
        scanner_status="pass",
    )
    errors: list[str] = []

    _security_evidence_index(path, errors)

    assert any("scanned_file_count must be" in error for error in errors)
