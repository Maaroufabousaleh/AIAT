"""BudgetTracker — runtime enforcement of per-task resource caps.

The budget is derived from ``MessageEnvelope.budget`` (a ``TaskBudget``) when
a new task arrives. ``BudgetTracker`` wraps the static ``TaskBudget`` and adds
mutable counters for live tracking during a ``think()`` loop.

Usage
-----
::

    tracker = BudgetTracker.from_task_budget(envelope.budget)
    tracker.consume_llm_call(tokens_in=512, tokens_out=256, cost_usd=0.003)
    tracker.consume_tool_call()
    if not tracker.ok_to_continue():
        raise BudgetExhausted(tracker.exhaustion_reason())
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..protocols.envelope import TaskBudget


class BudgetExhausted(Exception):
    """Raised when any budget cap is hit during a think() loop.

    The message contains a human-readable explanation of which cap was exceeded.
    Agents should catch this, emit a partial RESULT, and return cleanly (ACK the
    task so it doesn't loop back to the PEL).
    """


@dataclass
class BudgetTracker:
    """Mutable runtime companion to ``TaskBudget``.

    Parameters mirror those of ``TaskBudget`` but are mutable counters
    rather than immutable caps. ``None`` caps mean "uncapped".
    """

    # --- Caps (from TaskBudget, None = uncapped) ---
    max_llm_calls: int | None = None
    max_tool_calls: int | None = None
    max_subtasks: int | None = None
    deadline: datetime | None = None
    max_cost_usd: float | None = None

    # --- Live counters ---
    llm_calls_used: int = field(default=0)
    tool_calls_used: int = field(default=0)
    subtasks_used: int = field(default=0)
    cost_usd_used: float = field(default=0.0)
    tokens_in_used: int = field(default=0)
    tokens_out_used: int = field(default=0)

    # ----------------------------------------------------------------
    # Construction
    # ----------------------------------------------------------------

    @classmethod
    def from_task_budget(cls, budget: TaskBudget | None) -> "BudgetTracker":
        """Create a tracker from an optional ``TaskBudget`` envelope field.

        If ``budget`` is ``None``, all caps are uncapped.
        """
        if budget is None:
            return cls()
        return cls(
            max_llm_calls=budget.max_llm_calls,
            max_tool_calls=budget.max_tool_calls,
            max_subtasks=budget.max_subtasks,
            deadline=budget.deadline,
            max_cost_usd=budget.max_cost_usd,
        )

    # ----------------------------------------------------------------
    # Consume methods — call BEFORE the corresponding action
    # ----------------------------------------------------------------

    def consume_llm_call(
        self,
        *,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        """Record one LLM call.  Raises ``BudgetExhausted`` if a cap is hit.

        Call this *after* the LLM call returns (so the call can complete).
        Pass token counts and cost from the LLM response so the tracker can
        accumulate them for the ``snapshot()`` and cost cap enforcement.
        """
        self.llm_calls_used += 1
        self.tokens_in_used += tokens_in
        self.tokens_out_used += tokens_out
        self.cost_usd_used += cost_usd

        # Check caps *after* incrementing so the last call completes.
        reason = self._check_caps()
        if reason:
            raise BudgetExhausted(reason)

    def consume_tool_call(self) -> None:
        """Record one tool-service call.  Raises ``BudgetExhausted`` if over cap."""
        self.tool_calls_used += 1
        reason = self._check_caps()
        if reason:
            raise BudgetExhausted(reason)

    def consume_subtask(self) -> None:
        """Record spawning one sub-agent / subtask.  Raises ``BudgetExhausted`` if over cap."""
        self.subtasks_used += 1
        reason = self._check_caps()
        if reason:
            raise BudgetExhausted(reason)

    # ----------------------------------------------------------------
    # Convenience properties (plan §4a)
    # ----------------------------------------------------------------

    @property
    def llm_calls_remaining(self) -> int | None:
        """LLM calls left before cap, or ``None`` if uncapped."""
        if self.max_llm_calls is None:
            return None
        return max(0, self.max_llm_calls - self.llm_calls_used)

    @property
    def tool_calls_remaining(self) -> int | None:
        """Tool calls left before cap, or ``None`` if uncapped."""
        if self.max_tool_calls is None:
            return None
        return max(0, self.max_tool_calls - self.tool_calls_used)

    @property
    def subtasks_remaining(self) -> int | None:
        """Subtask spawns left before cap, or ``None`` if uncapped."""
        if self.max_subtasks is None:
            return None
        return max(0, self.max_subtasks - self.subtasks_used)

    @property
    def cost_so_far(self) -> float:
        """Accumulated USD cost across all LLM calls in this budget."""
        return self.cost_usd_used

    # ----------------------------------------------------------------
    # Pre-call guards — call BEFORE attempting an action
    # ----------------------------------------------------------------

    def check_before_llm_call(self) -> None:
        """Raise ``BudgetExhausted`` if the *next* LLM call would exceed caps."""
        if self.max_llm_calls is not None and self.llm_calls_used >= self.max_llm_calls:
            raise BudgetExhausted(
                f"LLM call budget exhausted: {self.llm_calls_used}/{self.max_llm_calls} calls used."
            )
        if self._deadline_exceeded():
            dl: datetime = self.deadline  # type: ignore[assignment]
            raise BudgetExhausted(f"Task deadline exceeded: {dl.isoformat()}")
        if self.max_cost_usd is not None and self.cost_usd_used >= self.max_cost_usd:
            raise BudgetExhausted(
                f"Cost budget exhausted: ${self.cost_usd_used:.4f}/${self.max_cost_usd:.4f} used."
            )

    def check_before_tool_call(self) -> None:
        """Raise ``BudgetExhausted`` if the *next* tool call would exceed caps."""
        if self.max_tool_calls is not None and self.tool_calls_used >= self.max_tool_calls:
            raise BudgetExhausted(
                f"Tool call budget exhausted: {self.tool_calls_used}/{self.max_tool_calls} calls used."
            )

    def check_before_subtask(self) -> None:
        """Raise ``BudgetExhausted`` if spawning another subtask would exceed caps."""
        if self.max_subtasks is not None and self.subtasks_used >= self.max_subtasks:
            raise BudgetExhausted(
                f"Subtask budget exhausted: {self.subtasks_used}/{self.max_subtasks} subtasks used."
            )

    # ----------------------------------------------------------------
    # Internal helpers
    # ----------------------------------------------------------------

    def _deadline_exceeded(self) -> bool:
        if self.deadline is None:
            return False
        return datetime.now(tz=timezone.utc) >= self.deadline

    def _check_caps(self) -> str | None:
        """Return an exhaustion message if any cap is exceeded, else None."""
        if self.max_llm_calls is not None and self.llm_calls_used > self.max_llm_calls:
            return f"LLM call budget exceeded: {self.llm_calls_used}/{self.max_llm_calls}."
        if self.max_tool_calls is not None and self.tool_calls_used > self.max_tool_calls:
            return f"Tool call budget exceeded: {self.tool_calls_used}/{self.max_tool_calls}."
        if self.max_subtasks is not None and self.subtasks_used > self.max_subtasks:
            return f"Subtask budget exceeded: {self.subtasks_used}/{self.max_subtasks}."
        if self.max_cost_usd is not None and self.cost_usd_used > self.max_cost_usd:
            return f"Cost budget exceeded: ${self.cost_usd_used:.4f}/${self.max_cost_usd:.4f}."
        if self._deadline_exceeded():
            dl2: datetime = self.deadline  # type: ignore[assignment]
            return f"Task deadline exceeded: {dl2.isoformat()}."
        return None

    # ----------------------------------------------------------------
    # Snapshot — for checkpoint saving
    # ----------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict of the current budget state.

        Stored in ``agent_checkpoints.checkpoint_data`` so the budget can be
        restored after a restart (via ``restore_snapshot``).
        """
        return {
            "max_llm_calls": self.max_llm_calls,
            "max_tool_calls": self.max_tool_calls,
            "max_subtasks": self.max_subtasks,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "max_cost_usd": self.max_cost_usd,
            "llm_calls_used": self.llm_calls_used,
            "tool_calls_used": self.tool_calls_used,
            "subtasks_used": self.subtasks_used,
            "cost_usd_used": self.cost_usd_used,
            "tokens_in_used": self.tokens_in_used,
            "tokens_out_used": self.tokens_out_used,
        }

    @classmethod
    def restore_snapshot(cls, data: dict[str, Any]) -> "BudgetTracker":
        """Reconstruct a BudgetTracker from a saved ``snapshot()`` dict."""
        deadline = None
        if data.get("deadline"):
            deadline = datetime.fromisoformat(data["deadline"])
        return cls(
            max_llm_calls=data.get("max_llm_calls"),
            max_tool_calls=data.get("max_tool_calls"),
            max_subtasks=data.get("max_subtasks"),
            deadline=deadline,
            max_cost_usd=data.get("max_cost_usd"),
            llm_calls_used=data.get("llm_calls_used", 0),
            tool_calls_used=data.get("tool_calls_used", 0),
            subtasks_used=data.get("subtasks_used", 0),
            cost_usd_used=data.get("cost_usd_used", 0.0),
            tokens_in_used=data.get("tokens_in_used", 0),
            tokens_out_used=data.get("tokens_out_used", 0),
        )

    def ok_to_continue(self) -> bool:
        """Quick boolean check — True if no caps are currently exceeded."""
        return self._check_caps() is None
