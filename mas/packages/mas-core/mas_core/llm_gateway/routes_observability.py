"""FastAPI routes for LLM gateway observability.

Mount these in any FastAPI application that has an ``LLMGatewayClient``
to expose audit, metrics, rate-limit, and routing dashboards via HTTP,
plus a live HTML dashboard UI.

Usage::

    from fastapi import FastAPI
    from mas_core.llm_gateway.routes_observability import create_observability_router

    app = FastAPI()
    # client = LLMGatewayClient(...)
    router = create_observability_router(client)
    app.include_router(router, prefix="/llm")
    # Dashboard UI is then available at  GET /llm/ui
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse


def create_observability_router(client: Any) -> APIRouter:
    """Create an APIRouter with LLM gateway observability endpoints.

    Parameters
    ----------
    client:
        An ``LLMGatewayClient`` instance (with audit_log, metrics,
        rate_limits, and smart_router).
    """
    router = APIRouter(tags=["LLM Observability"])

    # ==================================================================
    # HTML Dashboard UI
    # ==================================================================

    @router.get("/ui", response_class=HTMLResponse)
    async def dashboard_ui() -> str:
        """Serve the interactive observability dashboard."""
        from mas_core.llm_gateway.dashboard import DASHBOARD_HTML
        return DASHBOARD_HTML

    # ------------------------------------------------------------------
    # Full dashboard (JSON)
    # ------------------------------------------------------------------

    @router.get("/dashboard")
    async def llm_dashboard() -> dict[str, Any]:
        """Combined audit + metrics + rate-limits + routing dashboard."""
        return client.observability_dashboard()

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    @router.get("/audit/summary")
    async def audit_summary() -> dict[str, Any]:
        """Aggregate audit summary (total requests, errors, cost, per-model)."""
        return client.audit_log.summary()

    @router.get("/audit/events")
    async def audit_events(
        model: str | None = Query(None, description="Filter by model"),
        provider: str | None = Query(None, description="Filter by provider"),
        status: str | None = Query(None, description="Filter by status"),
        last_minutes: float | None = Query(None, ge=0.1, description="Time window"),
        last_n: int | None = Query(50, ge=1, le=1000, description="Max events"),
    ) -> list[dict[str, Any]]:
        """Return recent audit events with optional filters."""
        events = client.audit_log.query(
            model=model,
            provider=provider,
            status=status,
            last_minutes=last_minutes,
            last_n=last_n,
        )
        return [e.to_dict() for e in events]

    @router.get("/audit/model/{model}")
    async def audit_model_detail(model: str) -> dict[str, Any]:
        """Detailed audit summary for a specific model."""
        return client.audit_log.summary_for_model(model)

    @router.get("/audit/timeline")
    async def audit_timeline(
        last_minutes: float = Query(60.0, ge=1, description="Time window in minutes"),
        buckets: int = Query(30, ge=5, le=200, description="Number of time buckets"),
        model: str | None = Query(None, description="Filter by model"),
    ) -> list[dict[str, Any]]:
        """Bucketed audit event timeline for charting."""
        return client.audit_log.timeline(
            last_minutes=last_minutes, buckets=buckets, model=model,
        )

    @router.get("/audit/status-breakdown")
    async def audit_status_breakdown(
        last_minutes: float = Query(60.0, ge=1),
    ) -> dict[str, int]:
        """Count audit events by status over the given window."""
        return client.audit_log.status_breakdown(last_minutes=last_minutes)

    @router.get("/audit/top-models")
    async def audit_top_models(
        last_minutes: float = Query(60.0, ge=1),
        top_n: int = Query(10, ge=1, le=50),
    ) -> list[dict[str, Any]]:
        """Top models by request count in the given window."""
        return client.audit_log.top_models(last_minutes=last_minutes, top_n=top_n)

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    @router.get("/metrics")
    async def metrics_dashboard() -> dict[str, Any]:
        """Full metrics dashboard with all models and windows."""
        return client.metrics.dashboard()

    @router.get("/metrics/time-series")
    async def metrics_global_timeseries(
        window: str = Query("hour", description="minute | hour | day"),
        buckets: int = Query(30, ge=5, le=200),
    ) -> list[dict[str, Any]]:
        """Global (all-models) time-series for charting."""
        from mas_core.llm_gateway.metrics import Window
        try:
            w = Window(window)
        except ValueError:
            w = Window.HOUR
        return client.metrics.global_time_series(w, buckets)

    @router.get("/metrics/time-series/{model}")
    async def metrics_model_timeseries(
        model: str,
        window: str = Query("hour", description="minute | hour | day"),
        buckets: int = Query(30, ge=5, le=200),
    ) -> list[dict[str, Any]]:
        """Per-model time-series for charting."""
        from mas_core.llm_gateway.metrics import Window
        try:
            w = Window(window)
        except ValueError:
            w = Window.HOUR
        return client.metrics.time_series(model, w, buckets)

    @router.get("/metrics/{model}")
    async def metrics_model(
        model: str,
        window: str = Query("hour", description="minute | hour | day"),
    ) -> dict[str, Any]:
        """Metrics for a specific model in the given time window."""
        from mas_core.llm_gateway.metrics import Window
        try:
            w = Window(window)
        except ValueError:
            w = Window.HOUR
        return client.metrics.snapshot(model, w)

    @router.get("/metrics/{model}/all-windows")
    async def metrics_model_all(model: str) -> dict[str, Any]:
        """Metrics for a model across all time windows (minute, hour, day)."""
        return client.metrics.snapshot_all_windows(model)

    @router.get("/metrics/{model}/health")
    async def metrics_health(model: str) -> dict[str, float]:
        """Health score for a model (0–1, higher is better)."""
        return {"model": model, "health_score": client.metrics.health_score(model)}

    @router.get("/metrics/{model}/latency-histogram")
    async def metrics_latency_histogram(
        model: str,
        window: str = Query("hour", description="minute | hour | day"),
        bins: int = Query(20, ge=5, le=100),
    ) -> dict[str, Any]:
        """Latency histogram for a model (for bar chart rendering)."""
        from mas_core.llm_gateway.metrics import Window
        try:
            w = Window(window)
        except ValueError:
            w = Window.HOUR
        return client.metrics.latency_histogram(model, w, bins)

    # ------------------------------------------------------------------
    # Rate limits (experimental + documented)
    # ------------------------------------------------------------------

    @router.get("/rate-limits")
    async def rate_limits_dashboard() -> dict[str, Any]:
        """Full rate-limit dashboard for all tracked models."""
        return client.rate_limits.dashboard()

    @router.get("/rate-limits/{model}")
    async def rate_limits_model(model: str) -> dict[str, Any]:
        """Experimental rate limits for a specific model.

        Shows per-dimension (RPM, RPH, RPD, TPM, TPH, TPD):
        - Estimated limit (conservative, from 429 observations)
        - Documented limit (from provider, if known)
        - Current usage
        - Utilisation %
        - Confidence score
        """
        limits = client.rate_limits.get_limits(model)
        usage = client.rate_limits.get_current_usage(model)
        return {
            "model": model,
            "limits": limits.to_dict(),
            "current_usage": usage,
            "headroom_score": client.rate_limits.headroom_score(model),
        }

    # ------------------------------------------------------------------
    # Smart routing
    # ------------------------------------------------------------------

    @router.get("/routing")
    async def routing_dashboard() -> dict[str, Any]:
        """Routing dashboard showing ranked model scores."""
        return client.smart_router.dashboard()

    @router.get("/routing/score/{model}")
    async def routing_score(model: str) -> dict[str, Any]:
        """Routing score for a specific model."""
        return client.smart_router.score_model(model).to_dict()

    @router.post("/routing/rank")
    async def routing_rank(candidates: list[str]) -> list[dict[str, Any]]:
        """Rank a list of candidate models by routing score."""
        return [s.to_dict() for s in client.smart_router.rank_models(candidates)]

    @router.get("/routing/recommend")
    async def routing_recommend(
        min_score: float = Query(0.1, description="Minimum acceptable score"),
    ) -> dict[str, Any]:
        """Recommend the best model from all known models."""
        all_models = client.metrics.all_models()
        if not all_models:
            return {"recommended": None, "reason": "no models with metrics data"}
        best = client.smart_router.pick_best(all_models, min_score=min_score)
        return {
            "recommended": best,
            "candidates": len(all_models),
            "ranking": [
                s.to_dict()
                for s in client.smart_router.rank_models(all_models)[:5]
            ],
        }

    return router
