"""Experimental rate-limit discovery for LLM models.

Instead of relying solely on hardcoded rate limits from provider
documentation (which are often wrong, tier-dependent, or change without
notice), this module **observes actual 429 responses** and successful
request patterns to *empirically* estimate per-model limits:

- **RPM** — Requests per minute
- **RPH** — Requests per hour
- **RPD** — Requests per day
- **TPM** — Tokens per minute
- **TPH** — Tokens per hour
- **TPD** — Tokens per day

How it works
------------
1. Every successful request is recorded with its timestamp and token count.
2. Every 429 (rate-limited) response is a "limit hit" event.
3. When a 429 occurs, the tracker looks back at the successful requests in
   the relevant window (e.g. last 60 s for RPM).  The count just before the
   429 is an empirical estimate of the real limit.
4. Over multiple limit-hit events the estimate converges — we take the
   **minimum** observed pre-429 count as the conservative limit and the
   **median** as the likely limit.
5. A **confidence score** (0–1) increases with the number of observed 429s,
   reaching ~0.8 after 5 observations.

The discovered limits are "experimental" — they are estimates, not
ground truth.  They are displayed alongside provider-documented limits
in the dashboard so operators can compare and calibrate.

Usage::

    from mas_core.llm_gateway.rate_limits import RateLimitTracker

    tracker = RateLimitTracker()
    tracker.record_success(model="gpt-4o", tokens=1500)
    tracker.record_rate_limit(model="gpt-4o")
    limits = tracker.get_limits("gpt-4o")
"""

from __future__ import annotations

import logging
import math
import statistics
import time
from collections import defaultdict, deque
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Limit dimensions
# ---------------------------------------------------------------------------


@dataclass
class ExperimentalLimit:
    """Estimated limit for a single dimension (e.g. RPM)."""

    dimension: str          # rpm | rph | rpd | tpm | tph | tpd
    window_seconds: float   # 60 | 3600 | 86400

    # Observed values just before a 429 hit
    observations: list[int] = field(default_factory=list)

    # Documented / configured limit (from provider, if known)
    documented_limit: int | None = None

    @property
    def sample_count(self) -> int:
        """Number of 429 events used to estimate this limit."""
        return len(self.observations)

    @property
    def confidence(self) -> float:
        """0–1 confidence score. Approaches 1.0 with more observations."""
        if not self.observations:
            return 0.0
        # Sigmoid-like: reaches ~0.8 at 5 observations, ~0.95 at 10
        return round(1.0 - math.exp(-0.35 * len(self.observations)), 3)

    @property
    def estimated_limit(self) -> int | None:
        """Best estimate of the real limit (conservative: minimum observed)."""
        if not self.observations:
            return self.documented_limit
        return min(self.observations)

    @property
    def median_limit(self) -> int | None:
        """Median of observed pre-429 counts (less conservative)."""
        if not self.observations:
            return self.documented_limit
        return int(statistics.median(self.observations))

    @property
    def max_observed(self) -> int | None:
        """Highest count observed before a 429."""
        return max(self.observations) if self.observations else None

    def add_observation(self, count: int) -> None:
        """Record the request/token count that was reached before a 429."""
        self.observations.append(count)
        # Keep only the last 50 observations to avoid unbounded growth
        if len(self.observations) > 50:
            self.observations = self.observations[-50:]
        logger.info(
            "Rate limit observation: %s = %d (confidence: %.2f, "
            "estimated: %s, documented: %s)",
            self.dimension, count, self.confidence,
            self.estimated_limit, self.documented_limit,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "window_seconds": self.window_seconds,
            "sample_count": self.sample_count,
            "confidence": self.confidence,
            "estimated_limit": self.estimated_limit,
            "median_limit": self.median_limit,
            "max_observed": self.max_observed,
            "documented_limit": self.documented_limit,
            "observations_last_10": self.observations[-10:],
        }


# ---------------------------------------------------------------------------
# Per-model limit set
# ---------------------------------------------------------------------------


