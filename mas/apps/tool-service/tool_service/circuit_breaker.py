"""Per-tool circuit breaker.

Pattern: ≥ ``failure_threshold`` failures within ``failure_window`` seconds
→ OPEN for ``open_duration`` seconds → HALF_OPEN (one probe allowed)
→ if probe succeeds → CLOSED; if probe fails → OPEN again.

Each tool gets its own ``CircuitBreaker`` instance.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque

from mas_core.protocols.tool import CircuitState


class CircuitBreaker:
    """Async-safe per-tool circuit breaker.

    Parameters
    ----------
    name : str
        Tool name (for logging).
    failure_threshold : int
        Number of failures within *failure_window* to trip OPEN.
    failure_window : float
        Rolling window in seconds.
    open_duration : float
        Seconds to remain OPEN before transitioning to HALF_OPEN.
    """

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 3,
        failure_window: float = 60.0,
        open_duration: float = 120.0,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.failure_window = failure_window
        self.open_duration = open_duration

        self._state = CircuitState.CLOSED
        self._failures: deque[float] = deque()
        self._opened_at: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        """Return current state, auto-transitioning OPEN → HALF_OPEN if timer expired."""
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self.open_duration:
                self._state = CircuitState.HALF_OPEN
        return self._state

    def _check_half_open_transition(self) -> None:
        """Transition OPEN → HALF_OPEN if the open timer has expired.

        MUST be called while ``self._lock`` is held.
        """
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self.open_duration:
                self._state = CircuitState.HALF_OPEN

    async def allow_request(self) -> bool:
        """Return ``True`` if the request should proceed, ``False`` to reject."""
        async with self._lock:
            self._check_half_open_transition()
            s = self._state
            if s == CircuitState.CLOSED:
                return True
            if s == CircuitState.HALF_OPEN:
                return True
            return False

    async def record_success(self) -> None:
        """Record a successful execution."""
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                self._failures.clear()

    async def record_failure(self) -> None:
        """Record a failed execution. May trip the breaker to OPEN."""
        async with self._lock:
            now = time.monotonic()

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._opened_at = now
                return

            self._failures.append(now)
            cutoff = now - self.failure_window
            while self._failures and self._failures[0] < cutoff:
                self._failures.popleft()

            if len(self._failures) >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = now

    async def reset(self) -> None:
        """Force-reset the breaker to CLOSED (for admin/testing)."""
        async with self._lock:
            self._state = CircuitState.CLOSED
            self._failures.clear()
            self._opened_at = 0.0

    def to_dict(self) -> dict[str, str | int]:
        """Snapshot for the /health endpoint."""
        return {
            "tool": self.name,
            "state": self._state.value,
            "recent_failures": len(self._failures),
        }
