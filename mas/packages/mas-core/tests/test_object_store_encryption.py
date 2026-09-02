"""Authenticated encrypted object-store backup tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from mas_core.memory import (
    ENCRYPTION_ALGORITHM,
    OBJECT_STORE_ENCRYPTED_BACKUP_SCHEMA,
    OBJECT_STORE_ENCRYPTED_RESTORE_SCHEMA,
    EncryptedBackupManifest,
    InMemoryObjectStore,
    build_encrypted_backup,
    replicate_encrypted_backup,
    verify_encrypted_backup,
)

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_object_store_encryption.py"
CLEAN_RESTORE_SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "check_object_store_clean_environment_restore.py"
)


@pytest.mark.asyncio
async def test_encrypted_backup_replication_and_authenticated_restore() -> None:
    source = InMemoryObjectStore(bucket="source")
    backup = InMemoryObjectStore(bucket="backup")
    restore = InMemoryObjectStore(bucket="restore")
    project_id = "encrypted-backup-project"
    key = b"k" * 32
    refs = [
        await source.upload(project_id, "docs/pdr.json", b"private-pdr", content_type="application/json"),
        await source.upload(project_id, "artifacts/empty.bin", b""),
    ]

    manifest = await build_encrypted_backup(
        source,
        backup,
        refs,
        project_id=project_id,
        source_bucket="source",
        target_bucket="backup",
        key=key,
        key_id="fixture-key-v1",
        require_clean_target=True,
    )
    assert manifest.schema_version == OBJECT_STORE_ENCRYPTED_BACKUP_SCHEMA
    assert manifest.encryption_algorithm == ENCRYPTION_ALGORITHM
    assert manifest.key_id == "fixture-key-v1"
    serialized = json.dumps(manifest.as_dict(), sort_keys=True)
    assert "private-pdr" not in serialized
    assert key.hex() not in serialized

    rows = await backup.list_objects(project_id, bucket="backup")
    assert [row["key"] for row in rows] == [
        f"{project_id}/encrypted/artifacts/empty.bin.enc",
        f"{project_id}/encrypted/docs/pdr.json.enc",
    ]
    assert await backup.exists(project_id, "docs/pdr.json", bucket="backup") is False

    backup_check = await verify_encrypted_backup(
        backup,
        manifest,
        project_id=project_id,
        target_bucket="backup",
        key=key,
        clean_target_verified=True,
    )
    restore_check = await replicate_encrypted_backup(
        backup,
        restore,
        manifest,
        project_id=project_id,
        source_bucket="backup",
        target_bucket="restore",
        key=key,
        require_clean_target=True,
    )
    assert backup_check.schema_version == OBJECT_STORE_ENCRYPTED_RESTORE_SCHEMA
    assert backup_check.checked_object_count == 2
    assert restore_check.checked_object_count == 2
    assert restore_check.clean_target_verified is True


@pytest.mark.asyncio
async def test_encrypted_restore_rejects_wrong_key_and_ciphertext_tampering() -> None:
    source = InMemoryObjectStore(bucket="source")
    backup = InMemoryObjectStore(bucket="backup")
    project_id = "encrypted-backup-project"
    key = b"k" * 32
    ref = await source.upload(project_id, "artifact.bin", b"private-payload")
    manifest = await build_encrypted_backup(
        source,
        backup,
        [ref],
        project_id=project_id,
        source_bucket="source",
        target_bucket="backup",
        key=key,
        key_id="fixture-key-v1",
    )

    with pytest.raises(ValueError, match="authentication failed"):
        await verify_encrypted_backup(
            backup,
            manifest,
            project_id=project_id,
            target_bucket="backup",
            key=b"wrong" * 6 + b"!" * 2,
        )

    stored_key = ("backup", f"{project_id}/encrypted/artifact.bin.enc")
    stored = backup._objects[stored_key]
    backup._objects[stored_key] = (stored[0][:-1] + bytes([stored[0][-1] ^ 1]), stored[1])
    with pytest.raises(ValueError, match="SHA-256 mismatch|authentication failed"):
        await verify_encrypted_backup(
            backup,
            manifest,
            project_id=project_id,
            target_bucket="backup",
            key=key,
        )


@pytest.mark.asyncio
async def test_encrypted_replication_refuses_non_empty_target_before_mutation() -> None:
    source = InMemoryObjectStore(bucket="source")
    backup = InMemoryObjectStore(bucket="backup")
    restore = InMemoryObjectStore(bucket="restore")
    project_id = "encrypted-backup-project"
    key = b"k" * 32
    ref = await source.upload(project_id, "artifact.bin", b"payload")
    manifest = await build_encrypted_backup(
        source,
        backup,
        [ref],
        project_id=project_id,
        source_bucket="source",
        target_bucket="backup",
        key=key,
        key_id="fixture-key-v1",
    )
    await restore.upload(project_id, "stale.bin", b"old")

    with pytest.raises(ValueError, match="restore target must be empty"):
        await replicate_encrypted_backup(
            backup,
            restore,
            manifest,
            project_id=project_id,
            source_bucket="backup",
            target_bucket="restore",
            key=key,
            require_clean_target=True,
        )

    assert await restore.exists(project_id, "stale.bin", bucket="restore") is True
    assert await restore.exists(project_id, "encrypted/artifact.bin.enc", bucket="restore") is False


@pytest.mark.asyncio
async def test_encrypted_manifest_bundle_round_trip_rejects_count_or_digest_tampering() -> None:
    source = InMemoryObjectStore(bucket="source")
    backup = InMemoryObjectStore(bucket="backup")
    project_id = "encrypted-manifest-bundle-project"
    ref = await source.upload(project_id, "artifact.bin", b"payload")
    manifest = await build_encrypted_backup(
        source,
        backup,
        [ref],
        project_id=project_id,
        source_bucket="source",
        target_bucket="backup",
        key=b"m" * 32,
        key_id="fixture-key-v1",
    )

    bundle = manifest.as_dict()
    restored = EncryptedBackupManifest.from_dict(bundle)
    assert restored == manifest

    bad_count = dict(bundle)
    bad_count["object_count"] = 0
    with pytest.raises(ValueError, match="manifest bundle is malformed"):
        EncryptedBackupManifest.from_dict(bad_count)

    bad_digest = dict(bundle)
    bad_digest["manifest_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="digest or schema"):
        EncryptedBackupManifest.from_dict(bad_digest)


def test_encrypted_backup_checker_fixture_is_payload_free() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "pass"
    assert report["encryption_algorithm"] == ENCRYPTION_ALGORITHM
    assert report["manifest"]["object_count"] == 2
    assert report["plaintext_keys_absent"] is True
    assert report["wrong_key_rejected"] is True
    assert report["tamper_rejected"] is True
    assert report["clean_target_rejected_before_mutation"] is True
    assert "aiat encrypted backup fixture plaintext must never enter evidence" not in result.stdout
    assert report["payload_free"] is True
    assert report["licence_metadata_is_gate"] is False


def test_clean_environment_restore_checker_uses_fresh_process_and_is_payload_free() -> None:
    result = subprocess.run(
        [sys.executable, str(CLEAN_RESTORE_SCRIPT), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.object-store-clean-environment-restore.v1"
    assert report["status"] == "pass"
    assert report["child_process_distinct"] is True
    assert report["child_return_code"] == 0
    assert report["bundle_removed_after_restore"] is True
    assert report["child_report"]["fresh_adapter_restore"] is True
    assert report["child_report"]["remaining_fixture_counts"] == {
        "fresh_backup": 0,
        "fresh_restore": 0,
    }
    assert report["payload_free"] is True
    assert report["key_material_retained"] is False
    assert report["licence_metadata_is_gate"] is False
    assert "aiat clean restore fixture plaintext must never enter evidence" not in result.stdout
    assert (b"c" * 32).hex() not in result.stdout
