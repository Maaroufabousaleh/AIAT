"""Governed object-store migration workflow.

The object-store conformance, copy, and backup modules prove byte-level
integrity.  This module composes those primitives into an explicit control
plane record for a provider migration:

``planned -> inventoried -> copied -> dual_write_ready -> cutover -> rolled_back``

Inventory and copy are provider operations; cutover and rollback are explicit
human-confirmed decisions.  The workflow records the intended active adapter
but does not silently mutate deployment configuration or delete source data.
That keeps provider routing, credentials, and deployment ownership in their
existing services while making migration evidence structured and deterministic.
Licence or restriction metadata is not part of any transition predicate.
"""

from __future__ import annotations

import hashlib
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from .object_store_backup import (
    BackupManifest,
    RestoreVerification,
    build_backup_manifest,
    copy_manifest_objects,
)

if TYPE_CHECKING:
    from .blob import BlobRef
    from .object_store_conformance import ObjectStoreAdapter
    from .object_store_migration import ObjectStoreCopyReport

OBJECT_STORE_MIGRATION_SCHEMA = "aiat.object-store-migration.v1"
MigrationStatus = Literal[
    "PLANNED",
    "INVENTORIED",
    "COPIED",
    "DUAL_WRITE_READY",
    "CUTOVER",
    "ROLLED_BACK",
]
MigrationActorKind = Literal["human", "system"]


class ObjectStoreMigrationError(ValueError):
    """Raised when a migration workflow lacks required evidence or authority."""


@dataclass(frozen=True, slots=True)
class MigrationTransition:
    """One state transition in the migration evidence chain."""

    from_status: MigrationStatus
    to_status: MigrationStatus
    actor: str
    actor_kind: MigrationActorKind
    reason: str
    occurred_at: str


@dataclass(frozen=True, slots=True)
class DualWriteRecord:
    """Parity result for one write mirrored to both providers."""

    key: str
    source_bucket: str
    target_bucket: str
    sha256: str
    size_bytes: int
    status: Literal["PASS"]


