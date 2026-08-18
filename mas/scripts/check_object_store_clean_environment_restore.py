"""Certify encrypted restore in a fresh local process/environment.

The parent writes only an encrypted ciphertext bundle and scalar manifest to a
temporary directory.  A newly spawned Python process reconstructs that bundle,
loads it into fresh object-store adapters, runs the production encrypted
restore/replication helpers, and removes its fixture objects.  Plaintext and
key material are never emitted in the report.

This is a clean-process/bundle certificate.  It does not claim a clean host,
provider-pair durability, provider-managed KMS/SSE, independent machines,
regional outage recovery, or a production disaster-recovery exercise.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from mas_core.memory import (
    ENCRYPTION_ALGORITHM,
    BlobRef,
    EncryptedBackupManifest,
    InMemoryObjectStore,
    build_encrypted_backup,
    replicate_encrypted_backup,
    verify_encrypted_backup,
)

CHECK_SCHEMA = "aiat.object-store-clean-environment-restore.v1"
PROJECT_ID = "aiat-clean-environment-restore-certification-v1"
PAYLOAD_MARKER = "aiat clean restore fixture plaintext must never enter evidence"
KEY = b"c" * 32
KEY_ID = "aiat-clean-restore-fixture-key-v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON evidence")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--bundle-dir", default="", help=argparse.SUPPRESS)
    return parser


def _child_key() -> bytes:
    raw = os.getenv("AIAT_CLEAN_RESTORE_KEY_B64", "")
    if not raw:
        raise ValueError("clean restore key is unavailable")
    try:
        value = base64.b64decode(raw.encode("ascii"), validate=True)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("clean restore key is malformed") from exc
    if len(value) != 32:
        raise ValueError("clean restore key has an invalid length")
    return value


def _bundle_object_path(bundle_dir: Path, encrypted_key: str) -> Path:
    root = (bundle_dir / "objects").resolve()
    path = (root / encrypted_key).resolve()
    if root not in path.parents:
        raise ValueError("encrypted bundle key escapes its object directory")
    return path


async def _cleanup_store(store: InMemoryObjectStore, *, bucket: str) -> int:
    rows = await store.list_objects(PROJECT_ID, bucket=bucket)
    deleted = 0
    for row in rows:
        full_key = str(row["key"])
        payload, content_type = store._objects[(bucket, full_key)]
        await store.delete(
            BlobRef(
                bucket=bucket,
                key=full_key,
                sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
                content_type=content_type,
            )
        )
        deleted += 1
    return deleted


async def _remaining(store: InMemoryObjectStore, *, bucket: str) -> int:
    return len(await store.list_objects(PROJECT_ID, bucket=bucket))


async def _run_child(bundle_dir: Path) -> dict[str, Any]:
    """Restore a persisted encrypted bundle through fresh adapters."""

    try:
        manifest_payload = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
        if not isinstance(manifest_payload, dict):
            raise ValueError("manifest bundle must be an object")
        manifest = EncryptedBackupManifest.from_dict(manifest_payload)
        key = _child_key()
        fresh_backup = InMemoryObjectStore(bucket="fresh-encrypted-backup")
        fresh_restore = InMemoryObjectStore(bucket="fresh-encrypted-restore")
        for obj in manifest.objects:
            ciphertext = _bundle_object_path(bundle_dir, obj.encrypted_key).read_bytes()
            await fresh_backup.upload(
                PROJECT_ID,
                obj.encrypted_key,
                ciphertext,
                content_type="application/octet-stream",
                bucket="fresh-encrypted-backup",
            )
        backup_check = await verify_encrypted_backup(
            fresh_backup,
            manifest,
            project_id=PROJECT_ID,
            target_bucket="fresh-encrypted-backup",
            key=key,
            clean_target_verified=True,
        )
        restore_check = await replicate_encrypted_backup(
            fresh_backup,
            fresh_restore,
            manifest,
            project_id=PROJECT_ID,
            source_bucket="fresh-encrypted-backup",
            target_bucket="fresh-encrypted-restore",
            key=key,
            require_clean_target=True,
        )
        expected_keys = {
            f"{PROJECT_ID}/{obj.encrypted_key}" for obj in manifest.objects
        }
        actual_keys = {
            str(row["key"])
            for row in await fresh_backup.list_objects(
                PROJECT_ID,
                bucket="fresh-encrypted-backup",
            )
        }
        plaintext_keys_absent = actual_keys == expected_keys
        cleanup_deleted_counts = {
            "fresh_backup": await _cleanup_store(
                fresh_backup,
                bucket="fresh-encrypted-backup",
            ),
            "fresh_restore": await _cleanup_store(
                fresh_restore,
                bucket="fresh-encrypted-restore",
            ),
        }
        remaining_fixture_counts = {
            "fresh_backup": await _remaining(
                fresh_backup,
                bucket="fresh-encrypted-backup",
            ),
            "fresh_restore": await _remaining(
                fresh_restore,
                bucket="fresh-encrypted-restore",
            ),
        }
        manifest_json = json.dumps(manifest.as_dict(), sort_keys=True)
        passed = all(
            (
                backup_check.status == "pass",
                restore_check.status == "pass",
                plaintext_keys_absent,
                PAYLOAD_MARKER not in manifest_json,
                KEY.hex() not in manifest_json,
                all(value == 0 for value in remaining_fixture_counts.values()),
            )
        )
        return {
            "schema_version": CHECK_SCHEMA,
            "status": "pass" if passed else "fail",
            "fresh_process_pid": os.getpid(),
            "fresh_adapter_restore": True,
            "manifest_sha256": manifest.manifest_sha256,
            "object_count": len(manifest.objects),
            "encryption_algorithm": ENCRYPTION_ALGORITHM,
            "backup_verification": backup_check.as_dict(),
            "restore_verification": restore_check.as_dict(),
            "plaintext_keys_absent": plaintext_keys_absent,
            "payload_free": True,
            "key_material_retained": False,
            "cleanup_deleted_counts": cleanup_deleted_counts,
            "remaining_fixture_counts": remaining_fixture_counts,
            "external_network_access_performed": False,
            "external_provider_mutation_performed": False,
            "licence_metadata_is_gate": False,
        }
    except Exception as exc:
        return {
            "schema_version": CHECK_SCHEMA,
            "status": "fail",
            "fresh_process_pid": os.getpid(),
            "reason_type": type(exc).__name__,
            "payload_free": True,
            "key_material_retained": False,
            "external_network_access_performed": False,
            "external_provider_mutation_performed": False,
            "licence_metadata_is_gate": False,
        }


async def _run_parent() -> dict[str, Any]:
    source = InMemoryObjectStore(bucket="source")
    backup = InMemoryObjectStore(bucket="encrypted-backup")
    refs = [
        await source.upload(
            PROJECT_ID,
            "documents/pdr.json",
            PAYLOAD_MARKER.encode("utf-8"),
            content_type="application/json",
        ),
        await source.upload(PROJECT_ID, "artifacts/empty.bin", b""),
    ]
    manifest = await build_encrypted_backup(
        source,
        backup,
        refs,
        project_id=PROJECT_ID,
        source_bucket="source",
        target_bucket="encrypted-backup",
        key=KEY,
        key_id=KEY_ID,
        require_clean_target=True,
    )
    bundle_dir = Path(tempfile.mkdtemp(prefix="aiat-clean-restore-"))
    child_report: dict[str, Any] = {}
    child_stdout = ""
    child_stderr = ""
    child_return_code: int | None = None
    try:
        (bundle_dir / "objects").mkdir(parents=True, exist_ok=True)
        manifest_json = json.dumps(manifest.as_dict(), sort_keys=True, indent=2)
        (bundle_dir / "manifest.json").write_text(manifest_json, encoding="utf-8")
        for obj in manifest.objects:
            source_key = f"{PROJECT_ID}/{obj.encrypted_key}"
            ciphertext = backup._objects[("encrypted-backup", source_key)][0]
            path = _bundle_object_path(bundle_dir, obj.encrypted_key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(ciphertext)

        child_environment = os.environ.copy()
        child_environment["AIAT_CLEAN_RESTORE_KEY_B64"] = base64.b64encode(KEY).decode("ascii")
        completed = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--child",
                "--bundle-dir",
                str(bundle_dir),
                "--json",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=child_environment,
            timeout=45,
        )
        child_return_code = completed.returncode
        child_stdout = completed.stdout
        child_stderr = completed.stderr
        try:
            parsed = json.loads(child_stdout)
            child_report = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            child_report = {}
    except (OSError, subprocess.SubprocessError) as exc:
        child_report = {"status": "fail", "reason_type": type(exc).__name__}
    finally:
        with suppress(Exception):
            shutil.rmtree(bundle_dir)

    child_pid = child_report.get("fresh_process_pid")
    child_passed = (
        child_return_code == 0
        and child_report.get("status") == "pass"
        and isinstance(child_pid, int)
        and child_pid != os.getpid()
        and PAYLOAD_MARKER not in child_stdout
        and PAYLOAD_MARKER not in child_stderr
        and KEY.hex() not in child_stdout
        and KEY.hex() not in child_stderr
    )
    bundle_removed = not bundle_dir.exists()
    passed = child_passed and bundle_removed
    return {
        "schema_version": CHECK_SCHEMA,
        "status": "pass" if passed else "fail",
        "mode": "local-fresh-process-encrypted-bundle",
        "parent_process_pid": os.getpid(),
        "child_process_pid": child_pid,
        "child_process_distinct": isinstance(child_pid, int) and child_pid != os.getpid(),
        "child_return_code": child_return_code,
        "child_report": child_report,
        "bundle_manifest_sha256": manifest.manifest_sha256,
        "bundle_object_count": len(manifest.objects),
        "bundle_removed_after_restore": bundle_removed,
        "payload_free": child_passed,
        "key_material_retained": False,
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "provider_managed_sse_or_kms_checked": False,
        "licence_metadata_is_gate": False,
        "certification_boundary": {
            "persisted_ciphertext_and_scalar_manifest_bundle": "checked",
            "fresh_python_process": "checked",
            "fresh_object_store_adapters": "checked",
            "production_encrypted_restore_helper": "checked",
            "clean_target_before_restore": "checked",
            "bundle_cleanup": "checked",
            "plaintext_or_key_material_in_report": "not_observed",
            "clean_host_or_filesystem_disaster_recovery": "not_checked",
            "provider_pair_or_external_backend": "not_checked",
            "provider_managed_sse_or_kms": "not_checked",
            "independent_machine_or_outage_recovery": "not_checked",
        },
        "notes": [
            "The parent persisted only ciphertext and scalar manifest metadata, then a distinct child process restored through the production encrypted-object verifier.",
            "This is a local clean-process prerequisite, not clean-host, provider-pair, KMS, outage, or disaster-recovery evidence.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.child:
        report = asyncio.run(_run_child(Path(args.bundle_dir)))
    else:
        report = asyncio.run(_run_parent())
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"object-store-clean-environment-restore: {report['status']}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
