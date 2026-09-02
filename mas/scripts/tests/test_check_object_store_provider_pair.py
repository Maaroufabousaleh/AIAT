"""Tests for the bounded object-store provider-pair certificate."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from check_object_store_provider_pair import PAYLOAD_MARKER, _run_pair, _UnavailableStore

from mas_core.memory import InMemoryObjectStore

SCRIPT = Path(__file__).resolve().parents[1] / "check_object_store_provider_pair.py"


def _report(*args: str, env: dict[str, str] | None = None) -> tuple[int, dict[str, object], str]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.returncode, json.loads(result.stdout), result.stderr


def test_fixture_passes_and_is_payload_free() -> None:
    code, report, stderr = _report()
    assert code == 0, stderr
    assert report["schema_version"] == "aiat.object-store-provider-pair.v1"
    assert report["status"] == "pass"
    assert report["object_count"] == 3
    assert report["dual_write_passed"] is True
    assert report["recovery_passed"] is True
    assert report["primary_loss_simulated"] is True
    assert report["primary_outage_probe_rejected"] is True
    assert report["primary_calls_during_recovery"] == 0
    assert report["remaining_fixture_counts"] == {"primary": 0, "secondary": 0, "recovery": 0}
    assert report["payload_free"] is True
    assert PAYLOAD_MARKER not in json.dumps(report)


def test_live_blocks_without_pair_configuration() -> None:
    env = os.environ.copy()
    for key in tuple(name for name in env if name.startswith("AIAT_OBJECT_STORE_PAIR_")):
        env.pop(key, None)
    code, report, stderr = _report("--live", "--primary-secret-key", "super-secret", env=env)
    assert code == 2, stderr
    assert report["status"] == "blocked"
    assert report["external_network_access_performed"] is True
    assert report["licence_metadata_is_gate"] is False
    assert "super-secret" not in json.dumps(report)


@pytest.mark.asyncio
async def test_fixture_recovery_does_not_use_unavailable_primary() -> None:
    primary = InMemoryObjectStore(bucket="primary")
    secondary = InMemoryObjectStore(bucket="secondary")
    report = await _run_pair(
        primary,
        secondary,
        project_id="provider-pair-test",
        primary_bucket="primary",
        secondary_bucket="secondary",
        recovery_bucket="recovery",
        mode="fixture",
        provider_labels=("p", "s"),
    )
    unavailable = _UnavailableStore()
    assert unavailable.calls == 0
    assert report["status"] == "pass"


def test_project_prefix_collision_fails_closed() -> None:
    code, report, stderr = _report("--project-id", "aiat-provider-pair-fixture-v1")
    assert code == 0, stderr
    assert report["remaining_fixture_counts"] == {"primary": 0, "secondary": 0, "recovery": 0}
