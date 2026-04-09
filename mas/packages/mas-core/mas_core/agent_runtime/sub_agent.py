"""SubAgent — lightweight child agent spawned by workers for subtasks.

A SubAgent is scoped to its parent task. It is essentially a thin
``WorkerAgent`` whose lifecycle is bounded by a single parent message.
It never fans-out further (no ``spawn_subtask``).

SubAgents are ephemeral — they don't persist independently beyond the
parent's checkpoint.
"""

from __future__ import annotations

import logging
from typing import Any

from ..protocols.enums import MessageType
from ..protocols.envelope import MessageEnvelope
from .base import AgentBase
from .config import AgentConfig

logger = logging.getLogger(__name__)


class SubAgent(AgentBase):
    """Lightweight ephemeral agent for subtask execution.

    Identical to WorkerAgent but:
    - Cannot fan-out to further sub-agents.
    - Scoped to a single parent envelope's budget.
    - Designed to be constructed and torn down per-subtask by the parent.

    Parameters
    ----------
    config : AgentConfig
        Sub-agent config (role should be ``AgentRole.SUB_AGENT``).
    storage : Any | None
        Optional storage for checkpointing.
    parent_envelope : MessageEnvelope | None
        The parent task envelope for context/budget scoping.
    tool_client : Any | None
        Optional ``ToolServiceClient``.
    system_prompt : str | None
        Override system prompt.
    """

    def __init__(
        self,
        config: AgentConfig,
        storage: Any | None = None,
        *,
        parent_envelope: MessageEnvelope | None = None,
        tool_client: Any | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(config, storage, **kwargs)
        self._tool_client = tool_client
        self._parent_envelope = parent_envelope
        self._system_prompt = system_prompt or self._default_system_prompt()

    def _default_system_prompt(self) -> str:
        return (
            f"You are {self.agent_id}, a sub-agent in team {self.team_id}. "
            "Complete the assigned subtask concisely and accurately. "
            "Return only the essential result."
        )

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    async def execute_tool(self, tool_name: str, tool_kwargs: dict[str, Any]) -> Any:
        """Execute a tool via ToolServiceClient, or fall back to base."""
        if self._tool_client is not None:
            env = self._current_envelope or self._parent_envelope
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
                return resp.result or resp.data
            return {"error": resp.error, "error_code": resp.error_code}
        return await super().execute_tool(tool_name, tool_kwargs)

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------

    async def handle_message(self, envelope: MessageEnvelope) -> None:
        """Process ADMIN_TASK messages (subtask execution)."""
        if envelope.msg_type in (MessageType.ADMIN_TASK, MessageType.TASK):
            await self._handle_task(envelope)
        else:
            logger.warning(
                "SubAgent ignoring unhandled message type %s",
                envelope.msg_type,
                extra=self._log_extra(),
            )

    async def _handle_task(self, envelope: MessageEnvelope) -> None:
        """Execute a subtask via think() and return the result."""
        task_desc = envelope.payload.get("task", "")
        context = envelope.payload.get("context", "")

        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": self._build_task_prompt(task_desc, context)},
        ]

        result_messages = await self.think(messages=messages)
        result_content = self._extract_result(result_messages)

        reply = envelope.reply(
            msg_type=MessageType.ADMIN_REPLY,
            sender_id=self.agent_id,
            sender_role=self.role,
            sender_team=self.team_id,
            payload={
                "result": result_content,
                "task": task_desc,
                "sub_agent": True,
            },
        )
        await self.publish(reply)
        logger.info(
            "subagent_task_completed",
            extra=self._log_extra(task=task_desc[:80]),
        )

    # ------------------------------------------------------------------
    # Direct execution (for in-process use by parent workers)
    # ------------------------------------------------------------------

    async def execute(self, task: str, context: str = "") -> str:
        """Execute a subtask directly, returning the result string.

        Useful when a WorkerAgent wants to spin up a SubAgent in-process
        rather than via the message bus.
        """
        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": self._build_task_prompt(task, context)},
        ]
        result_messages = await self.think(messages=messages)
        return self._extract_result(result_messages)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_task_prompt(task: str, context: str) -> str:
        parts = [f"## Subtask\n{task}"]
        if context:
            parts.append(f"\n## Context\n{context}")
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
