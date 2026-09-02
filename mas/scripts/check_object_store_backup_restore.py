"""Run checksum-manifest backup and clean-target restore evidence.

The default fixture uses three deterministic in-memory adapters: source,
backup, and restore.  ``--live`` requires three explicitly configured
S3-compatible endpoints and performs a project-scoped source inventory,
checksum manifest, backup copy, an empty-target preflight, and clean-target
restore verification.  It never deletes source objects or performs a provider
cutover.  Missing configuration, an empty inventory, a non-empty restore
prefix, or an unavailable provider returns exit code 2 with a bounded
``blocked`` report.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from typing import Any

from mas_core.memory import (
    BlobClient,
    BlobRef,
    InMemoryObjectStore,
    build_backup_manifest,
    copy_manifest_objects,
)

BACKUP_SCHEMA = "aiat.object-store-backup-runner.v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the full report as JSON")
    parser.add_argument("--live", action="store_true", help="use three configured S3-compatible providers")
    parser.add_argument("--source-endpoint", default=os.getenv("AIAT_OBJECT_STORE_SOURCE_ENDPOINT"))
    parser.add_argument("--source-access-key", default=os.getenv("AIAT_OBJECT_STORE_SOURCE_ACCESS_KEY"))
    parser.add_argument("--source-secret-key", default=os.getenv("AIAT_OBJECT_STORE_SOURCE_SECRET_KEY"))
    parser.add_argument("--backup-endpoint", default=os.getenv("AIAT_OBJECT_STORE_BACKUP_ENDPOINT"))
    parser.add_argument("--backup-access-key", default=os.getenv("AIAT_OBJECT_STORE_BACKUP_ACCESS_KEY"))
    parser.add_argument("--backup-secret-key", default=os.getenv("AIAT_OBJECT_STORE_BACKUP_SECRET_KEY"))
    parser.add_argument("--restore-endpoint", default=os.getenv("AIAT_OBJECT_STORE_RESTORE_ENDPOINT"))
    parser.add_argument("--restore-access-key", default=os.getenv("AIAT_OBJECT_STORE_RESTORE_ACCESS_KEY"))
    parser.add_argument("--restore-secret-key", default=os.getenv("AIAT_OBJECT_STORE_RESTORE_SECRET_KEY"))
    parser.add_argument("--source-bucket", default=os.getenv("AIAT_OBJECT_STORE_SOURCE_BUCKET", "mas-agents"))
    parser.add_argument("--backup-bucket", default=os.getenv("AIAT_OBJECT_STORE_BACKUP_BUCKET", "mas-backup"))
    parser.add_argument("--restore-bucket", default=os.getenv("AIAT_OBJECT_STORE_RESTORE_BUCKET", "mas-restore"))
    parser.add_argument("--region", default=os.getenv("AIAT_OBJECT_STORE_REGION", "us-east-1"))
    parser.add_argument("--project-id", default=os.getenv("AIAT_OBJECT_STORE_BACKUP_PROJECT", "aiat-backup-live"))
    parser.add_argument("--prefix", default=os.getenv("AIAT_OBJECT_STORE_BACKUP_PREFIX", ""))
    return parser


def _blocked(reason: str, *, missing: list[str] | None = None) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": BACKUP_SCHEMA,
        "mode": "live",
        "status": "blocked",
        "reason": reason,
    }
    if missing:
        report["missing_configuration"] = missing
    return report


async def _inventory_refs(
    client: BlobClient,
    *,
    project_id: str,
    bucket: str,
    prefix: str,
) -> list[BlobRef]:
    refs: list[BlobRef] = []
    for row in await client.list_objects(project_id, prefix=prefix, bucket=bucket):
        full_key = str(row["key"])
        project_prefix = f"{project_id}/"
        if not full_key.startswith(project_prefix):
            continue
        relative_key = full_key.removeprefix(project_prefix)
        payload = await client.download_by_key(project_id, relative_key, bucket=bucket)
        refs.append(
            BlobRef(
                bucket=bucket,
                key=full_key,
                sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
            )
        )
    return refs


async def _run_fixture() -> dict[str, Any]:
    source = InMemoryObjectStore(bucket="source")
    backup = InMemoryObjectStore(bucket="backup")
    restore = InMemoryObjectStore(bucket="restore")
    project_id = "aiat-backup-fixture"
    refs = [
        await source.upload(project_id, "documents/pdr.json", b'{"version":1}', content_type="application/json"),
        await source.upload(project_id, "artifacts/empty.bin", b""),
    ]
    manifest = await build_backup_manifest(source, refs, project_id=project_id)
    backup_copy, backup_verification = await copy_manifest_objects(
        source,
        backup,
        manifest,
        project_id=project_id,
        source_bucket="source",
        target_bucket="backup",
    )
    restore_copy, restore_verification = await copy_manifest_objects(
        backup,
        restore,
        manifest,
        project_id=project_id,
        source_bucket="backup",
        target_bucket="restore",
        require_clean_target=True,
    )
    return {
        "schema_version": BACKUP_SCHEMA,
        "mode": "fixture",
        "status": "pass",
        "project_id": project_id,
        "manifest": manifest.as_dict(),
        "backup_copy": backup_copy.as_dict(),
        "backup_verification": backup_verification.as_dict(),
        "restore_copy": restore_copy.as_dict(),
        "restore_verification": restore_verification.as_dict(),
    }


async def _run_live(args: argparse.Namespace) -> dict[str, Any]:
    required = (
        ("source_endpoint", args.source_endpoint),
        ("source_access_key", args.source_access_key),
        ("source_secret_key", args.source_secret_key),
        ("backup_endpoint", args.backup_endpoint),
        ("backup_access_key", args.backup_access_key),
        ("backup_secret_key", args.backup_secret_key),
        ("restore_endpoint", args.restore_endpoint),
        ("restore_access_key", args.restore_access_key),
        ("restore_secret_key", args.restore_secret_key),
    )
    missing = [name for name, value in required if not value]
    if missing:
        return _blocked(f"missing live configuration: {', '.join(missing)}", missing=missing)

    source = BlobClient(
        str(args.source_endpoint),
        access_key=str(args.source_access_key),
        secret_key=str(args.source_secret_key),
        bucket=str(args.source_bucket),
        region=str(args.region),
    )
    backup = BlobClient(
        str(args.backup_endpoint),
        access_key=str(args.backup_access_key),
        secret_key=str(args.backup_secret_key),
        bucket=str(args.backup_bucket),
        region=str(args.region),
    )
    restore = BlobClient(
        str(args.restore_endpoint),
        access_key=str(args.restore_access_key),
        secret_key=str(args.restore_secret_key),
        bucket=str(args.restore_bucket),
        region=str(args.region),
    )
    project_id = str(args.project_id)
    try:
        await source.connect()
        await backup.connect()
        await restore.connect()
        refs = await _inventory_refs(
            source,
            project_id=project_id,
            bucket=str(args.source_bucket),
            prefix=str(args.prefix),
        )
        if not refs:
            return _blocked(
                "source inventory is empty; refusing to report a no-op backup/restore as success"
            )
        manifest = await build_backup_manifest(source, refs, project_id=project_id)
        backup_copy, backup_verification = await copy_manifest_objects(
            source,
            backup,
            manifest,
            project_id=project_id,
            source_bucket=str(args.source_bucket),
            target_bucket=str(args.backup_bucket),
        )
        restore_copy, restore_verification = await copy_manifest_objects(
            backup,
            restore,
            manifest,
            project_id=project_id,
            source_bucket=str(args.backup_bucket),
            target_bucket=str(args.restore_bucket),
            require_clean_target=True,
        )
        return {
            "schema_version": BACKUP_SCHEMA,
            "mode": "live",
            "status": "pass",
            "project_id": project_id,
            "source_endpoint": str(args.source_endpoint),
            "backup_endpoint": str(args.backup_endpoint),
            "restore_endpoint": str(args.restore_endpoint),
            "manifest": manifest.as_dict(),
            "backup_copy": backup_copy.as_dict(),
            "backup_verification": backup_verification.as_dict(),
            "restore_copy": restore_copy.as_dict(),
            "restore_verification": restore_verification.as_dict(),
        }
    except Exception as exc:  # pragma: no cover - depends on external providers
        return _blocked(f"live backup/restore unavailable: {type(exc).__name__}: {exc}")
    finally:
        await source.close()
        await backup.close()
        await restore.close()


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    return await (_run_live(args) if args.live else _run_fixture())


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = asyncio.run(_run(args))
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"object-store-backup-restore: {report['status']} — {report['reason'] if report['status'] == 'blocked' else report['project_id']}")
    return 2 if report["status"] == "blocked" else (0 if report["status"] == "pass" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
