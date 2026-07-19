"""AdminAgent — department project-manager agent.

Routes tasks from the executive layer to workers within a single team.
For example an office worker receives ``ADMIN_TASK`` from the COO
(ExecutiveAgent) and fans it out to workers, aggregating results back.

Cross-team communication is handled exclusively via ``RouterClient.publish``
— there is no longer a ``read_admin_channel`` or XREADGROUP on a global
admin stream.  AdminAgent receives cross-team traffic the same way as
intra-team: through its team's Redis stream subscription.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..protocols.enums import MessageType
from ..protocols.envelope import MessageEnvelope
from .base import AgentBase

if TYPE_CHECKING:
    from .config import AgentConfig

logger = logging.getLogger(__name__)


class AdminAgent(AgentBase):
    """Department project-manager.

    Responsibilities
    ----------------
    - Receives ``ADMIN_TASK`` from executive or orchestrator.
    - Decomposes tasks and delegates ``ADMIN_TASK`` to workers.
    - Aggregates ``ADMIN_REPLY`` results from workers.
    - Optionally runs a ``think()`` loop for planning / decomposition.

    Parameters
    ----------
    config : AgentConfig
        Must have ``agent_role`` in {``ADMIN``, ``WORKER``} (some PMs reuse
        WORKER role in small teams).
    storage : Any | None
        Optional ``AgentStorage`` for checkpointing.
    tool_client : Any | None
        Optional ``ToolServiceClient`` for tool calls.
    system_prompt : str | None
        Override system prompt.
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
        self._system_prompt = self.with_runtime_tool_catalog(
            system_prompt or self._default_system_prompt()
        )
        # Track pending delegated tasks: correlation_id → list of pending entry_ids
        self._pending_delegations: dict[str, list[str]] = {}
        # Accumulate results keyed by correlation_id
        self._aggregated_results: dict[str, list[dict[str, Any]]] = {}
        # Preserve the original upstream request so the final aggregate is
        # routed back to the requesting team rather than the local worker team.
        self._delegation_parents: dict[str, MessageEnvelope] = {}

    def _default_system_prompt(self) -> str:
        return (
            f"You are {self.agent_id}, a project-manager (admin) for team {self.team_id}. "
            "Decompose tasks into actionable sub-tasks, delegate to your workers, "
            "and aggregate their results. Operate with this loop: observe current state, "
            "plan concrete subtasks, delegate with clear acceptance criteria, verify worker "
            "outputs and blocked reasons, preserve important context in AIAT state when a "
            "tool exists, then report concise next actions. Coordinate clearly and concisely."
        )

    # ------------------------------------------------------------------
    # Tool execution (delegates to ToolServiceClient)
    # ------------------------------------------------------------------

    async def execute_tool(self, tool_name: str, tool_kwargs: dict[str, Any]) -> Any:
        """Execute a tool via ToolServiceClient, or fall back to base."""
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
        """Route messages to the appropriate handler."""
        handler = self._get_handler(envelope.msg_type)
        if handler is not None:
            await handler(envelope)
        else:
            logger.warning(
                "AdminAgent ignoring unhandled message type %s",
                envelope.msg_type,
                extra=self._log_extra(),
            )

    def _get_handler(self, msg_type: MessageType) -> Any:
        handlers = {
            MessageType.ADMIN_TASK: self._handle_admin_task,
            MessageType.TASK: self._handle_admin_task,
            MessageType.ADMIN_REPLY: self._handle_admin_reply,
            MessageType.RESULT: self._handle_admin_reply,
            MessageType.DIRECTIVE: self._handle_directive,
            MessageType.ISSUE_ASSIGN: self._handle_issue_assign,
            MessageType.ISSUE_COMPLETE: self._handle_issue_complete,
            MessageType.SHUTDOWN: self._handle_shutdown,
        }
        return handlers.get(msg_type)

    # ------------------------------------------------------------------
    # Inbound task from executive / orchestrator
    # ------------------------------------------------------------------

    async def _handle_admin_task(self, envelope: MessageEnvelope) -> None:
        """Receive a task, optionally decompose, then delegate to workers.

        If the payload contains ``"subtasks"`` (a list), delegate them directly.
        Otherwise, use the think() loop to plan a decomposition.
        """
        if (
            self.team_id == "dept_devops"
            and str(envelope.payload.get("action") or "").upper() == "PROVISION_INFRA"
        ):
            await self._handle_infrastructure_provision(envelope)
            return

        subtasks = envelope.payload.get("subtasks")
        if subtasks and isinstance(subtasks, list):
            await self._delegate_subtasks(subtasks, envelope)
        else:
            # Use think() to plan the decomposition
            task_desc = envelope.payload.get("task", "")
            context = envelope.payload.get("context", "")
            messages = [
                {"role": "system", "content": self._system_prompt},
                {
                    "role": "user",
                    "content": (
                        f"## Task to decompose\n{task_desc}\n"
                        f"## Context\n{context}\n"
                        f"## Instructions\n"
                        "Analyze this task and produce a plan. Return a JSON array "
                        "of sub-tasks, each with a 'task' and optional 'context' field."
                    ),
                },
            ]
            await self.think(
                messages=messages,
                tools=self.available_tool_definitions(),
            )
            # For now, forward the full task to a single worker
            await self._delegate_single_task(
                task_desc, context, envelope
            )

    async def _handle_infrastructure_provision(self, envelope: MessageEnvelope) -> None:
        """Run the governed DevOps adapter and signal readiness on success."""
        project_id = envelope.project_id or envelope.payload.get("project_id")
        if not project_id:
            logger.warning("devops_infra_missing_project", extra=self._log_extra())
            return

        result = await self.execute_tool(
            "infra.provision",
            {
                "project_id": project_id,
                "resource": "aiat-project-runtime",
                "config": {
                    "target": "local_docker",
                    "image": "alpine:3.20",
                    "output": "infra/docker-compose.preview.yml",
                },
            },
        )
        success = (
            isinstance(result, dict)
            and not result.get("error")
            and result.get("available") is True
            and result.get("verified") is not False
        )
        if not success:
            logger.error(
                "devops_infra_provision_failed",
                extra=self._log_extra(project_id=project_id, result=result),
            )
            return

        ready = await self.execute_tool(
            "infra.ready_signal",
            {"project_id": project_id, "actor_id": self.agent_id},
        )
        logger.info(
            "devops_infra_ready",
            extra=self._log_extra(project_id=project_id, result=ready),
        )

    async def _delegate_subtasks(
        self,
        subtasks: list[dict[str, Any]],
        parent_envelope: MessageEnvelope,
    ) -> None:
        """Delegate multiple subtasks to workers."""
        corr_id = str(parent_envelope.correlation_id)
        self._pending_delegations.setdefault(corr_id, [])
        self._aggregated_results.setdefault(corr_id, [])
        self._delegation_parents[corr_id] = parent_envelope

        for subtask in subtasks:
            sub_envelope = MessageEnvelope(
                msg_type=MessageType.ADMIN_TASK,
                sender_id=self.agent_id,
                sender_role=self.role,
                sender_team=self.team_id,
                recipient_team=self.team_id,
                project_id=parent_envelope.project_id,
                correlation_id=parent_envelope.correlation_id,
                parent_id=parent_envelope.message_id,
                payload={
                    "task": subtask.get("task", ""),
                    "context": subtask.get("context", ""),
                    "delegated_by": self.agent_id,
                },
            )
            entry_id = await self.publish(sub_envelope)
            self._pending_delegations[corr_id].append(entry_id)

        logger.info(
            "admin_delegated_subtasks",
            extra=self._log_extra(
                count=len(subtasks),
                correlation_id=corr_id,
            ),
        )

    async def _delegate_single_task(
        self,
        task: str,
        context: str,
        parent_envelope: MessageEnvelope,
    ) -> None:
        """Delegate a single task to a worker."""
        corr_id = str(parent_envelope.correlation_id)
        self._pending_delegations.setdefault(corr_id, [])
        self._aggregated_results.setdefault(corr_id, [])
        self._delegation_parents[corr_id] = parent_envelope

        sub_envelope = MessageEnvelope(
            msg_type=MessageType.ADMIN_TASK,
            sender_id=self.agent_id,
            sender_role=self.role,
            sender_team=self.team_id,
            recipient_team=self.team_id,
            project_id=parent_envelope.project_id,
            correlation_id=parent_envelope.correlation_id,
            parent_id=parent_envelope.message_id,
            payload={
                "task": task,
                "context": context,
                "delegated_by": self.agent_id,
            },
        )
        entry_id = await self.publish(sub_envelope)
        self._pending_delegations[corr_id].append(entry_id)

    # ------------------------------------------------------------------
    # Inbound replies from workers
    # ------------------------------------------------------------------

    async def _handle_admin_reply(self, envelope: MessageEnvelope) -> None:
        """Aggregate replies from workers.

        When all pending tasks for a correlation_id are accounted for,
        send a consolidated ADMIN_REPLY upstream.
        """
        corr_id = str(envelope.correlation_id)
        results = self._aggregated_results.setdefault(corr_id, [])
        results.append({
            "sender": envelope.sender_id,
            "result": envelope.payload.get("result", ""),
            "task": envelope.payload.get("task", ""),
        })

        pending = self._pending_delegations.get(corr_id, [])
        # Check if we have received enough replies
        if len(results) >= len(pending) and pending:
            await self._send_aggregated_reply(corr_id, envelope)

    async def _send_aggregated_reply(
        self,
        corr_id: str,
        trigger_envelope: MessageEnvelope,
    ) -> None:
        """Build and send an aggregated reply upstream."""
        results = self._aggregated_results.pop(corr_id, [])
        self._pending_delegations.pop(corr_id, None)
        parent_envelope = self._delegation_parents.pop(corr_id, None)
        if parent_envelope is None:
            raise RuntimeError(f"Missing upstream envelope for delegated task {corr_id}")

        reply = MessageEnvelope(
            msg_type=MessageType.ADMIN_REPLY,
            sender_id=self.agent_id,
            sender_role=self.role,
            sender_team=self.team_id,
            recipient_team=parent_envelope.sender_team,
            project_id=parent_envelope.project_id,
            correlation_id=parent_envelope.correlation_id,
            parent_id=parent_envelope.message_id,
            payload={
                "results": results,
                "aggregated_by": self.agent_id,
                "result_count": len(results),
                # Preserve durable work identity so the executive can validate
                # the returned artifact before changing issue state.
                "action": parent_envelope.payload.get("action"),
                "issue_id": parent_envelope.payload.get("issue_id"),
                "sprint_id": parent_envelope.payload.get("sprint_id"),
            },
        )
        await self.publish(reply)
        logger.info(
            "admin_aggregated_reply_sent",
            extra=self._log_extra(
                correlation_id=corr_id,
                result_count=len(results),
            ),
        )

    # ------------------------------------------------------------------
    # Issue lifecycle
    # ------------------------------------------------------------------

    async def _handle_issue_assign(self, envelope: MessageEnvelope) -> None:
        """Delegate an issue to a worker as ISSUE_ASSIGN."""
        sub_envelope = MessageEnvelope(
            msg_type=MessageType.ISSUE_ASSIGN,
            sender_id=self.agent_id,
            sender_role=self.role,
            sender_team=self.team_id,
            recipient_team=self.team_id,
            project_id=envelope.project_id,
            correlation_id=envelope.correlation_id,
            parent_id=envelope.message_id,
            payload=envelope.payload,
        )
        await self.publish(sub_envelope)
        logger.info(
            "admin_issue_delegated",
            extra=self._log_extra(
                issue_type=envelope.payload.get("issue_type"),
            ),
        )

    async def _handle_issue_complete(self, envelope: MessageEnvelope) -> None:
        """Forward issue completion upstream to the executive."""
        reply = MessageEnvelope(
            msg_type=MessageType.ISSUE_COMPLETE,
            sender_id=self.agent_id,
            sender_role=self.role,
            sender_team=self.team_id,
            recipient_team=envelope.sender_team,
            project_id=envelope.project_id,
            correlation_id=envelope.correlation_id,
            parent_id=envelope.parent_id,
            payload={
                "result": envelope.payload.get("result", ""),
                "aggregated_by": self.agent_id,
            },
        )
        await self.publish(reply)

    # ------------------------------------------------------------------
    # Directive / shutdown
    # ------------------------------------------------------------------

    async def _handle_directive(self, envelope: MessageEnvelope) -> None:
        """Translate a team directive into a worker task or handle it locally.

        Team streams route ``DIRECTIVE`` messages to the PM first.  Publishing
        another ``DIRECTIVE`` to that same stream would route it back to the
        PM and create a loop, so worker fan-out uses the normal ``ADMIN_TASK``
        path instead.
        """
        action = str(envelope.payload.get("action", ""))
        logger.info(
            "admin_directive_%s",
            action.lower(),
            extra=self._log_extra(action=action),
        )

        # Infrastructure resume is an admin-owned operation: DevOps must run
        # its governed adapter before it can signal readiness.  Do not send it
        # through an LLM worker that cannot access the admin-only tool.
        if (
            self.team_id == "dept_devops"
            and action.upper() == "RESUME"
            and str(envelope.payload.get("state") or "") == "INFRA_PROVISIONING"
        ):
            await self._handle_infrastructure_provision(envelope)
            return

        task = MessageEnvelope(
            msg_type=MessageType.ADMIN_TASK,
            sender_id=self.agent_id,
            sender_role=self.role,
            sender_team=self.team_id,
            recipient_team=self.team_id,
            project_id=envelope.project_id,
            correlation_id=envelope.correlation_id,
            parent_id=envelope.message_id,
            payload={
                "task": str(envelope.payload.get("task") or f"Apply team directive {action}"),
                "context": envelope.payload.get("context", ""),
                "action": action,
                "state": envelope.payload.get("state"),
                "directive_forwarded": True,
                "source_directive_id": str(envelope.message_id),
            },
        )
        await self.publish(task)

    async def _handle_shutdown(self, envelope: MessageEnvelope) -> None:
        """Handle graceful shutdown."""
        logger.info("admin_shutdown_received", extra=self._log_extra())
        # Forward shutdown to team workers
        shutdown_env = MessageEnvelope(
            msg_type=MessageType.SHUTDOWN,
            sender_id=self.agent_id,
            sender_role=self.role,
            sender_team=self.team_id,
            recipient_team=self.team_id,
            project_id=envelope.project_id,
            correlation_id=envelope.correlation_id,
            payload={"reason": "shutdown_cascade", "initiated_by": envelope.sender_id},
        )
        await self.publish(shutdown_env)
        # Acknowledge upstream
        ack = envelope.reply(
            msg_type=MessageType.SHUTDOWN_ACK,
            sender_id=self.agent_id,
            sender_role=self.role,
            sender_team=self.team_id,
            payload={"acknowledged_by": self.agent_id},
        )
        await self.publish(ack)
        self._shutting_down.set()
