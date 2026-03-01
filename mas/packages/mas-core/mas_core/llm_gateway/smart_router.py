"""Metrics-enhanced smart router for the LLM gateway.

The ``SmartRouter`` uses live metrics from ``MetricsCollector`` and
``RateLimitTracker`` to make better model-selection decisions than the
default round-robin pool strategy:

- **Latency-aware**: prefers models with lower p95 latency.
- **Error-aware**: penalises models with high recent error rates.
- **Rate-limit-aware**: steers away from models approaching their
  empirical rate ceiling.
- **Cost-aware**: factors in cost-per-token efficiency.

The router produces a **ranked ordering** of candidate models.  This can
be used:
1. When a pool ``pick()`` call is about to happen (override the default
   round-robin).
2. As a fallback selector when the primary model fails.
3. To populate a dashboard showing recommended vs. discouraged models.

Scoring formula
---------------
``score = w_health * health + w_headroom * headroom + w_cost * cost_eff + w_latency * latency_eff``

Default weights: ``health=0.35, headroom=0.30, cost=0.15, latency=0.20``.

Usage::

    from mas_core.llm_gateway.smart_router import SmartRouter

    router = SmartRouter(metrics=metrics_collector, rate_limits=rate_tracker)
    ranked = router.rank_models(candidates=["gpt-4o", "gemma-3-27b"])
    best = router.pick_best(candidates)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .metrics import MetricsCollector, Window
from .rate_limits import RateLimitTracker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Model score
# ---------------------------------------------------------------------------


@dataclass
class ModelScore:
    """Scored model candidate for routing."""

    model: str
    total_score: float

    # Component scores (0–1, higher is better)
    health_score: float = 0.0
    headroom_score: float = 0.0
    cost_score: float = 0.0
    latency_score: float = 0.0

    # Context
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "total_score": round(self.total_score, 4),
            "health_score": round(self.health_score, 4),
            "headroom_score": round(self.headroom_score, 4),
            "cost_score": round(self.cost_score, 4),
            "latency_score": round(self.latency_score, 4),
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Smart router
# ---------------------------------------------------------------------------


class SmartRouter:
    """Metrics-driven model selection router.

    Parameters
    ----------
    metrics:
        ``MetricsCollector`` instance for health/latency data.
    rate_limits:
        ``RateLimitTracker`` instance for headroom data.
    w_health:
        Weight for the health score component.
    w_headroom:
        Weight for the rate-limit headroom component.
    w_cost:
        Weight for cost efficiency.
    w_latency:
        Weight for latency efficiency.
    """

    def __init__(
        self,
        metrics: MetricsCollector,
        rate_limits: RateLimitTracker,
        *,
        w_health: float = 0.35,
        w_headroom: float = 0.30,
        w_cost: float = 0.15,
        w_latency: float = 0.20,
    ) -> None:
        self.metrics = metrics
        self.rate_limits = rate_limits
        self.w_health = w_health
        self.w_headroom = w_headroom
        self.w_cost = w_cost
        self.w_latency = w_latency

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def score_model(self, model: str) -> ModelScore:
        """Compute a composite routing score for a single model."""
        health = self.metrics.health_score(model)
        headroom = self.rate_limits.headroom_score(model)

        # Cost efficiency normalised to 0–1
        # tokens_per_dollar: inf (free) → 1.0, 0 → 0.0
        cost_eff_raw = self.metrics.cost_efficiency(model, Window.HOUR)
        if cost_eff_raw == float("inf"):
            cost_score = 1.0  # free model
        elif cost_eff_raw <= 0:
            cost_score = 0.5  # no data → neutral
        else:
            # Normalise: 1M tokens/$1 → score 1.0, 0 → 0.0
            cost_score = min(1.0, cost_eff_raw / 1_000_000)

        # Latency: use 1-minute window throughput as a proxy
        # Higher TPS → better.  Normalise: 100 TPS → 1.0, 0 → 0.0
        tps = self.metrics.throughput_efficiency(model, Window.MINUTE)
        latency_score = min(1.0, tps / 100.0) if tps > 0 else 0.5

        total = (
            self.w_health * health
            + self.w_headroom * headroom
            + self.w_cost * cost_score
            + self.w_latency * latency_score
        )

        reason_parts = []
        if health < 0.5:
            reason_parts.append(f"unhealthy({health:.2f})")
        if headroom < 0.2:
            reason_parts.append(f"near-limit({headroom:.2f})")
        if cost_score < 0.3:
            reason_parts.append("expensive")

        return ModelScore(
            model=model,
            total_score=round(total, 4),
            health_score=health,
            headroom_score=headroom,
            cost_score=cost_score,
            latency_score=latency_score,
            reason=", ".join(reason_parts) if reason_parts else "ok",
        )

    # ------------------------------------------------------------------
    # Ranking
    # ------------------------------------------------------------------

    def rank_models(
        self, candidates: list[str],
    ) -> list[ModelScore]:
        """Score and rank a list of candidate models (best first)."""
        scores = [self.score_model(m) for m in candidates]
        scores.sort(key=lambda s: s.total_score, reverse=True)
        return scores

    def pick_best(
        self,
        candidates: list[str],
        *,
        min_score: float = 0.1,
    ) -> str | None:
        """Pick the best candidate above ``min_score``, or None."""
        ranking = self.rank_models(candidates)
        if ranking and ranking[0].total_score >= min_score:
            chosen = ranking[0]
            logger.debug(
                "SmartRouter picked '%s' (score=%.3f, reason=%s) from %d candidates",
                chosen.model, chosen.total_score, chosen.reason, len(candidates),
            )
            return chosen.model
        return None

    def should_avoid(self, model: str, threshold: float = 0.25) -> bool:
        """Return True if a model's score is below the avoidance threshold."""
        score = self.score_model(model)
        if score.total_score < threshold:
            logger.warning(
                "SmartRouter: model '%s' scored %.3f (below %.2f) — "
                "consider alternatives. Reason: %s",
                model, score.total_score, threshold, score.reason,
            )
            return True
        return False

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------

    def dashboard(
        self,
        candidates: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return a routing dashboard for display/debugging.

        Parameters
        ----------
        candidates:
            Model IDs to score.  If None, scores all models known to the
            metrics collector.
        """
        if candidates is None:
            candidates = self.metrics.all_models()

        ranking = self.rank_models(candidates)

        return {
            "weights": {
                "health": self.w_health,
                "headroom": self.w_headroom,
                "cost": self.w_cost,
                "latency": self.w_latency,
            },
            "ranking": [s.to_dict() for s in ranking],
            "recommended": ranking[0].model if ranking else None,
            "avoided": [
                s.model for s in ranking if s.total_score < 0.25
            ],
        }
