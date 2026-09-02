"""Bounded, disposable benchmarks for S3-compatible object stores.

The benchmark deliberately measures the same checksum-bearing ``BlobRef``
boundary used by the conformance suite.  It is evidence, not a routing or
provider-selection authority: a comparison report always requires operator
review before a storage cutover.  Licence and restriction notices are outside
the benchmark predicate and remain provenance metadata only.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .blob import BlobRef
    from .object_store_conformance import ObjectStoreAdapter

OBJECT_STORE_BENCHMARK_SCHEMA = "aiat.object-store-benchmark.v1"
DEFAULT_PAYLOAD_SIZES = (1_024, 64 * 1_024, 1_024 * 1_024)
MAX_CONCURRENCY = 16
MAX_TOTAL_PAYLOAD_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ObjectStoreBenchmarkConfig:
    """Small deterministic benchmark plan suitable for disposable prefixes."""

    payload_sizes: tuple[int, ...] = DEFAULT_PAYLOAD_SIZES
    project_id: str = "aiat-benchmark"
    bucket: str = "mas-agents"
    concurrency: int = 1

    def __post_init__(self) -> None:
        if not self.payload_sizes:
            raise ValueError("payload_sizes must not be empty")
        if any(size <= 0 for size in self.payload_sizes):
            raise ValueError("payload_sizes must contain only positive values")
        if any(size > 16 * 1024 * 1024 for size in self.payload_sizes):
            raise ValueError("payload_sizes must stay within the 16 MiB benchmark bound")
        if self.concurrency < 1 or self.concurrency > MAX_CONCURRENCY:
            raise ValueError(f"concurrency must be between 1 and {MAX_CONCURRENCY}")
        total_payload_bytes = sum(self.payload_sizes) * self.concurrency
        if total_payload_bytes > MAX_TOTAL_PAYLOAD_BYTES:
            raise ValueError(
                "payload_sizes multiplied by concurrency must stay within the "
                "64 MiB total benchmark bound"
            )
        if not self.project_id.strip() or not self.bucket.strip():
            raise ValueError("project_id and bucket must not be blank")


@dataclass(frozen=True, slots=True)
class ObjectStoreBenchmarkReport:
    """Secret-safe result for one provider benchmark run."""

    provider: str
    adapter_type: str
    adapter_version: str
    rows: tuple[dict[str, Any], ...]
    status: str
    error_count: int = 0
    cleanup_verified: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": OBJECT_STORE_BENCHMARK_SCHEMA,
            "provider": self.provider,
            "adapter_type": self.adapter_type,
            "adapter_version": self.adapter_version,
            "status": self.status,
            "error_count": self.error_count,
            "cleanup_verified": self.cleanup_verified,
            "rows": [dict(row) for row in self.rows],
            "scope": (
                "disposable upload/download checksum read-back/delete benchmark; "
                "no routing or cutover decision"
            ),
        }


def _payload(size: int) -> bytes:
    """Create deterministic non-secret bytes without retaining user data."""

    seed = hashlib.sha256(f"aiat-object-store-benchmark:{size}".encode()).digest()
    repeats, remainder = divmod(size, len(seed))
    return seed * repeats + seed[:remainder]


async def _run_case(
    store: ObjectStoreAdapter,
    *,
    plan: ObjectStoreBenchmarkConfig,
    size: int,
    key_index: int,
    concurrency_index: int,
) -> tuple[dict[str, Any], BlobRef | None]:
    """Run one upload/read-back case and return its cleanup reference."""

    data = _payload(size)
    key = (
        f"benchmark/{key_index}-{size}.bin"
        if plan.concurrency == 1
        else f"benchmark/{key_index}-{size}-c{concurrency_index}.bin"
    )
    row: dict[str, Any] = {
        "size_bytes": size,
        "key_index": key_index,
        "concurrency_index": concurrency_index,
        "key": key,
        "status": "pass",
    }
    ref: BlobRef | None = None
    try:
        started = time.perf_counter()
        ref = await store.upload(
            plan.project_id,
            key,
            data,
            content_type="application/octet-stream",
            bucket=plan.bucket,
        )
        upload_ms = (time.perf_counter() - started) * 1000.0

        started = time.perf_counter()
        read_back = await store.download(ref)
        download_ms = (time.perf_counter() - started) * 1000.0
        if read_back != data:
            raise ValueError("checksum read-back mismatch")

        row.update(
            {
                "upload_ms": round(upload_ms, 3),
                "download_ms": round(download_ms, 3),
                "roundtrip_ms": round(upload_ms + download_ms, 3),
                "download_mib_per_second": round(
                    (size / (1024 * 1024)) / max(download_ms / 1000.0, 0.000001),
                    3,
                ),
                "sha256": ref.sha256,
            }
        )
    except Exception as exc:  # pragma: no cover - provider-specific
        row.update({"status": "fail", "error_type": type(exc).__name__})
    return row, ref


async def run_object_store_benchmark(
    store: ObjectStoreAdapter,
    *,
    provider: str,
    config: ObjectStoreBenchmarkConfig | None = None,
) -> ObjectStoreBenchmarkReport:
    """Run bounded provider measurements and clean every disposable object."""

    plan = config or ObjectStoreBenchmarkConfig()
    rows: list[dict[str, Any]] = []
    refs: list[BlobRef] = []
    errors = 0
    cleanup_verified = True
    adapter_type = str(getattr(store, "adapter_type", "unknown"))
    adapter_version = str(getattr(store, "adapter_version", "unknown"))
    try:
        for index, size in enumerate(plan.payload_sizes):
            results = await asyncio.gather(
                *(
                    _run_case(
                        store,
                        plan=plan,
                        size=size,
                        key_index=index,
                        concurrency_index=concurrency_index,
                    )
                    for concurrency_index in range(plan.concurrency)
                )
            )
            for row, ref in results:
                rows.append(row)
                if ref is not None:
                    refs.append(ref)
        rows.sort(key=lambda row: (int(row["key_index"]), int(row["concurrency_index"])))
        errors += sum(1 for row in rows if row.get("status") != "pass")
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
                str(row.get("key") or "").startswith(f"{plan.project_id}/")
                for row in remaining
            )
        except Exception:  # pragma: no cover - provider-specific cleanup
            errors += 1
            cleanup_verified = False
    return ObjectStoreBenchmarkReport(
        provider=provider,
        adapter_type=adapter_type,
        adapter_version=adapter_version,
        rows=tuple(rows),
        status=(
            "pass"
            if errors == 0
            and cleanup_verified
            and len(rows) == len(plan.payload_sizes) * plan.concurrency
            else "fail"
        ),
        error_count=errors,
        cleanup_verified=cleanup_verified,
    )
