"""Tests for the deterministic model-gateway worker adapter checker."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

MAS_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = MAS_ROOT / "scripts" / "check_gateway_worker_adapter.py"


def test_gateway_worker_adapter_fixture_passes_without_external_calls() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=MAS_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.gateway-worker-adapter.v1"
    assert report["status"] == "pass"
    assert report["adapter_type"] == "aiat_gateway"
    assert report["controller_terminal_state"] == "SUCCEEDED"
    assert report["usage_attribution_match"] is True
    assert report["external_provider_call_performed"] is False
    assert report["sandbox_execution_performed"] is False
    assert report["licence_metadata_is_gate"] is False
