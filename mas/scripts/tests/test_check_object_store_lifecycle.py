"""Checker coverage for the object-store lifecycle contract."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_object_store_lifecycle.py"


def test_lifecycle_checker_is_scalar_and_cleans_fixture() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.object-store-lifecycle-check.v1"
    assert report["status"] == "pass"
    assert report["payload_free"] is True
    assert report["external_network_access_performed"] is False
    assert report["fixture_cleanup_verified"] is True
    assert report["remaining_fixture_count"] == 0
    assert report["plan"]["size_mismatch_keys"]
    assert report["execution"]["cleanup_verified"] is True
    assert "fixture:expired.bin" not in result.stdout
