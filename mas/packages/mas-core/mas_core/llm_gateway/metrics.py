"""Real-time metrics collector for the LLM gateway.

Aggregates per-model statistics over sliding time windows (1 min, 1 hr,
24 hr) and provides derived metrics used for intelligent routing and
operational dashboards.

Tracked dimensions per model per window
---------------------------------------
- Request count (total, success, error, rate-limited, timeout)
- Token throughput (prompt, completion, total)
- Latency distribution (min, max, avg, p50, p95, p99)
- Cost accumulation (estimated USD)
- Error rate and rate-limit frequency

Derived signals
---------------
- **Health score** (0–1): composite of error rate, latency, and rate-limit
  frequency.  Used by ``SmartRouter`` to steer traffic.
- **Throughput efficiency**: tokens-per-second, useful for comparing models.
- **Cost efficiency**: tokens-per-dollar, useful for budget optimisation.

Usage::

    from mas_core.llm_gateway.metrics import MetricsCollector

    metrics = MetricsCollector()
    metrics.record_request(model="gpt-4o", latency_s=1.2, ...)

    snapshot = metrics.snapshot("gpt-4o")
    health   = metrics.health_score("gpt-4o")
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Time windows
# ---------------------------------------------------------------------------


class Window(str, Enum):
    """Aggregation time window."""

    MINUTE = "minute"  # 60 s
    HOUR = "hour"  # 3 600 s
    DAY = "day"  # 86 400 s

    @property
    def seconds(self) -> float:
        return {"minute": 60.0, "hour": 3600.0, "day": 86400.0}[self.value]


# ---------------------------------------------------------------------------
# Single data point recorded per request
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RequestRecord:
    """Lightweight record stored in the sliding window buffer."""

    timestamp: float
    model: str
    provider: str
    status: str  # success | error | rate_limited | timeout
    latency_s: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    retry_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary."""
        return {
            "timestamp": self.timestamp,
            "model": self.model,
            "provider": self.provider,
            "status": self.status,
            "latency_s": self.latency_s,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "retry_count": self.retry_count,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RequestRecord:
        """Deserialise from a JSON-safe dictionary."""
        return cls(
            timestamp=float(d["timestamp"]),
            model=d.get("model", ""),
            provider=d.get("provider", ""),
            status=d.get("status", "success"),
            latency_s=float(d.get("latency_s", 0.0)),
            prompt_tokens=int(d.get("prompt_tokens", 0)),
            completion_tokens=int(d.get("completion_tokens", 0)),
            total_tokens=int(d.get("total_tokens", 0)),
            estimated_cost_usd=float(d.get("estimated_cost_usd", 0.0)),
            retry_count=int(d.get("retry_count", 0)),
        )


# ---------------------------------------------------------------------------
# Per-model sliding-window aggregation
# ---------------------------------------------------------------------------


class _ModelWindowStats:
    """Rolling statistics for a single model within a time window."""

    def __init__(self, window: Window) -> None:
        self.window = window
        self._records: deque[RequestRecord] = deque()

    def _prune(self, now: float) -> None:
        cutoff = now - self.window.seconds
        while self._records and self._records[0].timestamp < cutoff:
            self._records.popleft()

    def add(self, record: RequestRecord) -> None:
        self._records.append(record)

    def count(self, now: float | None = None) -> int:
        now = now or time.time()
        self._prune(now)
        return len(self._records)

    def stats(self, now: float | None = None) -> dict[str, Any]:
        """Compute aggregate statistics for the current window."""
        now = now or time.time()
        self._prune(now)

        if not self._records:
            return self._empty_stats()

        records = list(self._records)
        n = len(records)

        successes = [r for r in records if r.status == "success"]
        errors = [r for r in records if r.status == "error"]
        rate_limits = [r for r in records if r.status == "rate_limited"]
        timeouts = [r for r in records if r.status == "timeout"]

        latencies = sorted(r.latency_s for r in successes)
        total_tokens = sum(r.total_tokens for r in records)
        prompt_tokens = sum(r.prompt_tokens for r in records)
        completion_tokens = sum(r.completion_tokens for r in records)
        total_cost = sum(r.estimated_cost_usd for r in records)
        total_retries = sum(r.retry_count for r in records)

        def _pct(data: list[float], p: float) -> float:
            if not data:
                return 0.0
            idx = int(len(data) * p / 100)
            return data[min(idx, len(data) - 1)]

        # Throughput: tokens per second over the effective time span
        # (time from first request to now)
        span_s = max(now - records[0].timestamp, 1.0)
        tps = total_tokens / span_s

        return {
            "window": self.window.value,
            "requests": n,
            "successes": len(successes),
            "errors": len(errors),
            "rate_limits": len(rate_limits),
            "timeouts": len(timeouts),
            "error_rate": round(len(errors) / n, 4) if n else 0.0,
            "rate_limit_rate": round(len(rate_limits) / n, 4) if n else 0.0,
            "total_retries": total_retries,
            "tokens": {
                "prompt": prompt_tokens,
                "completion": completion_tokens,
                "total": total_tokens,
            },
            "tokens_per_second": round(tps, 2),
            "cost_usd": round(total_cost, 8),
            "latency": {
                "avg": round(sum(latencies) / len(latencies), 4) if latencies else 0.0,
                "min": round(min(latencies), 4) if latencies else 0.0,
                "max": round(max(latencies), 4) if latencies else 0.0,
                "p50": round(_pct(latencies, 50), 4),
                "p95": round(_pct(latencies, 95), 4),
                "p99": round(_pct(latencies, 99), 4),
            },
        }

    @staticmethod
    def _empty_stats() -> dict[str, Any]:
        return {
            "window": "empty",
            "requests": 0,
            "successes": 0,
            "errors": 0,
            "rate_limits": 0,
            "timeouts": 0,
            "error_rate": 0.0,
            "rate_limit_rate": 0.0,
            "total_retries": 0,
            "tokens": {"prompt": 0, "completion": 0, "total": 0},
            "tokens_per_second": 0.0,
            "cost_usd": 0.0,
            "latency": {
                "avg": 0.0,
                "min": 0.0,
                "max": 0.0,
                "p50": 0.0,
                "p95": 0.0,
                "p99": 0.0,
            },
        }


# ---------------------------------------------------------------------------
# Metrics collector
# ---------------------------------------------------------------------------


class MetricsCollector:
    """Central metrics collector for all LLM gateway requests.

    Maintains per-model sliding-window statistics over minute, hour, and
    day windows.  Thread-safe for the common case of single-writer
    (the gateway client) with concurrent readers (dashboard, router).
    """

    def __init__(self) -> None:
        # {model_id: {window: _ModelWindowStats}}
        self._windows: dict[str, dict[Window, _ModelWindowStats]] = {}
        # Global record buffer for cross-model queries
        self._global: deque[RequestRecord] = deque(maxlen=200_000)
        # Persistence sinks — called after each record_request()
        self._sinks: list[Callable[[RequestRecord], None]] = []

    def _ensure_model(self, model: str) -> dict[Window, _ModelWindowStats]:
        if model not in self._windows:
            self._windows[model] = {w: _ModelWindowStats(w) for w in Window}
        return self._windows[model]

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_request(
        self,
        *,
        model: str,
        provider: str = "",
        status: str = "success",
        latency_s: float = 0.0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        estimated_cost_usd: float = 0.0,
        retry_count: int = 0,
        timestamp: float | None = None,
    ) -> None:
        """Record a completed (or failed) LLM request."""
        ts = timestamp or time.time()
        rec = RequestRecord(
            timestamp=ts,
            model=model,
            provider=provider,
            status=status,
            latency_s=latency_s,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=estimated_cost_usd,
            retry_count=retry_count,
        )

        windows = self._ensure_model(model)
        for w in windows.values():
            w.add(rec)
        self._global.append(rec)
        for sink in self._sinks:
            try:
                sink(rec)
            except Exception:
                logger.debug("Metrics sink error", exc_info=True)

    def add_sink(self, sink: Callable[[RequestRecord], None]) -> None:
        """Register a persistence sink called after each record_request."""
        self._sinks.append(sink)

    def remove_sink(self, sink: Callable[[RequestRecord], None]) -> None:
        """Unregister a previously added sink."""
        try:
            self._sinks.remove(sink)
        except ValueError:
            pass

    def load_record(self, record: RequestRecord) -> None:
        """Replay a persisted RequestRecord without notifying sinks.

        Called on startup to restore historical data from disk.
        """
        windows = self._ensure_model(record.model)
        for w in windows.values():
            w.add(record)
        self._global.append(record)

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def snapshot(
        self,
        model: str,
        window: Window = Window.HOUR,
    ) -> dict[str, Any]:
        """Return aggregated stats for a model in the given window."""
        windows = self._windows.get(model)
        if not windows:
            return _ModelWindowStats._empty_stats()
        return windows[window].stats()

    def snapshot_all_windows(self, model: str) -> dict[str, Any]:
        """Return stats for a model across all three windows."""
        windows = self._windows.get(model)
        if not windows:
            return {w.value: _ModelWindowStats._empty_stats() for w in Window}
        now = time.time()
        return {w.value: windows[w].stats(now) for w in Window}

    def all_models(self) -> list[str]:
        """Return list of all models with recorded metrics."""
        return sorted(self._windows.keys())

    def dashboard(self) -> dict[str, Any]:
        """Return a full dashboard snapshot for all models."""
        now = time.time()
        result: dict[str, Any] = {
            "timestamp": now,
            "models": {},
            "global": {
                "total_records": len(self._global),
            },
        }
        for model in sorted(self._windows.keys()):
            result["models"][model] = {w.value: self._windows[model][w].stats(now) for w in Window}
            result["models"][model]["health_score"] = self.health_score(model, now)
        return result

    # ------------------------------------------------------------------
    # Health score (composite metric for routing)
    # ------------------------------------------------------------------

    def health_score(
        self,
        model: str,
        now: float | None = None,
    ) -> float:
        """Compute a 0–1 health score for a model.

        Factors (weighted):
        - Error rate over last hour    (weight 0.35, lower is better)
        - Rate-limit rate last hour    (weight 0.25, lower is better)
        - Latency p95 relative to 30s  (weight 0.20, lower is better)
        - Timeout rate last hour        (weight 0.20, lower is better)

        Returns 1.0 for a model with no data (optimistic default).
        """
        windows = self._windows.get(model)
        if not windows:
            return 1.0

        now = now or time.time()
        hour_stats = windows[Window.HOUR].stats(now)
        n = hour_stats["requests"]
        if n == 0:
            return 1.0

        error_rate = hour_stats["error_rate"]
        rl_rate = hour_stats["rate_limit_rate"]
        timeout_rate = hour_stats["timeouts"] / max(n, 1)
        latency_p95 = hour_stats["latency"]["p95"]

        # Normalise latency: 0s → 1.0 score, 30s+ → 0.0
        latency_score = max(0.0, 1.0 - (latency_p95 / 30.0))

        score = (
            (1.0 - error_rate) * 0.35
            + (1.0 - rl_rate) * 0.25
            + latency_score * 0.20
            + (1.0 - timeout_rate) * 0.20
        )
        return round(max(0.0, min(1.0, score)), 4)

    # ------------------------------------------------------------------
    # Cost & throughput efficiency
    # ------------------------------------------------------------------

    def cost_efficiency(
        self,
        model: str,
        window: Window = Window.HOUR,
    ) -> float:
        """Tokens per USD dollar (higher is better).  0 if no cost data."""
        stats = self.snapshot(model, window)
        cost = stats["cost_usd"]
        if cost <= 0:
            return float("inf") if stats["tokens"]["total"] > 0 else 0.0
        return round(stats["tokens"]["total"] / cost, 2)

    def throughput_efficiency(
        self,
        model: str,
        window: Window = Window.MINUTE,
    ) -> float:
        """Average tokens-per-second in the given window."""
        stats = self.snapshot(model, window)
        return stats["tokens_per_second"]

    # ------------------------------------------------------------------
    # Time-series (bucketed) data for charts
    # ------------------------------------------------------------------

    def time_series(
        self,
        model: str,
        window: Window = Window.HOUR,
        buckets: int = 30,
    ) -> list[dict[str, Any]]:
        """Return bucketed time-series data for charting.

        Divides the *window* into *buckets* equal-width intervals and
        returns per-bucket aggregates (requests, errors, tokens, avg latency,
        cost).  Each bucket is labelled with its midpoint timestamp.
        """
        now = time.time()
        total_s = window.seconds
        bucket_s = total_s / buckets
        start = now - total_s

        # Collect raw records from the global buffer for this model
        records = [r for r in self._global if r.model == model and r.timestamp >= start]

        series: list[dict[str, Any]] = []
        for i in range(buckets):
            lo = start + i * bucket_s
            hi = lo + bucket_s
            mid = lo + bucket_s / 2
            bucket_recs = [r for r in records if lo <= r.timestamp < hi]

            n = len(bucket_recs)
            successes = sum(1 for r in bucket_recs if r.status == "success")
            errors = sum(1 for r in bucket_recs if r.status in ("error", "rate_limited", "timeout"))
            tokens = sum(r.total_tokens for r in bucket_recs)
            cost = sum(r.estimated_cost_usd for r in bucket_recs)
            latencies = [r.latency_s for r in bucket_recs if r.status == "success"]
            avg_latency = (sum(latencies) / len(latencies)) if latencies else 0.0

            series.append(
                {
                    "t": round(mid, 2),
                    "requests": n,
                    "successes": successes,
                    "errors": errors,
                    "tokens": tokens,
                    "cost_usd": round(cost, 8),
                    "avg_latency_s": round(avg_latency, 4),
                }
            )

        return series

    def global_time_series(
        self,
        window: Window = Window.HOUR,
        buckets: int = 30,
    ) -> list[dict[str, Any]]:
        """Aggregate time-series across all models."""
        now = time.time()
        total_s = window.seconds
        bucket_s = total_s / buckets
        start = now - total_s

        records = [r for r in self._global if r.timestamp >= start]
        series: list[dict[str, Any]] = []
        for i in range(buckets):
            lo = start + i * bucket_s
            hi = lo + bucket_s
            mid = lo + bucket_s / 2
            bucket_recs = [r for r in records if lo <= r.timestamp < hi]

            n = len(bucket_recs)
            tokens = sum(r.total_tokens for r in bucket_recs)
            cost = sum(r.estimated_cost_usd for r in bucket_recs)

            series.append(
                {
                    "t": round(mid, 2),
                    "requests": n,
                    "tokens": tokens,
                    "cost_usd": round(cost, 8),
                }
            )

        return series

    def latency_histogram(
        self,
        model: str,
        window: Window = Window.HOUR,
        bin_count: int = 20,
    ) -> dict[str, Any]:
        """Return a latency histogram for chart rendering."""
        now = time.time()
        start = now - window.seconds
        latencies = [
            r.latency_s
            for r in self._global
            if r.model == model and r.timestamp >= start and r.status == "success"
        ]
        if not latencies:
            return {"bins": [], "counts": [], "model": model}

        lo = 0.0
        hi = max(latencies) * 1.01 if latencies else 1.0
        bin_width = (hi - lo) / bin_count
        bins = [round(lo + i * bin_width, 4) for i in range(bin_count)]
        counts = [0] * bin_count

        for lat in latencies:
            idx = min(int((lat - lo) / bin_width), bin_count - 1)
            counts[idx] += 1

        return {
            "model": model,
            "bins": bins,
            "bin_width": round(bin_width, 4),
            "counts": counts,
            "total": len(latencies),
        }

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def reset(self, model: str | None = None) -> None:
        """Clear metrics.  If model is None, clears everything."""
        if model:
            self._windows.pop(model, None)
        else:
            self._windows.clear()
            self._global.clear()
