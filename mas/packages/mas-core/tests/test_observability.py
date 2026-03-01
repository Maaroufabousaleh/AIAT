"""Tests for LLM gateway observability: audit, metrics, rate limits, smart router.

All tests are pure unit tests — no network, no real LLM calls.
"""

from __future__ import annotations

import time
import pytest

from mas_core.llm_gateway.audit import (
    AuditEvent,
    AuditLevel,
    AuditLog,
    fingerprint_messages,
)
from mas_core.llm_gateway.metrics import MetricsCollector, Window
from mas_core.llm_gateway.rate_limits import (
    ExperimentalLimit,
    ModelRateLimits,
    RateLimitTracker,
)
from mas_core.llm_gateway.smart_router import SmartRouter


# =====================================================================
# Audit log tests
# =====================================================================


class TestAuditEvent:
    def test_to_dict_basic_fields(self):
        evt = AuditEvent(
            model="gpt-4o",
            resolved_model="gpt-4o",
            provider="openai",
            status="success",
            latency_s=1.23,
            prompt_tokens=100,
            completion_tokens=200,
            total_tokens=300,
        )
        d = evt.to_dict()
        assert d["model"] == "gpt-4o"
        assert d["resolved_model"] == "gpt-4o"
        assert d["provider"] == "openai"
        assert d["status"] == "success"
        assert d["latency_s"] == 1.23
        assert d["total_tokens"] == 300

    def test_to_dict_omits_empty_optional_fields(self):
        evt = AuditEvent(model="test")
        d = evt.to_dict()
        assert "tool_names" not in d
        assert "error_detail" not in d
        assert "content_fingerprint" not in d

    def test_to_dict_includes_optional_when_set(self):
        evt = AuditEvent(
            model="test",
            tool_names=["search"],
            error_detail="rate limited",
            pool_id="pool-1",
        )
        d = evt.to_dict()
        assert d["tool_names"] == ["search"]
        assert d["error_detail"] == "rate limited"
        assert d["pool_id"] == "pool-1"


class TestFingerprint:
    def test_string_content(self):
        msgs = [{"role": "user", "content": "Hello world"}]
        fp = fingerprint_messages(msgs)
        assert isinstance(fp, str)
        assert len(fp) == 16

    def test_same_input_same_fingerprint(self):
        msgs = [{"role": "user", "content": "Hello"}]
        assert fingerprint_messages(msgs) == fingerprint_messages(msgs)

    def test_different_input_different_fingerprint(self):
        m1 = [{"role": "user", "content": "A"}]
        m2 = [{"role": "user", "content": "B"}]
        assert fingerprint_messages(m1) != fingerprint_messages(m2)

    def test_multipart_content(self):
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this"},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            }
        ]
        fp = fingerprint_messages(msgs)
        assert len(fp) == 16