@dataclass
class ModelRateLimits:
    """All discovered limits for a single model."""

    model: str
    rpm: ExperimentalLimit = field(
        default_factory=lambda: ExperimentalLimit("rpm", 60.0)
    )
    rph: ExperimentalLimit = field(
        default_factory=lambda: ExperimentalLimit("rph", 3600.0)
    )
    rpd: ExperimentalLimit = field(
        default_factory=lambda: ExperimentalLimit("rpd", 86400.0)
    )
    tpm: ExperimentalLimit = field(
        default_factory=lambda: ExperimentalLimit("tpm", 60.0)
    )
    tph: ExperimentalLimit = field(
        default_factory=lambda: ExperimentalLimit("tph", 3600.0)
    )
    tpd: ExperimentalLimit = field(
        default_factory=lambda: ExperimentalLimit("tpd", 86400.0)
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "limits": {
                "rpm": self.rpm.to_dict(),
                "rph": self.rph.to_dict(),
                "rpd": self.rpd.to_dict(),
                "tpm": self.tpm.to_dict(),
                "tph": self.tph.to_dict(),
                "tpd": self.tpd.to_dict(),
            },
            "overall_confidence": round(
                sum(
                    getattr(self, d).confidence
                    for d in ("rpm", "rph", "rpd", "tpm", "tph", "tpd")
                )
                / 6,
                3,
            ),
        }


# ---------------------------------------------------------------------------
# Endpoint/provider transient-failure cooldowns
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CooldownState:
    """Bounded transient-failure state for one model or provider scope.

    Cooldowns are an operational routing aid, not an availability or licence
    gate. They only apply to transient provider failures and are cleared by
    the next successful request.
    """

    key: str
    failure_count: int = 0
    cooldown_until: float | None = None
    last_failure_at: float | None = None
    last_success_at: float | None = None
    last_status_code: int | None = None
    last_reason: str = ""

    def remaining(self, now: float | None = None) -> float:
        """Return the active cooldown in seconds, or ``0`` when available."""
        if self.cooldown_until is None:
            return 0.0
        now = time.time() if now is None else now
        return max(0.0, self.cooldown_until - now)

    def active(self, now: float | None = None) -> bool:
        return self.remaining(now) > 0.0

    def to_dict(self, now: float | None = None) -> dict[str, Any]:
        remaining = self.remaining(now)
        return {
            "key": self.key,
            "failure_count": self.failure_count,
            "cooldown_until": self.cooldown_until,
            "cooldown_remaining_s": round(remaining, 3),
            "in_cooldown": remaining > 0.0,
            "last_failure_at": self.last_failure_at,
            "last_success_at": self.last_success_at,
            "last_status_code": self.last_status_code,
            "last_reason": self.last_reason,
        }


# ---------------------------------------------------------------------------
# Success event buffer (for looking back on 429)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _SuccessEvent:
    """Lightweight record of a successful request."""
    timestamp: float
    tokens: int


# ---------------------------------------------------------------------------
# Rate-limit tracker
# ---------------------------------------------------------------------------


