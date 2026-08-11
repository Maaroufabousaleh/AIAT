"""Checksum-verified object copy and parity tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mas_core.memory import (
    OBJECT_STORE_COPY_SCHEMA,
    InMemoryObjectStore,
    verify_and_copy_blobs,
)


@pytest.mark.asyncio
async def test_live_copy_runner_fails_closed_without_provider_configuration() -> None:
    env = os.environ.copy()
    for key in (
        "AIAT_OBJECT_STORE_SOURCE_ENDPOINT",
        "AIAT_OBJECT_STORE_SOURCE_ACCESS_KEY",
        "AIAT_OBJECT_STORE_SOURCE_SECRET_KEY",
        "AIAT_OBJECT_STORE_TARGET_ENDPOINT",
        "AIAT_OBJECT_STORE_TARGET_ACCESS_KEY",
        "AIAT_OBJECT_STORE_TARGET_SECRET_KEY",
    ):
        env.pop(key, None)
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[3] / "scripts" / "check_object_store_copy.py"),
            "--live",
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    report = json.loads(result.stdout)
    assert result.returncode == 2
    assert report["status"] == "blocked"
    assert "missing live configuration" in report["reason"]


@pytest.mark.asyncio
async def test_verified_copy_preserves_project_scope_and_parity() -> None:
    source = InMemoryObjectStore(bucket="source")
    target = InMemoryObjectStore(bucket="backup")
    project_id = "copy-project"
    refs = [
        await source.upload(project_id, "artifacts/a.txt", b"alpha", content_type="text/plain"),
        await source.upload(project_id, "artifacts/empty.bin", b""),
    ]

    report = await verify_and_copy_blobs(
        source,
        target,
        refs,
        project_id=project_id,
        target_bucket="backup",
    )

    assert report.schema_version == OBJECT_STORE_COPY_SCHEMA
    assert report.passed is True
    assert report.counts == {"PASS": 2, "FAIL": 0}
    assert all(case.target_bucket == "backup" for case in report.cases)
    assert await target.exists(project_id, "artifacts/a.txt", bucket="backup") is True
    assert await target.exists(project_id, "artifacts/empty.bin", bucket="backup") is True
    assert await source.exists(project_id, "artifacts/a.txt", bucket="source") is True


@pytest.mark.asyncio
async def test_verified_copy_rejects_out_of_scope_reference() -> None:
    source = InMemoryObjectStore(bucket="source")
    target = InMemoryObjectStore(bucket="backup")
    ref = await source.upload("other-project", "artifact.bin", b"payload")

    report = await verify_and_copy_blobs(
        source,
        target,
        [ref],
        project_id="copy-project",
        target_bucket="backup",
    )

    assert report.passed is False
    assert report.cases[0].status == "FAIL"
    assert "outside project prefix" in report.cases[0].detail


@pytest.mark.asyncio
async def test_verified_copy_detects_source_corruption_without_copying() -> None:
    source = InMemoryObjectStore(bucket="source")
    target = InMemoryObjectStore(bucket="backup")
    ref = await source.upload("copy-project", "artifact.bin", b"payload")
    source._objects[(ref.bucket, ref.key)] = (b"tampered", ref.content_type)

    report = await verify_and_copy_blobs(
        source,
        target,
        [ref],
        project_id="copy-project",
        target_bucket="backup",
    )

    assert report.passed is False
    assert report.cases[0].status == "FAIL"
    assert await target.exists("copy-project", "artifact.bin", bucket="backup") is False


@pytest.mark.asyncio
async def test_verified_copy_is_stable_for_input_order() -> None:
    source = InMemoryObjectStore(bucket="source")
    first_target = InMemoryObjectStore(bucket="backup")
    second_target = InMemoryObjectStore(bucket="backup")
    project_id = "copy-project"
    first = await source.upload(project_id, "z.bin", b"z")
    second = await source.upload(project_id, "a.bin", b"a")

    first_report = (
        await verify_and_copy_blobs(
            source,
            first_target,
            [first, second],
            project_id=project_id,
            target_bucket="backup",
        )
    ).as_dict()
    second_report = (
        await verify_and_copy_blobs(
            source,
            second_target,
            [second, first],
            project_id=project_id,
            target_bucket="backup",
        )
    ).as_dict()

    assert first_report == second_report