class TestAuditLog:
    def test_record_and_query(self):
        log = AuditLog(level=AuditLevel.STANDARD)
        evt = AuditEvent(model="m1", resolved_model="m1", status="success")
        log.record(evt)
        assert len(log) == 1
        assert log.query(model="m1") == [evt]

    def test_level_none_skips_recording(self):
        log = AuditLog(level=AuditLevel.NONE)
        log.record(AuditEvent(model="m1"))
        assert len(log) == 0

    def test_ring_buffer_eviction(self):
        log = AuditLog(level=AuditLevel.BASIC, max_events=3)
        for i in range(5):
            log.record(AuditEvent(model=f"m{i}"))
        assert len(log) == 3
        # Oldest (m0, m1) should be evicted
        models = [e.model for e in log.events]
        assert models == ["m2", "m3", "m4"]

    def test_query_by_status(self):
        log = AuditLog(level=AuditLevel.BASIC)
        log.record(AuditEvent(model="m1", status="success"))
        log.record(AuditEvent(model="m1", status="error"))
        log.record(AuditEvent(model="m1", status="rate_limited"))
        errors = log.query(status="error")
        assert len(errors) == 1

    def test_summary_counters(self):
        log = AuditLog(level=AuditLevel.STANDARD)
        log.record(AuditEvent(
            model="m1", resolved_model="m1", status="success",
            total_tokens=100, estimated_cost_usd=0.001,
        ))
        log.record(AuditEvent(
            model="m1", resolved_model="m1", status="error",
        ))
        s = log.summary()
        assert s["total_requests"] == 2
        assert s["total_errors"] == 1
        assert s["total_tokens"] == 100
        assert s["per_model"]["m1"]["requests"] == 2
        assert s["per_model"]["m1"]["errors"] == 1

    def test_summary_for_model(self):
        log = AuditLog(level=AuditLevel.STANDARD)
        log.record(AuditEvent(
            model="gpt-4o", resolved_model="gpt-4o", status="success",
            latency_s=1.0, total_tokens=500,
        ))
        log.record(AuditEvent(
            model="gpt-4o", resolved_model="gpt-4o", status="success",
            latency_s=2.0, total_tokens=700,
        ))
        detail = log.summary_for_model("gpt-4o")
        assert detail["requests"] == 2
        assert detail["total_tokens"] == 1200
        assert detail["latency"]["avg"] == 1.5

    def test_sink_called(self):
        captured = []
        log = AuditLog(level=AuditLevel.BASIC, sinks=[captured.append])
        evt = AuditEvent(model="m1")
        log.record(evt)
        assert captured == [evt]

    def test_sink_error_swallowed(self):
        def bad_sink(evt):
            raise ValueError("boom")

        log = AuditLog(level=AuditLevel.BASIC, sinks=[bad_sink])
        log.record(AuditEvent(model="m1"))  # should not raise
        assert len(log) == 1

    def test_reset(self):
        log = AuditLog(level=AuditLevel.BASIC)
        log.record(AuditEvent(model="m1", resolved_model="m1"))
        log.reset()
        assert len(log) == 0
        assert log.summary()["total_requests"] == 0


# =====================================================================
# Metrics collector tests
# =====================================================================


class TestMetricsCollector:
    def test_record_and_snapshot(self):
        mc = MetricsCollector()
        mc.record_request(
            model="gpt-4o", provider="openai", status="success",
            latency_s=1.5, prompt_tokens=100, completion_tokens=200,
            total_tokens=300, estimated_cost_usd=0.003,
        )
        snap = mc.snapshot("gpt-4o", Window.MINUTE)
        assert snap["requests"] == 1
        assert snap["successes"] == 1
        assert snap["tokens"]["total"] == 300

    def test_all_windows(self):
        mc = MetricsCollector()
        mc.record_request(model="m1", status="success", total_tokens=100)
        result = mc.snapshot_all_windows("m1")
        assert "minute" in result
        assert "hour" in result
        assert "day" in result
        for _, stats in result.items():
            assert stats["requests"] == 1

    def test_health_score_perfect(self):
        mc = MetricsCollector()
        mc.record_request(
            model="m1", status="success", latency_s=0.5,
        )
        score = mc.health_score("m1")
        assert score >= 0.9  # very healthy

    def test_health_score_degraded(self):
        mc = MetricsCollector()
        # 5 successes, 5 errors → 50% error rate
        for _ in range(5):
            mc.record_request(model="m1", status="success", latency_s=1.0)
        for _ in range(5):
            mc.record_request(model="m1", status="error", latency_s=0.0)
        score = mc.health_score("m1")
        assert score < 0.85  # degraded compared to 1.0

    def test_health_score_unknown_model(self):
        mc = MetricsCollector()
        assert mc.health_score("unknown") == 1.0  # optimistic

    def test_all_models(self):
        mc = MetricsCollector()
        mc.record_request(model="a", status="success")
        mc.record_request(model="b", status="success")
        assert sorted(mc.all_models()) == ["a", "b"]

    def test_dashboard(self):
        mc = MetricsCollector()
        mc.record_request(model="m1", status="success", total_tokens=500)
        db = mc.dashboard()
        assert "models" in db
        assert "m1" in db["models"]
        assert "health_score" in db["models"]["m1"]

    def test_cost_efficiency_free(self):
        mc = MetricsCollector()
        mc.record_request(
            model="m1", status="success",
            total_tokens=1000, estimated_cost_usd=0.0,
        )
        eff = mc.cost_efficiency("m1")
        assert eff == float("inf")  # free

    def test_cost_efficiency_paid(self):
        mc = MetricsCollector()
        mc.record_request(
            model="m1", status="success",
            total_tokens=1000, estimated_cost_usd=0.01,
        )
        eff = mc.cost_efficiency("m1", Window.HOUR)
        assert eff > 0

    def test_reset_model(self):
        mc = MetricsCollector()
        mc.record_request(model="m1", status="success")
        mc.record_request(model="m2", status="success")
        mc.reset("m1")
        assert "m1" not in mc.all_models()
        assert "m2" in mc.all_models()


