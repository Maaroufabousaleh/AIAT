from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

MAS_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = MAS_ROOT / "check_mail_edge_postgres_ingress.py"


def test_postgres_mail_edge_checker_fails_closed_without_database_configuration() -> None:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("IDENTITY_DATABASE")
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=MAS_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.mail-edge-postgres-ingress-certification.v1"
    assert report["status"] == "blocked"
    assert report["mutation_performed"] is False
    assert report["external_provider_mutation_performed"] is False
    assert report["licence_metadata_is_gate"] is False
