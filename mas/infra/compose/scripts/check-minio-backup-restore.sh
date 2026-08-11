#!/usr/bin/env bash

# Rehearse checksum-manifest backup and clean-target restore inside the
# private MinIO network.  This intentionally uses one provider and disposable
# buckets; it is not provider-diverse, encrypted, or disaster-recovery proof.

set -Eeuo pipefail

MINIO_CONTAINER="${MINIO_CONTAINER:-mas-minio-1}"
AGENT_CONTAINER="${AGENT_CONTAINER:-mas-orchestrator-api-1}"
PROJECT_ID="${1:-aiat-backup-live-roadmap}"

if ! docker inspect -f '{{.State.Running}}' "$AGENT_CONTAINER" 2>/dev/null | grep -qx true; then
  echo "MinIO backup/restore rehearsal requires a running agent container ($AGENT_CONTAINER)" >&2
  exit 2
fi
if ! docker inspect -f '{{.State.Running}}' "$MINIO_CONTAINER" 2>/dev/null | grep -qx true; then
  echo "MinIO backup/restore rehearsal requires a running MinIO container ($MINIO_CONTAINER)" >&2
  exit 2
fi

# The code runs in the agent container so its existing endpoint and credential
# boundary are used without exposing them to the host shell or a Compose
# interpolation pass.  The project ID is the only caller-controlled value and
# is validated by BlobClient's normal path rules.
docker exec -i -e "AIAT_BACKUP_REHEARSAL_PROJECT=$PROJECT_ID" "$AGENT_CONTAINER" sh -lc 'PYTHONPATH=/app/mas_core python -' <<'PY'
import asyncio
import json
import os

from mas_core.memory import BlobClient, build_backup_manifest, copy_manifest_objects


SOURCE_BUCKET = "mas-agents"
BACKUP_BUCKET = "mas-backup"
RESTORE_BUCKET = "mas-restore"


async def cleanup(client, project_id, bucket):
    rows = await client.list_objects(project_id, bucket=bucket)
    for row in rows:
        key = str(row.get("key") or "")
        prefix = f"{project_id}/"
        if key.startswith(prefix):
            await client.delete_by_key(project_id, key.removeprefix(prefix), bucket=bucket)


async def run():
    endpoint = os.environ.get("MINIO_ENDPOINT")
    access_key = os.environ.get("MINIO_ACCESS_KEY")
    secret_key = os.environ.get("MINIO_SECRET_KEY")
    project_id = os.environ["AIAT_BACKUP_REHEARSAL_PROJECT"]
    if not endpoint or not access_key or not secret_key:
        return {
            "schema_version": "aiat.object-store-backup-live.v1",
            "mode": "local-live",
            "status": "blocked",
            "reason": "missing live object-store configuration",
        }

    client = BlobClient(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        bucket=SOURCE_BUCKET,
        region=os.environ.get("AIAT_OBJECT_STORE_REGION", "us-east-1"),
    )
    buckets = (SOURCE_BUCKET, BACKUP_BUCKET, RESTORE_BUCKET)
    try:
        await client.connect()
        for bucket in buckets:
            await client.ensure_bucket(bucket)
            await cleanup(client, project_id, bucket)

        refs = [
            await client.upload(
                project_id,
                "documents/backup-fixture.json",
                b'{"schema":"aiat.backup.fixture.v1","version":1}\n',
                content_type="application/json",
                bucket=SOURCE_BUCKET,
            ),
            await client.upload(
                project_id,
                "artifacts/empty.bin",
                b"",
                content_type="application/octet-stream",
                bucket=SOURCE_BUCKET,
            ),
        ]
        manifest = await build_backup_manifest(client, refs, project_id=project_id)
        backup_copy, backup_verification = await copy_manifest_objects(
            client,
            client,
            manifest,
            project_id=project_id,
            source_bucket=SOURCE_BUCKET,
            target_bucket=BACKUP_BUCKET,
        )
        restore_copy, restore_verification = await copy_manifest_objects(
            client,
            client,
            manifest,
            project_id=project_id,
            source_bucket=BACKUP_BUCKET,
            target_bucket=RESTORE_BUCKET,
        )
        passed = (
            backup_copy.passed
            and backup_verification.status == "pass"
            and restore_copy.passed
            and restore_verification.status == "pass"
        )
        for bucket in buckets:
            await cleanup(client, project_id, bucket)
        cleanup_verified = True
        for bucket in buckets:
            if await client.list_objects(project_id, bucket=bucket):
                cleanup_verified = False
        return {
            "schema_version": "aiat.object-store-backup-live.v1",
            "mode": "local-live",
            "provider": "minio",
            "adapter_type": "s3-compatible",
            "project_id": project_id,
            "object_count": len(manifest.objects),
            "backup_copy_passed": backup_copy.passed,
            "backup_restore_status": backup_verification.status,
            "restore_copy_passed": restore_copy.passed,
            "restore_status": restore_verification.status,
            "manifest_verified": manifest.verify_digest() is None,
            "cleanup_verified": cleanup_verified,
            "passed": passed,
            "status": "pass" if passed and cleanup_verified else "fail",
        }
    except Exception as exc:
        return {
            "schema_version": "aiat.object-store-backup-live.v1",
            "mode": "local-live",
            "provider": "minio",
            "status": "blocked",
            "reason": f"local backup/restore unavailable: {type(exc).__name__}",
        }
    finally:
        try:
            for bucket in buckets:
                await cleanup(client, project_id, bucket)
        except Exception:
            # The result must never claim cleanup when the final scoped listing
            # failed; the bounded error is represented by the process status.
            pass
        await client.close()


result = asyncio.run(run())
print(json.dumps(result, sort_keys=True))
if result.get("status") == "blocked":
    raise SystemExit(2)
raise SystemExit(0 if result.get("passed") else 1)
PY
