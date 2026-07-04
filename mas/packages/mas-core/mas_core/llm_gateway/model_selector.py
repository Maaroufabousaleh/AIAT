"""Task-aware model selector for the LLM gateway.

``ModelSelector`` is a helper that combines the ``SmartRouter`` (live
health / rate-limit / cost / latency scores) with static capability
metadata (tool-calling, vision, context size) and optional task hints
to choose the **best available model** for a given request.

It is the recommended entry-point when an agent wants to delegate
model choice to the gateway rather than hard-coding a model name.

Quick start
-----------
::

    from mas_core.llm_gateway import LLMGatewayClient, ModelSelector

    async with LLMGatewayClient() as client:
        selector = ModelSelector(client)

        # Let the selector choose
        model = selector.pick()                        # best overall

        # Task-aware selection
        model = selector.pick(task="code-generation")  # best for coding
        model = selector.pick(
            task="tool-calling",
            needs_tools=True,
            min_context=32_000,
        )

        # Ranked list (for fallback chains)
        ranked = selector.rank(task="reasoning", top_n=5)

Task hints
----------
Pass any of the ``best_for`` values registered in ``ModelEntry`` as
the ``task`` argument (e.g. ``"code-generation"``, ``"reasoning"``,
``"tool-calling"``, ``"vision"``, ``"multilingual"``).

The selector filters by:
1. **Required capabilities** — ``needs_tools``, ``needs_vision``,
   ``min_context``.
2. **Task hint** — models whose ``best_for`` contains the task string
   receive a +0.15 bonus to their composite score.
3. **Free models first** — a +0.10 bonus for models with
   ``cost_per_1m_input == 0``.
4. **SmartRouter score** — health, headroom, latency, cost (0.35/0.30/0.20/0.15).

The combined score is normalised to [0, 1].

Free-model shortlists
---------------------
``ModelSelector`` maintains curated shortlists of reliable free models
per task category so new agents can call ``pick()`` with no configuration
and still get a sensible default.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .client import LLMGatewayClient
    from .providers.base import ModelRegistry
    from .smart_router import ModelScore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Curated free-model shortlists
# ---------------------------------------------------------------------------

#: Default ordered list of free models for general tasks.
#: Listed roughly from highest to lowest quality.
FREE_MODELS_GENERAL: list[str] = [
    "groq/llama-3.3-70b-versatile",
    "groq/openai/gpt-oss-120b",
    "gemma-4-31b-it",
    "openrouter/meta-llama/llama-3.3-70b-instruct:free",
    "openrouter/google/gemma-4-31b-it:free",
    "groq/qwen/qwen3-32b",
    "big-pickle",
    "gemma-pool",
]

#: Free models with confirmed tool-calling support.
FREE_MODELS_TOOLS: list[str] = [
    "groq/llama-3.3-70b-versatile",
    "groq/openai/gpt-oss-120b",
    "groq/openai/gpt-oss-20b",
    "openrouter/qwen/qwen3-coder:free",
    "openrouter/meta-llama/llama-3.3-70b-instruct:free",
    "openrouter/openai/gpt-oss-120b:free",
    "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
    "openrouter/google/gemma-4-31b-it:free",
    "gemma-pool",
]

#: Free models with vision support.
FREE_MODELS_VISION: list[str] = [
    "groq/meta-llama/llama-4-scout-17b-16e-instruct",
    "gemma-4-31b-it",
    "openrouter/google/gemma-4-31b-it:free",
    "openrouter/nvidia/nemotron-nano-12b-v2-vl:free",
]

#: Free models optimised for code / agentic tasks.
FREE_MODELS_CODE: list[str] = [
    "openrouter/qwen/qwen3-coder:free",
    "groq/openai/gpt-oss-120b",
    "groq/llama-3.3-70b-versatile",
    "groq/qwen/qwen3-32b",
    "gemma-4-31b-it",
    "openrouter/nvidia/nemotron-3-nano-30b-a3b:free",
]

#: Free models optimised for reasoning.
FREE_MODELS_REASONING: list[str] = [
    "groq/openai/gpt-oss-120b",
    "groq/openai/gpt-oss-20b",
    "openrouter/qwen/qwen3-coder:free",
    "groq/qwen/qwen3-32b",
    "gemma-4-31b-it",
    "big-pickle",
    "gemma-think",
]

#: Free models with search-grounding support.
FREE_MODELS_GROUNDING: list[str] = [
    "gemini-3.1-flash-lite-preview",
    "gemini-3.1-flash-lite",
]

#: Fast / small models for quick classification, routing, and triage.
FREE_MODELS_FAST: list[str] = [
    "groq/llama-3.1-8b-instant",
    "groq/openai/gpt-oss-20b",
    "openrouter/liquid/lfm-2.5-1.2b-instruct:free",
]

#: Task-category → shortlist mapping (searched first).
_TASK_SHORTLISTS: dict[str, list[str]] = {
    # coding / agentic
    "code-generation": FREE_MODELS_CODE,
    "agentic-coding": FREE_MODELS_CODE,
    "debugging": FREE_MODELS_CODE,
    # reasoning
    "reasoning": FREE_MODELS_REASONING,
    "complex-reasoning": FREE_MODELS_REASONING,
    "complex-analysis": FREE_MODELS_REASONING,
    "structured-synthesis": FREE_MODELS_REASONING,
    # grounding / search
    "search-grounding": FREE_MODELS_GROUNDING,
    "grounding": FREE_MODELS_GROUNDING,
    "web-search": FREE_MODELS_GROUNDING,
    # tool use
    "tool-calling": FREE_MODELS_TOOLS,
    "structured-output": FREE_MODELS_TOOLS,
    "agentic-workflows": FREE_MODELS_TOOLS,
    # vision
    "vision": FREE_MODELS_VISION,
    "multimodal-vision": FREE_MODELS_VISION,
    "image-understanding": FREE_MODELS_VISION,
    # fast / classification
    "fast-classification": FREE_MODELS_FAST,
    "routing-decisions": FREE_MODELS_FAST,
    "simple-qa": FREE_MODELS_FAST,
    # general
    "general-purpose": FREE_MODELS_GENERAL,
    "drafting": FREE_MODELS_GENERAL,
    "summarisation": FREE_MODELS_GENERAL,
    "advisory-responses": FREE_MODELS_GENERAL,
    "multilingual": FREE_MODELS_GENERAL,
}


# ---------------------------------------------------------------------------
# ScoredCandidate
# ---------------------------------------------------------------------------


@dataclass
class ScoredCandidate:
    """A model candidate with its composite selection score."""

    model: str
    """Model ID (registry key)."""
    composite_score: float
    """Final weighted score in [0, 1]; higher is better."""
    router_score: float = 0.0
    """SmartRouter live score (health × headroom × cost × latency)."""
    task_bonus: float = 0.0
    """Bonus applied because model is in the task-specific shortlist."""
    free_bonus: float = 0.0
    """Bonus applied because model is free (cost_per_1m_input == 0)."""
    reason: str = ""
    """Human-readable explanation from SmartRouter."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "composite_score": round(self.composite_score, 4),
            "router_score": round(self.router_score, 4),
            "task_bonus": round(self.task_bonus, 4),
            "free_bonus": round(self.free_bonus, 4),
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# ModelSelector
# ---------------------------------------------------------------------------


