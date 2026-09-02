"""Tests for the gateway-worker/mail-edge composition certificate."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

MAS_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = MAS_ROOT / "scripts" / "check_gateway_worker_mail_edge_fixture.py"


def test_gateway_worker_mail_edge_fixture_passes_without_external_calls() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=MAS_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.gateway-worker-mail-edge-fixture.v1"
    assert report["status"] == "pass"
    assert report["controller_terminal_state"] == "SUCCEEDED"
    assert report["gateway_call_count"] == 1
    assert report["worker_trace_status"] == "pass"
    assert report["mail_edge_status"] == "pass"
    assert report["combined_status"] == "pass"
    assert report["usage_attribution_match"] is True
    assert report["verified_webhook_and_bounce"] is True
    assert report["network_access_performed"] is False
    assert report["external_provider_call_performed"] is False
    assert report["sandbox_execution_performed"] is False
    assert report["payload_free_report"] is True
    assert report["licence_metadata_is_gate"] is False
