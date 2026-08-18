"""Tests for the bounded object-store benchmark contract."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mas_core.memory import InMemoryObjectStore
from mas_core.memory.object_store_benchmark import (
    ObjectStoreBenchmarkConfig,
    run_object_store_benchmark,
)

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_object_store_benchmarks.py"


def test_benchmark_config_rejects_unbounded_payloads() -> None:
    with pytest.raises(ValueError, match="16 MiB"):
        ObjectStoreBenchmarkConfig(payload_sizes=(16 * 1024 * 1024 + 1,))
    with pytest.raises(ValueError, match="concurrency"):
        ObjectStoreBenchmarkConfig(concurrency=0)
    with pytest.raises(ValueError, match="64 MiB"):
        ObjectStoreBenchmarkConfig(payload_sizes=(16 * 1024 * 1024,), concurrency=5)


@pytest.mark.asyncio
async def test_fixture_benchmark_reads_back_checksums_and_cleans_objects() -> None:
    store = InMemoryObjectStore()
    config = ObjectStoreBenchmarkConfig(payload_sizes=(32, 128), project_id="benchmark-project")

    report = await run_object_store_benchmark(
        store,
        provider="fixture",
        config=config,
    )

    assert report.status == "pass"
    assert report.error_count == 0
    assert [row["size_bytes"] for row in report.rows] == [32, 128]
    assert all(row["status"] == "pass" for row in report.rows)
    assert report.cleanup_verified is True
    assert await store.list_objects(config.project_id, bucket=config.bucket) == []


@pytest.mark.asyncio
async def test_concurrent_large_benchmark_is_bounded_and_cleans_objects() -> None:
    store = InMemoryObjectStore()
    config = ObjectStoreBenchmarkConfig(
        payload_sizes=(64, 128 * 1024),
        project_id="benchmark-concurrent-project",
        concurrency=3,
    )

    report = await run_object_store_benchmark(store, provider="fixture", config=config)

    assert report.status == "pass"
    assert report.error_count == 0
    assert report.cleanup_verified is True
    assert len(report.rows) == 6
    assert len({row["key"] for row in report.rows}) == 6
    assert {row["concurrency_index"] for row in report.rows} == {0, 1, 2}
    assert await store.list_objects(config.project_id, bucket=config.bucket) == []


def test_cli_fixture_is_machine_readable() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", "--payload-size", "32"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.object-store-benchmark.v1"
    assert report["mode"] == "fixture"
    assert report["status"] == "pass"
    assert report["decision"] == "not_applicable"
    assert report["rows"][0]["size_bytes"] == 32
    assert report["benchmark_plan"] == {
        "payload_sizes_bytes": [32],
        "concurrency": 1,
        "case_count": 1,
        "total_payload_bytes": 32,
    }


def test_cli_live_requires_both_provider_configurations() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--live", "--json"],
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
