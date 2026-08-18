"""Scalar resource profiling for the bounded object-store benchmark.

The profile wraps the existing checksum/read-back benchmark and records only
bounded wall time, CPU time, and process-memory measurements.  It is disposable
comparison evidence, not a provider-selection, routing, or resource-allocation
authority.  Licence and restriction notices remain provenance metadata only.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .object_store_benchmark import (
    ObjectStoreBenchmarkConfig,
    run_object_store_benchmark,
)

if TYPE_CHECKING:
    from .object_store_benchmark import ObjectStoreBenchmarkReport
    from .object_store_conformance import ObjectStoreAdapter

OBJECT_STORE_RESOURCE_PROFILE_SCHEMA = "aiat.object-store-resource-profile.v1"
DEFAULT_RESOURCE_PROFILE_PAYLOAD_SIZES = (1 * 1024 * 1024, 8 * 1024 * 1024)
DEFAULT_RESOURCE_PROFILE_CONCURRENCY = 4


@dataclass(frozen=True, slots=True)
class ObjectStoreResourceProfileConfig:
    """Bounded plan for one disposable resource-profile run."""

    payload_sizes: tuple[int, ...] = DEFAULT_RESOURCE_PROFILE_PAYLOAD_SIZES
    project_id: str = "aiat-object-store-resource-profile"
    bucket: str = "mas-agents"
    concurrency: int = DEFAULT_RESOURCE_PROFILE_CONCURRENCY

    def __post_init__(self) -> None:
        if not self.payload_sizes:
            raise ValueError("payload_sizes must not be empty")
        if not self.project_id.strip() or not self.bucket.strip():
            raise ValueError("project_id and bucket must not be blank")
        # Reuse the benchmark's explicit size, concurrency, and total-byte
        # guards so the profile cannot become an unbounded load test.
        ObjectStoreBenchmarkConfig(
            payload_sizes=self.payload_sizes,
            project_id=self.project_id,
            bucket=self.bucket,
            concurrency=self.concurrency,
        )

    def benchmark_config(self) -> ObjectStoreBenchmarkConfig:
        return ObjectStoreBenchmarkConfig(
            payload_sizes=self.payload_sizes,
            project_id=self.project_id,
            bucket=self.bucket,
            concurrency=self.concurrency,
        )


def _proc_status_snapshot() -> tuple[int | None, int | None] | None:
    """Return current and high-water RSS in bytes when procfs is available."""

    try:
        values: dict[str, int] = {}
        with open("/proc/self/status", encoding="utf-8") as status_file:
            for line in status_file:
                name, separator, remainder = line.partition(":")
                if separator and name in {"VmRSS", "VmHWM"}:
                    amount, unit, *_ = remainder.strip().split()
                    multiplier = {"kB": 1024, "KB": 1024, "B": 1}.get(unit)
                    if multiplier is not None:
                        values[name] = int(amount) * multiplier
        if "VmRSS" in values or "VmHWM" in values:
            return values.get("VmRSS"), values.get("VmHWM")
    except (OSError, ValueError, UnicodeError):
        return None
    return None


def _resource_snapshot() -> tuple[str, int | None, int | None]:
    proc = _proc_status_snapshot()
    if proc is not None:
        rss_bytes, hwm_bytes = proc
        return "procfs", rss_bytes, hwm_bytes
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        value = int(usage.ru_maxrss)
        # Linux reports KiB; macOS reports bytes.  This fallback is only used
        # when procfs is absent, so preserve a conservative platform label.
        if sys.platform == "darwin":
            peak_bytes = value
        else:
            peak_bytes = value * 1024
        return "resource", None, peak_bytes
    except (ImportError, OSError, AttributeError, ValueError):
        return "unavailable", None, None


@dataclass(frozen=True, slots=True)
class ObjectStoreResourceProfileReport:
    """Secret-safe scalar resource profile for one provider."""

    provider: str
    adapter_type: str
    adapter_version: str
    rows: tuple[dict[str, Any], ...]
    status: str
    measurement_source: str
    wall_time_ms: float
    cpu_time_ms: float
    rss_before_bytes: int | None
    rss_peak_bytes: int | None
    rss_after_bytes: int | None
    cleanup_verified: bool
    error_count: int = 0
    blocked_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": OBJECT_STORE_RESOURCE_PROFILE_SCHEMA,
            "provider": self.provider,
            "adapter_type": self.adapter_type,
            "adapter_version": self.adapter_version,
            "status": self.status,
            "measurement_source": self.measurement_source,
            "wall_time_ms": self.wall_time_ms,
            "cpu_time_ms": self.cpu_time_ms,
            "rss_before_bytes": self.rss_before_bytes,
            "rss_peak_bytes": self.rss_peak_bytes,
            "rss_after_bytes": self.rss_after_bytes,
            "error_count": self.error_count,
            "cleanup_verified": self.cleanup_verified,
            "rows": [dict(row) for row in self.rows],
            "scope": (
                "disposable checksum benchmark resource profile; scalar timing and "
                "process-memory evidence only; no routing or provider decision"
            ),
        }
        if self.blocked_reason:
            payload["blocked_reason"] = self.blocked_reason
        return payload


async def run_object_store_resource_profile(
    store: ObjectStoreAdapter,
    *,
    provider: str,
    config: ObjectStoreResourceProfileConfig | None = None,
) -> ObjectStoreResourceProfileReport:
    """Profile one bounded benchmark run and verify its cleanup result."""

    plan = config or ObjectStoreResourceProfileConfig()
    benchmark_config = plan.benchmark_config()
    adapter_type = str(getattr(store, "adapter_type", "unknown"))
    adapter_version = str(getattr(store, "adapter_version", "unknown"))
    measurement_source, rss_before, hwm_before = _resource_snapshot()
    wall_started = time.perf_counter()
    cpu_started = time.process_time()
    benchmark: ObjectStoreBenchmarkReport = await run_object_store_benchmark(
        store,
        provider=provider,
        config=benchmark_config,
    )
    wall_time_ms = round((time.perf_counter() - wall_started) * 1000.0, 3)
    cpu_time_ms = round((time.process_time() - cpu_started) * 1000.0, 3)
    after_source, rss_after, hwm_after = _resource_snapshot()
    if measurement_source == "unavailable":
        measurement_source = after_source
    rss_peak_values = [value for value in (hwm_before, hwm_after, rss_before, rss_after) if value is not None]
    rss_peak = max(rss_peak_values) if rss_peak_values else None
    status = benchmark.status
    blocked_reason: str | None = None
    if status == "pass" and rss_peak is None:
        status = "blocked"
        blocked_reason = "resource measurement is unavailable on this host"
    return ObjectStoreResourceProfileReport(
        provider=provider,
        adapter_type=adapter_type,
        adapter_version=adapter_version,
        rows=benchmark.rows,
        status=status,
        measurement_source=measurement_source,
        wall_time_ms=wall_time_ms,
        cpu_time_ms=cpu_time_ms,
        rss_before_bytes=rss_before,
        rss_peak_bytes=rss_peak,
        rss_after_bytes=rss_after,
        cleanup_verified=benchmark.cleanup_verified,
        error_count=benchmark.error_count,
        blocked_reason=blocked_reason,
    )
