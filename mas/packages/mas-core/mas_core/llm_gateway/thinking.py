"""Thinking chain — multi-model reasoning pipeline for Gemma models.

Instead of sending everything to the largest model, this module chains
Gemma models of increasing capability so each stage builds on the
previous one's output:

1. **Decompose** (small/nano model, ~4 B)  — fast problem breakdown
2. **Analyse**   (mid-size model, ~12 B)   — deep per-part reasoning
3. **Synthesise** (largest model, 27 B)    — final polished answer

Why this beats a single 27 B call:
- The 27 B model receives *pre-structured* reasoning instead of raw input,
  so it spends tokens on quality synthesis rather than exploration.
- Each stage uses a **different model's** independent rate quota, giving
  3× effective TPM/RPM throughput.
- Total wall-clock time is comparable to one long 27 B call because the
  small stages are very fast and produce focused context.

Token budget discipline:
- Stage 1 (decompose):  max 400 tokens   — concise bullet-point plan
- Stage 2 (analyse):    max 1 000 tokens  — structured analysis
- Stage 3 (synthesise): caller's budget   — the actual deliverable

Usage::

    from mas_core.llm_gateway.thinking import ThinkingChain

    chain = ThinkingChain(client)         # wraps an LLMGatewayClient
    response = await chain.think(messages, depth="standard")

Or simply request ``model="gemma-think"`` from the gateway client — the
client detects this virtual model and internally delegates to
``ThinkingChain``.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .models import ChatMessage, ChatResponse, UsageStats

logger = logging.getLogger(__name__)

# Regex to strip <critique>…</critique> blocks from deep-mode output.
_CRITIQUE_RE = re.compile(r"<critique>.*?</critique>", re.DOTALL)


# ---------------------------------------------------------------------------
# Stage configuration
# ---------------------------------------------------------------------------


class Depth(str, Enum):
    """Reasoning depth — controls how many pipeline stages run."""

    LIGHT = "light"
    """2 stages: decompose (4 B) → synthesise (27 B).  Fastest."""

    STANDARD = "standard"
    """3 stages: decompose (4 B) → analyse (12 B) → synthesise (27 B)."""

    DEEP = "deep"
    """3 stages with larger budgets and explicit self-critique in stage 3."""


@dataclass(frozen=True)
class StageConfig:
    """Immutable config for one pipeline stage."""

    name: str
    model: str
    system_prompt: str
    max_tokens: int
    temperature: float = 0.4


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_DECOMPOSE_SYSTEM = """\
You are a problem decomposer.  Your job is to break the user's request \
into 2-5 clear, actionable sub-tasks.  Output a numbered list only — no \
preamble, no conclusion.  Be specific and concise."""

_ANALYSE_SYSTEM = """\
You are an analytical reasoner.  You receive a user question and a \
decomposition produced by a prior model.  For each sub-task, provide a \
short but rigorous analysis (2-4 sentences).  Reference facts, constraints, \
and edge cases.  Do NOT produce a final answer — only analysis."""

_SYNTHESISE_SYSTEM = """\
You are a synthesis expert.  You receive:
1. The user's original question.
2. A structured decomposition.
3. A detailed analysis of each sub-task.

Produce the **definitive, polished answer** to the user's question.  \
Integrate all prior reasoning but do NOT repeat it verbatim.  Be \
accurate, clear, and directly useful."""

_SYNTHESISE_DEEP_SYSTEM = """\
You are a synthesis expert with self-critique capability.  You receive:
1. The user's original question.
2. A structured decomposition.
3. A detailed analysis of each sub-task.

First, internally check the analysis for errors or gaps (1-2 sentences \
in <critique> tags).  Then produce the **definitive, polished answer** \
outside the tags.  Integrate all prior reasoning, correct any detected \
issues, and be accurate, clear, and directly useful."""


# ---------------------------------------------------------------------------
# Stage presets per depth
# ---------------------------------------------------------------------------