# =====================================================================
# Rate-limit tracker tests
# =====================================================================


class TestExperimentalLimit:
    def test_no_observations(self):
        el = ExperimentalLimit("rpm", 60.0)
        assert el.confidence == 0.0
        assert el.estimated_limit is None
        assert el.sample_count == 0

    def test_with_documented(self):
        el = ExperimentalLimit("rpm", 60.0, documented_limit=500)
        assert el.estimated_limit == 500  # falls back to documented

    def test_observations(self):
        el = ExperimentalLimit("rpm", 60.0)
        el.add_observation(25)
        el.add_observation(22)
        el.add_observation(28)
        assert el.estimated_limit == 22  # min
        assert el.median_limit == 25
        assert el.max_observed == 28
        assert el.confidence > 0.5

    def test_confidence_increases(self):
        el = ExperimentalLimit("rpm", 60.0)
        c1 = el.confidence
        el.add_observation(10)
        c2 = el.confidence
        el.add_observation(12)
        c3 = el.confidence
        assert c1 < c2 < c3

    def test_observation_cap(self):
        el = ExperimentalLimit("rpm", 60.0)
        for i in range(60):
            el.add_observation(i)
        assert len(el.observations) == 50  # capped

    def test_to_dict(self):
        el = ExperimentalLimit("rpm", 60.0, documented_limit=100)
        el.add_observation(90)
        d = el.to_dict()
        assert d["dimension"] == "rpm"
        assert d["documented_limit"] == 100
        assert d["estimated_limit"] == 90


class TestRateLimitTracker:
    def test_record_success(self):
        tracker = RateLimitTracker()
        tracker.record_success("m1", tokens=500)
        usage = tracker.get_current_usage("m1")
        assert usage["dimensions"]["rpm"]["current"] >= 1

    def test_record_rate_limit_populates_estimates(self):
        tracker = RateLimitTracker()
        # Simulate 10 requests then a 429
        now = time.time()
        for i in range(10):
            tracker.record_success("m1", tokens=100, timestamp=now + i)
        tracker.record_rate_limit("m1", timestamp=now + 11)

        limits = tracker.get_limits("m1")
        # Should have RPM estimate based on 10 requests in the last minute
        assert limits.rpm.estimated_limit is not None
        assert limits.rpm.estimated_limit <= 10

    def test_documented_limits(self):
        tracker = RateLimitTracker(
            documented_limits={"gpt-4o": {"rpm": 500, "tpm": 30000}}
        )
        limits = tracker.get_limits("gpt-4o")
        assert limits.rpm.documented_limit == 500
        assert limits.tpm.documented_limit == 30000

    def test_set_documented_limits(self):
        tracker = RateLimitTracker()
        tracker.set_documented_limits("m1", {"rpm": 100, "rpd": 5000})
        limits = tracker.get_limits("m1")
        assert limits.rpm.documented_limit == 100
        assert limits.rpd.documented_limit == 5000

    def test_headroom_score_no_data(self):
        tracker = RateLimitTracker()
        # No limits estimated → full headroom
        assert tracker.headroom_score("unknown") == 1.0

    def test_headroom_score_near_limit(self):
        tracker = RateLimitTracker()
        now = time.time()
        # Set a known RPM limit via observation
        for i in range(20):
            tracker.record_success("m1", tokens=100, timestamp=now + i * 0.5)
        tracker.record_rate_limit("m1", timestamp=now + 10.5)
        # Then simulate being close to that limit again
        for i in range(18):
            tracker.record_success(
                "m1", tokens=100, timestamp=now + 11 + i * 0.5,
            )
        score = tracker.headroom_score("m1", now=now + 20)
        assert score < 0.5  # should be near the estimated limit

    def test_dashboard(self):
        tracker = RateLimitTracker()
        tracker.record_success("m1", tokens=100)
        db = tracker.dashboard()
        assert "models" in db
        assert "m1" in db["models"]

    def test_reset_model(self):
        tracker = RateLimitTracker()
        tracker.record_success("m1", tokens=100)
        tracker.record_success("m2", tokens=100)
        tracker.reset("m1")
        assert "m1" not in tracker.get_all_limits()
        assert "m2" in tracker.get_all_limits()

    def test_reset_all(self):
        tracker = RateLimitTracker()
        tracker.record_success("m1", tokens=100)
        tracker.reset()
        assert len(tracker.get_all_limits()) == 0


