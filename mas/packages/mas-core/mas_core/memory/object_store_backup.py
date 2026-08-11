"""Checksum manifests and clean-target restore verification for object storage.

The helpers in this module are deliberately provider-neutral.  A backup is a
stable manifest of project-scoped object keys, checksums, sizes, and content
types.  Copying is delegated to :mod:`object_store_migration`, while restore
verification requires an exact key set and a checksum/read-back match for every
manifest entry.  No helper deletes source data, cuts over a provider, or claims
that a live backup target is encrypted or durable without provider evidence.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .blob import BlobRef
from .object_store_migration import ObjectStoreCopyReport, verify_and_copy_blobs

if TYPE_CHECKING:
    from .object_store_conformance import ObjectStoreAdapter

OBJECT_STORE_BACKUP_SCHEMA = "aiat.object-store-backup.v1"
OBJECT_STORE_RESTORE_SCHEMA = "aiat.object-store-restore.v1"


@dataclass(frozen=True, slots=True)
class BackupObject:
    """One logical project-scoped object in a backup manifest."""

    key: str
    sha256: str
    size_bytes: int
    content_type: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "content_type": self.content_type,
        }


@dataclass(frozen=True, slots=True)
class BackupManifest:
    """Stable checksum inventory for one project prefix."""

    schema_version: str
    project_id: str
    source_adapter_type: str
    objects: tuple[BackupObject, ...]
    manifest_sha256: str

    @staticmethod
    def _canonical_payload(
        *,
        schema_version: str,
        project_id: str,
        source_adapter_type: str,
        objects: tuple[BackupObject, ...],
    ) -> bytes:
        payload = {
            "schema_version": schema_version,
            "project_id": project_id,
            "source_adapter_type": source_adapter_type,
            "objects": [obj.as_dict() for obj in objects],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @classmethod
    def create(
        cls,
        *,
        project_id: str,
        source_adapter_type: str,
        objects: tuple[BackupObject, ...],
    ) -> BackupManifest:
        if not objects:
            raise ValueError("backup manifest cannot be empty")
        ordered = tuple(sorted(objects, key=lambda obj: obj.key))
        keys = [obj.key for obj in ordered]
        if len(keys) != len(set(keys)):
            raise ValueError("backup manifest contains duplicate logical keys")
        digest = hashlib.sha256(
            cls._canonical_payload(
                schema_version=OBJECT_STORE_BACKUP_SCHEMA,
                project_id=project_id,
                source_adapter_type=source_adapter_type,
                objects=ordered,
            )
        ).hexdigest()
        return cls(
            schema_version=OBJECT_STORE_BACKUP_SCHEMA,
            project_id=project_id,
            source_adapter_type=source_adapter_type,
            objects=ordered,
            manifest_sha256=digest,
        )

    def verify_digest(self) -> None:
        expected = self.create(
            project_id=self.project_id,
            source_adapter_type=self.source_adapter_type,
            objects=self.objects,
        ).manifest_sha256
        if self.schema_version != OBJECT_STORE_BACKUP_SCHEMA or self.manifest_sha256 != expected:
            raise ValueError("backup manifest digest or schema does not match its contents")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "source_adapter_type": self.source_adapter_type,
            "object_count": len(self.objects),
            "objects": [obj.as_dict() for obj in self.objects],
            "manifest_sha256": self.manifest_sha256,
        }


@dataclass(frozen=True, slots=True)
class RestoreVerification:
    """Exact key/checksum verification result for a restored prefix."""

    schema_version: str
    project_id: str
    target_bucket: str
    object_count: int
    checked_object_count: int
    status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "target_bucket": self.target_bucket,
            "object_count": self.object_count,
            "checked_object_count": self.checked_object_count,
            "status": self.status,
        }


async def build_backup_manifest(
    store: ObjectStoreAdapter,
    refs: list[BlobRef] | tuple[BlobRef, ...],
    *,
    project_id: str,
) -> BackupManifest:
    """Validate source refs and create a deterministic non-empty manifest."""

    prefix = f"{project_id}/"
    objects: list[BackupObject] = []
    seen: set[str] = set()
    for ref in sorted(refs, key=lambda item: (item.bucket, item.key)):
        if not ref.key.startswith(prefix) or ref.key == prefix:
            raise ValueError(f"source key {ref.key!r} is outside project prefix {prefix!r}")
        logical_key = ref.key.removeprefix(prefix)
        if logical_key in seen:
            raise ValueError(f"duplicate logical key in backup inventory: {logical_key}")
        payload = await store.download(ref)
        actual_sha = hashlib.sha256(payload).hexdigest()
        if actual_sha != ref.sha256 or len(payload) != ref.size_bytes:
            raise ValueError(f"source checksum or size mismatch for {ref.key}")
        seen.add(logical_key)
        objects.append(
            BackupObject(
                key=logical_key,
                sha256=actual_sha,
                size_bytes=len(payload),
                content_type=ref.content_type,
            )
        )
    return BackupManifest.create(
        project_id=project_id,
        source_adapter_type=str(getattr(store, "adapter_type", type(store).__name__)),
        objects=tuple(objects),
    )


async def verify_restored_manifest(
    store: ObjectStoreAdapter,
    manifest: BackupManifest,
    *,
    project_id: str,
    target_bucket: str,
) -> RestoreVerification:
    """Require exact project keys and checksum/read-back parity."""

    manifest.verify_digest()
    if manifest.project_id != project_id:
        raise ValueError("restore project does not match the backup manifest")
    rows = await store.list_objects(project_id, bucket=target_bucket)
    actual_keys = {str(row.get("key")) for row in rows}
    expected_keys = {f"{project_id}/{obj.key}" for obj in manifest.objects}
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        extra = sorted(actual_keys - expected_keys)
        raise ValueError(f"restore key set differs: missing={missing!r}, extra={extra!r}")
    for obj in manifest.objects:
        ref = BlobRef(
            bucket=target_bucket,
            key=f"{project_id}/{obj.key}",
            sha256=obj.sha256,
            size_bytes=obj.size_bytes,
            content_type=obj.content_type,
        )
        payload = await store.download(ref)
        if hashlib.sha256(payload).hexdigest() != obj.sha256 or len(payload) != obj.size_bytes:
            raise ValueError(f"restore checksum or size mismatch for {obj.key}")
    return RestoreVerification(
        schema_version=OBJECT_STORE_RESTORE_SCHEMA,
        project_id=project_id,
        target_bucket=target_bucket,
        object_count=len(manifest.objects),
        checked_object_count=len(manifest.objects),
        status="pass",
    )


async def copy_manifest_objects(
    source: ObjectStoreAdapter,
    target: ObjectStoreAdapter,
    manifest: BackupManifest,
    *,
    project_id: str,
    source_bucket: str,
    target_bucket: str,
) -> tuple[ObjectStoreCopyReport, RestoreVerification]:
    """Copy a manifest from one provider and verify the target read-back."""

    manifest.verify_digest()
    if manifest.project_id != project_id:
        raise ValueError("copy project does not match the backup manifest")
    refs = tuple(
        BlobRef(
            bucket=source_bucket,
            key=f"{project_id}/{obj.key}",
            sha256=obj.sha256,
            size_bytes=obj.size_bytes,
            content_type=obj.content_type,
        )
        for obj in manifest.objects
    )
    copy_report = await verify_and_copy_blobs(
        source,
        target,
        refs,
        project_id=project_id,
        target_bucket=target_bucket,
    )
    if not copy_report.passed:
        raise ValueError("backup/restore copy did not pass checksum parity")
    verification = await verify_restored_manifest(
        target,
        manifest,
        project_id=project_id,
        target_bucket=target_bucket,
    )
    return copy_report, verification


__all__ = [
    "OBJECT_STORE_BACKUP_SCHEMA",
    "OBJECT_STORE_RESTORE_SCHEMA",
    "BackupManifest",
    "BackupObject",
    "RestoreVerification",
    "build_backup_manifest",
    "copy_manifest_objects",
    "verify_restored_manifest",
]
