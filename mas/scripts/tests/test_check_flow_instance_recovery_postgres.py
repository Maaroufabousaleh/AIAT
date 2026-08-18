"""Regression coverage for the bounded Postgres flow-instance checker."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_flow_instance_recovery_postgres.py"


def test_flow_instance_postgres_checker_fails_closed_without_database_configuration() -> None:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"AIAT_FLOW_INSTANCE_RECOVERY_EVIDENCE_DSN", "PGBOUNCER_DSN", "POSTGRES_DSN"}
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=SCRIPT.parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.flow-instance-recovery-postgres-certification.v1"
    assert report["status"] == "blocked"
    assert report["mutation_performed"] is False
    assert report["external_provider_mutation_performed"] is False
    assert report["licence_metadata_is_gate"] is False

