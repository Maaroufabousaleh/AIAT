"""Bounded object-store lifecycle planning and garbage collection.

The lifecycle boundary compares a provider inventory with AIAT's canonical
object references.  It is deliberately conservative: unknown objects are
orphans, expired references are deletion candidates only when no authoritative
legal hold applies, and size drift is retained for review.  A mutation requires
an unchanged inventory, a verified hold snapshot, and explicit confirmation.
Reports contain scalar keys/counts only; object payloads and provider errors are
never returned.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Protocol

from .blob import BlobClient

OBJECT_STORE_LIFECYCLE_SCHEMA = "aiat.object-store-lifecycle.v1"
OBJECT_STORE_HOLD_SNAPSHOT_SCHEMA = "aiat.object-store-legal-hold-snapshot.v1"
MAX_LIFECYCLE_OBJECTS = 10_000
MAX_LIFECYCLE_KEY_LENGTH = 512
MAX_SOURCE_REF_LENGTH = 256


class ObjectLifecycleError(ValueError):
    """Stable, payload-free lifecycle validation or execution failure."""


class ObjectLifecycleDeleteAdapter(Protocol):
    """Minimal explicit mutation boundary used by lifecycle execution."""

    async def list_objects(
        self,
        project_id: str,
        *,
        prefix: str = "",
        bucket: str | None = None,
    ) -> list[dict[str, Any]]: ...

    async def delete_by_key(
        self,
        project_id: str,
        key: str,
        *,
        bucket: str | None = None,
    ) -> None: ...


def _validate_project_id(project_id: str) -> None:
    try:
        BlobClient._validate_path_component(project_id, "project_id")
    except (TypeError, ValueError) as exc:
        raise ObjectLifecycleError("invalid project_id") from exc
    if len(project_id) > MAX_LIFECYCLE_KEY_LENGTH:
        raise ObjectLifecycleError("project_id is too long")


def _validate_key(project_id: str, key: str) -> None:
    if not isinstance(key, str) or not key or len(key) > MAX_LIFECYCLE_KEY_LENGTH:
        raise ObjectLifecycleError("object key is invalid")
    try:
        BlobClient._validate_path_component(key, "key")
    except (TypeError, ValueError) as exc:
        raise ObjectLifecycleError("object key is invalid") from exc
    if not key.startswith(f"{project_id}/") or key == f"{project_id}/":
        raise ObjectLifecycleError("object key is outside the project scope")


def _validate_sha256(value: str) -> None:
    if not isinstance(value, str) or len(value) != 64:
        raise ObjectLifecycleError("canonical checksum is invalid")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ObjectLifecycleError("canonical checksum is invalid") from exc


def _validate_datetime(value: datetime, field: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ObjectLifecycleError(f"{field} must be timezone-aware")


def _validate_source_ref(source_ref: str) -> None:
    if not isinstance(source_ref, str) or not source_ref or len(source_ref) > MAX_SOURCE_REF_LENGTH:
        raise ObjectLifecycleError("legal-hold source reference is invalid")


@dataclass(frozen=True, slots=True)
class LifecycleInventoryObject:
    """Scalar provider inventory entry."""

    key: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise ObjectLifecycleError("inventory object size is invalid")

    @classmethod
    def from_mapping(cls, row: dict[str, Any]) -> LifecycleInventoryObject:
        if not isinstance(row, dict):
            raise ObjectLifecycleError("inventory row is invalid")
        return cls(key=row.get("key"), size_bytes=row.get("size", row.get("size_bytes")))

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "size_bytes": self.size_bytes}


@dataclass(frozen=True, slots=True)
class LifecycleCanonicalObject:
    """Canonical metadata reference owned by AIAT/Postgres."""

    key: str
    sha256: str
    size_bytes: int
    retention_until: datetime | None = None
    legal_hold: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise ObjectLifecycleError("canonical object size is invalid")
        _validate_sha256(self.sha256)
        if self.retention_until is not None:
            _validate_datetime(self.retention_until, "retention_until")
        if not isinstance(self.legal_hold, bool):
            raise ObjectLifecycleError("canonical legal_hold must be boolean")

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "retention_until": self.retention_until.isoformat() if self.retention_until else None,
            "legal_hold": self.legal_hold,
        }


@dataclass(frozen=True, slots=True)
class LegalHoldSnapshot:
    """Content-addressed, scalar legal-hold snapshot used by a mutation."""

    schema_version: str
    source_ref: str
    hold_keys: tuple[str, ...]
    snapshot_sha256: str

    @staticmethod
    def _canonical_payload(*, schema_version: str, source_ref: str, hold_keys: tuple[str, ...]) -> bytes:
        return json.dumps(
            {
                "schema_version": schema_version,
                "source_ref": source_ref,
                "hold_keys": list(hold_keys),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    @classmethod
    def create(cls, *, source_ref: str, hold_keys: list[str] | tuple[str, ...]) -> LegalHoldSnapshot:
        _validate_source_ref(source_ref)
        normalized = tuple(sorted(set(hold_keys)))
        if len(normalized) > MAX_LIFECYCLE_OBJECTS:
            raise ObjectLifecycleError("legal-hold snapshot is too large")
        if any(not isinstance(key, str) or not key for key in normalized):
            raise ObjectLifecycleError("legal-hold snapshot contains an invalid key")
        digest = hashlib.sha256(
            cls._canonical_payload(
                schema_version=OBJECT_STORE_HOLD_SNAPSHOT_SCHEMA,
                source_ref=source_ref,
                hold_keys=normalized,
            )
        ).hexdigest()
        return cls(
            schema_version=OBJECT_STORE_HOLD_SNAPSHOT_SCHEMA,
            source_ref=source_ref,
            hold_keys=normalized,
            snapshot_sha256=digest,
        )

    def verify(self) -> None:
        expected = self.create(source_ref=self.source_ref, hold_keys=self.hold_keys).snapshot_sha256
        if self.schema_version != OBJECT_STORE_HOLD_SNAPSHOT_SCHEMA or self.snapshot_sha256 != expected:
            raise ObjectLifecycleError("legal-hold snapshot digest or schema does not match")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_ref": self.source_ref,
            "hold_count": len(self.hold_keys),
            "hold_keys": list(self.hold_keys),
            "snapshot_sha256": self.snapshot_sha256,
        }


@dataclass(frozen=True, slots=True)
class ObjectLifecyclePlan:
    """Non-mutating lifecycle decision for one project/bucket scope."""

    schema_version: str
    project_id: str
    bucket: str
    evaluated_at: datetime
    inventory_count: int
    canonical_count: int
    orphan_keys: tuple[str, ...]
    expired_keys: tuple[str, ...]
    held_keys: tuple[str, ...]
    missing_keys: tuple[str, ...]
    size_mismatch_keys: tuple[str, ...]
    delete_keys: tuple[str, ...]
    retain_keys: tuple[str, ...]
    legal_hold_snapshot_ref: str
    legal_hold_snapshot_sha256: str
    plan_sha256: str

    @staticmethod
    def _canonical_payload(plan: ObjectLifecyclePlan) -> bytes:
        return json.dumps(
            {
                "schema_version": plan.schema_version,
                "project_id": plan.project_id,
                "bucket": plan.bucket,
                "evaluated_at": plan.evaluated_at.isoformat(),
                "inventory_count": plan.inventory_count,
                "canonical_count": plan.canonical_count,
                "orphan_keys": list(plan.orphan_keys),
                "expired_keys": list(plan.expired_keys),
                "held_keys": list(plan.held_keys),
                "missing_keys": list(plan.missing_keys),
                "size_mismatch_keys": list(plan.size_mismatch_keys),
                "delete_keys": list(plan.delete_keys),
                "retain_keys": list(plan.retain_keys),
                "legal_hold_snapshot_ref": plan.legal_hold_snapshot_ref,
                "legal_hold_snapshot_sha256": plan.legal_hold_snapshot_sha256,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def verify(self) -> None:
        expected = hashlib.sha256(self._canonical_payload(self)).hexdigest()
        if self.schema_version != OBJECT_STORE_LIFECYCLE_SCHEMA or self.plan_sha256 != expected:
            raise ObjectLifecycleError("lifecycle plan digest or schema does not match")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "bucket": self.bucket,
            "evaluated_at": self.evaluated_at.isoformat(),
            "inventory_count": self.inventory_count,
            "canonical_count": self.canonical_count,
            "orphan_keys": list(self.orphan_keys),
            "expired_keys": list(self.expired_keys),
            "held_keys": list(self.held_keys),
            "missing_keys": list(self.missing_keys),
            "size_mismatch_keys": list(self.size_mismatch_keys),
            "delete_keys": list(self.delete_keys),
            "retain_keys": list(self.retain_keys),
            "legal_hold_snapshot_ref": self.legal_hold_snapshot_ref,
            "legal_hold_snapshot_sha256": self.legal_hold_snapshot_sha256,
            "plan_sha256": self.plan_sha256,
            "mutation_performed": False,
            "status": "preview",
        }


@dataclass(frozen=True, slots=True)
class ObjectLifecycleExecution:
    """Scalar result of an explicitly confirmed lifecycle mutation."""

    schema_version: str
    project_id: str
    bucket: str
    deleted_keys: tuple[str, ...]
    remaining_keys: tuple[str, ...]
    cleanup_verified: bool
    mutation_performed: bool
    status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "bucket": self.bucket,
            "deleted_count": len(self.deleted_keys),
            "remaining_count": len(self.remaining_keys),
            "cleanup_verified": self.cleanup_verified,
            "mutation_performed": self.mutation_performed,
            "status": self.status,
        }


def _normalize_inventory(
    project_id: str,
    inventory: list[LifecycleInventoryObject] | tuple[LifecycleInventoryObject, ...],
) -> tuple[LifecycleInventoryObject, ...]:
    if len(inventory) > MAX_LIFECYCLE_OBJECTS:
        raise ObjectLifecycleError("object inventory is too large")
    normalized = tuple(sorted(inventory, key=lambda row: row.key))
    keys = [row.key for row in normalized]
    if len(keys) != len(set(keys)):
        raise ObjectLifecycleError("object inventory contains duplicate keys")
    for row in normalized:
        _validate_key(project_id, row.key)
    return normalized


def _normalize_canonical(
    project_id: str,
    canonical: list[LifecycleCanonicalObject] | tuple[LifecycleCanonicalObject, ...],
) -> tuple[LifecycleCanonicalObject, ...]:
    if len(canonical) > MAX_LIFECYCLE_OBJECTS:
        raise ObjectLifecycleError("canonical object inventory is too large")
    normalized = tuple(sorted(canonical, key=lambda row: row.key))
    keys = [row.key for row in normalized]
    if len(keys) != len(set(keys)):
        raise ObjectLifecycleError("canonical inventory contains duplicate keys")
    for row in normalized:
        _validate_key(project_id, row.key)
    return normalized


def plan_object_lifecycle(
    *,
    project_id: str,
    bucket: str,
    inventory: list[LifecycleInventoryObject] | tuple[LifecycleInventoryObject, ...],
    canonical: list[LifecycleCanonicalObject] | tuple[LifecycleCanonicalObject, ...],
    evaluated_at: datetime,
    legal_hold_snapshot: LegalHoldSnapshot,
) -> ObjectLifecyclePlan:
    """Build a deterministic, non-mutating orphan/expiry plan."""

    _validate_project_id(project_id)
    if not isinstance(bucket, str) or not bucket or len(bucket) > 128:
        raise ObjectLifecycleError("bucket is invalid")
    _validate_datetime(evaluated_at, "evaluated_at")
    legal_hold_snapshot.verify()
    inventory_rows = _normalize_inventory(project_id, inventory)
    canonical_rows = _normalize_canonical(project_id, canonical)
    inventory_by_key = {row.key: row for row in inventory_rows}
    canonical_by_key = {row.key: row for row in canonical_rows}
    hold_keys = set(legal_hold_snapshot.hold_keys)
    hold_keys.update(row.key for row in canonical_rows if row.legal_hold)

    orphan_keys = set(inventory_by_key) - set(canonical_by_key)
    expired_keys = {
        row.key
        for row in canonical_rows
        if row.key in inventory_by_key
        and row.retention_until is not None
        and row.retention_until <= evaluated_at
    }
    missing_keys = set(canonical_by_key) - set(inventory_by_key)
    size_mismatch_keys = {
        key
        for key in set(inventory_by_key) & set(canonical_by_key)
        if inventory_by_key[key].size_bytes != canonical_by_key[key].size_bytes
    }
    candidates = orphan_keys | expired_keys
    held_keys = candidates & hold_keys
    delete_keys = candidates - hold_keys - size_mismatch_keys
    retain_keys = set(inventory_by_key) - delete_keys
    plan = ObjectLifecyclePlan(
        schema_version=OBJECT_STORE_LIFECYCLE_SCHEMA,
        project_id=project_id,
        bucket=bucket,
        evaluated_at=evaluated_at,
        inventory_count=len(inventory_rows),
        canonical_count=len(canonical_rows),
        orphan_keys=tuple(sorted(orphan_keys)),
        expired_keys=tuple(sorted(expired_keys)),
        held_keys=tuple(sorted(held_keys)),
        missing_keys=tuple(sorted(missing_keys)),
        size_mismatch_keys=tuple(sorted(size_mismatch_keys)),
        delete_keys=tuple(sorted(delete_keys)),
        retain_keys=tuple(sorted(retain_keys)),
        legal_hold_snapshot_ref=legal_hold_snapshot.source_ref,
        legal_hold_snapshot_sha256=legal_hold_snapshot.snapshot_sha256,
        plan_sha256="",
    )
    return replace(
        plan,
        plan_sha256=hashlib.sha256(ObjectLifecyclePlan._canonical_payload(plan)).hexdigest(),
    )


async def execute_object_lifecycle(
    store: ObjectLifecycleDeleteAdapter,
    plan: ObjectLifecyclePlan,
    *,
    legal_hold_snapshot: LegalHoldSnapshot,
    confirm: bool = False,
) -> ObjectLifecycleExecution:
    """Apply a plan only after an unchanged inventory and explicit confirmation."""

    plan.verify()
    legal_hold_snapshot.verify()
    if not confirm:
        raise ObjectLifecycleError("lifecycle execution requires explicit confirmation")
    if (
        legal_hold_snapshot.source_ref != plan.legal_hold_snapshot_ref
        or legal_hold_snapshot.snapshot_sha256 != plan.legal_hold_snapshot_sha256
    ):
        raise ObjectLifecycleError("legal-hold snapshot does not match lifecycle plan")
    delete_by_key = getattr(store, "delete_by_key", None)
    if not callable(delete_by_key):
        raise ObjectLifecycleError("lifecycle adapter lacks explicit delete_by_key boundary")
    current_rows = await store.list_objects(plan.project_id, bucket=plan.bucket)
    current = _normalize_inventory(
        plan.project_id,
        tuple(LifecycleInventoryObject.from_mapping(row) for row in current_rows),
    )
    current_keys = {row.key for row in current}
    planned_keys = set(plan.orphan_keys) | {
        key for key in plan.expired_keys if key in current_keys
    }
    if current_keys != set(plan.retain_keys) | planned_keys:
        raise ObjectLifecycleError("object inventory changed after lifecycle preview")
    for key in plan.delete_keys:
        await delete_by_key(plan.project_id, key, bucket=plan.bucket)
    remaining_rows = await store.list_objects(plan.project_id, bucket=plan.bucket)
    remaining = _normalize_inventory(
        plan.project_id,
        tuple(LifecycleInventoryObject.from_mapping(row) for row in remaining_rows),
    )
    remaining_keys = tuple(row.key for row in remaining)
    cleanup_verified = set(remaining_keys) == set(plan.retain_keys)
    if not cleanup_verified:
        raise ObjectLifecycleError("lifecycle cleanup did not match the retained inventory")
    return ObjectLifecycleExecution(
        schema_version=OBJECT_STORE_LIFECYCLE_SCHEMA,
        project_id=plan.project_id,
        bucket=plan.bucket,
        deleted_keys=plan.delete_keys,
        remaining_keys=remaining_keys,
        cleanup_verified=True,
        mutation_performed=bool(plan.delete_keys),
        status="pass",
    )


__all__ = [
    "MAX_LIFECYCLE_KEY_LENGTH",
    "MAX_LIFECYCLE_OBJECTS",
    "OBJECT_STORE_HOLD_SNAPSHOT_SCHEMA",
    "OBJECT_STORE_LIFECYCLE_SCHEMA",
    "LegalHoldSnapshot",
    "LifecycleCanonicalObject",
    "LifecycleInventoryObject",
    "ObjectLifecycleDeleteAdapter",
    "ObjectLifecycleError",
    "ObjectLifecycleExecution",
    "ObjectLifecyclePlan",
    "execute_object_lifecycle",
    "plan_object_lifecycle",
]
