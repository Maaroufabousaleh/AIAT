"""Provider-neutral authenticated encryption for object-store backups.

The envelope is an AIAT-owned boundary around the existing object-store
adapter.  Plaintext is encrypted before it reaches a backup/replica adapter;
the manifest retains only checksums, sizes, nonces, an algorithm label, and an
opaque key identifier.  Key material is supplied by the caller and is never
written to a manifest or returned in a report.

This module does not claim provider-managed SSE/KMS, key escrow, clean-host
disaster recovery, or external provider durability.  Those remain deployment
and operator gates.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .blob import BlobClient, BlobRef, verify_blob_readback
from .object_store_backup import assert_clean_restore_target

if TYPE_CHECKING:
    from .object_store_conformance import ObjectStoreAdapter

OBJECT_STORE_ENCRYPTED_BACKUP_SCHEMA = "aiat.object-store-encrypted-backup.v1"
OBJECT_STORE_ENCRYPTED_RESTORE_SCHEMA = "aiat.object-store-encrypted-restore.v1"
ENCRYPTION_ALGORITHM = "AES-256-GCM"
_NONCE_BYTES = 12
_KEY_BYTES = 32


def _key_bytes(key: bytes | bytearray | memoryview) -> bytes:
    """Validate and copy one caller-owned AES-256 key."""

    if not isinstance(key, (bytes, bytearray, memoryview)):
        raise ValueError("encryption key must be bytes-like")
    value = bytes(key)
    if len(value) != _KEY_BYTES:
        raise ValueError("AES-256-GCM requires a 32-byte key")
    return value


def _key_id(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 128 or any(char.isspace() for char in normalized):
        raise ValueError("key_id must be a bounded non-empty token")
    return normalized


def _logical_key(project_id: str, full_key: str) -> str:
    prefix = f"{project_id}/"
    if not full_key.startswith(prefix):
        raise ValueError(f"source key {full_key!r} is outside project prefix {prefix!r}")
    logical = full_key.removeprefix(prefix)
    if not logical:
        raise ValueError("source key cannot be the project prefix")
    BlobClient._validate_path_component(logical, "logical_key")
    return logical


def _encrypted_key(logical_key: str) -> str:
    BlobClient._validate_path_component(logical_key, "logical_key")
    return f"encrypted/{logical_key}.enc"


def _associated_data(project_id: str, logical_key: str, key_id: str) -> bytes:
    return (
        f"{OBJECT_STORE_ENCRYPTED_BACKUP_SCHEMA}|{project_id}|{logical_key}|{key_id}"
    ).encode()


def _decode_nonce(value: str) -> bytes:
    try:
        nonce = base64.urlsafe_b64decode(str(value).encode("ascii"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("encrypted backup nonce is malformed") from exc
    if len(nonce) != _NONCE_BYTES:
        raise ValueError("encrypted backup nonce must be 12 bytes")
    return nonce


@dataclass(frozen=True, slots=True)
class EncryptedBackupObject:
    """Metadata for one encrypted object; no plaintext or key material."""

    logical_key: str
    encrypted_key: str
    plaintext_sha256: str
    plaintext_size_bytes: int
    ciphertext_sha256: str
    ciphertext_size_bytes: int
    content_type: str
    nonce_b64: str
    key_id: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "logical_key": self.logical_key,
            "encrypted_key": self.encrypted_key,
            "plaintext_sha256": self.plaintext_sha256,
            "plaintext_size_bytes": self.plaintext_size_bytes,
            "ciphertext_sha256": self.ciphertext_sha256,
            "ciphertext_size_bytes": self.ciphertext_size_bytes,
            "content_type": self.content_type,
            "nonce_b64": self.nonce_b64,
            "key_id": self.key_id,
        }


@dataclass(frozen=True, slots=True)
class EncryptedBackupManifest:
    """Authenticated-encryption manifest for one project prefix."""

    schema_version: str
    project_id: str
    source_adapter_type: str
    encryption_algorithm: str
    key_id: str
    objects: tuple[EncryptedBackupObject, ...]
    manifest_sha256: str

    @staticmethod
    def _canonical_payload(
        *,
        schema_version: str,
        project_id: str,
        source_adapter_type: str,
        encryption_algorithm: str,
        key_id: str,
        objects: tuple[EncryptedBackupObject, ...],
    ) -> bytes:
        payload = {
            "schema_version": schema_version,
            "project_id": project_id,
            "source_adapter_type": source_adapter_type,
            "encryption_algorithm": encryption_algorithm,
            "key_id": key_id,
            "objects": [obj.as_dict() for obj in objects],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @classmethod
    def create(
        cls,
        *,
        project_id: str,
        source_adapter_type: str,
        key_id: str,
        objects: tuple[EncryptedBackupObject, ...],
    ) -> EncryptedBackupManifest:
        normalized_key_id = _key_id(key_id)
        if not objects:
            raise ValueError("encrypted backup manifest cannot be empty")
        ordered = tuple(sorted(objects, key=lambda obj: obj.logical_key))
        logical_keys = [obj.logical_key for obj in ordered]
        if len(logical_keys) != len(set(logical_keys)):
            raise ValueError("encrypted backup manifest contains duplicate logical keys")
        if any(obj.key_id != normalized_key_id for obj in ordered):
            raise ValueError("encrypted backup object key IDs must match the manifest")
        digest = hashlib.sha256(
            cls._canonical_payload(
                schema_version=OBJECT_STORE_ENCRYPTED_BACKUP_SCHEMA,
                project_id=project_id,
                source_adapter_type=source_adapter_type,
                encryption_algorithm=ENCRYPTION_ALGORITHM,
                key_id=normalized_key_id,
                objects=ordered,
            )
        ).hexdigest()
        return cls(
            schema_version=OBJECT_STORE_ENCRYPTED_BACKUP_SCHEMA,
            project_id=project_id,
            source_adapter_type=source_adapter_type,
            encryption_algorithm=ENCRYPTION_ALGORITHM,
            key_id=normalized_key_id,
            objects=ordered,
            manifest_sha256=digest,
        )

    def verify_digest(self) -> None:
        expected = self.create(
            project_id=self.project_id,
            source_adapter_type=self.source_adapter_type,
            key_id=self.key_id,
            objects=self.objects,
        ).manifest_sha256
        if (
            self.schema_version != OBJECT_STORE_ENCRYPTED_BACKUP_SCHEMA
            or self.encryption_algorithm != ENCRYPTION_ALGORITHM
            or self.manifest_sha256 != expected
        ):
            raise ValueError("encrypted backup manifest digest or schema does not match")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> EncryptedBackupManifest:
        """Rehydrate and verify a scalar manifest from a persisted bundle."""

        try:
            raw_objects = payload["objects"]
            if not isinstance(raw_objects, list) or not all(
                isinstance(item, dict) for item in raw_objects
            ):
                raise ValueError("encrypted backup manifest objects are malformed")
            object_count = int(payload["object_count"])
            if object_count != len(raw_objects):
                raise ValueError("encrypted backup manifest object count is invalid")
            objects = tuple(EncryptedBackupObject(**dict(item)) for item in raw_objects)
            manifest = cls(
                schema_version=str(payload["schema_version"]),
                project_id=str(payload["project_id"]),
                source_adapter_type=str(payload["source_adapter_type"]),
                encryption_algorithm=str(payload["encryption_algorithm"]),
                key_id=str(payload["key_id"]),
                objects=objects,
                manifest_sha256=str(payload["manifest_sha256"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("encrypted backup manifest bundle is malformed") from exc
        manifest.verify_digest()
        return manifest

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "source_adapter_type": self.source_adapter_type,
            "encryption_algorithm": self.encryption_algorithm,
            "key_id": self.key_id,
            "object_count": len(self.objects),
            "objects": [obj.as_dict() for obj in self.objects],
            "manifest_sha256": self.manifest_sha256,
        }


@dataclass(frozen=True, slots=True)
class EncryptedRestoreVerification:
    """Scalar result of authenticated encrypted read-back."""

    schema_version: str
    project_id: str
    target_bucket: str
    encryption_algorithm: str
    key_id: str
    object_count: int
    checked_object_count: int
    clean_target_verified: bool
    status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "target_bucket": self.target_bucket,
            "encryption_algorithm": self.encryption_algorithm,
            "key_id": self.key_id,
            "object_count": self.object_count,
            "checked_object_count": self.checked_object_count,
            "clean_target_verified": self.clean_target_verified,
            "status": self.status,
        }


def _ciphertext_ref(
    obj: EncryptedBackupObject,
    *,
    project_id: str,
    bucket: str,
) -> BlobRef:
    return BlobRef(
        bucket=bucket,
        key=f"{project_id}/{obj.encrypted_key}",
        sha256=obj.ciphertext_sha256,
        size_bytes=obj.ciphertext_size_bytes,
        content_type="application/octet-stream",
    )


async def build_encrypted_backup(
    source: ObjectStoreAdapter,
    target: ObjectStoreAdapter,
    refs: list[BlobRef] | tuple[BlobRef, ...],
    *,
    project_id: str,
    source_bucket: str,
    target_bucket: str,
    key: bytes | bytearray | memoryview,
    key_id: str,
    require_clean_target: bool = False,
) -> EncryptedBackupManifest:
    """Encrypt source objects before writing them to the backup adapter."""

    secret = _key_bytes(key)
    normalized_key_id = _key_id(key_id)
    if not refs:
        raise ValueError("encrypted backup source inventory cannot be empty")
    if require_clean_target:
        await assert_clean_restore_target(
            target,
            project_id=project_id,
            target_bucket=target_bucket,
        )

    created: list[BlobRef] = []
    objects: list[EncryptedBackupObject] = []
    try:
        seen: set[str] = set()
        for ref in sorted(refs, key=lambda item: item.key):
            if ref.bucket != source_bucket:
                raise ValueError("source reference bucket does not match source_bucket")
            logical = _logical_key(project_id, ref.key)
            if logical in seen:
                raise ValueError(f"duplicate encrypted backup key: {logical}")
            seen.add(logical)
            plaintext = await source.download(ref)
            verify_blob_readback(ref, plaintext)
            nonce = os.urandom(_NONCE_BYTES)
            ciphertext = AESGCM(secret).encrypt(
                nonce,
                plaintext,
                _associated_data(project_id, logical, normalized_key_id),
            )
            encrypted_key = _encrypted_key(logical)
            target_ref = await target.upload(
                project_id,
                encrypted_key,
                ciphertext,
                content_type="application/octet-stream",
                bucket=target_bucket,
            )
            created.append(target_ref)
            readback = await target.download(target_ref)
            verify_blob_readback(target_ref, readback)
            objects.append(
                EncryptedBackupObject(
                    logical_key=logical,
                    encrypted_key=encrypted_key,
                    plaintext_sha256=hashlib.sha256(plaintext).hexdigest(),
                    plaintext_size_bytes=len(plaintext),
                    ciphertext_sha256=hashlib.sha256(ciphertext).hexdigest(),
                    ciphertext_size_bytes=len(ciphertext),
                    content_type=ref.content_type,
                    nonce_b64=base64.urlsafe_b64encode(nonce).decode("ascii"),
                    key_id=normalized_key_id,
                )
            )
        return EncryptedBackupManifest.create(
            project_id=project_id,
            source_adapter_type=str(getattr(source, "adapter_type", type(source).__name__)),
            key_id=normalized_key_id,
            objects=tuple(objects),
        )
    except Exception:
        for ref in reversed(created):
            with suppress(Exception):
                await target.delete(ref)
        raise


async def verify_encrypted_backup(
    store: ObjectStoreAdapter,
    manifest: EncryptedBackupManifest,
    *,
    project_id: str,
    target_bucket: str,
    key: bytes | bytearray | memoryview,
    clean_target_verified: bool = False,
) -> EncryptedRestoreVerification:
    """Verify ciphertext checksums and decrypt every target object."""

    manifest.verify_digest()
    secret = _key_bytes(key)
    normalized_key_id = _key_id(manifest.key_id)
    if manifest.project_id != project_id:
        raise ValueError("encrypted restore project does not match the manifest")
    rows = await store.list_objects(project_id, bucket=target_bucket)
    actual_keys = {str(row.get("key")) for row in rows}
    expected_keys = {f"{project_id}/{obj.encrypted_key}" for obj in manifest.objects}
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        extra = sorted(actual_keys - expected_keys)
        raise ValueError(f"encrypted restore key set differs: missing={missing!r}, extra={extra!r}")

    for obj in manifest.objects:
        ciphertext_ref = _ciphertext_ref(obj, project_id=project_id, bucket=target_bucket)
        ciphertext = await store.download(ciphertext_ref)
        try:
            plaintext = AESGCM(secret).decrypt(
                _decode_nonce(obj.nonce_b64),
                ciphertext,
                _associated_data(project_id, obj.logical_key, normalized_key_id),
            )
        except InvalidTag as exc:
            raise ValueError("encrypted backup authentication failed") from exc
        if (
            len(plaintext) != obj.plaintext_size_bytes
            or hashlib.sha256(plaintext).hexdigest() != obj.plaintext_sha256
        ):
            raise ValueError(f"encrypted backup plaintext checksum mismatch for {obj.logical_key}")

    return EncryptedRestoreVerification(
        schema_version=OBJECT_STORE_ENCRYPTED_RESTORE_SCHEMA,
        project_id=project_id,
        target_bucket=target_bucket,
        encryption_algorithm=ENCRYPTION_ALGORITHM,
        key_id=normalized_key_id,
        object_count=len(manifest.objects),
        checked_object_count=len(manifest.objects),
        clean_target_verified=clean_target_verified,
        status="pass",
    )


async def replicate_encrypted_backup(
    source: ObjectStoreAdapter,
    target: ObjectStoreAdapter,
    manifest: EncryptedBackupManifest,
    *,
    project_id: str,
    source_bucket: str,
    target_bucket: str,
    key: bytes | bytearray | memoryview,
    require_clean_target: bool = False,
) -> EncryptedRestoreVerification:
    """Replicate ciphertext and verify authenticated read-back at the target."""

    manifest.verify_digest()
    _key_bytes(key)
    if manifest.project_id != project_id:
        raise ValueError("encrypted replication project does not match the manifest")
    if require_clean_target:
        await assert_clean_restore_target(
            target,
            project_id=project_id,
            target_bucket=target_bucket,
        )
    created: list[BlobRef] = []
    try:
        for obj in manifest.objects:
            source_ref = _ciphertext_ref(obj, project_id=project_id, bucket=source_bucket)
            ciphertext = await source.download(source_ref)
            target_ref = await target.upload(
                project_id,
                obj.encrypted_key,
                ciphertext,
                content_type="application/octet-stream",
                bucket=target_bucket,
            )
            created.append(target_ref)
            verify_blob_readback(target_ref, await target.download(target_ref))
        return await verify_encrypted_backup(
            target,
            manifest,
            project_id=project_id,
            target_bucket=target_bucket,
            key=key,
            clean_target_verified=require_clean_target,
        )
    except Exception:
        for ref in reversed(created):
            with suppress(Exception):
                await target.delete(ref)
        raise


__all__ = [
    "ENCRYPTION_ALGORITHM",
    "OBJECT_STORE_ENCRYPTED_BACKUP_SCHEMA",
    "OBJECT_STORE_ENCRYPTED_RESTORE_SCHEMA",
    "EncryptedBackupManifest",
    "EncryptedBackupObject",
    "EncryptedRestoreVerification",
    "build_encrypted_backup",
    "replicate_encrypted_backup",
    "verify_encrypted_backup",
]
