"""Bounded multipart upload/read-back checks for S3-compatible stores.

The multipart boundary is deliberately separate from the ordinary object-store
adapter contract. It exercises provider-managed create/part/complete/abort
operations, then uses the same checksum-bearing :class:`BlobRef` read-back and
scoped cleanup guarantees as the normal path. Reports are comparison evidence,
not provider-selection authority; licence metadata remains informational.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any, Protocol

from .blob import BlobRef, verify_blob_readback

OBJECT_STORE_MULTIPART_SCHEMA = "aiat.object-store-multipart.v1"
MIN_PART_SIZE_BYTES = 5 * 1024 * 1024
MAX_MULTIPART_PAYLOAD_BYTES = 64 * 1024 * 1024
MAX_MULTIPART_PARTS = 10_000


class MultipartObjectStoreAdapter(Protocol):
    """The provider operations required by the multipart checker."""

    adapter_type: str
    adapter_version: str

    async def create_multipart_upload(
        self,
        project_id: str,
        key: str,
        *,
        content_type: str = "application/octet-stream",
        bucket: str | None = None,
    ) -> str: ...

    async def upload_multipart_part(
        self,
        project_id: str,
        key: str,
        upload_id: str,
        part_number: int,
        data: bytes,
        *,
        bucket: str | None = None,
    ) -> str: ...

    async def complete_multipart_upload(
        self,
        project_id: str,
        key: str,
        upload_id: str,
        parts: list[dict[str, Any]],
        *,
        bucket: str | None = None,
    ) -> None: ...

    async def abort_multipart_upload(
        self,
        project_id: str,
        key: str,
        upload_id: str,
        *,
        bucket: str | None = None,
    ) -> None: ...

    async def download(self, ref: BlobRef) -> bytes: ...

    async def delete(self, ref: BlobRef) -> None: ...

    async def list_objects(
        self,
        project_id: str,
        *,
        prefix: str = "",
        bucket: str | None = None,
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class MultipartUploadConfig:
    """Bounded disposable multipart plan."""

    payload_sizes: tuple[int, ...] = (8 * 1024 * 1024, 16 * 1024 * 1024)
    part_size_bytes: int = MIN_PART_SIZE_BYTES
    project_id: str = "aiat-multipart-benchmark"
    bucket: str = "mas-agents"

    def __post_init__(self) -> None:
        if not self.payload_sizes:
            raise ValueError("payload_sizes must not be empty")
        if any(size <= 0 for size in self.payload_sizes):
            raise ValueError("payload_sizes must contain only positive values")
        if any(size > MAX_MULTIPART_PAYLOAD_BYTES for size in self.payload_sizes):
            raise ValueError("payload_sizes must stay within the 64 MiB multipart bound")
        if self.part_size_bytes < MIN_PART_SIZE_BYTES:
            raise ValueError("part_size_bytes must be at least 5 MiB")
        if any(
            (size + self.part_size_bytes - 1) // self.part_size_bytes > MAX_MULTIPART_PARTS
            for size in self.payload_sizes
        ):
            raise ValueError("multipart payloads must use at most 10,000 parts")
        if sum(self.payload_sizes) > MAX_MULTIPART_PAYLOAD_BYTES:
            raise ValueError("total multipart payload must stay within the 64 MiB bound")
        if not self.project_id.strip() or not self.bucket.strip():
            raise ValueError("project_id and bucket must not be blank")


def _payload(size: int) -> bytes:
    seed = hashlib.sha256(f"aiat-object-store-multipart:{size}".encode()).digest()
    repeats, remainder = divmod(size, len(seed))
    return seed * repeats + seed[:remainder]


def _part_count(size: int, part_size_bytes: int) -> int:
    return (size + part_size_bytes - 1) // part_size_bytes


@dataclass(frozen=True, slots=True)
class MultipartUploadReport:
    """Secret-safe result for one provider's multipart probe."""

    provider: str
    adapter_type: str
    adapter_version: str
    rows: tuple[dict[str, Any], ...]
    status: str
    abort_verified: bool
    cleanup_verified: bool
    error_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": OBJECT_STORE_MULTIPART_SCHEMA,
            "provider": self.provider,
            "adapter_type": self.adapter_type,
            "adapter_version": self.adapter_version,
            "status": self.status,
            "error_count": self.error_count,
            "abort_verified": self.abort_verified,
            "cleanup_verified": self.cleanup_verified,
            "rows": [dict(row) for row in self.rows],
            "scope": (
                "disposable multipart create/part/complete/abort and checksum "
                "read-back benchmark; no routing or cutover decision"
            ),
        }


