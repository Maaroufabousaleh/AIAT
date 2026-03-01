"""Continuous audit log for the LLM gateway.

Every ``chat_completion`` call is recorded as an ``AuditEvent`` with full
request/response metadata.  Events are kept in a bounded in-memory ring
buffer **and** optionally forwarded to external sinks (file, callback,
structured logger).

Audit levels
------------
BASIC
    Timestamp, model, provider, status, latency, token counts.
STANDARD
    + finish reason, retry count, tool definitions used, cost estimate.
FULL
    + SHA-256 content fingerprints of messages (never raw content),
    response text length, full error details, request/response headers
    summary.

The audit log is designed for continuous operation — the ring buffer caps
memory usage and old events are evicted automatically.

Usage::

    from mas_core.llm_gateway.audit import AuditLog, AuditLevel

    audit = AuditLog(level=AuditLevel.STANDARD, max_events=50_000)
    audit.record(event)

    # Query recent events
    recent = audit.query(model="gpt-4o", last_minutes=60)
    summary = audit.summary()
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Callable, Sequence
from uuid import uuid4

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Audit level
# ---------------------------------------------------------------------------


class AuditLevel(IntEnum):
    """How much detail to capture per event."""

    NONE = 0
    """Auditing disabled — no events recorded."""

    BASIC = 1
    """Minimal: timestamp, model, status, latency, token counts."""

    STANDARD = 2
    """Standard: + finish reason, retry count, tools used, cost."""

    FULL = 3
    """Maximum: + content fingerprints, error details, headers summary."""


# ---------------------------------------------------------------------------
# Audit event
# ---------------------------------------------------------------------------


@dataclass
class AuditEvent:
    """Single auditable LLM gateway call."""

    # Identity
    event_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: float = field(default_factory=time.time)

    # Request context
    model: str = ""
    resolved_model: str = ""          # after pool resolution
    provider: str = ""
    api_style: str = ""               # chat_completions | responses | cli

    # Request metadata
    message_count: int = 0
    tool_count: int = 0
    has_images: bool = False
    max_tokens_requested: int | None = None
    temperature: float = 0.7
    stream: bool = False

    # Response metadata
    status: str = "success"           # success | error | rate_limited | timeout
    status_code: int = 200
    finish_reason: str = "stop"
    latency_s: float = 0.0
    retry_count: int = 0

    # Token usage
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    # Cost
    estimated_cost_usd: float = 0.0

    # STANDARD-level fields
    tool_names: list[str] = field(default_factory=list)
    tool_calls_returned: int = 0

    # FULL-level fields
    content_fingerprint: str = ""     # SHA-256 of concatenated message content
    response_text_length: int = 0
    error_detail: str = ""
    pool_id: str | None = None
    pool_headroom: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary."""
        d = {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "model": self.model,
            "resolved_model": self.resolved_model,
            "provider": self.provider,
            "api_style": self.api_style,
            "status": self.status,
            "status_code": self.status_code,
            "latency_s": round(self.latency_s, 4),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 8),
            "finish_reason": self.finish_reason,
            "retry_count": self.retry_count,
            "message_count": self.message_count,
            "tool_count": self.tool_count,
            "stream": self.stream,
        }
        if self.tool_names:
            d["tool_names"] = self.tool_names
        if self.tool_calls_returned:
            d["tool_calls_returned"] = self.tool_calls_returned
        if self.content_fingerprint:
            d["content_fingerprint"] = self.content_fingerprint
        if self.response_text_length:
            d["response_text_length"] = self.response_text_length
        if self.error_detail:
            d["error_detail"] = self.error_detail
        if self.pool_id:
            d["pool_id"] = self.pool_id
        if self.pool_headroom is not None:
            d["pool_headroom"] = round(self.pool_headroom, 4)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AuditEvent":
        """Deserialise from a to_dict() snapshot."""
        return cls(
            event_id=d.get("event_id", ""),
            timestamp=float(d.get("timestamp", 0)),
            model=d.get("model", ""),
            resolved_model=d.get("resolved_model", ""),
            provider=d.get("provider", ""),
            api_style=d.get("api_style", ""),
            message_count=int(d.get("message_count", 0)),
            tool_count=int(d.get("tool_count", 0)),
            has_images=bool(d.get("has_images", False)),
            max_tokens_requested=d.get("max_tokens_requested"),
            temperature=float(d.get("temperature", 0.7)),
            stream=bool(d.get("stream", False)),
            status=d.get("status", "success"),
            status_code=int(d.get("status_code", 200)),
            finish_reason=d.get("finish_reason", "stop"),
            latency_s=float(d.get("latency_s", 0.0)),
            retry_count=int(d.get("retry_count", 0)),
            prompt_tokens=int(d.get("prompt_tokens", 0)),
            completion_tokens=int(d.get("completion_tokens", 0)),
            total_tokens=int(d.get("total_tokens", 0)),
            estimated_cost_usd=float(d.get("estimated_cost_usd", 0.0)),
            tool_names=d.get("tool_names", []),
            tool_calls_returned=int(d.get("tool_calls_returned", 0)),
            content_fingerprint=d.get("content_fingerprint", ""),
            response_text_length=int(d.get("response_text_length", 0)),
            error_detail=d.get("error_detail", ""),
            pool_id=d.get("pool_id"),
            pool_headroom=(
                float(d["pool_headroom"]) if d.get("pool_headroom") is not None else None
            ),
        )


