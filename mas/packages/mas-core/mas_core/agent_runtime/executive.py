"""ExecutiveAgent — COO-level operational coordinator.

Extends ``AdminAgent`` with cross-department orchestration:

* **Document lifecycle**: receives ``DOCUMENT_SUBMIT``, fans-out
  ``REVIEW_REQUEST`` to C-Suite reviewers, aggregates ``REVIEW_RESPONSE``,
  emits workflow events (does NOT write ``projects.state`` directly).
* **Review fan-out / fan-in**: parallel ``REVIEW_REQUEST`` to each reviewer,
  with timeout and circuit-breaker (≥ 2 timeouts → ``CIRCUIT_OPEN``).
* **CSO veto handling**: on ``veto=True`` + ``severity=BLOCKER``, emit
  a ``cso_veto`` event → workflow controller transitions to ``SECURITY_BLOCKED``.
* **Department tasking**: fan-out ``ADMIN_TASK`` to department PMs with
  budget scoping.
* **Revision loops**: on ``NEEDS_REVISION`` verdict, sends
  ``DOCUMENT_REVISION`` back to the originating team and re-enters review.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from ..llm_gateway.models import ToolDefinition, ToolFunction
from ..protocols.domain import (
    ReviewComment,
    ReviewResponse,
    ReviewSummary,
)
from ..protocols.enums import (
    MessageType,
    ReviewSeverity,
    ReviewVerdict,
)
from ..protocols.envelope import MessageEnvelope
from .admin import AdminAgent
from .config import AgentConfig

logger = logging.getLogger(__name__)


class ExecutiveAgent(AdminAgent):
    """COO-level executive that coordinates document lifecycle and reviews.

    Parameters
    ----------
    config : AgentConfig
        Should have ``agent_role == AgentRole.EXECUTIVE``.
    reviewer_teams : list[str]
        Team IDs of C-Suite reviewers to include in review fan-outs.
    review_timeout_secs : float
        Seconds to wait for each reviewer before counting a timeout.
    max_revisions : int
        Maximum revision loop iterations before rejecting a document.
    """

    def __init__(
        self,
        config: AgentConfig,
        storage: Any | None = None,
        *,
        tool_client: Any | None = None,
        system_prompt: str | None = None,
        reviewer_teams: list[str] | None = None,
        review_timeout_secs: float = 120.0,
        max_revisions: int = 3,
        event_emitter: Any | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            config,
            storage,
            tool_client=tool_client,
            system_prompt=system_prompt,
            **kwargs,
        )
        self._reviewer_teams: list[str] = reviewer_teams or []
        self._review_timeout_secs = review_timeout_secs
        self._max_revisions = max_revisions
        self._event_emitter = event_emitter

        # Active review sessions: session_id → ReviewSummary
        self._review_sessions: dict[str, ReviewSummary] = {}
        # Map correlation_id → parent envelope (for aggregation callbacks)
        self._review_parents: dict[str, MessageEnvelope] = {}
        # Track revision counts per document: document_id → count
        self._revision_counts: dict[str, int] = {}

    def _default_system_prompt(self) -> str:
        return (
            f"You are {self.agent_id}, the COO (Executive) for team {self.team_id}. "
            "Coordinate document lifecycle: creation, review, revision. "
            "Delegate tasks to department PMs and aggregate results. "
            "Halt on CSO security vetoes. Use an observe-plan-delegate-verify-report loop, "
            "track blockers and timeouts explicitly, preserve durable context in AIAT state, "
            "and never bypass approval, credential, budget, or observability boundaries. "
            "Be systematic and thorough."
        )

    # ------------------------------------------------------------------
    # Extended message routing
    # ------------------------------------------------------------------

    def _get_handler(self, msg_type: MessageType) -> Any:
        executive_handlers = {
            MessageType.DOCUMENT_SUBMIT: self._handle_document_submit,
            MessageType.REVIEW_RESPONSE: self._handle_review_response,
            MessageType.DOCUMENT_REVISION: self._handle_document_revision,
            MessageType.APPROVAL_RESPONSE: self._handle_approval_response,
            MessageType.INFRA_READY: self._handle_infra_ready,
            MessageType.SPRINT_REPORT: self._handle_sprint_report,
            MessageType.DIRECTIVE: self._handle_directive,
            MessageType.SYSTEM_EVENT: self._handle_system_event,
        }
        return executive_handlers.get(msg_type) or super()._get_handler(msg_type)

    # ------------------------------------------------------------------
    # Directive dispatch — action-driven think() loop
    # ------------------------------------------------------------------

    async def _handle_directive(self, envelope: MessageEnvelope) -> None:
        """Dispatch directive actions to the think() loop for COO.

        Actions that require LLM reasoning trigger think() with appropriate
        tools. Unknown actions fall through to AdminAgent's re-broadcast.
        """
        action = envelope.payload.get("action", "")
        project_id = envelope.project_id or envelope.payload.get("project_id", "")

        logger.info(
            "executive_directive_%s",
            action.lower(),
            extra=self._log_extra(action=action),
        )

        # Skip directives for terminal/archived projects to avoid wasted LLM calls
        if project_id and self._storage is not None:
            try:
                from uuid import UUID

                project = await self._storage.get_project(UUID(project_id))
                if project is not None and project.get("state") in (
                    "ARCHIVED",
                    "COMPLETED",
                    "FAILED",
                ):
                    logger.info(
                        "executive_directive_skip_terminal_project",
                        extra=self._log_extra(
                            action=action,
                            project_id=project_id,
                            state=project.get("state"),
                        ),
                    )
                    return
            except Exception:
                pass  # If we can't check, proceed normally

        # Actions that trigger LLM work for the COO
        actionable = {
            "RESUME",
            "START_PDR",
            "START_CDR",
            "START_RR",
            "START_SPRINT_PLANNING",
            "START_RETROSPECTIVE",
        }
        if action.upper() in actionable:
            await self._directive_think(envelope, action)
        else:
            # Unknown actions — broadcast to team (default AdminAgent behaviour)
            await super()._handle_directive(envelope)

    async def _directive_think(self, envelope: MessageEnvelope, action: str) -> None:
        """Run think() in response to a directive action.

        Builds a task-oriented prompt from the directive payload, then runs
        the LLM loop with the COO's workflow tools.
        """
        project_id = envelope.project_id or envelope.payload.get("project_id", "unknown")
        state = envelope.payload.get("state", "")
        context_str = envelope.payload.get("context", "")

        action_instructions: dict[str, str] = {
            "RESUME": (
                f"The system is resuming from a restart. Project is currently in state: **{state}**.\n\n"
                "Check the current project status with `project.status`, then determine and "
                "execute the next action required based on the current state.\n"
                "If in FEASIBILITY_CHECK: perform feasibility analysis and call `review.aggregate` "
                "with `event=all_reviews_in` and an appropriate verdict.\n"
                "If in PDR_REVIEW or CDR_REVIEW: check if all reviews are in and call `review.aggregate`.\n"
                "If in SPRINT_PLANNING: create sprint plan and call `project.transition` with "
                "`event=sprints_created`.\n"
                "If in RETROSPECTIVE: complete retrospective and call `project.transition` with "
                "`event=retrospective_done`.\n"
            ),
            "START_PDR": (
                "Coordinate the **Preliminary Design Review (PDR)**.\n\n"
                "Fan-out REVIEW_REQUEST to all C-Suite reviewers, then once all reviews are "
                "received call `review.aggregate` to advance the project."
            ),
            "START_CDR": (
                "Coordinate the **Critical Design Review (CDR)**.\n\n"
                "Fan-out REVIEW_REQUEST to all C-Suite reviewers, then once all reviews are "
                "received call `review.aggregate` with the aggregate verdict."
            ),
            "START_SPRINT_PLANNING": (
                "Coordinate **Sprint Planning** with the CTO.\n\n"
                "Work with the CTO to create the sprint plan, then call `project.transition` "
                "with `event=sprints_created` once the plan is ready."
            ),
            "START_RETROSPECTIVE": (
                "Facilitate the **Retrospective** review.\n\n"
                "Gather KPI data and team feedback, then call `project.transition` with "
                "`event=retrospective_done` to complete the retrospective."
            ),
        }

        task_instruction = action_instructions.get(
            action.upper(),
            f"Execute the required COO action for directive: {action}. "
            "Check project status first and determine next steps.",
        )

        messages = [
            {"role": "system", "content": self._system_prompt},
            {
                "role": "user",
                "content": (
                    f"## Directive: {action}\n\n"
                    f"## Project\n"
                    f"- ID: {project_id}\n"
                    + (f"- State: {state}\n" if state else "")
                    + (f"- Context: {context_str}\n" if context_str else "")
                    + f"\n{task_instruction}"
                ),
            },
        ]

        tools = self._build_workflow_tool_definitions()
        await self.think(messages=messages, tools=tools)

    def _build_workflow_tool_definitions(self) -> list[ToolDefinition]:
        """Build ToolDefinition objects for the core workflow tools."""
        return [
            ToolDefinition(
                function=ToolFunction(
                    name="project.status",
                    description="Get current project status and state.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "project_id": {
                                "type": "string",
                                "description": "UUID of the project.",
                            }
                        },
                        "required": ["project_id"],
                    },
                )
            ),
            ToolDefinition(
                function=ToolFunction(
                    name="review.aggregate",
                    description=(
                        "Aggregate all reviews and advance project state. "
                        "Use for FEASIBILITY_CHECK → FEASIBILITY_REPORT (verdict required), "
                        "PDR_REVIEW → CDR_CREATION, CDR_REVIEW → HUMAN_APPROVAL."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "project_id": {
                                "type": "string",
                                "description": "UUID of the project.",
                            },
                            "verdict": {
                                "type": "string",
                                "enum": [
                                    "APPROVED",
                                    "APPROVED_WITH_COMMENTS",
                                    "NEEDS_REVISION",
                                    "REJECTED",
                                ],
                                "description": "Aggregate review verdict.",
                            },
                            "actor_id": {
                                "type": "string",
                                "description": "ID of the agent submitting the aggregate.",
                            },
                        },
                        "required": ["project_id", "verdict"],
                    },
                )
            ),
            ToolDefinition(
                function=ToolFunction(
                    name="project.transition",
                    description="Transition the project to a new state via a workflow event.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "project_id": {
                                "type": "string",
                                "description": "UUID of the project.",
                            },
                            "event": {
                                "type": "string",
                                "description": (
                                    "Workflow event name. Examples: "
                                    "all_reviews_in, pdr_submitted, cdr_submitted, "
                                    "sprints_created, infra_ready, all_sprints_done, "
                                    "retrospective_done, kpi_saved."
                                ),
                            },
                            "actor_id": {
                                "type": "string",
                                "description": "Agent performing the transition.",
                            },
                            "context": {
                                "type": "object",
                                "description": "Optional context for the transition.",
                            },
                        },
                        "required": ["project_id", "event"],
                    },
                )
            ),
        ]

    # ------------------------------------------------------------------
    # SYSTEM_EVENT handler — react to orchestrator state transitions
    # ------------------------------------------------------------------

    async def _handle_system_event(self, envelope: MessageEnvelope) -> None:
        """React to workflow state transition notifications.

        For COO: log and no-op. Major actions are delegated by the
        orchestrator via DIRECTIVE messages.
        """
        event = envelope.payload.get("event", "")
        to_state = envelope.payload.get("to_state", "")
        logger.info(
            "executive_system_event_%s",
            event.lower(),
            extra=self._log_extra(event=event, to_state=to_state),
        )

    # ------------------------------------------------------------------
    # Document lifecycle
    # ------------------------------------------------------------------

    async def _handle_document_submit(self, envelope: MessageEnvelope) -> None:
        """A department submitted a document for review.

        Start a review fan-out to all configured C-Suite reviewers.
        """
        document_id = envelope.payload.get("document_id", str(uuid4()))
        doc_type = envelope.payload.get("doc_type", "UNKNOWN")
        project_id = envelope.project_id

        logger.info(
            "executive_document_received",
            extra=self._log_extra(
                document_id=document_id,
                doc_type=doc_type,
            ),
        )

        # Emit event: document submitted
        await self._emit_event(
            "document_submitted",
            project_id=project_id,
            document_id=document_id,
            doc_type=doc_type,
        )

        # Start review fan-out
        session_id = str(uuid4())
        await self._start_review_fanout(
            session_id=session_id,
            document_id=document_id,
            doc_type=doc_type,
            parent_envelope=envelope,
        )

    async def _start_review_fanout(
        self,
        *,
        session_id: str,
        document_id: str,
        doc_type: str,
        parent_envelope: MessageEnvelope,
    ) -> None:
        """Fan-out REVIEW_REQUEST to all configured reviewer teams."""
        summary = ReviewSummary(
            session_id=UUID(session_id) if len(session_id) == 36 else uuid4(),
            project_id=parent_envelope.project_id or "",
            document_id=UUID(document_id) if len(document_id) == 36 else uuid4(),
            doc_type=doc_type,  # type: ignore[arg-type]
            reviewer_count=len(self._reviewer_teams),
        )
        self._review_sessions[session_id] = summary
        self._review_parents[session_id] = parent_envelope

        for team_id in self._reviewer_teams:
            review_env = MessageEnvelope(
                msg_type=MessageType.REVIEW_REQUEST,
                sender_id=self.agent_id,
                sender_role=self.role,
                sender_team=self.team_id,
                recipient_team=team_id,
                project_id=parent_envelope.project_id,
                correlation_id=parent_envelope.correlation_id,
                parent_id=parent_envelope.message_id,
                payload={
                    "session_id": session_id,
                    "document_id": document_id,
                    "doc_type": doc_type,
                    "document_payload": parent_envelope.payload,
                },
            )
            await self.publish(review_env)

        logger.info(
            "executive_review_fanout_started",
            extra=self._log_extra(
                session_id=session_id,
                reviewer_count=len(self._reviewer_teams),
                document_id=document_id,
            ),
        )

    # ------------------------------------------------------------------
    # Review aggregation
    # ------------------------------------------------------------------

    async def _handle_review_response(self, envelope: MessageEnvelope) -> None:
        """Aggregate a C-Suite reviewer's response."""
        session_id = envelope.payload.get("session_id", "")
        summary = self._review_sessions.get(session_id)
        if summary is None:
            logger.warning(
                "executive_review_response_for_unknown_session",
                extra=self._log_extra(session_id=session_id),
            )
            return

        # Parse reviewer response
        reviewer_id = envelope.sender_id
        verdict_str = envelope.payload.get("verdict", "APPROVED")
        try:
            verdict = ReviewVerdict(verdict_str)
        except ValueError:
            verdict = ReviewVerdict.APPROVED

        comments_raw = envelope.payload.get("comments", [])
        comments = []
        has_veto = False

        for c in comments_raw:
            comment = ReviewComment(
                reviewer_id=reviewer_id,
                reviewer_role=envelope.payload.get("reviewer_role", envelope.sender_role.value),
                reviewer_team=envelope.sender_team,
                section=c.get("section"),
                severity=ReviewSeverity(c.get("severity", "INFO")),
                body=c.get("body", ""),
                suggested_change=c.get("suggested_change"),
                veto=c.get("veto", False),
            )
            comments.append(comment)
            if comment.veto and comment.severity == ReviewSeverity.BLOCKER:
                has_veto = True

        veto_flag = envelope.payload.get("veto", False) or has_veto

        response = ReviewResponse(
            reviewer_id=reviewer_id,
            reviewer_role=envelope.payload.get("reviewer_role", envelope.sender_role.value),
            reviewer_team=envelope.sender_team,
            verdict=verdict,
            comments=comments,
            veto=veto_flag,
        )

        # Add to summary
        summary.responses[reviewer_id] = response
        summary.comments.extend(comments)
        summary.responses_received += 1

        logger.info(
            "executive_review_response_received",
            extra=self._log_extra(
                session_id=session_id,
                reviewer=reviewer_id,
                verdict=verdict.value,
                veto=veto_flag,
                received=summary.responses_received,
                total=summary.reviewer_count,
            ),
        )

        # CSO veto check — immediate halt
        if veto_flag:
            await self._handle_cso_veto(session_id, response, envelope)
            return

        # Check if all responses received
        if summary.is_complete:
            await self._finalize_review(session_id)

    async def _handle_cso_veto(
        self,
        session_id: str,
        response: ReviewResponse,
        envelope: MessageEnvelope,
    ) -> None:
        """Handle CSO veto: emit event → SECURITY_BLOCKED."""
        summary = self._review_sessions.get(session_id)
        parent = self._review_parents.get(session_id)
        project_id = parent.project_id if parent else (envelope.project_id or "")
        document_id = str(summary.document_id) if summary else ""

        logger.warning(
            "executive_cso_veto",
            extra=self._log_extra(
                session_id=session_id,
                reviewer=response.reviewer_id,
                document_id=document_id,
            ),
        )

        await self._emit_event(
            "cso_veto",
            project_id=project_id,
            document_id=document_id,
            session_id=session_id,
            reviewer_id=response.reviewer_id,
            veto_comments=[c.body for c in response.comments if c.veto],
        )

        # Clean up session
        self._review_sessions.pop(session_id, None)
        self._review_parents.pop(session_id, None)

    async def _finalize_review(self, session_id: str) -> None:
        """All reviews received — aggregate verdict and act."""
        summary = self._review_sessions.pop(session_id, None)
        parent = self._review_parents.pop(session_id, None)
        if summary is None:
            return

        # Determine overall verdict
        verdicts = [r.verdict for r in summary.responses.values()]
        if any(v == ReviewVerdict.REJECTED for v in verdicts):
            overall = ReviewVerdict.REJECTED
        elif any(v == ReviewVerdict.NEEDS_REVISION for v in verdicts):
            overall = ReviewVerdict.NEEDS_REVISION
        elif any(v == ReviewVerdict.APPROVED_WITH_COMMENTS for v in verdicts):
            overall = ReviewVerdict.APPROVED_WITH_COMMENTS
        else:
            overall = ReviewVerdict.APPROVED

        summary.overall_verdict = overall
        summary.completed_at = datetime.now(tz=UTC)

        document_id = str(summary.document_id)
        project_id = summary.project_id

        logger.info(
            "executive_review_finalized",
            extra=self._log_extra(
                session_id=session_id,
                verdict=overall.value,
                comment_count=len(summary.comments),
            ),
        )

        if overall in (ReviewVerdict.APPROVED, ReviewVerdict.APPROVED_WITH_COMMENTS):
            await self._emit_event(
                "review_approved",
                project_id=project_id,
                document_id=document_id,
                session_id=session_id,
                verdict=overall.value,
            )
        elif overall == ReviewVerdict.NEEDS_REVISION:
            await self._request_revision(summary, parent)
        elif overall == ReviewVerdict.REJECTED:
            await self._emit_event(
                "review_rejected",
                project_id=project_id,
                document_id=document_id,
                session_id=session_id,
            )

    # ------------------------------------------------------------------
    # Revision loop
    # ------------------------------------------------------------------

    async def _request_revision(
        self,
        summary: ReviewSummary,
        parent_envelope: MessageEnvelope | None,
    ) -> None:
        """Request document revision from the originating team."""
        document_id = str(summary.document_id)
        rev_count = self._revision_counts.get(document_id, 0) + 1
        self._revision_counts[document_id] = rev_count

        if rev_count > self._max_revisions:
            logger.warning(
                "executive_max_revisions_exceeded",
                extra=self._log_extra(
                    document_id=document_id,
                    revision_count=rev_count,
                ),
            )
            await self._emit_event(
                "review_rejected",
                project_id=summary.project_id,
                document_id=document_id,
                reason="max_revisions_exceeded",
            )
            self._revision_counts.pop(document_id, None)
            return

        # Send DOCUMENT_REVISION back to the originating team
        target_team = parent_envelope.sender_team if parent_envelope else self.team_id
        revision_comments = [
            {
                "reviewer": c.reviewer_id,
                "section": c.section,
                "severity": c.severity.value,
                "body": c.body,
                "suggested_change": c.suggested_change,
            }
            for c in summary.comments
            if c.severity in (ReviewSeverity.MAJOR, ReviewSeverity.BLOCKER, ReviewSeverity.CONCERN)
        ]

        revision_env = MessageEnvelope(
            msg_type=MessageType.DOCUMENT_REVISION,
            sender_id=self.agent_id,
            sender_role=self.role,
            sender_team=self.team_id,
            recipient_team=target_team,
            project_id=summary.project_id,
            correlation_id=parent_envelope.correlation_id if parent_envelope else None,
            payload={
                "document_id": document_id,
                "revision_number": rev_count,
                "comments": revision_comments,
                "verdict": ReviewVerdict.NEEDS_REVISION.value,
            },
        )
        await self.publish(revision_env)

        logger.info(
            "executive_revision_requested",
            extra=self._log_extra(
                document_id=document_id,
                revision_number=rev_count,
                target_team=target_team,
            ),
        )

    async def _handle_document_revision(self, envelope: MessageEnvelope) -> None:
        """Handle a revised document re-submission — restart review."""
        document_id = envelope.payload.get("document_id", str(uuid4()))

        logger.info(
            "executive_revised_document_received",
            extra=self._log_extra(document_id=document_id),
        )

        # Restart review fan-out
        session_id = str(uuid4())
        await self._start_review_fanout(
            session_id=session_id,
            document_id=document_id,
            doc_type=envelope.payload.get("doc_type", "UNKNOWN"),
            parent_envelope=envelope,
        )

    # ------------------------------------------------------------------
    # Department tasking
    # ------------------------------------------------------------------

    async def task_department(
        self,
        *,
        team_id: str,
        task: str,
        context: str = "",
        envelope: MessageEnvelope | None = None,
    ) -> str:
        """Delegate a task to a department PM."""
        env = envelope or self._current_envelope
        task_env = MessageEnvelope(
            msg_type=MessageType.ADMIN_TASK,
            sender_id=self.agent_id,
            sender_role=self.role,
            sender_team=self.team_id,
            recipient_team=team_id,
            project_id=env.project_id if env else None,
            correlation_id=env.correlation_id if env else None,
            parent_id=env.message_id if env else None,
            payload={
                "task": task,
                "context": context,
                "delegated_by": self.agent_id,
            },
        )
        entry_id = await self.publish(task_env)
        logger.info(
            "executive_task_delegated",
            extra=self._log_extra(team=team_id, task=task[:80]),
        )
        return entry_id

    # ------------------------------------------------------------------
    # Approval handling (human-in-the-loop responses)
    # ------------------------------------------------------------------

    async def _handle_approval_response(self, envelope: MessageEnvelope) -> None:
        """Handle human approval/rejection decision."""
        decision = envelope.payload.get("decision", "APPROVE")
        project_id = envelope.project_id

        logger.info(
            "executive_approval_response",
            extra=self._log_extra(decision=decision),
        )

        await self._emit_event(
            "human_decision",
            project_id=project_id,
            decision=decision,
            comment=envelope.payload.get("comment"),
            edit_instructions=envelope.payload.get("edit_instructions"),
        )

    # ------------------------------------------------------------------
    # Infrastructure coordination
    # ------------------------------------------------------------------

    async def _handle_infra_ready(self, envelope: MessageEnvelope) -> None:
        """DevOps PM signalled infrastructure is provisioned."""
        logger.info(
            "executive_infra_ready",
            extra=self._log_extra(
                sprint_id=envelope.payload.get("sprint_id"),
            ),
        )
        await self._emit_event(
            "infra_ready",
            project_id=envelope.project_id,
            sprint_id=envelope.payload.get("sprint_id"),
        )

    # ------------------------------------------------------------------
    # Sprint reports
    # ------------------------------------------------------------------

    async def _handle_sprint_report(self, envelope: MessageEnvelope) -> None:
        """Forward sprint report upstream or store for aggregation."""
        logger.info(
            "executive_sprint_report_received",
            extra=self._log_extra(
                sprint_number=envelope.payload.get("sprint_number"),
            ),
        )
        await self._emit_event(
            "sprint_report_received",
            project_id=envelope.project_id,
            sprint_number=envelope.payload.get("sprint_number"),
            report=envelope.payload,
        )

    # ------------------------------------------------------------------
    # Review session timeout (called by external watchdog or timer)
    # ------------------------------------------------------------------

    async def record_review_timeout(self, session_id: str, reviewer_id: str) -> None:
        """Record that a reviewer timed out. Check circuit breaker."""
        summary = self._review_sessions.get(session_id)
        if summary is None:
            return

        summary.timeout_count += 1
        logger.warning(
            "executive_review_timeout",
            extra=self._log_extra(
                session_id=session_id,
                reviewer=reviewer_id,
                timeout_count=summary.timeout_count,
            ),
        )

        if summary.circuit_open:
            logger.error(
                "executive_review_circuit_open",
                extra=self._log_extra(session_id=session_id),
            )
            parent = self._review_parents.pop(session_id, None)
            self._review_sessions.pop(session_id, None)

            await self._emit_event(
                "review_circuit_open",
                project_id=summary.project_id,
                document_id=str(summary.document_id),
                session_id=session_id,
                timeout_count=summary.timeout_count,
            )

    # ------------------------------------------------------------------
    # Event emission helper
    # ------------------------------------------------------------------

    async def _emit_event(self, event_type: str, **data: Any) -> None:
        """Emit a workflow event.

        If an ``event_emitter`` callable was injected, delegate to it.
        Otherwise, publish a SYSTEM_EVENT message to the orchestrator team.
        """
        if self._event_emitter is not None:
            if asyncio.iscoroutinefunction(self._event_emitter):
                await self._event_emitter(event_type, **data)
            else:
                self._event_emitter(event_type, **data)
            return

        event_env = MessageEnvelope(
            msg_type=MessageType.SYSTEM_EVENT,
            sender_id=self.agent_id,
            sender_role=self.role,
            sender_team=self.team_id,
            recipient_team="exec_ceo",
            project_id=data.get("project_id"),
            payload={"event_type": event_type, **data},
        )
        await self.publish(event_env)
