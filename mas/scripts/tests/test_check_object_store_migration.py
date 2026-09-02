"""Tests for the bounded object-store migration rehearsal checker."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from check_object_store_migration import MIGRATION_PAYLOAD_MARKER

SCRIPT = Path(__file__).resolve().parents[1] / "check_object_store_migration.py"


def _report(*args: str, env: dict[str, str] | None = None) -> tuple[int, dict[str, object], str]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.returncode, json.loads(result.stdout), result.stderr


def test_fixture_passes_and_records_rollback() -> None:
    code, report, stderr = _report()
    assert code == 0, stderr
    assert report["schema_version"] == "aiat.object-store-migration.v1"
    assert report["mode"] == "fixture"
    assert report["status"] == "pass"
    assert report["final_workflow_status"] == "ROLLED_BACK"


def test_live_requires_all_endpoint_configuration() -> None:
    env = os.environ.copy()
    for key in tuple(name for name in env if name.startswith("AIAT_OBJECT_STORE_MIGRATION_")):
        env.pop(key, None)
    code, report, stderr = _report("--live", env=env)
    assert code == 2, stderr
    assert report["status"] == "blocked"
    assert report["deployment_routing_mutated"] is False
    assert report["licence_metadata_is_gate"] is False
    assert MIGRATION_PAYLOAD_MARKER not in json.dumps(report)


def test_live_requires_explicit_reserved_fixture_and_human_flags() -> None:
    args = (
        "--live",
        "--source-endpoint",
        "http://source.invalid",
        "--source-access-key",
        "source-key",
        "--source-secret-key",
        "source-secret",
        "--target-endpoint",
        "http://target.invalid",
        "--target-access-key",
        "target-key",
        "--target-secret-key",
        "target-secret",
    )
    code, report, stderr = _report(*args)
    assert code == 2, stderr
    assert report["status"] == "blocked"
    assert "seed-fixture" in str(report["reason"])
    assert "source-secret" not in json.dumps(report)

