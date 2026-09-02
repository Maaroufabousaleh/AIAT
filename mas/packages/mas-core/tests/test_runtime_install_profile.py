"""Tests for the reproducible default runtime install contract."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_default_runtime_install_profile_matches_lock_and_production_image() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_runtime_install_profile.py", "--json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.runtime-install-profile-check.v1"
    assert report["status"] == "pass"
    assert report["profile"] == "runtime-default"
    assert report["locked_versions"] == {
        "crewai": "1.6.1",
        "langgraph": "0.6.11",
    }
    assert report["dockerfile_installs_profile"] is True
    assert report["certification_boundary"]["runtime_imports"] == "not_checked"
    assert report["policy"]["licence_metadata_is_gate"] is False
