"""AgentBase — abstract foundation for all MAS agent types.

Architecture overview
---------------------
- Connects to the message-router via ``RouterClient`` (HTTP for publish,
  WebSocket for subscribe).
- Maintains a 1 000-entry LRU set of recently processed ``message_id`` values
  for consume-side idempotency (guards against XAUTOCLAIM re-delivery races).
- Exposes a shared ``think()`` loop for task-executing subclasses. The loop:
    1. Restores checkpoint state when resuming.
    2. Calls the LLM gateway with budget/deadline enforcement.
    3. Executes tool calls (if any) with tool-budget tracking.
    4. Saves checkpoint snapshots during execution and clears them on success.
- Checkpoint save/restore is wired to the ``AgentStorage`` layer (injected
  at construction time, optional — if None, checkpoints are disabled).

Subclasses must implement
--------------------------
``handle_message(envelope: MessageEnvelope) -> None``
    The per-message async handler. For task-executing agents this is where the
    ``think()`` loop lives; for routing/admin agents it's the dispatch logic.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from abc import ABC, abstractmethod
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from ..llm_gateway.client import LLMGatewayClient
from ..llm_gateway.models import ToolDefinition
from ..protocols.envelope import MessageEnvelope
from ..protocols.enums import AgentRole
from ..protocols.ws import WSMessageFrame
from .attachment_manager import TempAttachmentManager
from .budget import BudgetExhausted, BudgetTracker
from .config import AgentConfig
from .router_client import RouterClient

logger = logging.getLogger(__name__)

ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[Any] | Any]


# ---------------------------------------------------------------------------
# Simple LRU set (ordered dict used as ordered set)
# ---------------------------------------------------------------------------


class _LRUSet:
    """Fixed-capacity LRU set.  ``add`` evicts the oldest entry when full."""

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._data: OrderedDict[str, None] = OrderedDict()

    def __contains__(self, item: str) -> bool:
        return item in self._data

    def add(self, item: str) -> None:
        if item in self._data:
            self._data.move_to_end(item)
            return
        self._data[item] = None
        if len(self._data) > self._capacity:
            self._data.popitem(last=False)

    def __len__(self) -> int:
        return len(self._data)


# ---------------------------------------------------------------------------
# AgentBase
# ---------------------------------------------------------------------------


class AgentBase(ABC):
    """Abstract base class for all MAS agents.

    Parameters
    ----------
    config:
        Per-agent settings (id, role, team, router URL, secret, budgets, …).
    storage:
        Optional async storage object.  Must implement:
        - ``save_checkpoint(agent_id, project_id, data) -> None``
        - ``load_checkpoint(agent_id, project_id) -> dict | None``
        - ``delete_checkpoint(agent_id, project_id) -> None``
        If ``None``, checkpoint functionality is disabled (safe for tests).
    """

    def __init__(
        self,
        config: AgentConfig,
        storage: Any | None = None,
        *,
        llm_client: LLMGatewayClient | None = None,
        tool_executor: ToolExecutor | None = None,
    ) -> None:
        self.config = config
        self.agent_id: str = config.agent_id
        self.team_id: str = config.team_id
        self.role: AgentRole = config.agent_role
        self._storage = storage

        # Consume-side idempotency
        self._processed_lru: _LRUSet = _LRUSet(config.lru_size)

        # RouterClient — created lazily by start()
        self._router: RouterClient = RouterClient(
            router_url=config.router_url,
            agent_id=config.agent_id,
            agent_secret=config.agent_secret,
        )

        # Shutdown flag — set by team-runner on SIGTERM / SHUTDOWN message
        self._shutting_down: asyncio.Event = asyncio.Event()

        # Currently active budget tracker (set when processing a task)
        self._budget: BudgetTracker | None = None

        # Currently active envelope (set when processing a message)
        self._current_envelope: MessageEnvelope | None = None

        # Checkpoint restored from Postgres (set by restore_from_checkpoint)
        self._checkpoint: dict[str, Any] | None = None
        self._llm: LLMGatewayClient = llm_client or LLMGatewayClient()
        self._llm_started: bool = False
        self._tool_executor = tool_executor

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the HTTP client.  Call before using publish/subscribe."""
        await self._router.start()
        if not self._llm_started:
            await self._llm.start()
            self._llm_started = True

    async def stop(self) -> None:
        """Signal shutdown and close the HTTP client."""
        self._shutting_down.set()
        await self._router.stop()
        if self._llm_started:
            await self._llm.stop()
            self._llm_started = False

    # ------------------------------------------------------------------
    # Abstract interface — subclasses must implement
    # ------------------------------------------------------------------

    @abstractmethod
    async def handle_message(self, envelope: MessageEnvelope) -> None:
        """Process one incoming message.

        Subclasses implement all domain logic here (task dispatch, routing,
        delegation, think() loops, etc.).

        The base class handles LRU dedup, checkpoint save/restore, and error
        logging around this call.  Do NOT call ``super().handle_message()`` —
        it is intentionally abstract.
        """

    # ------------------------------------------------------------------
    # Main subscribe loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Start the subscribe loop.  Runs until ``_shutting_down`` is set.

        Designed to be the ``asyncio.Task`` created by the team-runner.
        """
        await self._router.subscribe(
            team_id=self.team_id,
            handler=self._dispatch,
            stop_event=self._shutting_down,
        )

    # ------------------------------------------------------------------
    # Internal dispatch — called for every WSMessageFrame from the router
    # ------------------------------------------------------------------

    async def _dispatch(self, frame: WSMessageFrame) -> None:
        """LRU check → delegate to handle_message → checkpoint lifecycle.

        This method is the ``handler`` passed to ``RouterClient.subscribe``.
        The router's WS loop calls it and automatically sends ACK on success
        or NACK on exception.

        Idempotency: if ``message_id`` is in the LRU set (already processed
        in this process lifetime) we skip processing and return immediately.
        The router will still ACK the entry (the subscribe loop does that
        after this function returns without raising).
        """
        envelope = frame.envelope
        msg_id_str = str(envelope.message_id)

        if msg_id_str in self._processed_lru:
            logger.debug(
                "Skipping duplicate message %s (LRU hit)", msg_id_str,
                extra=self._log_extra(),
            )
            return

        self._current_envelope = envelope
        task_budget = envelope.budget if envelope.budget is not None else self.config.budget_defaults
        self._budget = BudgetTracker.from_task_budget(task_budget)

        try:
            await self.handle_message(envelope)
        except BudgetExhausted as exc:
            logger.warning(
                "Budget exhausted for message %s: %s",
                msg_id_str, exc,
                extra=self._log_extra(),
            )
            self._current_envelope = None
            raise
        except Exception as exc:
            logger.error(
                "Unhandled exception in handle_message for %s: %s",
                msg_id_str, exc,
                extra=self._log_extra(),
            )
            self._current_envelope = None
            raise
        else:
            self._processed_lru.add(msg_id_str)
            self._current_envelope = None

    # ------------------------------------------------------------------
    # Checkpoint helpers — used by concrete subclasses during think() loops
    # ------------------------------------------------------------------

    async def save_checkpoint(self, data: dict[str, Any]) -> None:
        """Persist a checkpoint to Postgres.

        ``data`` should contain everything needed to resume the think() loop:
        messages list, iteration count, accumulated tool results, and a
        ``budget_snapshot`` from ``self._budget.snapshot()``.

        No-op if storage is not configured.
        """
        if self._storage is None or self._current_envelope is None:
            return
        project_id = self._current_envelope.project_id or "none"
        try:
            await self._storage.save_checkpoint(self.agent_id, project_id, data)
        except Exception as exc:
            logger.error("Failed to save checkpoint: %s", exc, extra=self._log_extra())

    async def load_checkpoint(self) -> dict[str, Any] | None:
        """Load a previously saved checkpoint from Postgres.

        Returns None if no checkpoint exists or storage is not configured.
        """
        if self._storage is None or self._current_envelope is None:
            return None
        project_id = self._current_envelope.project_id or "none"
        try:
            return await self._storage.load_checkpoint(self.agent_id, project_id)
        except Exception as exc:
            logger.error("Failed to load checkpoint: %s", exc, extra=self._log_extra())
            return None

    async def delete_checkpoint(self) -> None:
        """Delete the checkpoint after successful task completion.

        No-op if storage is not configured.
        """
        if self._storage is None or self._current_envelope is None:
            return
        project_id = self._current_envelope.project_id or "none"
        try:
            await self._storage.delete_checkpoint(self.agent_id, project_id)
        except Exception as exc:
            logger.error("Failed to delete checkpoint: %s", exc, extra=self._log_extra())

    def restore_from_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        """Restore in-memory state from a loaded checkpoint dict.

        Sets ``self._checkpoint`` so that the think() loop can detect it and
        skip already-completed iterations.
        """
        self._checkpoint = checkpoint
        if self._budget is not None and "budget_snapshot" in checkpoint:
            self._budget = BudgetTracker.restore_snapshot(checkpoint["budget_snapshot"])

    # ------------------------------------------------------------------
    # Publish helpers
    # ------------------------------------------------------------------

    async def publish(self, envelope: MessageEnvelope) -> str:
        """Publish a message via the router.  Returns the stream entry ID."""
        return await self._router.publish(envelope)

    async def broadcast(self, envelope: MessageEnvelope) -> dict[str, Any]:
        """Broadcast a message to all 11 team streams via the router."""
        return await self._router.broadcast(envelope)

    # ------------------------------------------------------------------
    # LLM + tool helpers
    # ------------------------------------------------------------------

    def _log_extra(self, **extra: Any) -> dict[str, Any]:
        """Build structured log fields required by the runtime plan."""
        env = self._current_envelope
        payload: dict[str, Any] = {
            "trace_id": str(env.correlation_id) if env else None,
            "span_id": str(env.message_id) if env else None,
            "agent_id": self.agent_id,
            "team_id": self.team_id,
        }
        if env and env.project_id is not None:
            payload["project_id"] = env.project_id
        payload.update(extra)
        return payload

    async def _ensure_llm_started(self) -> bool:
        """Ensure the LLM client is started. Returns True if started here."""
        if self._llm_started:
            return False
        await self._llm.start()
        self._llm_started = True
        return True

    @staticmethod
    def _parse_tool_arguments(raw: str) -> dict[str, Any]:
        """Parse JSON function arguments emitted by the LLM."""
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw": raw}
        if isinstance(parsed, dict):
            return parsed
        return {"_value": parsed}

    @staticmethod
    def _format_tool_result(
        result: Any,
        attachment_mgr: TempAttachmentManager | None = None,
    ) -> str | list[dict[str, Any]]:
        """Convert a tool result to OpenAI 'tool' message content.

        When *attachment_mgr* is provided the result is inspected for
        embedded files (base64 data-URLs, raw bytes).  Detected files
        are saved to a temp staging directory and an OpenAI multipart
        ``content`` array is returned instead of a plain string.
        """
        if attachment_mgr is not None:
            processed = attachment_mgr.process_tool_result(result)
            if isinstance(processed, list):
                return processed  # multipart content array
            return processed  # plain string (no files found)
        if isinstance(result, str):
            return result
        return json.dumps(result, default=str)

    async def execute_tool(self, tool_name: str, tool_kwargs: dict[str, Any]) -> Any:
        """Execute a tool call (overridden or injected by concrete agents)."""
        if self._tool_executor is None:
            return {
                "error": "tool_executor_not_configured",
                "tool_name": tool_name,
            }
        result = self._tool_executor(tool_name, tool_kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    # ------------------------------------------------------------------
    # think() loop
    # ------------------------------------------------------------------

    async def think(
        self,
        *,
        messages: list[dict[str, Any]],
        resume: bool = False,
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        stream: bool | None = None,
    ) -> list[dict[str, Any]]:
        """LLM think-loop with budget and checkpoint enforcement.

        Subclasses (WorkerAgent, ExecutiveAgent, etc.) call this with an initial
        ``messages`` list (system prompt + user task). The loop handles:

        - Checkpoint restore on ``resume=True``.
        - Per-iteration checkpoint saves.
        - Budget enforcement before each LLM call.
        - Tool-call execution with budget accounting.
        - Deadline/iteration-count termination.

        Returns the final ``messages`` list (LLM conversation history).
        """
        iteration = 0
        budget = self._budget or BudgetTracker()
        self._budget = budget
        tool_results: list[dict[str, Any]] = []

        # Attachment manager — stages files extracted from tool results
        # so the LLM receives multipart content arrays with image_url parts.
        # Cleaned up after the think loop (temp dir deleted).
        attachment_mgr = TempAttachmentManager()

        # Restore from checkpoint if resuming
        if resume:
            checkpoint = self._checkpoint or await self.load_checkpoint()
            if checkpoint:
                messages = checkpoint.get("messages", messages)
                iteration = checkpoint.get("iteration", 0)
                tool_results = checkpoint.get("tool_results", [])
                if "budget_snapshot" in checkpoint:
                    budget = BudgetTracker.restore_snapshot(checkpoint["budget_snapshot"])
                    self._budget = budget
                logger.info(
                    "Resuming from checkpoint at iteration %d", iteration,
                    extra=self._log_extra(iteration=iteration),
                )

        max_iter = self.config.max_think_iterations
        checkpoint_interval = self.config.checkpoint_interval
        completed = False
        llm_started_here = await self._ensure_llm_started()
        try:
            while iteration < max_iter:
                try:
                    budget.check_before_llm_call()
                except BudgetExhausted:
                    logger.warning(
                        "Budget exhausted at iteration %d, stopping think()",
                        iteration,
                        extra=self._log_extra(iteration=iteration),
                    )
                    break

                response = await self._llm.chat_completion(
                    messages,
                    model=model or self.config.llm_model,
                    tools=tools,
                    max_tokens=max_tokens or self.config.llm_max_tokens,
                    temperature=(
                        self.config.llm_temperature
                        if temperature is None else temperature
                    ),
                    stream=self.config.llm_stream if stream is None else stream,
                )
                try:
                    budget.consume_llm_call(
                        tokens_in=response.usage.prompt_tokens,
                        tokens_out=response.usage.completion_tokens,
                        cost_usd=response.usage.estimated_cost_usd,
                    )
                except BudgetExhausted:
                    logger.warning(
                        "LLM budget exhausted after iteration %d, stopping think()",
                        iteration,
                        extra=self._log_extra(iteration=iteration),
                    )
                    break

                messages.append(response.message.model_dump(exclude_none=True))
                iteration += 1

                tool_budget_exhausted = False
                if response.has_tool_calls:
                    for tool_call in response.tool_calls:
                        try:
                            budget.check_before_tool_call()
                            budget.consume_tool_call()
                        except BudgetExhausted:
                            logger.warning(
                                "Tool budget exhausted at iteration %d",
                                iteration,
                                extra=self._log_extra(
                                    iteration=iteration,
                                    tool_name=tool_call.function.name,
                                ),
                            )
                            tool_budget_exhausted = True
                            break

                        args = self._parse_tool_arguments(tool_call.function.arguments)
                        result = await self.execute_tool(tool_call.function.name, args)
                        tool_results.append(
                            {
                                "tool_call_id": tool_call.id,
                                "tool_name": tool_call.function.name,
                                "args": args,
                                "result": result,
                            }
                        )
                        content = self._format_tool_result(
                            result, attachment_mgr=attachment_mgr,
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "name": tool_call.function.name,
                                "content": content,
                            }
                        )

                if iteration % checkpoint_interval == 0 and self._current_envelope is not None:
                    # Strip base64 data-URLs before persisting to Postgres
                    ckpt_messages = attachment_mgr.strip_base64_for_checkpoint(messages)
                    await self.save_checkpoint(
                        {
                            "messages": ckpt_messages,
                            "iteration": iteration,
                            "tool_results": tool_results,
                            "budget_snapshot": budget.snapshot(),
                            "task_envelope_id": str(self._current_envelope.message_id),
                        }
                    )

                if tool_budget_exhausted:
                    break
                if response.has_tool_calls:
                    continue
                completed = True
                break

            if completed:
                await self.delete_checkpoint()
            elif self._current_envelope is not None:
                ckpt_messages = attachment_mgr.strip_base64_for_checkpoint(messages)
                await self.save_checkpoint(
                    {
                        "messages": ckpt_messages,
                        "iteration": iteration,
                        "tool_results": tool_results,
                        "budget_snapshot": budget.snapshot(),
                        "task_envelope_id": str(self._current_envelope.message_id),
                    }
                )
        finally:
            # Clean up temp attachment staging directory
            attachment_mgr.cleanup()
            if llm_started_here:
                await self._llm.stop()
                self._llm_started = False
        return messages

