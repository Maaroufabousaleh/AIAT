"""Per-group token-bucket rate limiter.

Each canonical tool group gets its own limiter with rates from
``GROUP_RATE_LIMITS``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from time import monotonic

from mas_tools_sdk.groups import GROUP_RATE_LIMITS, ToolGroup

try:
    from aiolimiter import AsyncLimiter as _AsyncLimiter
except ModuleNotFoundError:
    class _AsyncLimiter:  # pragma: no cover - fallback for minimal test environments
        """Small in-process limiter fallback when aiolimiter is unavailable."""

        def __init__(self, max_rate: int, time_period: int) -> None:
            self.time_period = float(time_period)
            self._max_rate = float(max_rate)
            self._rate_per_sec = self._max_rate / self.time_period
            self._level = 0.0
            self._window_started = monotonic()
            self._lock = asyncio.Lock()

        def _refill(self) -> None:
            if monotonic() - self._window_started >= self.time_period:
                self._level = 0.0
                self._window_started = monotonic()

        def has_capacity(self) -> bool:
            self._refill()
            return self._level + 1.0 <= self._max_rate

        async def acquire(self) -> None:
            async with self._lock:
                self._refill()
                if self._level + 1.0 <= self._max_rate:
                    self._level += 1.0
                    return
                sleep_for = max(0.0, self.time_period - (monotonic() - self._window_started))
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            async with self._lock:
                self._refill()
                self._level = min(self._max_rate, self._level + 1.0)


class RateLimiterPool:
    """A pool of per-group token-bucket rate limiters.

    Usage::

        pool = RateLimiterPool()
        ok, remaining, reset_at = await pool.acquire(ToolGroup.WEB)
        if not ok:
            # Return 429
    """

    def __init__(self, overrides: dict[ToolGroup, int] | None = None) -> None:
        rates = dict(GROUP_RATE_LIMITS)
        if overrides:
            rates.update(overrides)

        # AsyncLimiter(max_rate, time_period_in_seconds)
        # We express as calls-per-minute → (max_rate=N, time_period=60)
        self._limiters: dict[ToolGroup, _AsyncLimiter] = {
            group: _AsyncLimiter(max_rate=rate, time_period=60)
            for group, rate in rates.items()
        }
        self._rates: dict[ToolGroup, int] = rates

    async def acquire(self, group: ToolGroup) -> tuple[bool, int | None, datetime | None]:
        """Try to acquire a token for the given group.

        Returns
        -------
        tuple[bool, int | None, datetime | None]
            (allowed, remaining, reset_at)
            *allowed* is ``True`` if the request may proceed.
            *remaining* is an estimate of remaining tokens.
            *reset_at* is an approximate UTC time when the bucket refills.
        """
        limiter = self._limiters.get(group)
        if limiter is None:
            return True, None, None

        if not limiter.has_capacity():
            reset_at = datetime.now(tz=timezone.utc)
            return False, 0, reset_at

        await limiter.acquire()
        # Estimate remaining (aiolimiter doesn't expose this cleanly)
        remaining = max(0, int(limiter._rate_per_sec * limiter.time_period - limiter._level))  # noqa: SLF001
        return True, remaining, None

    def get_rate(self, group: ToolGroup) -> int:
        """Return the configured rate limit for a group."""
        return self._rates.get(group, 0)
