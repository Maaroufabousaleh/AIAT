"""
memory — Persistent storage helpers.

Exports
-------
AgentStorage    Async Postgres wrapper (asyncpg + SQLAlchemy core).
                All queries filter by agent_id automatically.
                Connection string points to PgBouncer.
BlobClient      Thin async MinIO/S3 wrapper (aioboto3).
                upload(project_id, path, data) → BlobRef
                download(blob_ref) → bytes
                Enforces agent_id prefix on all keys.
CheckpointStore Thin helpers: save_checkpoint / load_checkpoint / delete_checkpoint
                backed by the agent_checkpoints Postgres table.
Object-store conformance/copy/backup fixtures
                deterministic contract, checksum-parity, manifest, and
                restore reports; CLI runners can target a configured provider
                but no live provider or migration claim is implied.
metadata        SQLAlchemy MetaData with all table definitions.
"""

from mas_core.memory.blob import BlobClient, BlobRef
from mas_core.memory.checkpoints import CheckpointStore
from mas_core.memory.models import metadata
from mas_core.memory.object_store_conformance import (
    OBJECT_STORE_CONFORMANCE_SCHEMA,
    InMemoryObjectStore,
    ObjectStoreAdapter,
    ObjectStoreConformanceCase,
    ObjectStoreConformanceReport,
    run_object_store_conformance,
)
from mas_core.memory.object_store_migration import (
    OBJECT_STORE_COPY_SCHEMA,
    ObjectStoreCopyCase,
    ObjectStoreCopyReport,
    verify_and_copy_blobs,
)
from mas_core.memory.object_store_backup import (
    OBJECT_STORE_BACKUP_SCHEMA,
    OBJECT_STORE_RESTORE_SCHEMA,
    BackupManifest,
    BackupObject,
    RestoreVerification,
    build_backup_manifest,
    copy_manifest_objects,
    verify_restored_manifest,
)
from mas_core.memory.storage import AgentStorage

__all__ = [
    "AgentStorage",
    "BlobClient",
    "BlobRef",
    "CheckpointStore",
    "OBJECT_STORE_CONFORMANCE_SCHEMA",
    "InMemoryObjectStore",
    "ObjectStoreAdapter",
    "ObjectStoreConformanceCase",
    "ObjectStoreConformanceReport",
    "run_object_store_conformance",
    "OBJECT_STORE_COPY_SCHEMA",
    "ObjectStoreCopyCase",
    "ObjectStoreCopyReport",
    "verify_and_copy_blobs",
    "OBJECT_STORE_BACKUP_SCHEMA",
    "OBJECT_STORE_RESTORE_SCHEMA",
    "BackupManifest",
    "BackupObject",
    "RestoreVerification",
    "build_backup_manifest",
    "copy_manifest_objects",
    "verify_restored_manifest",
    "metadata",
]