# =====================================================================
# Smart router tests
# =====================================================================


class TestSmartRouter:
    def _setup(self):
        mc = MetricsCollector()
        rl = RateLimitTracker()
        return mc, rl, SmartRouter(mc, rl)

    def test_score_unknown_model(self):
        mc, rl, router = self._setup()
        score = router.score_model("unknown")
        # Should get optimistic defaults
        assert score.total_score > 0.5

    def test_rank_models(self):
        mc, rl, router = self._setup()
        # Model A: healthy
        mc.record_request(model="a", status="success", latency_s=0.5)
        # Model B: erroring
        mc.record_request(model="b", status="error", latency_s=0.0)
        mc.record_request(model="b", status="error", latency_s=0.0)

        ranking = router.rank_models(["a", "b"])
        assert ranking[0].model == "a"  # healthy model should rank higher

    def test_pick_best(self):
        mc, rl, router = self._setup()
        mc.record_request(model="good", status="success", latency_s=0.5)
        mc.record_request(model="bad", status="error")
        best = router.pick_best(["good", "bad"])
        assert best == "good"

    def test_pick_best_none_when_all_bad(self):
        mc, rl, router = self._setup()
        # Record many errors for both
        for _ in range(10):
            mc.record_request(model="a", status="error")
            mc.record_request(model="b", status="error")
        # min_score=0.9 is very high
        best = router.pick_best(["a", "b"], min_score=0.9)
        assert best is None

    def test_should_avoid(self):
        mc, rl, router = self._setup()
        # Model with terrible metrics — the should_avoid method uses the
        # model's own score as the basis for comparison
        for _ in range(20):
            mc.record_request(model="bad", status="error", latency_s=25.0)
            mc.record_request(model="bad", status="rate_limited")
            mc.record_request(model="bad", status="timeout")
        score_bad = router.score_model("bad")
        # Put a good model for comparison
        for _ in range(20):
            mc.record_request(model="good", status="success", latency_s=0.3)
        score_good = router.score_model("good")
        # Bad model should score worse than good model
        assert score_bad.total_score < score_good.total_score
        # should_avoid should trigger when threshold is above the bad score
        assert router.should_avoid("bad", threshold=score_bad.total_score + 0.01) is True
        # should_avoid should NOT trigger for the good model at same threshold
        assert router.should_avoid("good", threshold=score_bad.total_score + 0.01) is False

    def test_dashboard(self):
        mc, rl, router = self._setup()
        mc.record_request(model="m1", status="success")
        db = router.dashboard()
        assert "ranking" in db
        assert "weights" in db
        assert db["ranking"][0]["model"] == "m1"

    def test_score_model_dict(self):
        mc, rl, router = self._setup()
        mc.record_request(model="m1", status="success", latency_s=0.3)
        score = router.score_model("m1")
        d = score.to_dict()
        assert "total_score" in d
        assert "health_score" in d
        assert "headroom_score" in d
        assert "reason" in d


# =====================================================================
# Integration: audit + metrics + rate-limits working together
# =====================================================================