@dataclass(slots=True)
class ObjectStoreMigrationWorkflow:
    """Stateful, provider-neutral migration decision record.

    The workflow owns evidence and transition rules, not provider routing.  A
    caller supplies adapters for inventory/copy/dual-write operations and must
    explicitly confirm the human-controlled cutover and rollback decisions.
    """

    migration_id: str
    project_id: str
    source_adapter_type: str
    target_adapter_type: str
    source_bucket: str
    target_bucket: str
    dual_write_required: bool = False
    status: MigrationStatus = "PLANNED"
    active_adapter_type: str | None = None
    active_bucket: str | None = None
    manifest: BackupManifest | None = None
    copy_report: ObjectStoreCopyReport | None = None
    restore_verification: RestoreVerification | None = None
    dual_writes: list[DualWriteRecord] = field(default_factory=list)
    history: list[MigrationTransition] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.migration_id.strip():
            raise ObjectStoreMigrationError("migration_id must not be blank")
        if not self.project_id.strip():
            raise ObjectStoreMigrationError("project_id must not be blank")
        if not self.source_adapter_type.strip() or not self.target_adapter_type.strip():
            raise ObjectStoreMigrationError("source and target adapter types are required")
        if not self.source_bucket.strip() or not self.target_bucket.strip():
            raise ObjectStoreMigrationError("source and target buckets are required")
        if (
            self.source_adapter_type == self.target_adapter_type
            and self.source_bucket == self.target_bucket
        ):
            raise ObjectStoreMigrationError("source and target object stores must differ")
        if self.active_adapter_type is None:
            self.active_adapter_type = self.source_adapter_type
        if self.active_bucket is None:
            self.active_bucket = self.source_bucket

    @classmethod
    def create(
        cls,
        *,
        migration_id: str,
        project_id: str,
        source_adapter_type: str,
        target_adapter_type: str,
        source_bucket: str,
        target_bucket: str,
        dual_write_required: bool = False,
    ) -> ObjectStoreMigrationWorkflow:
        return cls(
            migration_id=migration_id,
            project_id=project_id,
            source_adapter_type=source_adapter_type,
            target_adapter_type=target_adapter_type,
            source_bucket=source_bucket,
            target_bucket=target_bucket,
            dual_write_required=dual_write_required,
        )

    async def inventory(
        self,
        source: ObjectStoreAdapter,
        refs: list[BlobRef] | tuple[BlobRef, ...],
        *,
        actor: str,
        actor_kind: MigrationActorKind = "system",
    ) -> BackupManifest:
        """Create a checksum inventory and advance to ``INVENTORIED``."""

        self._require_status("PLANNED")
        self._require_actor(actor)
        manifest = await build_backup_manifest(source, refs, project_id=self.project_id)
        if manifest.source_adapter_type != self.source_adapter_type:
            raise ObjectStoreMigrationError("source adapter type differs from the migration plan")
        self.manifest = manifest
        self._transition(
            "INVENTORIED",
            actor=actor,
            actor_kind=actor_kind,
            reason="checksum inventory verified",
        )
        return manifest

    async def copy(
        self,
        source: ObjectStoreAdapter,
        target: ObjectStoreAdapter,
        *,
        actor: str,
        actor_kind: MigrationActorKind = "system",
    ) -> ObjectStoreCopyReport:
        """Copy the inventory and require exact target read-back parity."""

        self._require_status("INVENTORIED")
        self._require_actor(actor)
        if self.manifest is None:
            raise ObjectStoreMigrationError("copy requires a checksum inventory")
        if str(getattr(source, "adapter_type", type(source).__name__)) != self.source_adapter_type:
            raise ObjectStoreMigrationError("source adapter type differs from the migration plan")
        if str(getattr(target, "adapter_type", type(target).__name__)) != self.target_adapter_type:
            raise ObjectStoreMigrationError("target adapter type differs from the migration plan")
        copy_report, verification = await copy_manifest_objects(
            source,
            target,
            self.manifest,
            project_id=self.project_id,
            source_bucket=self.source_bucket,
            target_bucket=self.target_bucket,
        )
        self.copy_report = copy_report
        self.restore_verification = verification
        self._transition(
            "COPIED",
            actor=actor,
            actor_kind=actor_kind,
            reason="provider copy and clean-target parity verified",
        )
        return copy_report

    async def dual_write(
        self,
        source: ObjectStoreAdapter,
        target: ObjectStoreAdapter,
        *,
        key: str,
        payload: bytes,
        content_type: str = "application/octet-stream",
        actor: str,
        actor_kind: MigrationActorKind = "system",
    ) -> DualWriteRecord:
        """Mirror one new write and verify both provider reads.

        Dual-write mode is optional but, when enabled, at least one successful
        parity write is required before cutover.  A source write is never
        deleted if the target write or verification fails.
        """

        if not self.dual_write_required:
            raise ObjectStoreMigrationError("dual-write is not enabled for this migration")
        if self.status not in {"COPIED", "DUAL_WRITE_READY"}:
            raise ObjectStoreMigrationError("dual-write requires a completed provider copy")
        self._require_actor(actor)
        source_ref = await source.upload(
            self.project_id,
            key,
            payload,
            content_type=content_type,
            bucket=self.source_bucket,
        )
        target_ref: BlobRef | None = None
        try:
            target_ref = await target.upload(
                self.project_id,
                key,
                payload,
                content_type=content_type,
                bucket=self.target_bucket,
            )
            expected_sha = hashlib.sha256(payload).hexdigest()
            expected_key = f"{self.project_id}/{key}"
            if source_ref.bucket != self.source_bucket or source_ref.key != expected_key:
                raise ObjectStoreMigrationError(
                    "source dual-write reference escaped the planned project bucket/key"
                )
            if target_ref.bucket != self.target_bucket or target_ref.key != expected_key:
                raise ObjectStoreMigrationError(
                    "target dual-write reference escaped the planned project bucket/key"
                )
            if source_ref.sha256 != expected_sha or target_ref.sha256 != expected_sha:
                raise ObjectStoreMigrationError("dual-write upload checksum differs from payload")
            if source_ref.size_bytes != len(payload) or target_ref.size_bytes != len(payload):
                raise ObjectStoreMigrationError("dual-write upload size differs from payload")
            if (
                await source.download(source_ref) != payload
                or await target.download(target_ref) != payload
            ):
                raise ObjectStoreMigrationError("dual-write read-back differs from payload")
        except Exception:
            if target_ref is not None:
                with suppress(Exception):
                    await target.delete(target_ref)
            raise
        record = DualWriteRecord(
            key=source_ref.key,
            source_bucket=source_ref.bucket,
            target_bucket=target_ref.bucket,
            sha256=source_ref.sha256,
            size_bytes=source_ref.size_bytes,
            status="PASS",
        )
        self.dual_writes.append(record)
        if self.status == "COPIED":
            self._transition(
                "DUAL_WRITE_READY",
                actor=actor,
                actor_kind=actor_kind,
                reason="dual-write parity verified",
            )
        return record

    def cutover(
        self,
        *,
        actor: str,
        actor_kind: MigrationActorKind,
        confirm: bool,
    ) -> None:
        """Record an explicit human-confirmed cutover decision."""

        if self.dual_write_required:
            self._require_status("DUAL_WRITE_READY")
            if not self.dual_writes:
                raise ObjectStoreMigrationError("cutover requires a passing dual-write record")
        else:
            self._require_status("COPIED")
        self._require_human_confirmation(actor, actor_kind, confirm, "cutover")
        self.active_adapter_type = self.target_adapter_type
        self.active_bucket = self.target_bucket
        self._transition(
            "CUTOVER",
            actor=actor,
            actor_kind=actor_kind,
            reason="operator confirmed target provider cutover",
        )

    def rollback(
        self,
        *,
        actor: str,
        actor_kind: MigrationActorKind,
        confirm: bool,
        reason: str,
    ) -> None:
        """Record an explicit human-confirmed rollback to the source."""

        self._require_status("CUTOVER")
        self._require_human_confirmation(actor, actor_kind, confirm, "rollback")
        if not reason.strip():
            raise ObjectStoreMigrationError("rollback requires a reason")
        self.active_adapter_type = self.source_adapter_type
        self.active_bucket = self.source_bucket
        self._transition(
            "ROLLED_BACK",
            actor=actor,
            actor_kind=actor_kind,
            reason=reason.strip(),
        )

    def as_dict(self, *, include_timestamps: bool = True) -> dict[str, Any]:
        """Return a secret-safe migration evidence record.

        Runtime evidence keeps transition timestamps.  Deterministic fixture
        runners may set ``include_timestamps=False`` so repeated reports can
        be compared byte-for-byte without weakening the live record.
        """

        if self.manifest is not None:
            self.manifest.verify_digest()
        return {
            "schema_version": OBJECT_STORE_MIGRATION_SCHEMA,
            "migration_id": self.migration_id,
            "project_id": self.project_id,
            "source_adapter_type": self.source_adapter_type,
            "target_adapter_type": self.target_adapter_type,
            "source_bucket": self.source_bucket,
            "target_bucket": self.target_bucket,
            "dual_write_required": self.dual_write_required,
            "status": self.status,
            "active_adapter_type": self.active_adapter_type,
            "active_bucket": self.active_bucket,
            "manifest": self.manifest.as_dict() if self.manifest is not None else None,
            "copy": self.copy_report.as_dict() if self.copy_report is not None else None,
            "restore_verification": (
                self.restore_verification.as_dict()
                if self.restore_verification is not None
                else None
            ),
            "dual_writes": [
                {
                    "key": record.key,
                    "source_bucket": record.source_bucket,
                    "target_bucket": record.target_bucket,
                    "sha256": record.sha256,
                    "size_bytes": record.size_bytes,
                    "status": record.status,
                }
                for record in self.dual_writes
            ],
            "history": [
                {
                    "from_status": transition.from_status,
                    "to_status": transition.to_status,
                    "actor": transition.actor,
                    "actor_kind": transition.actor_kind,
                    "reason": transition.reason,
                    "occurred_at": transition.occurred_at if include_timestamps else None,
                }
                for transition in self.history
            ],
        }

    def _require_status(self, expected: MigrationStatus) -> None:
        if self.status != expected:
            raise ObjectStoreMigrationError(
                f"migration action requires {expected}, current status is {self.status}"
            )

    @staticmethod
    def _require_actor(actor: str) -> None:
        if not actor.strip():
            raise ObjectStoreMigrationError("migration action requires an actor")

    @classmethod
    def _require_human_confirmation(
        cls,
        actor: str,
        actor_kind: MigrationActorKind,
        confirm: bool,
        action: str,
    ) -> None:
        cls._require_actor(actor)
        if actor_kind != "human":
            raise ObjectStoreMigrationError(f"{action} requires a human operator")
        if not confirm:
            raise ObjectStoreMigrationError(f"{action} requires explicit confirmation")

    def _transition(
        self,
        target: MigrationStatus,
        *,
        actor: str,
        actor_kind: MigrationActorKind,
        reason: str,
    ) -> None:
        self._require_actor(actor)
        if not reason.strip():
            raise ObjectStoreMigrationError("migration transition requires a reason")
        prior = self.status
        occurred_at = datetime.now(tz=UTC).isoformat()
        self.status = target
        self.history.append(
            MigrationTransition(
                from_status=prior,
                to_status=target,
                actor=actor.strip(),
                actor_kind=actor_kind,
                reason=reason.strip(),
                occurred_at=occurred_at,
            )
        )


__all__ = [
    "OBJECT_STORE_MIGRATION_SCHEMA",
    "DualWriteRecord",
    "MigrationActorKind",
    "MigrationStatus",
    "MigrationTransition",
    "ObjectStoreMigrationError",
    "ObjectStoreMigrationWorkflow",
]