async def _abort_probe(
    store: MultipartObjectStoreAdapter,
    *,
    config: MultipartUploadConfig,
) -> bool:
    key = "multipart/abort-probe.bin"
    upload_id = await store.create_multipart_upload(
        config.project_id,
        key,
        content_type="application/octet-stream",
        bucket=config.bucket,
    )
    try:
        await store.upload_multipart_part(
            config.project_id,
            key,
            upload_id,
            1,
            _payload(min(config.part_size_bytes, 1024 * 1024)),
            bucket=config.bucket,
        )
    finally:
        await store.abort_multipart_upload(
            config.project_id,
            key,
            upload_id,
            bucket=config.bucket,
        )
    remaining = await store.list_objects(
        config.project_id,
        prefix="multipart/abort-probe",
        bucket=config.bucket,
    )
    return not remaining


async def _upload_one(
    store: MultipartObjectStoreAdapter,
    *,
    config: MultipartUploadConfig,
    size: int,
    index: int,
) -> tuple[dict[str, Any], BlobRef | None]:
    data = _payload(size)
    key = f"multipart/{index}-{size}.bin"
    row: dict[str, Any] = {
        "key": key,
        "key_index": index,
        "size_bytes": size,
        "part_size_bytes": config.part_size_bytes,
        "expected_part_count": _part_count(size, config.part_size_bytes),
        "status": "pass",
    }
    upload_id: str | None = None
    ref: BlobRef | None = None
    try:
        started = time.perf_counter()
        upload_id = await store.create_multipart_upload(
            config.project_id,
            key,
            content_type="application/octet-stream",
            bucket=config.bucket,
        )
        parts: list[dict[str, Any]] = []
        for part_number, offset in enumerate(
            range(0, size, config.part_size_bytes),
            start=1,
        ):
            chunk = data[offset : offset + config.part_size_bytes]
            etag = await store.upload_multipart_part(
                config.project_id,
                key,
                upload_id,
                part_number,
                chunk,
                bucket=config.bucket,
            )
            parts.append({"PartNumber": part_number, "ETag": etag})
        await store.complete_multipart_upload(
            config.project_id,
            key,
            upload_id,
            parts,
            bucket=config.bucket,
        )
        upload_id = None
        ref = BlobRef(
            bucket=config.bucket,
            key=f"{config.project_id}/{key}",
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=size,
            content_type="application/octet-stream",
        )
        read_back = await store.download(ref)
        verify_blob_readback(ref, read_back)
        row.update(
            {
                "actual_part_count": len(parts),
                "roundtrip_ms": round((time.perf_counter() - started) * 1000.0, 3),
                "sha256": ref.sha256,
            }
        )
    except Exception as exc:  # pragma: no cover - provider-specific boundary
        row.update({"status": "fail", "error_type": type(exc).__name__})
        if upload_id is not None:
            await store.abort_multipart_upload(
                config.project_id,
                key,
                upload_id,
                bucket=config.bucket,
            )
    return row, ref


async def run_object_store_multipart_probe(
    store: MultipartObjectStoreAdapter,
    *,
    provider: str,
    config: MultipartUploadConfig | None = None,
) -> MultipartUploadReport:
    """Run successful multipart cases, an explicit abort, and scoped cleanup."""

    plan = config or MultipartUploadConfig()
    rows: list[dict[str, Any]] = []
    refs: list[BlobRef] = []
    errors = 0
    abort_verified = False
    cleanup_verified = True
    try:
        for index, size in enumerate(plan.payload_sizes):
            row, ref = await _upload_one(store, config=plan, size=size, index=index)
            rows.append(row)
            if row["status"] != "pass":
                errors += 1
            if ref is not None:
                refs.append(ref)
        try:
            abort_verified = await _abort_probe(store, config=plan)
        except Exception:  # pragma: no cover - provider-specific boundary
            errors += 1
            abort_verified = False
    finally:
        for ref in refs:
            try:
                await store.delete(ref)
            except Exception:  # pragma: no cover - provider-specific cleanup
                errors += 1
                cleanup_verified = False
        try:
            remaining = await store.list_objects(plan.project_id, bucket=plan.bucket)
            cleanup_verified = cleanup_verified and not any(
                str(item.get("key") or "").startswith(f"{plan.project_id}/")
                for item in remaining
            )
        except Exception:  # pragma: no cover - provider-specific cleanup
            errors += 1
            cleanup_verified = False
    return MultipartUploadReport(
        provider=provider,
        adapter_type=str(getattr(store, "adapter_type", "unknown")),
        adapter_version=str(getattr(store, "adapter_version", "unknown")),
        rows=tuple(rows),
        status=(
            "pass"
            if errors == 0
            and abort_verified
            and cleanup_verified
            and all(row["status"] == "pass" for row in rows)
            else "fail"
        ),
        abort_verified=abort_verified,
        cleanup_verified=cleanup_verified,
        error_count=errors,
    )


__all__ = [
    "MAX_MULTIPART_PARTS",
    "MAX_MULTIPART_PAYLOAD_BYTES",
    "MIN_PART_SIZE_BYTES",
    "OBJECT_STORE_MULTIPART_SCHEMA",
    "MultipartObjectStoreAdapter",
    "MultipartUploadConfig",
    "MultipartUploadReport",
    "run_object_store_multipart_probe",
]