class ModelSelector:
    """Task-aware model selector backed by live SmartRouter data.

    Parameters
    ----------
    client:
        An **already started** ``LLMGatewayClient``.
    free_only:
        When ``True`` (default), only consider models with zero cost.
        Set to ``False`` to include paid models.
    task_bonus:
        Score bonus (0–1) added when a model appears in the task-specific
        shortlist.  Default 0.15.
    free_bonus:
        Score bonus (0–1) added for models with zero cost.  Default 0.10.
    """

    def __init__(
        self,
        client: LLMGatewayClient,
        *,
        free_only: bool = True,
        task_bonus: float = 0.15,
        free_bonus: float = 0.10,
    ) -> None:
        self._client = client
        self.free_only = free_only
        self.task_bonus = task_bonus
        self.free_bonus = free_bonus

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def pick(
        self,
        *,
        task: str | None = None,
        needs_tools: bool = False,
        needs_vision: bool = False,
        needs_reasoning: bool = False,
        needs_search_grounding: bool = False,
        min_context: int = 0,
        exclude: list[str] | None = None,
        fallback: str | None = None,
    ) -> str:
        """Return the model ID of the best available model.

        Parameters
        ----------
        task:
            Optional task category string (e.g. ``"code-generation"``,
            ``"reasoning"``, ``"tool-calling"``).  Matched against
            ``ModelEntry.best_for`` and the curated shortlists.
        needs_tools:
            Require models with ``supports_tools=True``.
        needs_vision:
            Require models with ``capabilities.supports_images=True``.
        needs_reasoning:
            Require models with ``capabilities.supports_reasoning=True``.
        min_context:
            Minimum ``max_context_tokens`` required (0 = no constraint).
        exclude:
            Model IDs to skip (e.g. models already tried that failed).
        fallback:
            Model ID to return if no candidate survives the filters.
            Defaults to ``"auto"``, the LiteLLM/OmniRoute router alias.

        Returns
        -------
        str
            The best model ID, or ``fallback`` if nothing qualifies.
        """
        ranking = self.rank(
            task=task,
            needs_tools=needs_tools,
            needs_vision=needs_vision,
            needs_reasoning=needs_reasoning,
            needs_search_grounding=needs_search_grounding,
            min_context=min_context,
            exclude=exclude,
        )
        if ranking:
            chosen = ranking[0].model
            logger.debug(
                "ModelSelector picked '%s' (score=%.3f, task=%s)",
                chosen,
                ranking[0].composite_score,
                task or "general",
            )
            return chosen

        fb = fallback or "auto"
        logger.warning(
            "ModelSelector: no qualifying candidates (task=%s, "
            "needs_tools=%s, needs_vision=%s). Using fallback '%s'.",
            task,
            needs_tools,
            needs_vision,
            fb,
        )
        return fb

    def rank(
        self,
        *,
        task: str | None = None,
        needs_tools: bool = False,
        needs_vision: bool = False,
        needs_reasoning: bool = False,
        needs_search_grounding: bool = False,
        min_context: int = 0,
        exclude: list[str] | None = None,
        top_n: int = 10,
    ) -> list[ScoredCandidate]:
        """Return a ranked list of candidates best-first.

        Parameters
        ----------
        task:
            Optional task category (see ``pick()`` docs).
            needs_tools:
            Require tool-calling support.
        needs_vision:
            Require image/vision support.
        needs_reasoning:
            Require reasoning capability flag.
        needs_search_grounding:
            Require built-in search grounding support.
        min_context:
            Minimum context window size.
        exclude:
            Model IDs to skip.
        top_n:
            Maximum number of candidates to return.

        Returns
        -------
        list[ScoredCandidate]
            Sorted best-first.  Empty if nothing qualifies.
        """
        exclude_set: set[str] = set(exclude or [])
        registry: ModelRegistry = self._client._registry

        # Build task-specific shortlist first, then fall back to all models
        task_shortlist: list[str] = []
        if task:
            # exact key first
            task_shortlist = list(_TASK_SHORTLISTS.get(task, []))
            if not task_shortlist:
                # Fuzzy match: find shortlists whose key is a substring
                for key, candidates in _TASK_SHORTLISTS.items():
                    if key in task or task in key:
                        task_shortlist = list(candidates)
                        break

        # Candidate pool: task shortlist first, then all remaining models
        seen: set[str] = set()
        candidates: list[str] = []
        for mid in task_shortlist:
            if mid not in seen and mid not in exclude_set:
                candidates.append(mid)
                seen.add(mid)
        for mid in registry.model_ids():
            if mid not in seen and mid not in exclude_set:
                candidates.append(mid)
                seen.add(mid)
        # Include pool IDs too
        for pool in registry.list_pools():
            if pool.pool_id not in seen and pool.pool_id not in exclude_set:
                candidates.append(pool.pool_id)
                seen.add(pool.pool_id)

        # Score each candidate
        scored: list[ScoredCandidate] = []
        router = self._client.smart_router
        task_shortlist_set = set(task_shortlist)

        for mid in candidates:
            entry = registry.get(mid)
            pool = registry.get_pool(mid)

            # Capability filtering
            if entry is not None:
                if self.free_only and (
                    entry.cost_per_1m_input is not None and entry.cost_per_1m_input > 0
                ):
                    continue
                if needs_tools and not entry.supports_tools:
                    continue
                if needs_vision and not entry.capabilities.supports_images:
                    continue
                if needs_reasoning and not entry.capabilities.supports_reasoning:
                    continue
                if needs_search_grounding and not entry.capabilities.supports_search_grounding:
                    continue
                if min_context > 0 and (
                    entry.max_context_tokens is None or entry.max_context_tokens < min_context
                ):
                    continue
            elif pool is None:
                # Unknown ID — skip
                continue

            # Router score
            router_score_obj: ModelScore = router.score_model(mid)
            rs = router_score_obj.total_score

            # Bonuses
            in_task_list = mid in task_shortlist_set
            tb = self.task_bonus if in_task_list else 0.0

            is_free = (
                entry is not None
                and entry.cost_per_1m_input is not None
                and entry.cost_per_1m_input == 0.0
            ) or (pool is not None)
            fb_bonus = self.free_bonus if is_free else 0.0

            # Also check best_for for task match
            if (
                task
                and entry is not None
                and not in_task_list
                and any(task in bf or bf in task for bf in entry.best_for)
            ):
                tb = self.task_bonus * 0.5  # half bonus for partial match

            composite = min(1.0, rs + tb + fb_bonus)
            scored.append(
                ScoredCandidate(
                    model=mid,
                    composite_score=composite,
                    router_score=rs,
                    task_bonus=tb,
                    free_bonus=fb_bonus,
                    reason=router_score_obj.reason,
                )
            )

        scored.sort(key=lambda s: s.composite_score, reverse=True)
        return scored[:top_n]

    def fallback_chain(
        self,
        *,
        task: str | None = None,
        needs_tools: bool = False,
        needs_vision: bool = False,
        needs_search_grounding: bool = False,
        min_context: int = 0,
        chain_length: int = 4,
    ) -> list[str]:
        """Return an ordered fallback chain of model IDs.

        The caller can iterate through the chain, trying each model in
        turn until one succeeds.

        Parameters
        ----------
        task:
            Optional task category.
        needs_tools:
            Require tool-calling support.
        needs_vision:
            Require vision support.
        min_context:
            Minimum context window.
        chain_length:
            How many models to include in the chain.

        Returns
        -------
        list[str]
            Ordered list of model IDs, best first.
        """
        ranked = self.rank(
            task=task,
            needs_tools=needs_tools,
            needs_vision=needs_vision,
            needs_search_grounding=needs_search_grounding,
            min_context=min_context,
            top_n=chain_length,
        )
        return [c.model for c in ranked]

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------

    def dashboard(
        self,
        *,
        task: str | None = None,
        needs_tools: bool = False,
        needs_vision: bool = False,
        needs_search_grounding: bool = False,
        top_n: int = 10,
    ) -> dict[str, Any]:
        """Return a dictionary suitable for display / debugging."""
        ranking = self.rank(
            task=task,
            needs_tools=needs_tools,
            needs_vision=needs_vision,
            needs_search_grounding=needs_search_grounding,
            top_n=top_n,
        )
        return {
            "task": task or "general",
            "needs_tools": needs_tools,
            "needs_vision": needs_vision,
            "free_only": self.free_only,
            "ranking": [c.to_dict() for c in ranking],
            "recommended": ranking[0].model if ranking else None,
        }
