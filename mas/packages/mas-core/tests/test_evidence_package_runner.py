from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_project_evidence_package_fixture_is_deterministic() -> None:
    script = Path(__file__).resolve().parents[3] / "scripts" / "check_project_evidence_package.py"
    result = subprocess.run(
        [sys.executable, str(script), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "pass"
    assert report["package"]["schema_version"] == "aiat.project-evidence-package.v1"
    assert report["required_categories_present"] is True
    assert report["package"]["notices"] == [
        {
            "artifact_id": "artifact-security",
            "field": "license",
            "value": "internal-use-notice",
        }
    ]


def test_project_evidence_package_live_mode_fails_closed() -> None:
    script = Path(__file__).resolve().parents[3] / "scripts" / "check_project_evidence_package.py"
    result = subprocess.run(
        [sys.executable, str(script), "--live", "--json"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["status"] == "blocked"
