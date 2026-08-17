"""Regression tests for the worker/mail-edge evidence certificate."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_worker_mail_edge_checker_passes_with_integration_sources() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/check_worker_mail_edge_coverage.py",
            "--json",
            "--require-integration",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.worker-mail-edge-coverage-check.v1"
    assert report["coverage_schema"] == "aiat.worker-mail-edge-coverage.v1"
    assert report["status"] == "pass"
    assert report["worker_trace_status"] == "pass"
    assert report["mail_edge_status"] == "pass"
    assert report["mail_edge_event_counts"]["bounced"] == 1
    assert report["network_access_performed"] is False
    assert report["mutation_performed"] is False
    assert report["payload_free_report"] is True
    assert report["licence_metadata_is_gate"] is False
