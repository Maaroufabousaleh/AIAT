from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "check_maf_runtime.py"


def test_maf_runtime_certification_profile_contract_is_secret_safe() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.maf-runtime-certification.v1"
    assert report["profile"]["declared_versions"] == {
        "agent-framework": "==1.13.0",
        "mcp": "==1.29.0",
    }
    assert report["licence_metadata_is_gate"] is False
    assert report["mutation_performed"] is False
    assert report["network_access_performed"] is False
    assert report["status"] in {"pass", "blocked"}
    assert result.returncode in {0, 2}