class RateLimitTracker:
    """Discovers empirical limits and steers around transient endpoint failures.

    Parameters
    ----------
    documented_limits:
        Optional dict of ``{model: {dimension: limit}}`` for display
        alongside discovered limits.  E.g.
        ``{"gpt-4o": {"rpm": 500, "tpm": 30_000}}``.
    max_history:
        Maximum number of success events to keep per model (ring buffer).
    cooldown_base_s / cooldown_max_s:
        Exponential backoff bounds for transient model failures.
    cooldown_failure_window_s:
        Idle time after which consecutive failure backoff starts over.
    provider_cooldown_threshold:
        Number of transient failures in one provider scope before the
        provider-wide cooldown becomes active. Model scopes cool down after
        the first failure; provider scopes require a second failure so one
        isolated model outage does not suppress every sibling model.
    """

    def __init__(
        self,
        *,
        documented_limits: dict[str, dict[str, int]] | None = None,
        max_history: int = 100_000,
        cooldown_base_s: float = 15.0,
        cooldown_max_s: float = 300.0,
        cooldown_failure_window_s: float = 300.0,
        provider_cooldown_threshold: int = 2,
    ) -> None:
        self._max_history = max_history
        if cooldown_base_s <= 0:
            raise ValueError("cooldown_base_s must be positive")
        if cooldown_max_s < cooldown_base_s:
            raise ValueError("cooldown_max_s must be >= cooldown_base_s")
        if cooldown_failure_window_s <= 0:
            raise ValueError("cooldown_failure_window_s must be positive")
        if provider_cooldown_threshold < 1:
            raise ValueError("provider_cooldown_threshold must be >= 1")
        self._cooldown_base_s = float(cooldown_base_s)
        self._cooldown_max_s = float(cooldown_max_s)
        self._cooldown_failure_window_s = float(cooldown_failure_window_s)
        self._provider_cooldown_threshold = int(provider_cooldown_threshold)
        # {model: deque[_SuccessEvent]}
        self._success_log: dict[str, deque[_SuccessEvent]] = defaultdict(
            lambda: deque(maxlen=max_history)
        )
        # {model: ModelRateLimits}
        self._limits: dict[str, ModelRateLimits] = {}
        # Documented limits for reference
        self._documented: dict[str, dict[str, int]] = documented_limits or {}
        # Persistence sinks — called after each success/rate_limit event
        self._sinks: list[Callable] = []
        # {model-id | provider:<provider-id>: CooldownState}
        self._cooldowns: dict[str, CooldownState] = {}

    def _ensure_model(self, model: str) -> ModelRateLimits:
        if model not in self._limits:
            mrl = ModelRateLimits(model=model)
            # Attach documented limits if available
            doc = self._documented.get(model, {})
            for dim in ("rpm", "rph", "rpd", "tpm", "tph", "tpd"):
                if dim in doc:
                    getattr(mrl, dim).documented_limit = doc[dim]
            self._limits[model] = mrl
        return self._limits[model]

    def set_documented_limits(
        self,
        model: str,
        limits: dict[str, int],
    ) -> None:
        """Set or update documented (official) limits for a model.

        Parameters
        ----------
        model:
            Model ID.
        limits:
            Dict of ``{dimension: value}``, e.g. ``{"rpm": 500, "tpm": 30000}``.
        """
        self._documented[model] = limits
        mrl = self._ensure_model(model)
        for dim, val in limits.items():
            if hasattr(mrl, dim):
                getattr(mrl, dim).documented_limit = val

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_success(
        self,
        model: str,
        tokens: int = 0,
        timestamp: float | None = None,
        provider: str | None = None,
    ) -> None:
        """Record a successful LLM request."""
        ts = timestamp or time.time()
        self._success_log[model].append(_SuccessEvent(timestamp=ts, tokens=tokens))
        self._ensure_model(model)
        self._clear_cooldown(model, ts)
        if provider:
            self._clear_cooldown(self.provider_key(provider), ts)
        for sink in self._sinks:
            try:
                sink(model, "success")
            except Exception:
                logger.debug("RateLimitTracker sink error", exc_info=True)

    @staticmethod
    def provider_key(provider: str) -> str:
        """Return the namespaced key used for provider-wide health state."""
        return f"provider:{provider}"

    def _clear_cooldown(self, key: str, timestamp: float) -> None:
        state = self._cooldowns.get(key)
        if state is None:
            return
        state.failure_count = 0
        state.cooldown_until = None
        state.last_success_at = timestamp

    def _record_cooldown(
        self,
        key: str,
        *,
        status_code: int,
        reason: str,
        timestamp: float,
        threshold: int,
    ) -> CooldownState:
        state = self._cooldowns.setdefault(key, CooldownState(key=key))
        if (
            state.last_failure_at is None
            or timestamp - state.last_failure_at > self._cooldown_failure_window_s
        ):
            state.failure_count = 0
        state.failure_count += 1
        state.last_failure_at = timestamp
        state.last_status_code = status_code
        state.last_reason = reason[:240]
        if state.failure_count >= threshold:
            exponent = state.failure_count - threshold
            duration = min(self._cooldown_max_s, self._cooldown_base_s * (2**exponent))
            state.cooldown_until = timestamp + duration
        else:
            state.cooldown_until = None
        return state

    def record_transient_failure(
        self,
        model: str,
        *,
        status_code: int = 0,
        reason: str = "",
        provider: str | None = None,
        timestamp: float | None = None,
        retryable: bool = True,
    ) -> None:
        """Record a transient failure and arm model/provider cooldowns.

        Permanent client/configuration failures are deliberately ignored by
        this method when ``retryable`` is false. They remain visible in the
        normal audit and metrics streams but do not silently turn into a
        routing block.
        """
        if not retryable:
            return
        ts = time.time() if timestamp is None else timestamp
        self._record_cooldown(
            model,
            status_code=status_code,
            reason=reason,
            timestamp=ts,
            threshold=1,
        )
        if provider:
            self._record_cooldown(
                self.provider_key(provider),
                status_code=status_code,
                reason=reason,
                timestamp=ts,
                threshold=self._provider_cooldown_threshold,
            )
        for sink in self._sinks:
            try:
                sink(model, "transient_failure")
            except Exception:
                logger.debug("RateLimitTracker sink error", exc_info=True)

    def cooldown_status(
        self,
        model: str,
        *,
        provider: str | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Return deterministic model/provider cooldown evidence."""
        now = time.time() if now is None else now
        scopes: list[dict[str, Any]] = []
        for key in (model, self.provider_key(provider) if provider else None):
            if not key:
                continue
            state = self._cooldowns.get(key)
            if state is not None:
                scopes.append(state.to_dict(now))
        active = [scope for scope in scopes if scope["in_cooldown"]]
        selected = min(active, key=lambda item: item["cooldown_remaining_s"]) if active else None
        return {
            "model": model,
            "provider": provider,
            "in_cooldown": bool(active),
            "cooldown_remaining_s": selected["cooldown_remaining_s"] if selected else 0.0,
            "scope": selected["key"] if selected else None,
            "scopes": scopes,
        }

    def is_in_cooldown(
        self,
        model: str,
        *,
        provider: str | None = None,
        now: float | None = None,
    ) -> bool:
        """Return whether model or its provider is currently cooling down."""
        return bool(self.cooldown_status(model, provider=provider, now=now)["in_cooldown"])

    def available_models(
        self,
        models: list[str],
        *,
        provider_for_model: Callable[[str], str | None] | None = None,
        now: float | None = None,
    ) -> list[str]:
        """Filter a candidate list without changing its deterministic order."""
        return [
            model
            for model in models
            if not self.is_in_cooldown(
                model,
                provider=provider_for_model(model) if provider_for_model else None,
                now=now,
            )
        ]

    def earliest_available_model(
        self,
        models: list[str],
        *,
        provider_for_model: Callable[[str], str | None] | None = None,
        now: float | None = None,
    ) -> str | None:
        """Return the candidate whose cooldown expires first for probing."""
        now = time.time() if now is None else now
        if not models:
            return None
        ranked = sorted(
            models,
            key=lambda model: self.cooldown_status(
                model,
                provider=provider_for_model(model) if provider_for_model else None,
                now=now,
            )["cooldown_remaining_s"],
        )
        return ranked[0]

    def cooldown_dashboard(self, now: float | None = None) -> dict[str, Any]:
        """Return model/provider cooldown evidence for operator dashboards."""
        now = time.time() if now is None else now
        return {
            "timestamp": now,
            "configuration": {
                "base_cooldown_s": self._cooldown_base_s,
                "max_cooldown_s": self._cooldown_max_s,
                "failure_window_s": self._cooldown_failure_window_s,
                "provider_failure_threshold": self._provider_cooldown_threshold,
            },
            "scopes": {
                key: state.to_dict(now)
                for key, state in sorted(self._cooldowns.items())
            },
        }

    def record_rate_limit(
        self,
        model: str,
        timestamp: float | None = None,
    ) -> None:
        """Record a 429 rate-limit event and update estimated limits.

        Looks back at successful requests in each time window to determine
        how many requests/tokens were used just before hitting the limit.
        """
        ts = timestamp or time.time()
        mrl = self._ensure_model(model)
        log = self._success_log.get(model, deque())

        # Count requests and tokens in each window ending at `ts`
        for limit_obj, count_fn in [
            (mrl.rpm, lambda: self._count_in_window(log, ts, 60.0)),
            (mrl.rph, lambda: self._count_in_window(log, ts, 3600.0)),
            (mrl.rpd, lambda: self._count_in_window(log, ts, 86400.0)),
        ]:
            req_count, _ = count_fn()
            if req_count > 0:
                limit_obj.add_observation(req_count)

        for limit_obj, count_fn in [
            (mrl.tpm, lambda: self._count_in_window(log, ts, 60.0)),
            (mrl.tph, lambda: self._count_in_window(log, ts, 3600.0)),
            (mrl.tpd, lambda: self._count_in_window(log, ts, 86400.0)),
        ]:
            _, token_count = count_fn()
            if token_count > 0:
                limit_obj.add_observation(token_count)

        logger.info(
            "Rate limit hit for '%s': estimated limits updated "
            "(RPM≈%s, TPM≈%s, RPD≈%s, TPD≈%s)",
            model,
            mrl.rpm.estimated_limit,
            mrl.tpm.estimated_limit,
            mrl.rpd.estimated_limit,
            mrl.tpd.estimated_limit,
        )
        for sink in self._sinks:
            try:
                sink(model, "rate_limit")
            except Exception:
                logger.debug("RateLimitTracker sink error", exc_info=True)

    @staticmethod
    def _count_in_window(
        log: deque[_SuccessEvent],
        now: float,
        window_s: float,
    ) -> tuple[int, int]:
        """Count requests and total tokens within ``[now - window_s, now]``."""
        cutoff = now - window_s
        req_count = 0
        token_count = 0
        for evt in reversed(log):
            if evt.timestamp < cutoff:
                break
            req_count += 1
            token_count += evt.tokens
        return req_count, token_count

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def get_limits(self, model: str) -> ModelRateLimits:
        """Return the current estimated limits for a model."""
        return self._ensure_model(model)

    def get_all_limits(self) -> dict[str, ModelRateLimits]:
        """Return limit data for all tracked models."""
        return dict(self._limits)

    def get_current_usage(
        self,
        model: str,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Return current usage vs estimated limits for a model.

        Useful for a real-time dashboard showing how close we are to
        each limit dimension.
        """
        now = now or time.time()
        log = self._success_log.get(model, deque())
        mrl = self._ensure_model(model)

        windows = {
            "minute": 60.0,
            "hour": 3600.0,
            "day": 86400.0,
        }
        usage: dict[str, Any] = {"model": model, "timestamp": now, "dimensions": {}}

        for period, secs in windows.items():
            req_count, token_count = self._count_in_window(log, now, secs)

            rpm_dim = {"minute": "rpm", "hour": "rph", "day": "rpd"}[period]
            tpm_dim = {"minute": "tpm", "hour": "tph", "day": "tpd"}[period]

            rpm_limit_obj: ExperimentalLimit = getattr(mrl, rpm_dim)
            tpm_limit_obj: ExperimentalLimit = getattr(mrl, tpm_dim)

            rpm_est = rpm_limit_obj.estimated_limit
            tpm_est = tpm_limit_obj.estimated_limit

            usage["dimensions"][rpm_dim] = {
                "current": req_count,
                "estimated_limit": rpm_est,
                "documented_limit": rpm_limit_obj.documented_limit,
                "utilisation": (
                    round(req_count / rpm_est, 4) if rpm_est else None
                ),
                "confidence": rpm_limit_obj.confidence,
            }
            usage["dimensions"][tpm_dim] = {
                "current": token_count,
                "estimated_limit": tpm_est,
                "documented_limit": tpm_limit_obj.documented_limit,
                "utilisation": (
                    round(token_count / tpm_est, 4) if tpm_est else None
                ),
                "confidence": tpm_limit_obj.confidence,
            }

        return usage

    def dashboard(self, now: float | None = None) -> dict[str, Any]:
        """Full rate-limit and transient-endpoint dashboard."""
        now = now or time.time()
        return {
            "timestamp": now,
            "models": {
                model: {
                    "limits": mrl.to_dict(),
                    "current_usage": self.get_current_usage(model, now),
                }
                for model, mrl in sorted(self._limits.items())
            },
            "cooldowns": self.cooldown_dashboard(now),
        }

    # ------------------------------------------------------------------
    # Routing helpers
    # ------------------------------------------------------------------

    def headroom_score(
        self,
        model: str,
        now: float | None = None,
    ) -> float:
        """Return 0–1 score of remaining capacity (1.0 = fully available).

        Considers the *most constrained* dimension.  If no limits are
        estimated, returns 1.0 (optimistic).
        """
        now = now or time.time()
        log = self._success_log.get(model, deque())
        mrl = self._ensure_model(model)

        worst = 0.0  # worst utilisation fraction

        for period_s, req_dim, tok_dim in [
            (60.0, "rpm", "tpm"),
            (3600.0, "rph", "tph"),
            (86400.0, "rpd", "tpd"),
        ]:
            req_count, token_count = self._count_in_window(log, now, period_s)

            req_limit_obj: ExperimentalLimit = getattr(mrl, req_dim)
            tok_limit_obj: ExperimentalLimit = getattr(mrl, tok_dim)

            req_est = req_limit_obj.estimated_limit
            tok_est = tok_limit_obj.estimated_limit

            if req_est and req_est > 0:
                worst = max(worst, req_count / req_est)
            if tok_est and tok_est > 0:
                worst = max(worst, token_count / tok_est)

        return round(max(0.0, 1.0 - worst), 4)

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def reset(self, model: str | None = None) -> None:
        """Clear tracking data.  If model is None, clears everything."""
        if model:
            self._success_log.pop(model, None)
            self._limits.pop(model, None)
            self._cooldowns.pop(model, None)
        else:
            self._success_log.clear()
            self._limits.clear()
            self._cooldowns.clear()

    def add_sink(self, sink: Callable) -> None:
        """Register a sink called after each record_success / record_rate_limit."""
        self._sinks.append(sink)

    def remove_sink(self, sink: Callable) -> None:
        """Unregister a previously added sink."""
        with suppress(ValueError):
            self._sinks.remove(sink)

    def dump_state(self, now: float | None = None) -> dict[str, Any]:
        """Serialize full tracker state for disk persistence.

        Returns a JSON-safe dict suitable for ``load_state()``.
        """
        now = now or time.time()
        state: dict[str, Any] = {
            "timestamp": now,
            "success_log": {},
            "limits": {},
            "cooldowns": {},
        }
        for model, log in self._success_log.items():
            state["success_log"][model] = [
                {"timestamp": e.timestamp, "tokens": e.tokens} for e in log
            ]
        for model, mrl in self._limits.items():
            dims: dict[str, Any] = {}
            for dim in ("rpm", "rph", "rpd", "tpm", "tph", "tpd"):
                lim: ExperimentalLimit = getattr(mrl, dim)
                dims[dim] = {
                    "observations": list(lim.observations),
                    "documented_limit": lim.documented_limit,
                }
            state["limits"][model] = dims
        state["cooldowns"] = {
            key: {
                "failure_count": cooldown.failure_count,
                "cooldown_until": cooldown.cooldown_until,
                "last_failure_at": cooldown.last_failure_at,
                "last_success_at": cooldown.last_success_at,
                "last_status_code": cooldown.last_status_code,
                "last_reason": cooldown.last_reason,
            }
            for key, cooldown in self._cooldowns.items()
        }
        return state

    def load_state(self, state: dict[str, Any], max_age_s: float = 86400.0) -> None:
        """Restore tracker state from a previously dumped dict.

        Called on startup.  Only success events within ``max_age_s`` are
        restored; older events wouldn't affect current-window estimates.
        """
        now = time.time()
        cutoff = now - max_age_s
        for model, entries in state.get("success_log", {}).items():
            log = self._success_log[model]
            for e in entries:
                ts = float(e.get("timestamp", 0))
                if ts >= cutoff:
                    log.append(_SuccessEvent(timestamp=ts, tokens=int(e.get("tokens", 0))))
            if log:
                self._ensure_model(model)
        for model, dims in state.get("limits", {}).items():
            mrl = self._ensure_model(model)
            for dim, d in dims.items():
                if not hasattr(mrl, dim):
                    continue
                lim: ExperimentalLimit = getattr(mrl, dim)
                lim.observations = list(d.get("observations", []))
                doc = d.get("documented_limit")
                if doc is not None:
                    lim.documented_limit = int(doc)
        for key, raw in state.get("cooldowns", {}).items():
            if not isinstance(raw, dict):
                continue
            last_failure_at = raw.get("last_failure_at")
            cooldown_until = raw.get("cooldown_until")
            try:
                last_failure = float(last_failure_at) if last_failure_at is not None else None
                until = float(cooldown_until) if cooldown_until is not None else None
            except (TypeError, ValueError):
                continue
            # Keep a still-active cooldown even when its failure event is just
            # outside the normal history window; discard stale inactive state.
            if until is not None and until <= now and (
                last_failure is None or last_failure < cutoff
            ):
                continue
            self._cooldowns[key] = CooldownState(
                key=key,
                failure_count=int(raw.get("failure_count", 0) or 0),
                cooldown_until=until,
                last_failure_at=last_failure,
                last_success_at=(
                    float(raw["last_success_at"])
                    if raw.get("last_success_at") is not None
                    else None
                ),
                last_status_code=(
                    int(raw["last_status_code"])
                    if raw.get("last_status_code") is not None
                    else None
                ),
                last_reason=str(raw.get("last_reason", ""))[:240],
            )