# ---------------------------------------------------------------------------
# Helper: content fingerprinting (FULL level only)
# ---------------------------------------------------------------------------


def fingerprint_messages(messages: list[dict[str, Any]]) -> str:
    """Return a SHA-256 hex digest of the concatenated message content.

    Never stores raw content — only a one-way hash for correlation.
    """
    h = hashlib.sha256()
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    h.update(str(part.get("text", "")).encode())
        elif content:
            h.update(str(content).encode())
    return h.hexdigest()[:16]  # truncated for readability


# ---------------------------------------------------------------------------
# Audit sink protocol
# ---------------------------------------------------------------------------

AuditSink = Callable[[AuditEvent], None]
"""Signature for external audit sinks — called synchronously per event."""


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


class AuditLog:
    """Bounded in-memory audit log with optional external sinks.

    Parameters
    ----------
    level:
        Detail level.  ``NONE`` disables all recording.
    max_events:
        Ring buffer capacity.  Oldest events are evicted first.
    sinks:
        Optional list of callables invoked for each new event (e.g. file
        writer, metrics forwarder, alerting hook).
    """

    def __init__(
        self,
        *,
        level: AuditLevel = AuditLevel.STANDARD,
        max_events: int = 50_000,
        sinks: list[AuditSink] | None = None,
    ) -> None:
        self.level = level
        self._events: deque[AuditEvent] = deque(maxlen=max_events)
        self._sinks: list[AuditSink] = list(sinks or [])

        # Running counters for fast summary queries
        self._total_requests: int = 0
        self._total_tokens: int = 0
        self._total_errors: int = 0
        self._total_cost_usd: float = 0.0
        self._per_model_requests: dict[str, int] = {}
        self._per_model_tokens: dict[str, int] = {}
        self._per_model_errors: dict[str, int] = {}
        self._per_model_cost: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(self, event: AuditEvent) -> None:
        """Append an audit event to the log and forward to sinks."""
        if self.level == AuditLevel.NONE:
            return

        self._events.append(event)

        # Update running counters
        self._total_requests += 1
        self._total_tokens += event.total_tokens
        self._total_cost_usd += event.estimated_cost_usd
        model_key = event.resolved_model or event.model
        self._per_model_requests[model_key] = (
            self._per_model_requests.get(model_key, 0) + 1
        )
        self._per_model_tokens[model_key] = (
            self._per_model_tokens.get(model_key, 0) + event.total_tokens
        )
        self._per_model_cost[model_key] = (
            self._per_model_cost.get(model_key, 0.0) + event.estimated_cost_usd
        )

        if event.status != "success":
            self._total_errors += 1
            self._per_model_errors[model_key] = (
                self._per_model_errors.get(model_key, 0) + 1
            )

        # Forward to sinks (fire-and-forget, errors swallowed)
        for sink in self._sinks:
            try:
                sink(event)
            except Exception:
                logger.debug("Audit sink error", exc_info=True)

    def add_sink(self, sink: AuditSink) -> None:
        """Register an external audit sink."""
        self._sinks.append(sink)

    def remove_sink(self, sink: AuditSink) -> None:
        """Remove a previously registered sink."""
        try:
            self._sinks.remove(sink)
        except ValueError:
            pass

    def _load_event(self, event: AuditEvent) -> None:
        """Replay a persisted event without notifying sinks.

        Called on startup to restore historical data from disk.
        Updates the ring buffer and all running counters but skips
        write-through sinks to avoid re-writing the source file.
        """
        self._events.append(event)
        self._total_requests += 1
        self._total_tokens += event.total_tokens
        self._total_cost_usd += event.estimated_cost_usd
        model_key = event.resolved_model or event.model
        self._per_model_requests[model_key] = (
            self._per_model_requests.get(model_key, 0) + 1
        )
        self._per_model_tokens[model_key] = (
            self._per_model_tokens.get(model_key, 0) + event.total_tokens
        )
        self._per_model_cost[model_key] = (
            self._per_model_cost.get(model_key, 0.0) + event.estimated_cost_usd
        )
        if event.status != "success":
            self._total_errors += 1
            self._per_model_errors[model_key] = (
                self._per_model_errors.get(model_key, 0) + 1
            )

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def query(
        self,
        *,
        model: str | None = None,
        provider: str | None = None,
        status: str | None = None,
        last_minutes: float | None = None,
        last_n: int | None = None,
    ) -> list[AuditEvent]:
        """Return matching events from the ring buffer.

        Filters are AND-ed.  ``last_minutes`` filters by wall-clock time.
        ``last_n`` limits the result count (most recent first).
        """
        cutoff = (time.time() - last_minutes * 60) if last_minutes else 0.0
        results: list[AuditEvent] = []
        for ev in reversed(self._events):
            if last_minutes and ev.timestamp < cutoff:
                break
            if model and ev.resolved_model != model and ev.model != model:
                continue
            if provider and ev.provider != provider:
                continue
            if status and ev.status != status:
                continue
            results.append(ev)
            if last_n and len(results) >= last_n:
                break
        return results

    @property
    def events(self) -> Sequence[AuditEvent]:
        """Read-only view of all buffered events (oldest first)."""
        return self._events

    def __len__(self) -> int:
        return len(self._events)

    # ------------------------------------------------------------------
    # Summary / reporting
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Return an aggregate summary of all recorded events."""
        return {
            "total_requests": self._total_requests,
            "total_tokens": self._total_tokens,
            "total_errors": self._total_errors,
            "total_cost_usd": round(self._total_cost_usd, 6),
            "error_rate": (
                round(self._total_errors / self._total_requests, 4)
                if self._total_requests > 0
                else 0.0
            ),
            "buffered_events": len(self._events),
            "per_model": {
                model: {
                    "requests": self._per_model_requests.get(model, 0),
                    "tokens": self._per_model_tokens.get(model, 0),
                    "errors": self._per_model_errors.get(model, 0),
                    "cost_usd": round(self._per_model_cost.get(model, 0.0), 6),
                    "error_rate": round(
                        self._per_model_errors.get(model, 0)
                        / max(self._per_model_requests.get(model, 1), 1),
                        4,
                    ),
                }
                for model in sorted(self._per_model_requests.keys())
            },
        }

    def summary_for_model(self, model: str) -> dict[str, Any]:
        """Return a detailed summary for a specific model."""
        events = self.query(model=model)
        if not events:
            return {"model": model, "requests": 0}

        latencies = [e.latency_s for e in events if e.status == "success"]
        latencies.sort()
        tokens = [e.total_tokens for e in events]

        def _percentile(data: list[float], p: float) -> float:
            if not data:
                return 0.0
            idx = int(len(data) * p / 100)
            return data[min(idx, len(data) - 1)]

        return {
            "model": model,
            "requests": len(events),
            "successes": sum(1 for e in events if e.status == "success"),
            "errors": sum(1 for e in events if e.status != "success"),
            "rate_limits": sum(1 for e in events if e.status == "rate_limited"),
            "timeouts": sum(1 for e in events if e.status == "timeout"),
            "total_tokens": sum(tokens),
            "avg_tokens": round(sum(tokens) / len(tokens), 1) if tokens else 0,
            "total_cost_usd": round(
                sum(e.estimated_cost_usd for e in events), 6
            ),
            "latency": {
                "avg": round(sum(latencies) / len(latencies), 3) if latencies else 0,
                "p50": round(_percentile(latencies, 50), 3),
                "p95": round(_percentile(latencies, 95), 3),
                "p99": round(_percentile(latencies, 99), 3),
                "min": round(min(latencies), 3) if latencies else 0,
                "max": round(max(latencies), 3) if latencies else 0,
            },
        }

    def reset(self) -> None:
        """Clear all events and counters."""
        self._events.clear()
        self._total_requests = 0
        self._total_tokens = 0
        self._total_errors = 0
        self._total_cost_usd = 0.0
        self._per_model_requests.clear()
        self._per_model_tokens.clear()
        self._per_model_errors.clear()
        self._per_model_cost.clear()

    # ------------------------------------------------------------------
    # Time-series / timeline for charts
    # ------------------------------------------------------------------

    def timeline(
        self,
        *,
        last_minutes: float = 60.0,
        buckets: int = 30,
        model: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return bucketed event counts for timeline visualisation.

        Divides the last *last_minutes* into *buckets* equal-width intervals.
        Each bucket contains: request count, error count, total tokens,
        total cost, and average latency.
        """
        now = time.time()
        total_s = last_minutes * 60.0
        bucket_s = total_s / buckets
        start = now - total_s

        # Filter events
        events = [
            e for e in self._events
            if e.timestamp >= start
            and (model is None or e.model == model or e.resolved_model == model)
        ]

        series: list[dict[str, Any]] = []
        for i in range(buckets):
            lo = start + i * bucket_s
            hi = lo + bucket_s
            mid = lo + bucket_s / 2
            in_bucket = [e for e in events if lo <= e.timestamp < hi]

            n = len(in_bucket)
            errors = sum(1 for e in in_bucket if e.status != "success")
            tokens = sum(e.total_tokens for e in in_bucket)
            cost = sum(e.estimated_cost_usd for e in in_bucket)
            latencies = [e.latency_s for e in in_bucket if e.status == "success"]
            avg_lat = (sum(latencies) / len(latencies)) if latencies else 0.0

            series.append({
                "t": round(mid, 2),
                "requests": n,
                "errors": errors,
                "tokens": tokens,
                "cost_usd": round(cost, 8),
                "avg_latency_s": round(avg_lat, 4),
            })

        return series

    def status_breakdown(
        self,
        *,
        last_minutes: float = 60.0,
    ) -> dict[str, int]:
        """Count events by status over the given time window."""
        cutoff = time.time() - last_minutes * 60.0
        breakdown: dict[str, int] = {}
        for e in self._events:
            if e.timestamp >= cutoff:
                breakdown[e.status] = breakdown.get(e.status, 0) + 1
        return breakdown

    def top_models(
        self,
        *,
        last_minutes: float = 60.0,
        top_n: int = 10,
    ) -> list[dict[str, Any]]:
        """Return top models by request count in the given window."""
        cutoff = time.time() - last_minutes * 60.0
        counts: dict[str, dict[str, Any]] = {}
        for e in self._events:
            if e.timestamp >= cutoff:
                key = e.resolved_model or e.model
                if key not in counts:
                    counts[key] = {"model": key, "requests": 0, "tokens": 0, "cost_usd": 0.0, "errors": 0}
                counts[key]["requests"] += 1
                counts[key]["tokens"] += e.total_tokens
                counts[key]["cost_usd"] += e.estimated_cost_usd
                if e.status != "success":
                    counts[key]["errors"] += 1

        ranked = sorted(counts.values(), key=lambda x: x["requests"], reverse=True)
        for item in ranked:
            item["cost_usd"] = round(item["cost_usd"], 6)
        return ranked[:top_n]
