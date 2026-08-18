"""Tests for bounded object-store resource profiling."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mas_core.memory import InMemoryObjectStore
from mas_core.memory.object_store_resource_profile import (
    ObjectStoreResourceProfileConfig,
    run_object_store_resource_profile,
)

SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "check_object_store_resource_profile.py"
)


def test_resource_profile_config_reuses_benchmark_bounds() -> None:
    with pytest.raises(ValueError, match="16 MiB"):
        ObjectStoreResourceProfileConfig(payload_sizes=(16 * 1024 * 1024 + 1,))
    with pytest.raises(ValueError, match="concurrency"):
        ObjectStoreResourceProfileConfig(concurrency=0)
    with pytest.raises(ValueError, match="64 MiB"):
        ObjectStoreResourceProfileConfig(
            payload_sizes=(16 * 1024 * 1024,),
            concurrency=5,
        )


@pytest.mark.asyncio
async def test_fixture_profile_records_scalar_resources_and_cleanup() -> None:
    config = ObjectStoreResourceProfileConfig(
        payload_sizes=(32, 128 * 1024),
        project_id="resource-profile-project",
        concurrency=2,
    )
    store = InMemoryObjectStore()

    report = await run_object_store_resource_profile(
        store,
        provider="fixture",
        config=config,
    )

    assert report.status == "pass"
    assert report.error_count == 0
    assert report.cleanup_verified is True
    assert report.measurement_source in {"procfs", "resource"}
    assert report.wall_time_ms >= 0
    assert report.cpu_time_ms >= 0
    assert report.rss_peak_bytes is not None
    assert len(report.rows) == 4
    assert await store.list_objects(config.project_id, bucket=config.bucket) == []


def test_cli_fixture_is_machine_readable() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", "--payload-size", "32", "--concurrency", "1"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.object-store-resource-profile.v1"
    assert report["mode"] == "fixture"
    assert report["status"] == "pass"
    assert report["decision"] == "not_applicable"
    assert report["measurement_source"] in {"procfs", "resource"}
    assert report["benchmark_plan"] == {
        "payload_sizes_bytes": [32],
        "concurrency": 1,
        "case_count": 1,
        "total_payload_bytes": 32,
    }


def test_cli_live_requires_both_provider_configurations() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--live", "--json", "--concurrency", "1"],
        check=False,
        capture_output=True,
        text=True,
        env={
            key: value
            for key, value in os.environ.items()
            if not key.startswith("AIAT_MINIO_")
            and not key.startswith("AIAT_SEAWEEDFS_")
        },
    )

    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["status"] == "blocked"
    assert report["decision"] == "operator_review_required"
    assert "minio.endpoint" in report["reason"]
    assert "seaweedfs.endpoint" in report["reason"]
