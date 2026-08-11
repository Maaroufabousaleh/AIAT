"""Checksum manifest backup and restore tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mas_core.memory import (
    OBJECT_STORE_BACKUP_SCHEMA,
    InMemoryObjectStore,
    build_backup_manifest,
    copy_manifest_objects,
    verify_restored_manifest,
)

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_object_store_backup_restore.py"


@pytest.mark.asyncio
async def test_manifest_backup_and_clean_restore_round_trip() -> None:
    source = InMemoryObjectStore(bucket="source")
    backup = InMemoryObjectStore(bucket="backup")
    restore = InMemoryObjectStore(bucket="restore")
    project_id = "backup-project"
    refs = [
        await source.upload(project_id, "docs/pdr.json", b"pdr", content_type="application/json"),
        await source.upload(project_id, "artifacts/empty.bin", b""),
    ]

    manifest = await build_backup_manifest(source, refs, project_id=project_id)
    assert manifest.schema_version == OBJECT_STORE_BACKUP_SCHEMA
    assert manifest.manifest_sha256 == (await build_backup_manifest(source, refs, project_id=project_id)).manifest_sha256

    backup_copy, backup_check = await copy_manifest_objects(
        source,
        backup,
        manifest,
        project_id=project_id,
        source_bucket="source",
        target_bucket="backup",
    )
    restore_copy, restore_check = await copy_manifest_objects(
        backup,
        restore,
        manifest,
        project_id=project_id,
        source_bucket="backup",
        target_bucket="restore",
    )

    assert backup_copy.passed is True
    assert restore_copy.passed is True
    assert backup_check.checked_object_count == 2
    assert restore_check.checked_object_count == 2


@pytest.mark.asyncio
async def test_restore_rejects_tampered_target_bytes() -> None:
    source = InMemoryObjectStore(bucket="source")
    restore = InMemoryObjectStore(bucket="restore")
    project_id = "backup-project"
    ref = await source.upload(project_id, "artifact.bin", b"payload")
    manifest = await build_backup_manifest(source, [ref], project_id=project_id)
    await copy_manifest_objects(
        source,
        restore,
        manifest,
        project_id=project_id,
        source_bucket="source",
        target_bucket="restore",
    )

    stored = restore._objects[("restore", f"{project_id}/artifact.bin")]
    restore._objects[("restore", f"{project_id}/artifact.bin")] = (b"tampered", stored[1])
    with pytest.raises(ValueError, match="SHA-256 mismatch|checksum"):
        await verify_restored_manifest(
            restore,
            manifest,
            project_id=project_id,
            target_bucket="restore",
        )


@pytest.mark.asyncio
async def test_manifest_rejects_empty_and_out_of_scope_inventory() -> None:
    source = InMemoryObjectStore(bucket="source")
    with pytest.raises(ValueError, match="cannot be empty"):
        await build_backup_manifest(source, [], project_id="backup-project")
    other = await source.upload("other-project", "artifact.bin", b"payload")
    with pytest.raises(ValueError, match="outside project prefix"):
        await build_backup_manifest(source, [other], project_id="backup-project")


def test_backup_restore_runner_fixture_and_live_block() -> None:
    fixture = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert fixture.returncode == 0
    report = json.loads(fixture.stdout)
    assert report["schema_version"] == "aiat.object-store-backup-runner.v1"
    assert report["status"] == "pass"
    assert report["manifest"]["object_count"] == 2

    env = os.environ.copy()
    for key in (
        "AIAT_OBJECT_STORE_SOURCE_ENDPOINT",
        "AIAT_OBJECT_STORE_SOURCE_ACCESS_KEY",
        "AIAT_OBJECT_STORE_SOURCE_SECRET_KEY",
        "AIAT_OBJECT_STORE_BACKUP_ENDPOINT",
        "AIAT_OBJECT_STORE_BACKUP_ACCESS_KEY",
        "AIAT_OBJECT_STORE_BACKUP_SECRET_KEY",
        "AIAT_OBJECT_STORE_RESTORE_ENDPOINT",
        "AIAT_OBJECT_STORE_RESTORE_ACCESS_KEY",
        "AIAT_OBJECT_STORE_RESTORE_SECRET_KEY",
    ):
        env.pop(key, None)
    blocked = subprocess.run(
        [sys.executable, str(SCRIPT), "--live", "--json", "--source-secret-key", "super-secret"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert blocked.returncode == 2
    assert "super-secret" not in blocked.stdout
    assert json.loads(blocked.stdout)["status"] == "blocked"
