"""Tests for the bounded multipart object-store boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mas_core.memory import InMemoryObjectStore
from mas_core.memory.object_store_multipart import (
    MIN_PART_SIZE_BYTES,
    MultipartUploadConfig,
    run_object_store_multipart_probe,
)

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_object_store_multipart.py"


def test_multipart_config_rejects_unbounded_plan() -> None:
    with pytest.raises(ValueError, match="5 MiB"):
        MultipartUploadConfig(part_size_bytes=MIN_PART_SIZE_BYTES - 1)
    with pytest.raises(ValueError, match="64 MiB"):
        MultipartUploadConfig(payload_sizes=(32 * 1024 * 1024, 33 * 1024 * 1024))


@pytest.mark.asyncio
async def test_fixture_multipart_readback_abort_and_cleanup() -> None:
    config = MultipartUploadConfig(
        payload_sizes=(6 * 1024 * 1024, 11 * 1024 * 1024),
        project_id="multipart-test-project",
    )
    report = await run_object_store_multipart_probe(
        InMemoryObjectStore(),
        provider="fixture",
        config=config,
    )

    assert report.status == "pass"
    assert report.abort_verified is True
    assert report.cleanup_verified is True
    assert report.error_count == 0
    assert [row["actual_part_count"] for row in report.rows] == [2, 3]


def test_cli_fixture_is_machine_readable() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", "--payload-size", str(6 * 1024 * 1024)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.object-store-multipart.v1"
    assert report["status"] == "pass"
    assert report["multipart_plan"]["expected_part_counts"] == [2]
    assert report["abort_verified"] is True
    assert report["cleanup_verified"] is True


def test_cli_live_requires_both_provider_configurations() -> None:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AIAT_MINIO_") and not key.startswith("AIAT_SEAWEEDFS_")
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--live", "--json"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["status"] == "blocked"
    assert "minio.endpoint" in report["reason"]
    assert "seaweedfs.endpoint" in report["reason"]
