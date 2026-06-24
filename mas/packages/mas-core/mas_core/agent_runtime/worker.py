"""WorkerAgent — task-executing agent in the MAS corporate hierarchy.

Uses ``RouterClient`` for messaging, ``ToolServiceClient`` from ``mas-tools-sdk``
for tool execution, and ``BudgetTracker`` for resource caps. Can fan-out to
sub-agents when ``budget.subtasks_remaining`` permits.

Lifecycle
---------
1. Receives ``ADMIN_TASK`` or ``ISSUE_ASSIGN`` from its AdminAgent (dept PM).
2. Builds a system prompt from its agent prompt file + task context.
3. Runs ``think()`` with available tools.
4. Returns ``ADMIN_REPLY`` / ``ISSUE_COMPLETE`` to the sender.
"""

from __future__ import annotations

import logging
from typing import Any

from mas_core.protocols.enums import MessageType
from mas_core.protocols.envelope import MessageEnvelope

from .base import AgentBase
from .budget import BudgetExhausted, BudgetTracker
from .config import AgentConfig

logger = logging.getLogger(__name__)


class WorkerAgent(AgentBase):
    """Concrete agent that executes tasks via the LLM think() loop.

    Parameters
    ----------
    config : AgentConfig
        Must have ``agent_role == AgentRole.WORKER``.
    storage : Any | None
        Optional ``AgentStorage`` / checkpoint-capable storage.
    tool_client : Any | None
        ``ToolServiceClient`` instance for calling tools on tool-service.
    system_prompt : str | None
        Override system prompt (otherwise loaded from config/prompts).
    """

    def __init__(
        self,
        config: AgentConfig,
        storage: Any | None = None,
        *,
        tool_client: Any | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(config, storage, **kwargs)
        self._tool_client = tool_client
        self._system_prompt = system_prompt or self._default_system_prompt()

    def _default_system_prompt(self) -> str:
        return (
            f"You are {self.agent_id}, a {self.role.value} agent in team {self.team_id}. "
            "Complete your assigned tasks accurately and thoroughly. "
            "Use available tools when needed. Work like a frontier-grade specialist: clarify "
            "the objective, inspect relevant context, plan before acting, execute only allowed "
            "tool calls, verify the result, surface uncertainties or blockers, and return a "
            "concise artifact-oriented response."
        )

    # ------------------------------------------------------------------
    # Tool execution (delegates to ToolServiceClient)
    # ------------------------------------------------------------------

    async def execute_tool(self, tool_name: str, tool_kwargs: dict[str, Any]) -> Any:
        """Execute a tool via the tool-service, falling back to injected executor."""
        if self._tool_client is not None:
            env = self._current_envelope
            resp = await self._tool_client.execute(
                tool_name=tool_name,
                caller_id=self.agent_id,
                caller_role=self.role,
                caller_team=self.team_id,
                kwargs=tool_kwargs,
                project_id=env.project_id if env else None,
                trace_id=str(env.correlation_id) if env else None,
                span_id=str(env.message_id) if env else None,
            )
            if resp.success:
                return resp.result if resp.result is not None else getattr(resp, "data", None)
            return {"error": resp.error, "error_code": resp.error_code}
        return await super().execute_tool(tool_name, tool_kwargs)

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------

    async def handle_message(self, envelope: MessageEnvelope) -> None:
        """Process ADMIN_TASK, ISSUE_ASSIGN, and DIRECTIVE messages."""
        handler = self._get_handler(envelope.msg_type)
        if handler is not None:
            await handler(envelope)
        else:
            logger.warning(
                "WorkerAgent ignoring unhandled message type %s",
                envelope.msg_type,
                extra=self._log_extra(),
            )

    def _get_handler(self, msg_type: MessageType) -> Any:
        handlers = {
            MessageType.ADMIN_TASK: self._handle_task,
            MessageType.ISSUE_ASSIGN: self._handle_task,
            MessageType.DIRECTIVE: self._handle_directive,
        }
        return handlers.get(msg_type)

    async def _handle_task(self, envelope: MessageEnvelope) -> None:
        """Execute a task via think() and return the result."""
        task_desc = envelope.payload.get("task", "")
        context = envelope.payload.get("context", "")

        messages = [
            {"role": "system", "content": self._system_prompt},
            {
                "role": "user",
                "content": self._build_task_prompt(task_desc, context, envelope),
            },
        ]

        # Check for resume
        resume = envelope.payload.get("action") == "RESUME"

        result_messages = await self.think(messages=messages, resume=resume)

        # Extract the last assistant message as the result
        result_content = self._extract_result(result_messages)

        # Send reply back to sender
        reply = envelope.reply(
            msg_type=(
                MessageType.ISSUE_COMPLETE
                if envelope.msg_type == MessageType.ISSUE_ASSIGN
                else MessageType.ADMIN_REPLY
            ),
            sender_id=self.agent_id,
            sender_role=self.role,
            sender_team=self.team_id,
            payload={
                "result": result_content,
                "iterations": len(
                    [m for m in result_messages if m.get("role") == "assistant"]
                ),
                "task": task_desc,
            },
        )
        await self.publish(reply)
        logger.info(
            "worker_task_completed",
            extra=self._log_extra(
                task=task_desc[:80],
                reply_msg_id=str(reply.message_id),
            ),
        )

    async def _handle_directive(self, envelope: MessageEnvelope) -> None:
        """Handle DIRECTIVE messages (RESUME, etc.)."""
        action = envelope.payload.get("action", "")
        if action == "RESUME":
            await self._handle_task(envelope)
        else:
            logger.info(
                "worker_directive_%s", action.lower(),
                extra=self._log_extra(action=action),
            )

    # ------------------------------------------------------------------
    # Fan-out to sub-agents
    # ------------------------------------------------------------------

    async def spawn_subtask(
        self,
        *,
        task: str,
        context: str = "",
        recipient_team: str | None = None,
    ) -> str | None:
        """Spawn a subtask to a sub-agent if budget allows.

        Returns the published message entry_id, or None if budget exhausted.
        """
        budget = self._budget or BudgetTracker()
        try:
            budget.check_before_subtask()
            budget.consume_subtask()
        except BudgetExhausted:
            logger.warning(
                "subtask_budget_exhausted",
                extra=self._log_extra(subtask=task[:80]),
            )
            return None

        env = self._current_envelope
        subtask_envelope = MessageEnvelope(
            msg_type=MessageType.ADMIN_TASK,
            sender_id=self.agent_id,
            sender_role=self.role,
            sender_team=self.team_id,
            recipient_team=recipient_team or self.team_id,
            project_id=env.project_id if env else None,
            correlation_id=env.correlation_id if env else None,
            parent_id=env.message_id if env else None,
            payload={"task": task, "context": context, "spawned_by": self.agent_id},
        )
        entry_id = await self.publish(subtask_envelope)
        logger.info(
            "subtask_spawned",
            extra=self._log_extra(subtask=task[:80], entry_id=entry_id),
        )
        return entry_id

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_task_prompt(
        task: str, context: str, envelope: MessageEnvelope
    ) -> str:
        parts = [f"## Task\n{task}"]
        if context:
            parts.append(f"\n## Context\n{context}")
        parts.append(
            f"\n## Metadata\n"
            f"- Project: {envelope.project_id}\n"
            f"- Sender: {envelope.sender_id} ({envelope.sender_role.value})\n"
            f"- Correlation: {envelope.correlation_id}"
        )
        return "\n".join(parts)

    @staticmethod
    def _extract_result(messages: list[dict[str, Any]]) -> str:
        """Extract the final assistant response text."""
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                content = msg.get("content")
                if content:
                    return content
        return ""