def _stages_for_depth(
    depth: Depth,
    caller_max_tokens: int | None,
) -> list[StageConfig]:
    """Build the ordered list of pipeline stages for the given depth."""

    synth_tokens = caller_max_tokens or 2048

    if depth == Depth.LIGHT:
        return [
            StageConfig(
                name="decompose",
                model="gemini-3.1-flash-lite-preview",
                system_prompt=_DECOMPOSE_SYSTEM,
                max_tokens=300,
                temperature=0.3,
            ),
            StageConfig(
                name="synthesise",
                model="gemma-4-31b-it",
                system_prompt=_SYNTHESISE_SYSTEM,
                max_tokens=synth_tokens,
                temperature=0.5,
            ),
        ]

    if depth == Depth.STANDARD:
        return [
            StageConfig(
                name="decompose",
                model="gemini-3.1-flash-lite-preview",
                system_prompt=_DECOMPOSE_SYSTEM,
                max_tokens=400,
                temperature=0.3,
            ),
            StageConfig(
                name="analyse",
                model="gemini-3.1-flash-lite-preview",
                system_prompt=_ANALYSE_SYSTEM,
                max_tokens=1000,
                temperature=0.4,
            ),
            StageConfig(
                name="synthesise",
                model="gemma-4-31b-it",
                system_prompt=_SYNTHESISE_SYSTEM,
                max_tokens=synth_tokens,
                temperature=0.5,
            ),
        ]

    # Depth.DEEP
    return [
        StageConfig(
            name="decompose",
            model="gemini-3.1-flash-lite-preview",
            system_prompt=_DECOMPOSE_SYSTEM,
            max_tokens=500,
            temperature=0.3,
        ),
        StageConfig(
            name="analyse",
            model="gemma-4-31b-it",
            system_prompt=_ANALYSE_SYSTEM,
            max_tokens=1500,
            temperature=0.4,
        ),
        StageConfig(
            name="synthesise",
            model="gemma-4-31b-it",
            system_prompt=_SYNTHESISE_DEEP_SYSTEM,
            max_tokens=synth_tokens,
            temperature=0.5,
        ),
    ]


# Models that do NOT support the "system" / "developer" role.
# For these, the system prompt is prepended to the first user message.
# NOTE: ALL Gemma models on AI Studio's OpenAI-compat endpoint reject
# system messages with "Developer instruction is not enabled".
_NO_SYSTEM_ROLE_MODELS = frozenset(
    {
        "gemma-4-31b-it",
    }
)


# ---------------------------------------------------------------------------
# ThinkingChain
# ---------------------------------------------------------------------------


@dataclass
class StageResult:
    """Output of one pipeline stage (for introspection / logging)."""

    stage_name: str
    model: str
    content: str
    usage: UsageStats
    elapsed_s: float


@dataclass
class ThinkingResult:
    """Full pipeline result — the caller gets the final ``response`` plus
    per-stage diagnostics in ``stages``."""

    response: ChatResponse
    stages: list[StageResult] = field(default_factory=list)
    total_elapsed_s: float = 0.0

    @property
    def total_usage(self) -> UsageStats:
        """Aggregate token usage across all stages."""
        return UsageStats(
            prompt_tokens=sum(s.usage.prompt_tokens for s in self.stages),
            completion_tokens=sum(s.usage.completion_tokens for s in self.stages),
            total_tokens=sum(s.usage.total_tokens for s in self.stages),
        )


