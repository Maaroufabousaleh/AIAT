"""Tests for the maintained documentation authority check."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_docs_index.py"


def test_docs_index_has_one_target_eleven_features_and_three_plans() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=SCRIPT.parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.docs-index-check.v1"
    assert report["status"] == "pass"
    assert report["canonical_feature_count"] == 11
    assert report["canonical_plan_count"] == 3
    assert report["policy"] == {
        "licence_metadata_is_gate": False,
        "programme_scope": "personal-internal-only",
    }
