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
Encrypted object-store backup envelope
                AES-256-GCM ciphertext replication with opaque key IDs,
                authenticated read-back, and clean-target verification.
metadata        SQLAlchemy MetaData with all table definitions.
"""

from mas_core.memory.blob import BlobClient, BlobRef
from mas_core.memory.checkpoints import CheckpointStore
from mas_core.memory.models import metadata
from mas_core.memory.object_store_backup import (
    OBJECT_STORE_BACKUP_SCHEMA,
    OBJECT_STORE_RESTORE_SCHEMA,
    BackupManifest,
    BackupObject,
    RestoreVerification,
    assert_clean_restore_target,
    build_backup_manifest,
    copy_manifest_objects,
    verify_restored_manifest,
)
from mas_core.memory.object_store_conformance import (
    OBJECT_STORE_CONFORMANCE_SCHEMA,
    InMemoryObjectStore,
    ObjectStoreAdapter,
    ObjectStoreConformanceCase,
    ObjectStoreConformanceReport,
    run_object_store_conformance,
)
from mas_core.memory.object_store_encryption import (
    ENCRYPTION_ALGORITHM,
    OBJECT_STORE_ENCRYPTED_BACKUP_SCHEMA,
    OBJECT_STORE_ENCRYPTED_RESTORE_SCHEMA,
    EncryptedBackupManifest,
    EncryptedBackupObject,
    EncryptedRestoreVerification,
    build_encrypted_backup,
    replicate_encrypted_backup,
    verify_encrypted_backup,
)
from mas_core.memory.object_store_migration import (
    OBJECT_STORE_COPY_SCHEMA,
    ObjectStoreCopyCase,
    ObjectStoreCopyReport,
    verify_and_copy_blobs,
)
from mas_core.memory.object_store_multipart import (
    MAX_MULTIPART_PARTS,
    MAX_MULTIPART_PAYLOAD_BYTES,
    MIN_PART_SIZE_BYTES,
    OBJECT_STORE_MULTIPART_SCHEMA,
    MultipartObjectStoreAdapter,
    MultipartUploadConfig,
    MultipartUploadReport,
    run_object_store_multipart_probe,
)
from mas_core.memory.object_store_rollout import (
    OBJECT_STORE_MIGRATION_SCHEMA,
    DualWriteRecord,
    MigrationActorKind,
    MigrationStatus,
    MigrationTransition,
    ObjectStoreMigrationError,
    ObjectStoreMigrationWorkflow,
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
    "MAX_MULTIPART_PARTS",
    "MAX_MULTIPART_PAYLOAD_BYTES",
    "MIN_PART_SIZE_BYTES",
    "OBJECT_STORE_MULTIPART_SCHEMA",
    "MultipartObjectStoreAdapter",
    "MultipartUploadConfig",
    "MultipartUploadReport",
    "run_object_store_multipart_probe",
    "ENCRYPTION_ALGORITHM",
    "OBJECT_STORE_ENCRYPTED_BACKUP_SCHEMA",
    "OBJECT_STORE_ENCRYPTED_RESTORE_SCHEMA",
    "EncryptedBackupManifest",
    "EncryptedBackupObject",
    "EncryptedRestoreVerification",
    "build_encrypted_backup",
    "replicate_encrypted_backup",
    "verify_encrypted_backup",
    "OBJECT_STORE_BACKUP_SCHEMA",
    "OBJECT_STORE_RESTORE_SCHEMA",
    "BackupManifest",
    "BackupObject",
    "RestoreVerification",
    "assert_clean_restore_target",
    "build_backup_manifest",
    "copy_manifest_objects",
    "verify_restored_manifest",
    "OBJECT_STORE_MIGRATION_SCHEMA",
    "DualWriteRecord",
    "MigrationActorKind",
    "MigrationStatus",
    "MigrationTransition",
    "ObjectStoreMigrationError",
    "ObjectStoreMigrationWorkflow",
    "metadata",
]