class TestObservabilityIntegration:
    """Tests that verify the modules work correctly when composed together,
    as they would inside LLMGatewayClient."""

    def test_success_flow(self):
        """Simulate a successful request recording across all systems."""
        audit = AuditLog(level=AuditLevel.FULL)
        mc = MetricsCollector()
        rl = RateLimitTracker()

        model = "gpt-4o"

        # Record success
        evt = AuditEvent(
            model=model, resolved_model=model, provider="openai",
            status="success", latency_s=1.0, prompt_tokens=100,
            completion_tokens=200, total_tokens=300,
            estimated_cost_usd=0.003,
        )
        audit.record(evt)
        mc.record_request(
            model=model, provider="openai", status="success",
            latency_s=1.0, total_tokens=300, estimated_cost_usd=0.003,
        )
        rl.record_success(model, tokens=300)

        # All systems agree
        assert audit.summary()["total_requests"] == 1
        assert mc.snapshot(model)["requests"] == 1
        assert rl.get_current_usage(model)["dimensions"]["rpm"]["current"] >= 1

    def test_rate_limit_flow(self):
        """Simulate a rate-limit event propagating through all systems."""
        audit = AuditLog(level=AuditLevel.STANDARD)
        mc = MetricsCollector()
        rl = RateLimitTracker()

        model = "cheap-model"

        # 5 successes
        for _ in range(5):
            audit.record(AuditEvent(
                model=model, resolved_model=model, status="success",
                total_tokens=100,
            ))
            mc.record_request(model=model, status="success", total_tokens=100)
            rl.record_success(model, tokens=100)

        # Then a 429
        audit.record(AuditEvent(
            model=model, resolved_model=model, status="rate_limited",
            status_code=429, error_detail="Too Many Requests",
        ))
        mc.record_request(model=model, status="rate_limited")
        rl.record_rate_limit(model)

        # Check everything updated
        summary = audit.summary()
        assert summary["per_model"][model]["errors"] == 1

        snap = mc.snapshot(model, Window.MINUTE)
        assert snap["rate_limits"] == 1

        limits = rl.get_limits(model)
        assert limits.rpm.estimated_limit is not None

        # Smart router should detect degradation
        router = SmartRouter(mc, rl)
        score = router.score_model(model)
        # With 1/6 rate-limited, health should be somewhat reduced
        assert score.health_score < 1.0


# =====================================================================
# New tests — time-series, histograms, timeline, dashboard HTML
# =====================================================================


class TestMetricsTimeSeries:
    """Tests for MetricsCollector time-series and histogram methods."""

    def test_time_series_empty(self):
        mc = MetricsCollector()
        series = mc.time_series("nonexistent", Window.MINUTE, buckets=5)
        assert len(series) == 5
        assert all(p["requests"] == 0 for p in series)

    def test_time_series_with_data(self):
        mc = MetricsCollector()
        now = time.time()
        for i in range(10):
            mc.record_request(
                model="m1", status="success",
                latency_s=0.1, total_tokens=50,
                estimated_cost_usd=0.001,
                timestamp=now - 30 + i,  # spread over last 30s
            )
        series = mc.time_series("m1", Window.MINUTE, buckets=10)
        assert len(series) == 10
        total_reqs = sum(p["requests"] for p in series)
        assert total_reqs == 10

    def test_time_series_errors_counted(self):
        mc = MetricsCollector()
        now = time.time()
        mc.record_request(model="m1", status="success", timestamp=now - 5)
        mc.record_request(model="m1", status="error", timestamp=now - 3)
        series = mc.time_series("m1", Window.MINUTE, buckets=5)
        total_errors = sum(p["errors"] for p in series)
        assert total_errors == 1

    def test_global_time_series(self):
        mc = MetricsCollector()
        now = time.time()
        mc.record_request(model="m1", status="success", total_tokens=100, timestamp=now - 10)
        mc.record_request(model="m2", status="success", total_tokens=200, timestamp=now - 5)
        series = mc.global_time_series(Window.MINUTE, buckets=5)
        assert len(series) == 5
        total_tokens = sum(p["tokens"] for p in series)
        assert total_tokens == 300

    def test_latency_histogram_empty(self):
        mc = MetricsCollector()
        hist = mc.latency_histogram("nonexistent", Window.HOUR, 10)
        assert hist["bins"] == []
        assert hist["counts"] == []

    def test_latency_histogram_with_data(self):
        mc = MetricsCollector()
        now = time.time()
        for lat in [0.1, 0.2, 0.3, 0.5, 1.0, 2.0]:
            mc.record_request(
                model="m1", status="success",
                latency_s=lat, timestamp=now - 10,
            )
        hist = mc.latency_histogram("m1", Window.HOUR, 10)
        assert len(hist["bins"]) == 10
        assert len(hist["counts"]) == 10
        assert hist["total"] == 6
        assert sum(hist["counts"]) == 6

    def test_latency_histogram_only_successes(self):
        mc = MetricsCollector()
        now = time.time()
        mc.record_request(model="m1", status="success", latency_s=0.5, timestamp=now - 5)
        mc.record_request(model="m1", status="error", latency_s=10.0, timestamp=now - 3)
        hist = mc.latency_histogram("m1", Window.HOUR, 10)
        assert hist["total"] == 1  # only the success


