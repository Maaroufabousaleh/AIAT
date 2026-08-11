"""Bounded, disposable benchmarks for S3-compatible object stores.

The benchmark deliberately measures the same checksum-bearing ``BlobRef``
boundary used by the conformance suite.  It is evidence, not a routing or
provider-selection authority: a comparison report always requires operator
review before a storage cutover.  Licence and restriction notices are outside
the benchmark predicate and remain provenance metadata only.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any

from .blob import BlobRef
from .object_store_conformance import ObjectStoreAdapter

OBJECT_STORE_BENCHMARK_SCHEMA = "aiat.object-store-benchmark.v1"
DEFAULT_PAYLOAD_SIZES = (1_024, 64 * 1_024, 1_024 * 1_024)


@dataclass(frozen=True, slots=True)
class ObjectStoreBenchmarkConfig:
    """Small deterministic benchmark plan suitable for disposable prefixes."""

    payload_sizes: tuple[int, ...] = DEFAULT_PAYLOAD_SIZES
    project_id: str = "aiat-benchmark"
    bucket: str = "mas-agents"

    def __post_init__(self) -> None:
        if not self.payload_sizes:
            raise ValueError("payload_sizes must not be empty")
        if any(size <= 0 for size in self.payload_sizes):
            raise ValueError("payload_sizes must contain only positive values")
        if any(size > 16 * 1024 * 1024 for size in self.payload_sizes):
            raise ValueError("payload_sizes must stay within the 16 MiB benchmark bound")
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": OBJECT_STORE_BENCHMARK_SCHEMA,
            "provider": self.provider,
            "adapter_type": self.adapter_type,
            "adapter_version": self.adapter_version,
            "status": self.status,
            "error_count": self.error_count,
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
    adapter_type = str(getattr(store, "adapter_type", "unknown"))
    adapter_version = str(getattr(store, "adapter_version", "unknown"))
    try:
        for index, size in enumerate(plan.payload_sizes):
            data = _payload(size)
            key = f"benchmark/{index}-{size}.bin"
            row: dict[str, Any] = {
                "size_bytes": size,
                "key_index": index,
                "status": "pass",
            }
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
                refs.append(ref)

                started = time.perf_counter()
                read_back = await store.download(ref)
                download_ms = (time.perf_counter() - started) * 1000.0
                if read_back != data:
                    raise ValueError("checksum read-back mismatch")

                total_ms = upload_ms + download_ms
                row.update(
                    {
                        "upload_ms": round(upload_ms, 3),
                        "download_ms": round(download_ms, 3),
                        "roundtrip_ms": round(total_ms, 3),
                        "download_mib_per_second": round(
                            (size / (1024 * 1024)) / max(download_ms / 1000.0, 0.000001),
                            3,
                        ),
                        "sha256": ref.sha256,
                    }
                )
            except Exception as exc:  # pragma: no cover - provider-specific
                errors += 1
                row.update({"status": "fail", "error_type": type(exc).__name__})
            rows.append(row)
    finally:
        for ref in refs:
            try:
                await store.delete(ref)
            except Exception:  # pragma: no cover - provider-specific cleanup
                errors += 1
    return ObjectStoreBenchmarkReport(
        provider=provider,
        adapter_type=adapter_type,
        adapter_version=adapter_version,
        rows=tuple(rows),
        status="pass" if errors == 0 and len(rows) == len(plan.payload_sizes) else "fail",
        error_count=errors,
    )