class ThinkingChain:
    """Multi-model reasoning pipeline.

    Parameters
    ----------
    client:
        An **already started** ``LLMGatewayClient``.
    depth:
        Reasoning depth (``"light"``, ``"standard"``, ``"deep"``).
    max_tokens:
        Token budget for the final synthesis stage.  ``None`` = 2 048.
    """

    def __init__(
        self,
        client: Any,  # LLMGatewayClient — avoid circular import
        *,
        depth: str | Depth = Depth.STANDARD,
        max_tokens: int | None = None,
    ) -> None:
        self._client = client
        self._depth = Depth(depth) if isinstance(depth, str) else depth
        self._max_tokens = max_tokens

    # ----- public API -----------------------------------------------------

    async def think(
        self,
        messages: list[dict[str, Any]],
        *,
        depth: str | Depth | None = None,
        max_tokens: int | None = None,
    ) -> ThinkingResult:
        """Run the full reasoning pipeline and return a ``ThinkingResult``.

        Parameters
        ----------
        messages:
            Original conversation in OpenAI format (system + user messages).
        depth:
            Override the chain-level depth for this call.
        max_tokens:
            Override the synthesis token budget for this call.
        """
        effective_depth = Depth(depth) if depth is not None else self._depth
        effective_max = max_tokens or self._max_tokens
        stages = _stages_for_depth(effective_depth, effective_max)

        # Extract the user's actual question from messages for context
        # injection into later stages.
        user_text = self._extract_user_text(messages)

        chain_start = time.monotonic()
        stage_results: list[StageResult] = []
        accumulated_reasoning: list[str] = []

        for i, stage in enumerate(stages):
            stage_messages = self._build_stage_messages(
                stage=stage,
                original_messages=messages,
                user_text=user_text,
                accumulated_reasoning=accumulated_reasoning,
                is_first=i == 0,
                is_last=i == len(stages) - 1,
            )

            t0 = time.monotonic()
            try:
                resp = await self._client.chat_completion(
                    messages=stage_messages,
                    model=stage.model,
                    max_tokens=stage.max_tokens,
                    temperature=stage.temperature,
                )
            except Exception as exc:
                elapsed = time.monotonic() - t0
                logger.error(
                    "Thinking stage '%s' (%s) failed after %.1fs: %s",
                    stage.name,
                    stage.model,
                    elapsed,
                    exc,
                )
                # If this is the first stage, re-raise — nothing to salvage.
                # If a later stage fails, synthesise from what we have so far.
                if i == 0:
                    raise
                logger.warning(
                    "Falling back: synthesising from %d completed stage(s)",
                    len(stage_results),
                )
                break
            elapsed = time.monotonic() - t0

            content = resp.text or ""
            sr = StageResult(
                stage_name=stage.name,
                model=stage.model,
                content=content,
                usage=resp.usage,
                elapsed_s=round(elapsed, 2),
            )
            stage_results.append(sr)
            accumulated_reasoning.append(f"[{stage.name.upper()} — {stage.model}]\n{content}")

            logger.info(
                "Thinking stage '%s' (%s): %d tokens in %.1fs",
                stage.name,
                stage.model,
                resp.usage.total_tokens,
                elapsed,
            )

        # The final stage's response is the pipeline output.
        # Strip <critique>…</critique> tags from deep-mode synthesis so
        # the internal self-check doesn't leak into the user-visible answer.
        final_content = stage_results[-1].content if stage_results else ""
        if effective_depth == Depth.DEEP and final_content:
            final_content = _CRITIQUE_RE.sub("", final_content).strip()

        # Aggregate usage directly — avoids creating a throwaway ThinkingResult.
        agg_usage = UsageStats(
            prompt_tokens=sum(s.usage.prompt_tokens for s in stage_results),
            completion_tokens=sum(s.usage.completion_tokens for s in stage_results),
            total_tokens=sum(s.usage.total_tokens for s in stage_results),
        )

        final_resp = ChatResponse(
            response_id=f"think-{int(time.time())}",
            model=f"gemma-think/{effective_depth.value}",
            finish_reason="stop",
            message=ChatMessage(
                role="assistant",
                content=final_content or None,
            ),
            usage=agg_usage,
        )

        total_elapsed = round(time.monotonic() - chain_start, 2)
        return ThinkingResult(
            response=final_resp,
            stages=stage_results,
            total_elapsed_s=total_elapsed,
        )

    # ----- internals ------------------------------------------------------

    @staticmethod
    def _extract_user_text(messages: list[dict[str, Any]]) -> str:
        """Pull the last user message text from the conversation."""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content
                # Multipart content (text + image)
                if isinstance(content, list):
                    parts = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            parts.append(block["text"])
                    return "\n".join(parts)
        return ""

    @staticmethod
    def _build_stage_messages(
        *,
        stage: StageConfig,
        original_messages: list[dict[str, Any]],
        user_text: str,
        accumulated_reasoning: list[str],
        is_first: bool,
        is_last: bool,
    ) -> list[dict[str, Any]]:
        """Construct the message list for a pipeline stage.

        First stage: system prompt + original user messages.
        Middle stages: system prompt + original question + prior reasoning.
        Last stage: system prompt + original question + all prior reasoning.

        For models that don't support the system role, the system prompt
        is prepended to the first user message instead.
        """
        supports_system = stage.model not in _NO_SYSTEM_ROLE_MODELS

        msgs: list[dict[str, Any]] = []

        if supports_system:
            msgs.append({"role": "system", "content": stage.system_prompt})

        # Prefix to inject into the first user message when system role
        # is not available.
        sys_prefix = f"[Instructions: {stage.system_prompt}]\n\n" if not supports_system else ""

        if is_first:
            # Pass the original conversation (skip any existing system prompt
            # so we don't conflict with the stage system prompt).
            first_user_done = False
            for msg in original_messages:
                if msg.get("role") == "system":
                    continue
                if not first_user_done and sys_prefix and msg.get("role") == "user":
                    # Prepend system instructions to the first user message.
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        msgs.append({"role": "user", "content": sys_prefix + content})
                    else:
                        # Multipart content — prepend as a text block.
                        new_content = [{"type": "text", "text": sys_prefix}] + list(content)
                        msgs.append({"role": "user", "content": new_content})
                    first_user_done = True
                else:
                    msgs.append(msg)

            # Edge case: no user messages in the original conversation.
            # Inject the system prompt as a standalone user message so the
            # model still receives instructions.
            if not first_user_done and sys_prefix:
                msgs.append({"role": "user", "content": sys_prefix.rstrip()})
        else:
            # Build a structured user message with context
            reasoning_block = "\n\n".join(accumulated_reasoning)

            if is_last:
                user_content = (
                    f"{sys_prefix}"
                    f"## Original question\n{user_text}\n\n"
                    f"## Prior reasoning\n{reasoning_block}\n\n"
                    f"Produce the final, definitive answer."
                )
            else:
                user_content = (
                    f"{sys_prefix}"
                    f"## Original question\n{user_text}\n\n"
                    f"## Prior stage output\n{reasoning_block}\n\n"
                    f"Continue your analysis."
                )

            msgs.append({"role": "user", "content": user_content})

        return msgs