class TestAuditTimeline:
    """Tests for AuditLog timeline, status_breakdown, top_models."""

    def test_timeline_empty(self):
        log = AuditLog(level=AuditLevel.STANDARD)
        tl = log.timeline(last_minutes=1.0, buckets=5)
        assert len(tl) == 5
        assert all(p["requests"] == 0 for p in tl)

    def test_timeline_with_data(self):
        log = AuditLog(level=AuditLevel.STANDARD)
        now = time.time()
        for i in range(8):
            log.record(AuditEvent(
                model="x", resolved_model="x", status="success",
                total_tokens=10, timestamp=now - 20 + i,
            ))
        tl = log.timeline(last_minutes=1.0, buckets=10)
        total = sum(p["requests"] for p in tl)
        assert total == 8

    def test_timeline_model_filter(self):
        log = AuditLog(level=AuditLevel.STANDARD)
        now = time.time()
        log.record(AuditEvent(model="a", resolved_model="a", status="success", timestamp=now - 5))
        log.record(AuditEvent(model="b", resolved_model="b", status="success", timestamp=now - 3))
        tl = log.timeline(last_minutes=1.0, buckets=5, model="a")
        total = sum(p["requests"] for p in tl)
        assert total == 1

    def test_status_breakdown(self):
        log = AuditLog(level=AuditLevel.STANDARD)
        now = time.time()
        for s in ["success", "success", "error", "rate_limited"]:
            log.record(AuditEvent(model="m", resolved_model="m", status=s, timestamp=now - 1))
        bd = log.status_breakdown(last_minutes=1.0)
        assert bd["success"] == 2
        assert bd["error"] == 1
        assert bd["rate_limited"] == 1

    def test_top_models(self):
        log = AuditLog(level=AuditLevel.STANDARD)
        now = time.time()
        for _ in range(5):
            log.record(AuditEvent(model="big", resolved_model="big", status="success",
                                  total_tokens=100, timestamp=now - 1))
        for _ in range(2):
            log.record(AuditEvent(model="small", resolved_model="small", status="success",
                                  total_tokens=10, timestamp=now - 1))
        top = log.top_models(last_minutes=1.0, top_n=5)
        assert top[0]["model"] == "big"
        assert top[0]["requests"] == 5
        assert top[1]["model"] == "small"


class TestDashboardHTML:
    """Basic tests for the embedded dashboard HTML."""

    def test_html_is_string(self):
        from mas_core.llm_gateway.dashboard import DASHBOARD_HTML
        assert isinstance(DASHBOARD_HTML, str)
        assert len(DASHBOARD_HTML) > 1000

    def test_html_contains_chart_js(self):
        from mas_core.llm_gateway.dashboard import DASHBOARD_HTML
        assert "chart.js" in DASHBOARD_HTML.lower() or "Chart" in DASHBOARD_HTML

    def test_html_contains_key_elements(self):
        from mas_core.llm_gateway.dashboard import DASHBOARD_HTML
        assert "timeline-chart" in DASHBOARD_HTML
        assert "status-chart" in DASHBOARD_HTML
        assert "routing-chart" in DASHBOARD_HTML
        assert "event-log" in DASHBOARD_HTML
        assert "refreshAll" in DASHBOARD_HTML


class TestRoutesObservability:
    """Test that create_observability_router creates expected endpoints."""

    def test_router_creation(self):
        from mas_core.llm_gateway.routes_observability import create_observability_router

        # Minimal mock client
        class _MockClient:
            audit_log = AuditLog(level=AuditLevel.STANDARD)
            metrics = MetricsCollector()
            rate_limits = RateLimitTracker()
            smart_router = SmartRouter(metrics, rate_limits)
            def observability_dashboard(self):
                return {}

        router = create_observability_router(_MockClient())
        paths = [r.path for r in router.routes]
        assert "/ui" in paths
        assert "/dashboard" in paths
        assert "/metrics/time-series" in paths
        assert "/audit/timeline" in paths
        assert "/audit/status-breakdown" in paths
        assert "/audit/top-models" in paths
