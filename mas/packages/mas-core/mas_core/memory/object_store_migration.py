"""Checksum-verified object copy and parity reporting.

This helper is the migration-safety primitive for AIAT's S3-compatible blob
boundary.  It copies caller-supplied :class:`~mas_core.memory.blob.BlobRef`
objects between adapters, preserves the project-scoped key, verifies source
and target checksums/sizes, and reads the target back before reporting parity.

It deliberately does not choose a provider, delete source data, perform a
cutover, or claim backup/restore evidence.  Those operations remain explicit
operator workflows with provider-specific tests and recovery records.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .blob import BlobRef
    from .object_store_conformance import ObjectStoreAdapter

OBJECT_STORE_COPY_SCHEMA = "aiat.object-store-copy.v1"
ObjectStoreCopyStatus = Literal["PASS", "FAIL"]


@dataclass(frozen=True, slots=True)
class ObjectStoreCopyCase:
    """Parity result for one source object."""

    source_bucket: str
    source_key: str
    target_bucket: str | None
    target_key: str | None
    status: ObjectStoreCopyStatus
    source_sha256: str | None
    target_sha256: str | None
    source_size_bytes: int | None
    target_size_bytes: int | None
    detail: str


@dataclass(frozen=True, slots=True)
class ObjectStoreCopyReport:
    """Deterministic report for a verified copy operation."""

    schema_version: str
    source_adapter_type: str
    target_adapter_type: str
    project_id: str
    cases: tuple[ObjectStoreCopyCase, ...]

    @property
    def passed(self) -> bool:
        return not any(case.status == "FAIL" for case in self.cases)

    @property
    def counts(self) -> dict[str, int]:
        return {
            status: sum(1 for case in self.cases if case.status == status)
            for status in ("PASS", "FAIL")
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_adapter_type": self.source_adapter_type,
            "target_adapter_type": self.target_adapter_type,
            "project_id": self.project_id,
            "passed": self.passed,
            "counts": self.counts,
            "cases": [
                {
                    "source_bucket": case.source_bucket,
                    "source_key": case.source_key,
                    "target_bucket": case.target_bucket,
                    "target_key": case.target_key,
                    "status": case.status,
                    "source_sha256": case.source_sha256,
                    "target_sha256": case.target_sha256,
                    "source_size_bytes": case.source_size_bytes,
                    "target_size_bytes": case.target_size_bytes,
                    "detail": case.detail,
                }
                for case in self.cases
            ],
        }


async def verify_and_copy_blobs(
    source: ObjectStoreAdapter,
    target: ObjectStoreAdapter,
    refs: list[BlobRef] | tuple[BlobRef, ...],
    *,
    project_id: str,
    target_bucket: str | None = None,
) -> ObjectStoreCopyReport:
    """Copy *refs* and verify source/target parity without deleting sources.

    References must use the supplied project prefix.  Input ordering does not
    affect the report: cases are sorted by source bucket and key.  A failed
    target verification triggers a best-effort deletion of that target object;
    the source is never modified.
    """

    cases: list[ObjectStoreCopyCase] = []
    prefix = f"{project_id}/"
    ordered_refs = sorted(refs, key=lambda ref: (ref.bucket, ref.key))
    seen: set[tuple[str, str]] = set()

    for source_ref in ordered_refs:
        source_identity = (source_ref.bucket, source_ref.key)
        if source_identity in seen:
            cases.append(
                ObjectStoreCopyCase(
                    source_bucket=source_ref.bucket,
                    source_key=source_ref.key,
                    target_bucket=target_bucket,
                    target_key=None,
                    status="FAIL",
                    source_sha256=source_ref.sha256,
                    target_sha256=None,
                    source_size_bytes=source_ref.size_bytes,
                    target_size_bytes=None,
                    detail="duplicate source reference",
                )
            )
            continue
        seen.add(source_identity)

        target_ref: BlobRef | None = None
        source_sha: str | None = source_ref.sha256
        target_sha: str | None = None
        source_size: int | None = source_ref.size_bytes
        target_size: int | None = None
        target_key: str | None = None
        try:
            if not source_ref.key.startswith(prefix) or source_ref.key == prefix:
                raise ValueError(
                    f"source key {source_ref.key!r} is outside project prefix {prefix!r}"
                )
            target_key = source_ref.key.removeprefix(prefix)
            payload = await source.download(source_ref)
            actual_source_sha = hashlib.sha256(payload).hexdigest()
            if actual_source_sha != source_ref.sha256:
                raise ValueError("source download checksum differs from BlobRef")
            if len(payload) != source_ref.size_bytes:
                raise ValueError("source download size differs from BlobRef")
            source_sha = actual_source_sha
            source_size = len(payload)

            target_ref = await target.upload(
                project_id,
                target_key,
                payload,
                content_type=source_ref.content_type,
                bucket=target_bucket,
            )
            expected_target_key = f"{project_id}/{target_key}"
            if target_ref.key != expected_target_key:
                raise ValueError(
                    f"target key {target_ref.key!r} differs from {expected_target_key!r}"
                )
            if target_bucket is not None and target_ref.bucket != target_bucket:
                raise ValueError(
                    f"target bucket {target_ref.bucket!r} differs from {target_bucket!r}"
                )
            target_payload = await target.download(target_ref)
            target_sha = hashlib.sha256(target_payload).hexdigest()
            target_size = len(target_payload)
            if target_sha != source_sha or target_size != source_size:
                raise ValueError("target read-back checksum or size differs from source")
        except Exception as exc:
            if target_ref is not None:
                try:
                    await target.delete(target_ref)
                except Exception as cleanup_exc:  # pragma: no cover - provider-specific
                    exc = RuntimeError(f"{exc}; target cleanup failed: {cleanup_exc}")
            cases.append(
                ObjectStoreCopyCase(
                    source_bucket=source_ref.bucket,
                    source_key=source_ref.key,
                    target_bucket=target_ref.bucket if target_ref is not None else target_bucket,
                    target_key=target_ref.key if target_ref is not None else target_key,
                    status="FAIL",
                    source_sha256=source_sha,
                    target_sha256=target_sha,
                    source_size_bytes=source_size,
                    target_size_bytes=target_size,
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
        else:
            cases.append(
                ObjectStoreCopyCase(
                    source_bucket=source_ref.bucket,
                    source_key=source_ref.key,
                    target_bucket=target_ref.bucket if target_ref is not None else target_bucket,
                    target_key=target_ref.key if target_ref is not None else target_key,
                    status="PASS",
                    source_sha256=source_sha,
                    target_sha256=target_sha,
                    source_size_bytes=source_size,
                    target_size_bytes=target_size,
                    detail="copied and read back with matching checksum and size",
                )
            )

    return ObjectStoreCopyReport(
        schema_version=OBJECT_STORE_COPY_SCHEMA,
        source_adapter_type=str(getattr(source, "adapter_type", type(source).__name__)),
        target_adapter_type=str(getattr(target, "adapter_type", type(target).__name__)),
        project_id=project_id,
        cases=tuple(cases),
    )


__all__ = [
    "OBJECT_STORE_COPY_SCHEMA",
    "ObjectStoreCopyCase",
    "ObjectStoreCopyReport",
    "verify_and_copy_blobs",
]
