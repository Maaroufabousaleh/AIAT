"""Tests for ObservabilityPersistence — disk persistence across restarts.

All tests are pure unit tests using tmp_path (no network, no LLM calls).
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from mas_core.llm_gateway.audit import AuditEvent, AuditLevel, AuditLog
from mas_core.llm_gateway.metrics import MetricsCollector, RequestRecord, Window
from mas_core.llm_gateway.persistence import ObservabilityPersistence
from mas_core.llm_gateway.rate_limits import RateLimitTracker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_audit() -> AuditLog:
    return AuditLog(level=AuditLevel.STANDARD)


def make_metrics() -> MetricsCollector:
    return MetricsCollector()


def make_rl() -> RateLimitTracker:
    return RateLimitTracker()


def make_persist(tmp_path: Path, audit=None, mc=None, rl=None, **kw) -> ObservabilityPersistence:
    return ObservabilityPersistence(
        storage_dir=tmp_path,
        audit_log=audit if audit is not None else make_audit(),
        metrics=mc if mc is not None else make_metrics(),
        rate_limits=rl if rl is not None else make_rl(),
        flush_interval_s=9999,  # disable auto-flush in tests
        **kw,
    )


# ---------------------------------------------------------------------------
# Unit: RequestRecord serialisation
# ---------------------------------------------------------------------------


class TestRequestRecordSerde:
    def test_to_dict_roundtrip(self):
        now = time.time()
        rec = RequestRecord(
            timestamp=now, model="gpt-4o", provider="openai",
            status="success", latency_s=1.23,
            prompt_tokens=100, completion_tokens=200, total_tokens=300,
            estimated_cost_usd=0.005, retry_count=1,
        )
        d = rec.to_dict()
        rec2 = RequestRecord.from_dict(d)
        assert rec2.model == rec.model
        assert rec2.status == rec.status
        assert rec2.total_tokens == rec.total_tokens
        assert abs(rec2.estimated_cost_usd - rec.estimated_cost_usd) < 1e-9
        assert rec2.retry_count == rec.retry_count

    def test_from_dict_missing_optional_fields(self):
        rec = RequestRecord.from_dict({"timestamp": 1_000_000, "model": "m"})
        assert rec.model == "m"
        assert rec.status == "success"
        assert rec.retry_count == 0


# ---------------------------------------------------------------------------
# Unit: AuditEvent.from_dict
# ---------------------------------------------------------------------------


class TestAuditEventSerde:
    def test_roundtrip(self):
        evt = AuditEvent(
            model="gpt-4o", resolved_model="gpt-4o", provider="openai",
            status="error", latency_s=0.5, total_tokens=150,
            error_detail="timeout", pool_headroom=0.42,
        )
        d = evt.to_dict()
        evt2 = AuditEvent.from_dict(d)
        assert evt2.event_id == evt.event_id
        assert evt2.status == "error"
        assert evt2.error_detail == "timeout"
        assert pytest.approx(evt2.pool_headroom, abs=1e-4) == 0.42

    def test_from_dict_handles_missing_fields(self):
        evt = AuditEvent.from_dict({"timestamp": 1_000_000.0})
        assert evt.model == ""
        assert evt.status == "success"
        assert evt.pool_headroom is None


# ---------------------------------------------------------------------------
# Unit: MetricsCollector sink + load_record
# ---------------------------------------------------------------------------


class TestMetricsSink:
    def test_sink_called_on_record(self):
        mc = make_metrics()
        received = []
        mc.add_sink(received.append)
        mc.record_request(model="m1", status="success", total_tokens=100)
        assert len(received) == 1
        assert received[0].model == "m1"

    def test_remove_sink(self):
        mc = make_metrics()
        received = []
        mc.add_sink(received.append)
        mc.remove_sink(received.append)
        mc.record_request(model="m1", status="success")
        assert len(received) == 0

    def test_load_record_does_not_call_sink(self):
        mc = make_metrics()
        received = []
        mc.add_sink(received.append)
        rec = RequestRecord(
            timestamp=time.time(), model="m2", provider="", status="success",
            latency_s=0.1, prompt_tokens=10, completion_tokens=20, total_tokens=30,
            estimated_cost_usd=0.0,
        )
        mc.load_record(rec)
        assert len(received) == 0  # sink NOT called
        assert mc.snapshot("m2")["requests"] == 1

    def test_load_record_populates_windows(self):
        mc = make_metrics()
        rec = RequestRecord(
            timestamp=time.time() - 10, model="m3", provider="", status="success",
            latency_s=0.5, prompt_tokens=50, completion_tokens=100, total_tokens=150,
            estimated_cost_usd=0.001,
        )
        mc.load_record(rec)
        snap = mc.snapshot("m3", Window.MINUTE)
        assert snap["requests"] == 1
        assert snap["tokens"]["total"] == 150


# ---------------------------------------------------------------------------
# Unit: RateLimitTracker dump_state / load_state
# ---------------------------------------------------------------------------


class TestRateLimitStatePersistence:
    def test_dump_is_json_serialisable(self):
        rl = make_rl()
        rl.record_success("gpt-4o", tokens=500)
        state = rl.dump_state()
        json.dumps(state)  # must not raise

    def test_load_state_restores_success_log(self):
        rl = make_rl()
        now = time.time()
        rl.record_success("gpt-4o", tokens=200, timestamp=now - 30)
        rl.record_success("gpt-4o", tokens=300, timestamp=now - 10)
        state = rl.dump_state()

        rl2 = make_rl()
        rl2.load_state(state, max_age_s=3600)
        usage = rl2.get_current_usage("gpt-4o")
        assert usage["dimensions"]["rpm"]["current"] == 2

    def test_load_state_filters_old_events(self):
        rl = make_rl()
        now = time.time()
        rl.record_success("m", tokens=100, timestamp=now - 90_000)  # >24h
        state = rl.dump_state()

        rl2 = make_rl()
        rl2.load_state(state, max_age_s=86400.0)
        usage = rl2.get_current_usage("m")
        assert usage["dimensions"]["rpm"]["current"] == 0  # filtered out

    def test_load_state_restores_observations(self):
        rl = make_rl()
        now = time.time()
        for _ in range(3):
            rl.record_success("m", tokens=100, timestamp=now - 20)
        rl.record_rate_limit("m", timestamp=now - 10)
        state = rl.dump_state()

        rl2 = make_rl()
        rl2.load_state(state)
        limits = rl2.get_limits("m")
        assert limits.rpm.observations != []

    def test_load_state_restores_documented_limits(self):
        rl = make_rl()
        rl.set_documented_limits("gpt-4o", {"rpm": 500, "tpm": 30_000})
        state = rl.dump_state()

        rl2 = make_rl()
        rl2.load_state(state)
        limits = rl2.get_limits("gpt-4o")
        assert limits.rpm.documented_limit == 500
        assert limits.tpm.documented_limit == 30_000

    def test_load_state_restores_active_cooldown(self):
        rl = make_rl()
        rl.record_transient_failure("gpt-4o", status_code=503, provider="openai")
        state = rl.dump_state()

        rl2 = make_rl()
        rl2.load_state(state, max_age_s=3600)
        assert rl2.is_in_cooldown("gpt-4o", provider="openai") is True
        assert "cooldowns" in rl2.dump_state()


# ---------------------------------------------------------------------------
# ObservabilityPersistence integration
# ---------------------------------------------------------------------------


class TestObservabilityPersistence:
    def test_load_on_empty_directory(self, tmp_path):
        persist = make_persist(tmp_path)
        counts = persist.load()
        assert counts["audit"] == 0
        assert counts["metrics"] == 0

    def test_audit_written_on_event(self, tmp_path):
        audit = make_audit()
        persist = make_persist(tmp_path, audit=audit)
        persist.load()

        audit.record(AuditEvent(model="m", resolved_model="m", status="success",
                                total_tokens=100, timestamp=time.time()))
        # Write-through: file should exist and have content
        assert persist.audit_path.exists()
        lines = persist.audit_path.read_text().strip().splitlines()
        assert len(lines) == 1
        d = json.loads(lines[0])
        assert d["model"] == "m"
        persist.stop()

    def test_metrics_flushed_on_explicit_flush(self, tmp_path):
        mc = make_metrics()
        persist = make_persist(tmp_path, mc=mc)
        persist.load()

        mc.record_request(model="gpt-4o", status="success", total_tokens=50)
        assert len(persist._pending_metrics) == 1

        persist.flush()
        assert len(persist._pending_metrics) == 0
        assert persist.metrics_path.exists()
        lines = persist.metrics_path.read_text().strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["model"] == "gpt-4o"
        persist.stop()

    def test_rate_limits_flushed(self, tmp_path):
        rl = make_rl()
        persist = make_persist(tmp_path, rl=rl)
        persist.load()

        rl.record_success("m", tokens=100)
        persist.flush()

        assert persist.rate_limits_path.exists()
        state = json.loads(persist.rate_limits_path.read_text())
        assert "m" in state["success_log"]
        persist.stop()

    def test_restart_restores_audit_events(self, tmp_path):
        """Simulate shutdown + restart: data should survive."""
        audit = make_audit()
        persist = make_persist(tmp_path, audit=audit)
        persist.load()

        now = time.time()
        for i in range(5):
            audit.record(AuditEvent(
                model="gpt-4o", resolved_model="gpt-4o", status="success",
                total_tokens=100, timestamp=now - i,
            ))
        persist.stop()

        # --- Restart ---
        audit2 = make_audit()
        persist2 = make_persist(tmp_path, audit=audit2)
        counts = persist2.load()

        assert counts["audit"] == 5
        summary = audit2.summary()
        assert summary["total_requests"] == 5
        assert summary["total_tokens"] == 500
        persist2.stop()

    def test_restart_restores_metrics(self, tmp_path):
        mc = make_metrics()
        persist = make_persist(tmp_path, mc=mc)
        persist.load()

        now = time.time()
        mc.record_request(model="gpt-4o", status="success",
                          total_tokens=200, latency_s=0.5, timestamp=now - 60)
        persist.flush()
        persist.stop()

        # --- Restart ---
        mc2 = make_metrics()
        persist2 = make_persist(tmp_path, mc=mc2)
        counts = persist2.load()

        assert counts["metrics"] == 1
        snap = mc2.snapshot("gpt-4o", Window.HOUR)
        assert snap["requests"] == 1
        assert snap["tokens"]["total"] == 200
        persist2.stop()

    def test_restart_restores_rate_limits(self, tmp_path):
        rl = make_rl()
        persist = make_persist(tmp_path, rl=rl)
        persist.load()

        now = time.time()
        for _ in range(4):
            rl.record_success("gemini-flash", tokens=500, timestamp=now - 30)
        rl.record_rate_limit("gemini-flash")
        persist.flush()
        persist.stop()

        # --- Restart ---
        rl2 = make_rl()
        persist2 = make_persist(tmp_path, rl=rl2)
        persist2.load()

        limits = rl2.get_limits("gemini-flash")
        assert limits.rpm.estimated_limit is not None
        persist2.stop()

    def test_old_data_not_loaded_after_max_age(self, tmp_path):
        """Records older than max_age_hours should be discarded on load."""
        audit = make_audit()
        persist = make_persist(tmp_path, audit=audit, max_age_hours=1.0)
        persist.load()

        old_ts = time.time() - 7200  # 2 hours ago
        audit.record(AuditEvent(
            model="m", resolved_model="m", status="success",
            total_tokens=50, timestamp=old_ts,
        ))
        persist.stop()

        # Restart with same max_age_hours=1h — old event should be dropped
        audit2 = make_audit()
        persist2 = make_persist(tmp_path, audit=audit2, max_age_hours=1.0)
        counts = persist2.load()

        assert counts["audit"] == 0
        assert audit2.summary()["total_requests"] == 0
        persist2.stop()

    def test_context_manager_calls_stop(self, tmp_path):
        mc = make_metrics()
        with make_persist(tmp_path, mc=mc) as p:
            p.load()
            mc.record_request(model="m", status="success", total_tokens=10)
        # stop() flushes — metrics file should exist
        assert p.metrics_path.exists()

    def test_storage_info(self, tmp_path):
        persist = make_persist(tmp_path)
        persist.load()
        info = persist.storage_info()
        assert info["storage_dir"] == str(tmp_path)
        assert "files" in info
        assert "audit" in info["files"]
        persist.stop()

    def test_compaction_removes_old_lines(self, tmp_path):
        """On second load, lines older than max_age_hours are removed from file."""
        audit_path = tmp_path / "audit.jsonl"
        old_ts = time.time() - 7200
        new_ts = time.time() - 30

        # Pre-write a file with one old and one new event
        lines = [
            json.dumps({"event_id": "a", "timestamp": old_ts, "model": "m",
                        "resolved_model": "m", "status": "success", "total_tokens": 10,
                        "prompt_tokens": 5, "completion_tokens": 5, "latency_s": 0.1,
                        "estimated_cost_usd": 0, "finish_reason": "stop", "retry_count": 0,
                        "message_count": 1, "tool_count": 0, "stream": False,
                        "status_code": 200}),
            json.dumps({"event_id": "b", "timestamp": new_ts, "model": "m",
                        "resolved_model": "m", "status": "success", "total_tokens": 20,
                        "prompt_tokens": 8, "completion_tokens": 12, "latency_s": 0.2,
                        "estimated_cost_usd": 0, "finish_reason": "stop", "retry_count": 0,
                        "message_count": 1, "tool_count": 0, "stream": False,
                        "status_code": 200}),
        ]
        audit_path.write_text("\n".join(lines) + "\n")

        audit = make_audit()
        persist = make_persist(tmp_path, audit=audit, max_age_hours=1.0)
        counts = persist.load()

        assert counts["audit"] == 1  # only the recent one
        remaining = audit_path.read_text().strip().splitlines()
        assert len(remaining) == 1
        assert json.loads(remaining[0])["event_id"] == "b"
        persist.stop()
