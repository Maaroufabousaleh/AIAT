"""Disk persistence for LLM gateway observability data.

Persists three streams under a single ``storage_dir``:

    audit.jsonl         — append-only audit events (JSONL, one dict per line)
    metrics.jsonl       — append-only request records (JSONL)
    rate_limits.json    — full rate-limit state snapshot (overwritten periodically)

Behaviour
---------
* **Audit events** are written through to disk immediately on each event
  (low frequency, safety-critical for compliance).
* **Metrics records** are buffered in memory and flushed at
  ``flush_interval_s`` (default 30 s) to avoid per-request I/O overhead.
* **Rate-limit state** is flushed together with metrics via an atomic
  temp-file rename.
* On startup ``load()`` reads both JSONL files, filters out entries older
  than ``max_age_hours``, replays them into the in-memory objects, and
  **compacts** the files in place (removes stale lines) so they never
  grow unboundedly.

Usage::

    from mas_core.llm_gateway import (
        LLMGatewayClient, ObservabilityPersistence, AuditLevel,
    )

    client = LLMGatewayClient(audit_level=AuditLevel.STANDARD)

    persist = ObservabilityPersistence(
        storage_dir="~/.mas/observability",
        audit_log=client.audit_log,
        metrics=client.metrics,
        rate_limits=client.rate_limits,
    )
    persist.load()   # restore from disk on startup
    persist.start()  # begin background flushing

    # ... run your application ...

    persist.stop()   # final flush + clean shutdown

    # Or as a context manager (start/stop handled automatically):
    with ObservabilityPersistence(...) as p:
        p.load()
        ...
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from .audit import AuditEvent, AuditLog
from .metrics import MetricsCollector, RequestRecord
from .rate_limits import RateLimitTracker

logger = logging.getLogger(__name__)


class ObservabilityPersistence:
    """Persist and restore LLM gateway observability data across restarts.

    Parameters
    ----------
    storage_dir:
        Directory where data files are stored.  Created automatically if it
        does not exist.  Supports ``~`` expansion.
    audit_log:
        ``AuditLog`` instance to persist.
    metrics:
        ``MetricsCollector`` instance to persist.
    rate_limits:
        ``RateLimitTracker`` instance to persist.
    flush_interval_s:
        How often (in seconds) the background thread flushes buffered
        metrics records and the rate-limit state snapshot to disk.
        Default 30 s.
    max_age_hours:
        Records older than this are discarded on load and during compaction.
        Default 24 h (matches the longest MetricsCollector window).
    """

    #: File names inside storage_dir
    AUDIT_FILE = "audit.jsonl"
    METRICS_FILE = "metrics.jsonl"
    RATE_LIMITS_FILE = "rate_limits.json"

    def __init__(
        self,
        storage_dir: str | Path,
        audit_log: AuditLog,
        metrics: MetricsCollector,
        rate_limits: RateLimitTracker,
        *,
        flush_interval_s: float = 30.0,
        max_age_hours: float = 24.0,
    ) -> None:
        self._dir = Path(storage_dir).expanduser().resolve()
        self._dir.mkdir(parents=True, exist_ok=True)

        self._audit = audit_log
        self._metrics = metrics
        self._rl = rate_limits

        self._flush_interval = flush_interval_s
        self._max_age_s = max_age_hours * 3600.0

        # Audit: write-through — open/write/close per event (simple & safe)
        self._audit_lock = threading.Lock()

        # Metrics: buffered — drain on each flush cycle
        self._metrics_lock = threading.Lock()
        self._pending_metrics: list[dict[str, Any]] = []

        # Background flush thread
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    @property
    def audit_path(self) -> Path:
        return self._dir / self.AUDIT_FILE

    @property
    def metrics_path(self) -> Path:
        return self._dir / self.METRICS_FILE

    @property
    def rate_limits_path(self) -> Path:
        return self._dir / self.RATE_LIMITS_FILE

    # ------------------------------------------------------------------
    # Startup: load + compact
    # ------------------------------------------------------------------

    def load(self) -> dict[str, int]:
        """Load and compact all persisted data.

        Reads the JSONL files, filters old entries, replays each record
        into the in-memory objects via sink-bypassing methods, then
        rewrites the files without the stale entries.

        Call this **once** before ``start()``.

        Returns
        -------
        dict
            ``{"audit": N, "metrics": M}`` — number of records restored.
        """
        cutoff = time.time() - self._max_age_s
        counts: dict[str, int] = {"audit": 0, "metrics": 0}

        # ---- Audit ----
        if self.audit_path.exists():
            kept: list[str] = []
            for raw in self.audit_path.read_text(encoding="utf-8").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    d = json.loads(raw)
                    if float(d.get("timestamp", 0)) < cutoff:
                        continue
                    self._audit._load_event(AuditEvent.from_dict(d))
                    kept.append(raw)
                    counts["audit"] += 1
                except Exception:
                    logger.debug("Skipping malformed audit line", exc_info=True)
            # Compact
            self.audit_path.write_text(
                "\n".join(kept) + ("\n" if kept else ""),
                encoding="utf-8",
            )

        # ---- Metrics ----
        if self.metrics_path.exists():
            kept_m: list[str] = []
            for raw in self.metrics_path.read_text(encoding="utf-8").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    d = json.loads(raw)
                    if float(d.get("timestamp", 0)) < cutoff:
                        continue
                    self._metrics.load_record(RequestRecord.from_dict(d))
                    kept_m.append(raw)
                    counts["metrics"] += 1
                except Exception:
                    logger.debug("Skipping malformed metrics line", exc_info=True)
            # Compact
            self.metrics_path.write_text(
                "\n".join(kept_m) + ("\n" if kept_m else ""),
                encoding="utf-8",
            )

        # ---- Rate limits ----
        if self.rate_limits_path.exists():
            try:
                state = json.loads(
                    self.rate_limits_path.read_text(encoding="utf-8")
                )
                self._rl.load_state(state, max_age_s=self._max_age_s)
            except Exception as exc:
                logger.warning("Failed to load rate-limit state: %s", exc)

        # Register sinks AFTER loading so replayed events aren't re-written
        self._register_sinks()

        logger.info(
            "ObservabilityPersistence.load(): restored %d audit events, "
            "%d metrics records from '%s'",
            counts["audit"],
            counts["metrics"],
            self._dir,
        )
        return counts

    # ------------------------------------------------------------------
    # Sink registration
    # ------------------------------------------------------------------

    def _register_sinks(self) -> None:
        """Attach write-through / buffered sinks to the three objects."""
        self._audit.add_sink(self._on_audit_event)
        self._metrics.add_sink(self._on_metrics_record)
        self._rl.add_sink(self._on_rl_event)

    def _deregister_sinks(self) -> None:
        """Remove sinks (called on stop to avoid writes after close)."""
        self._audit.remove_sink(self._on_audit_event)
        self._metrics.remove_sink(self._on_metrics_record)
        self._rl.remove_sink(self._on_rl_event)

    # ------------------------------------------------------------------
    # Sink callbacks
    # ------------------------------------------------------------------

    def _on_audit_event(self, event: AuditEvent) -> None:
        """Write-through: immediately append event to audit.jsonl."""
        line = json.dumps(event.to_dict()) + "\n"
        with self._audit_lock, self.audit_path.open("a", encoding="utf-8") as fh:
            fh.write(line)

    def _on_metrics_record(self, record: RequestRecord) -> None:
        """Buffer a metrics record for the next periodic flush."""
        with self._metrics_lock:
            self._pending_metrics.append(record.to_dict())

    def _on_rl_event(self, model: str, event_type: str) -> None:  # noqa: ARG002
        """Rate-limit events are flushed on the next flush cycle; no-op here."""

    # ------------------------------------------------------------------
    # Flush
    # ------------------------------------------------------------------

    def flush(self) -> None:
        """Flush buffered metrics records and rate-limit state to disk.

        Safe to call from any thread.  Uses an atomic temp-file rename
        for ``rate_limits.json`` to avoid partial writes.
        """
        # Drain pending metrics
        with self._metrics_lock:
            pending = self._pending_metrics.copy()
            self._pending_metrics.clear()

        if pending:
            lines = "".join(json.dumps(d) + "\n" for d in pending)
            try:
                with self.metrics_path.open("a", encoding="utf-8") as fh:
                    fh.write(lines)
            except Exception as exc:
                logger.warning("Failed to flush metrics: %s", exc)
                # Put records back so they aren't lost
                with self._metrics_lock:
                    self._pending_metrics[:0] = pending

        # Rate-limit state — atomic overwrite
        try:
            state = self._rl.dump_state()
            tmp = self.rate_limits_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
            tmp.replace(self.rate_limits_path)
        except Exception as exc:
            logger.warning("Failed to flush rate-limit state: %s", exc)

    # ------------------------------------------------------------------
    # Background flush thread
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background periodic flush thread.

        Idempotent — calling multiple times is safe.
        """
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._flush_loop,
            name="obs-persistence-flush",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "ObservabilityPersistence: started (dir='%s', interval=%.0fs, max_age=%.1fh)",
            self._dir,
            self._flush_interval,
            self._max_age_s / 3600.0,
        )

    def stop(self) -> None:
        """Stop the background thread and perform a final flush.

        Blocks until the thread exits (up to ``flush_interval_s * 2``).
        """
        self._deregister_sinks()
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self._flush_interval * 2)
        self.flush()
        logger.info("ObservabilityPersistence: stopped, final flush complete")

    def _flush_loop(self) -> None:
        """Background thread body — flush every ``flush_interval_s``."""
        while not self._stop_event.wait(self._flush_interval):
            try:
                self.flush()
            except Exception:
                logger.warning(
                    "ObservabilityPersistence flush error", exc_info=True
                )

    # ------------------------------------------------------------------
    # Context-manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> ObservabilityPersistence:
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def storage_info(self) -> dict[str, Any]:
        """Return file sizes and config for the dashboard.

        Example response::

            {
                "storage_dir": "/home/user/.mas/observability",
                "flush_interval_s": 30.0,
                "max_age_hours": 24.0,
                "pending_metrics": 12,
                "files": {
                    "audit":       {"path": "...", "size_bytes": 102400},
                    "metrics":     {"path": "...", "size_bytes": 512000},
                    "rate_limits": {"path": "...", "size_bytes": 4096},
                },
            }
        """
        def _sz(p: Path) -> int:
            try:
                return p.stat().st_size
            except OSError:
                return 0

        return {
            "storage_dir": str(self._dir),
            "flush_interval_s": self._flush_interval,
            "max_age_hours": self._max_age_s / 3600.0,
            "pending_metrics": len(self._pending_metrics),
            "files": {
                "audit": {
                    "path": str(self.audit_path),
                    "size_bytes": _sz(self.audit_path),
                },
                "metrics": {
                    "path": str(self.metrics_path),
                    "size_bytes": _sz(self.metrics_path),
                },
                "rate_limits": {
                    "path": str(self.rate_limits_path),
                    "size_bytes": _sz(self.rate_limits_path),
                },
            },
        }
