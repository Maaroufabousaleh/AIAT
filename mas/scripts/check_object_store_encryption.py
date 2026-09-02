"""Certify the local encrypted object-store backup envelope.

The fixture encrypts a scoped source inventory with AIAT-owned AES-256-GCM,
replicates ciphertext between provider-neutral adapters, verifies authenticated
decryption and clean-target behavior, and checks wrong-key/tamper rejection.
Key material and fixture payloads never enter the JSON report.  This is a local
envelope certificate; provider-managed SSE/KMS, external Garage/R2/B2, clean
environment restore, and disaster-recovery evidence remain separate gates.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from typing import Any

from mas_core.memory import (
    ENCRYPTION_ALGORITHM,
    BlobRef,
    InMemoryObjectStore,
    build_encrypted_backup,
    replicate_encrypted_backup,
    verify_encrypted_backup,
)

CHECK_SCHEMA = "aiat.object-store-encryption-certification.v1"
PROJECT_ID = "aiat-encrypted-backup-certification-v1"
PAYLOAD_MARKER = "aiat encrypted backup fixture plaintext must never enter evidence"
KEY_ID = "aiat-fixture-encryption-key-v1"
KEY = b"a" * 32


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON evidence")
    return parser


async def _delete_project(store: InMemoryObjectStore, *, bucket: str) -> int:
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


async def _run_fixture() -> dict[str, Any]:
    source = InMemoryObjectStore(bucket="source")
    backup = InMemoryObjectStore(bucket="encrypted-backup")
    restore = InMemoryObjectStore(bucket="encrypted-restore")
    blocked_target = InMemoryObjectStore(bucket="blocked-restore")

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
    backup_verification = await verify_encrypted_backup(
        backup,
        manifest,
        project_id=PROJECT_ID,
        target_bucket="encrypted-backup",
        key=KEY,
        clean_target_verified=True,
    )
    restore_verification = await replicate_encrypted_backup(
        backup,
        restore,
        manifest,
        project_id=PROJECT_ID,
        source_bucket="encrypted-backup",
        target_bucket="encrypted-restore",
        key=KEY,
        require_clean_target=True,
    )

    await blocked_target.upload(PROJECT_ID, "stale.bin", b"stale")
    clean_target_rejected_before_mutation = False
    try:
        await replicate_encrypted_backup(
            backup,
            blocked_target,
            manifest,
            project_id=PROJECT_ID,
            source_bucket="encrypted-backup",
            target_bucket="blocked-restore",
            key=KEY,
            require_clean_target=True,
        )
    except ValueError as exc:
        clean_target_rejected_before_mutation = "restore target must be empty" in str(exc)

    wrong_key_rejected = False
    try:
        await verify_encrypted_backup(
            backup,
            manifest,
            project_id=PROJECT_ID,
            target_bucket="encrypted-backup",
            key=b"b" * 32,
        )
    except ValueError as exc:
        wrong_key_rejected = "authentication failed" in str(exc)

    tamper_rejected = False
    tamper_key = ("encrypted-backup", f"{PROJECT_ID}/encrypted/documents/pdr.json.enc")
    tampered_payload, content_type = backup._objects[tamper_key]
    backup._objects[tamper_key] = (
        tampered_payload[:-1] + bytes([tampered_payload[-1] ^ 1]),
        content_type,
    )
    try:
        await verify_encrypted_backup(
            backup,
            manifest,
            project_id=PROJECT_ID,
            target_bucket="encrypted-backup",
            key=KEY,
        )
    except ValueError as exc:
        tamper_rejected = "SHA-256 mismatch" in str(exc) or "authentication failed" in str(exc)

    backup_keys = {
        str(row["key"])
        for row in await backup.list_objects(PROJECT_ID, bucket="encrypted-backup")
    }
    expected_keys = {
        f"{PROJECT_ID}/{obj.encrypted_key}" for obj in manifest.objects
    }
    plaintext_keys_absent = backup_keys == expected_keys
    manifest_json = json.dumps(manifest.as_dict(), sort_keys=True)
    manifest_secret_free = PAYLOAD_MARKER not in manifest_json and KEY.hex() not in manifest_json

    cleanup_deleted_counts = {
        "source": await _delete_project(source, bucket="source"),
        "encrypted_backup": await _delete_project(backup, bucket="encrypted-backup"),
        "encrypted_restore": await _delete_project(restore, bucket="encrypted-restore"),
        "blocked_restore": await _delete_project(blocked_target, bucket="blocked-restore"),
    }
    remaining_fixture_counts = {
        "source": await _remaining(source, bucket="source"),
        "encrypted_backup": await _remaining(backup, bucket="encrypted-backup"),
        "encrypted_restore": await _remaining(restore, bucket="encrypted-restore"),
        "blocked_restore": await _remaining(blocked_target, bucket="blocked-restore"),
    }
    passed = all(
        (
            backup_verification.status == "pass",
            restore_verification.status == "pass",
            plaintext_keys_absent,
            manifest_secret_free,
            wrong_key_rejected,
            tamper_rejected,
            clean_target_rejected_before_mutation,
            all(value == 0 for value in remaining_fixture_counts.values()),
        )
    )
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "local-in-memory-encrypted-backup",
        "status": "pass" if passed else "fail",
        "encryption_algorithm": ENCRYPTION_ALGORITHM,
        "key_id": KEY_ID,
        "manifest": manifest.as_dict(),
        "backup_verification": backup_verification.as_dict(),
        "restore_verification": restore_verification.as_dict(),
        "plaintext_keys_absent": plaintext_keys_absent,
        "manifest_secret_free": manifest_secret_free,
        "wrong_key_rejected": wrong_key_rejected,
        "tamper_rejected": tamper_rejected,
        "clean_target_rejected_before_mutation": clean_target_rejected_before_mutation,
        "payload_free": True,
        "cleanup_deleted_counts": cleanup_deleted_counts,
        "remaining_fixture_counts": remaining_fixture_counts,
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "provider_managed_sse_or_kms_checked": False,
        "licence_metadata_is_gate": False,
        "certification_boundary": {
            "aiat_owned_aes_256_gcm_envelope": "checked",
            "opaque_key_id_only_manifest": "checked",
            "ciphertext_checksum_and_readback": "checked",
            "wrong_key_and_tamper_rejection": "checked",
            "clean_target_preflight": "checked",
            "provider_managed_sse_or_kms": "not_checked",
            "external_garage_r2_b2_or_other_backend": "not_checked",
            "clean_environment_disaster_recovery": "not_checked",
            "independent_host_or_provider_outage": "not_checked",
        },
        "notes": [
            "Plaintext is encrypted before the backup adapter receives it; the manifest retains scalar checksums, nonce metadata, and an opaque key ID only.",
            "The local fixture does not claim provider-managed encryption, KMS/key custody, external backend durability, or clean-environment disaster recovery.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = asyncio.run(_run_fixture())
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"object-store-encryption: {report['status']}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
