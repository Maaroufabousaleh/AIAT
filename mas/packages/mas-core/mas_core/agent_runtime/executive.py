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
import inspect
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from ..llm_gateway.models import ToolDefinition, ToolFunction
from ..protocols.domain import (
    ReviewComment,
    ReviewResponse,
    ReviewSummary,
)
from ..protocols.enums import (
    AgentRole,
    MessageType,
    ReviewSeverity,
    ReviewVerdict,
)
from ..protocols.envelope import MessageEnvelope
from .admin import AdminAgent
from .tool_catalog import tool_definitions_for_agent

if TYPE_CHECKING:
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
        review_storage: Any | None = None,
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
        # ``storage`` is the checkpoint adapter supplied to AgentBase.  The
        # team runner passes the shared AgentStorage separately so review
        # sessions/comments can be durable without changing checkpoint APIs.
        self._review_storage = review_storage

        # Active review sessions: session_id → ReviewSummary
        self._review_sessions: dict[str, ReviewSummary] = {}
        # Map correlation_id → parent envelope (for aggregation callbacks)
        self._review_parents: dict[str, MessageEnvelope] = {}
        # Track revision counts per project/document type. Immutable document
        # revisions receive new IDs, so a document-ID key cannot bound the
        # lineage-wide review loop.
        self._revision_counts: dict[str, int] = {}

    async def _persist_review_session(self, summary: ReviewSummary) -> bool:
        if self._review_storage is None:
            return True
        try:
            project_id = UUID(summary.project_id)
            document = await self._review_storage.get_document(summary.document_id)
            if document is None:
                # DOCUMENT_SUBMIT may arrive before the document metadata call
                # (for example after a worker restart).  Materialize a minimal
                # DRAFT row so the review session remains durable and its FK is
                # valid instead of silently losing the whole review.
                try:
                    await self._review_storage.create_document(
                        project_id=project_id,
                        doc_type=getattr(summary.doc_type, "value", str(summary.doc_type)),
                        created_by=self.agent_id,
                        document_id=summary.document_id,
                    )
                except Exception:
                    # Another worker may have materialized the same document
                    # concurrently.  Re-read it before treating persistence as
                    # failed; a row for a different project is still rejected.
                    document = await self._review_storage.get_document(summary.document_id)
                    if document is None:
                        raise
            if document is not None and str(document.get("project_id")) != str(project_id):
                raise ValueError(
                    f"Document {summary.document_id} belongs to project "
                    f"{document.get('project_id')}, not {project_id}"
                )
            await self._review_storage.create_review_session(
                project_id=project_id,
                document_id=summary.document_id,
                session_type=getattr(summary.doc_type, "value", str(summary.doc_type)),
                reviewer_ids=list(self._reviewer_teams),
                review_timeout_seconds=max(1, int(self._review_timeout_secs)),
                session_id=summary.session_id,
            )
            return True
        except Exception:
            logger.warning("executive_review_session_persist_failed", exc_info=True)
            return False

    async def _persist_review_comment(self, summary: ReviewSummary, response: ReviewResponse) -> None:
        if self._review_storage is None:
            return
        try:
            severities = [comment.severity.value for comment in response.comments]
            await self._review_storage.add_review_comment(
                session_id=summary.session_id,
                project_id=UUID(summary.project_id),
                reviewer_id=response.reviewer_id,
                reviewer_role=response.reviewer_role,
                verdict=response.verdict.value,
                veto=response.veto,
                severity=severities[0] if severities else None,
                comments=[comment.model_dump(mode="json") for comment in response.comments],
            )
        except Exception:
            logger.warning("executive_review_comment_persist_failed", exc_info=True)

    async def _update_document_status(self, summary: ReviewSummary, status: str) -> None:
        """Keep the durable document badge synchronized with review state."""
        if self._review_storage is None:
            return
        updater = getattr(self._review_storage, "update_document_status", None)
        if not callable(updater):
            return
        try:
            result = updater(summary.document_id, status=status)
            if inspect.isawaitable(result):
                await result
        except Exception:
            # Review persistence remains authoritative if this metadata update
            # is temporarily unavailable.
            logger.warning("executive_document_status_persist_failed", exc_info=True)

    async def _persist_review_status(
        self,
        summary: ReviewSummary,
        status: str,
        *,
        completed_at: datetime | None = None,
    ) -> None:
        if self._review_storage is None:
            return
        try:
            updates: dict[str, Any] = {"status": status}
            if completed_at is None and status in {
                "COMPLETED",
                "NEEDS_REVISION",
                "REJECTED",
                "VETOED",
                "CIRCUIT_OPEN",
                "TIMED_OUT",
                "SUPERSEDED",
            }:
                completed_at = datetime.now(tz=UTC)
            if completed_at is not None:
                updates["completed_at"] = completed_at
            await self._review_storage.update_review_session(summary.session_id, **updates)
        except Exception:
            logger.warning("executive_review_status_persist_failed", exc_info=True)

        document_status = {
            "COMPLETED": "APPROVED",
            "NEEDS_REVISION": "NEEDS_REVISION",
            "REJECTED": "REJECTED",
            "VETOED": "REJECTED",
            "CIRCUIT_OPEN": "NEEDS_REVISION",
            "TIMED_OUT": "NEEDS_REVISION",
            "SUPERSEDED": "SUPERSEDED",
        }.get(status)
        if document_status:
            await self._update_document_status(summary, document_status)

    async def _rehydrate_review_session(
        self,
        session_id: str,
        parent_envelope: MessageEnvelope | None = None,
    ) -> ReviewSummary | None:
        """Restore one active durable review into the runner's live maps."""
        if self._review_storage is None:
            return None
        try:
            row = await self._review_storage.get_review_session(UUID(str(session_id)))
            if row is None or str(row.get("status") or "").upper() not in {
                "IN_PROGRESS",
                "PENDING",
                "OPEN",
                "STARTED",
            }:
                return None
            document_id = row.get("document_id")
            project_id = row.get("project_id")
            if not document_id or not project_id:
                return None

            reviewer_ids = [str(value) for value in row.get("reviewer_ids") or []]
            summary = ReviewSummary(
                session_id=UUID(str(row["id"])),
                project_id=str(project_id),
                document_id=UUID(str(document_id)),
                doc_type=str(row.get("session_type") or "PDR").upper(),
                reviewer_count=len(reviewer_ids),
                timeout_count=int(row.get("timeout_count") or 0),
            )
            persisted = await self._review_storage.get_review_comments(summary.session_id)
            for item in persisted:
                reviewer_id = str(item.get("reviewer_id") or "")
                if not reviewer_id or reviewer_id in summary.responses:
                    continue
                comments: list[ReviewComment] = []
                for raw_comment in item.get("comments") or []:
                    if not isinstance(raw_comment, dict):
                        continue
                    payload = dict(raw_comment)
                    payload.setdefault("reviewer_id", reviewer_id)
                    payload.setdefault(
                        "reviewer_team",
                        str(payload.get("reviewer_team") or item.get("reviewer_role") or reviewer_id),
                    )
                    try:
                        comments.append(ReviewComment.model_validate(payload))
                    except Exception:
                        logger.warning(
                            "executive_review_comment_rehydrate_skipped",
                            extra=self._log_extra(session_id=session_id, reviewer=reviewer_id),
                        )
                try:
                    verdict = ReviewVerdict(str(item.get("verdict") or "APPROVED"))
                except ValueError:
                    verdict = ReviewVerdict.APPROVED
                response = ReviewResponse(
                    reviewer_id=reviewer_id,
                    reviewer_role=str(item.get("reviewer_role") or "reviewer"),
                    reviewer_team=(
                        comments[0].reviewer_team if comments else str(item.get("reviewer_role") or reviewer_id)
                    ),
                    verdict=verdict,
                    comments=comments,
                    veto=bool(item.get("veto")),
                    submitted_at=item.get("submitted_at") or datetime.now(tz=UTC),
                )
                summary.responses[reviewer_id] = response
                summary.comments.extend(comments)
            summary.responses_received = len(summary.responses)

            if parent_envelope is None:
                parent_envelope = MessageEnvelope(
                    msg_type=MessageType.DOCUMENT_SUBMIT,
                    sender_id="orchestrator",
                    sender_role=AgentRole.ORCHESTRATOR,
                    sender_team="exec_ceo",
                    recipient_team=self.team_id,
                    project_id=str(project_id),
                    payload={
                        "document_id": str(document_id),
                        "doc_type": str(row.get("session_type") or "PDR").upper(),
                        "session_id": session_id,
                        "rehydrated": True,
                    },
                )
            self._review_sessions[session_id] = summary
            self._review_parents[session_id] = parent_envelope
            logger.info(
                "executive_review_session_rehydrated",
                extra=self._log_extra(
                    session_id=session_id,
                    document_id=str(document_id),
                    responses_received=summary.responses_received,
                ),
            )
            return summary
        except Exception:
            logger.warning(
                "executive_review_session_rehydrate_failed",
                extra=self._log_extra(session_id=session_id),
                exc_info=True,
            )
            return None

    async def _publish_review_requests(
        self,
        *,
        session_id: str,
        document_id: str,
        doc_type: str,
        parent_envelope: MessageEnvelope,
        reviewer_teams: list[str] | None = None,
    ) -> None:
        """Publish idempotent reviewer work for a new or rehydrated session."""
        for team_id in reviewer_teams or self._reviewer_teams:
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
            MessageType.ADMIN_REPLY: self._handle_execution_reply,
            MessageType.RESULT: self._handle_execution_reply,
            MessageType.APPROVAL_RESPONSE: self._handle_approval_response,
            MessageType.INFRA_READY: self._handle_infra_ready,
            MessageType.SPRINT_REPORT: self._handle_sprint_report,
            MessageType.DIRECTIVE: self._handle_directive,
            MessageType.SYSTEM_EVENT: self._handle_system_event,
        }
        return executive_handlers.get(msg_type) or super()._get_handler(msg_type)

    async def _handle_execution_reply(self, envelope: MessageEnvelope) -> None:
        """Accept issue completion only when a dispatched worker returned output."""
        if str(envelope.payload.get("action") or "").upper() != "EXECUTE_ISSUE":
            await super()._handle_admin_reply(envelope)
            return

        issue_id = envelope.payload.get("issue_id")
        results = envelope.payload.get("results")
        valid_results = (
            isinstance(results, list)
            and bool(results)
            and all(
                isinstance(item, dict) and bool(str(item.get("result") or "").strip())
                for item in results
            )
        )
        if not issue_id or not valid_results or not envelope.project_id:
            logger.warning(
                "executive_issue_completion_rejected",
                extra=self._log_extra(
                    issue_id=issue_id,
                    reason="missing_issue_identity_or_worker_output",
                ),
            )
            return

        updated = await self.execute_tool(
            "issue.update_status",
            {
                "project_id": envelope.project_id,
                "issue_id": issue_id,
                "status": "DONE",
            },
        )
        if not isinstance(updated, dict) or updated.get("error"):
            logger.warning(
                "executive_issue_completion_persist_failed",
                extra=self._log_extra(issue_id=issue_id),
            )
            return

        # Reconcile against authoritative issue/sprint state. This closes and
        # advances only when every issue has independently completed.
        await self._recover_directive_progress(envelope, "START_EXECUTION")

    async def _handle_admin_task(self, envelope: MessageEnvelope) -> None:
        """Handle generic executive tasks directly.

        The COO team intentionally has no local worker pool. Inheriting the
        department-manager implementation would republish the task to
        ``exec_coo`` where the same COO consumes it again. Execute one bounded
        reasoning pass and return the result to the upstream team instead.
        """
        action = str(envelope.payload.get("action") or "").upper()
        if action == "START_REVIEW":
            # Older CEO turns may have queued START_REVIEW as an ADMIN_TASK.
            # This is a durable workflow handoff, not an open-ended reasoning
            # task; process it without blocking the COO stream on an LLM call.
            doc_type = str(
                envelope.payload.get("doc_type")
                or envelope.payload.get("session_type")
                or "CDR"
            ).upper()
            if doc_type not in {"PDR", "CDR", "RR"}:
                doc_type = "CDR"
            document_id = str(envelope.payload.get("document_id") or "")
            if document_id:
                await self._start_review_fanout(
                    session_id=str(envelope.payload.get("session_id") or uuid4()),
                    document_id=document_id,
                    doc_type=doc_type,
                    parent_envelope=envelope,
                )
            return

        task = str(envelope.payload.get("task") or "")
        context = str(envelope.payload.get("context") or "")
        messages = [
            {"role": "system", "content": self._system_prompt},
            {
                "role": "user",
                "content": f"## Executive Task\n{task}\n\n## Context\n{context}",
            },
        ]
        result_messages = await self.think(
            messages=messages,
            tools=self.available_tool_definitions(),
        )
        result = ""
        for message in reversed(result_messages):
            if message.get("role") == "assistant" and message.get("content"):
                result = str(message["content"])
                break

        reply = envelope.reply(
            msg_type=MessageType.ADMIN_REPLY,
            sender_id=self.agent_id,
            sender_role=self.role,
            sender_team=self.team_id,
            payload={"result": result, "task": task, "executed_by": self.agent_id},
        )
        await self.publish(reply)

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
            "START_INFRA",
            "START_EXECUTION",
            "START_RETROSPECTIVE",
            "START_KPI",
        }
        if action.upper() in actionable:
            # Resume is a delivery/reconciliation operation. Running an
            # open-ended LLM turn here can block the team's ordered stream
            # while newer DOCUMENT_SUBMIT or review responses wait behind it.
            if action.upper() == "RESUME":
                await self._recover_directive_progress(envelope, action)
                return
            # The post-review delivery stages are governed state-machine
            # handoffs. Their tool calls and transition events are fully
            # deterministic, so an LLM must not be able to hold the ordered
            # team stream open while it decides whether to perform them.
            deterministic_actions = {
                "START_SPRINT_PLANNING",
                "START_INFRA",
                "START_EXECUTION",
                "START_RETROSPECTIVE",
                "START_KPI",
            }
            if action.upper() in deterministic_actions:
                await self._recover_directive_progress(envelope, action)
                return
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
            "START_INFRA": (
                "Coordinate governed infrastructure provisioning with the DevOps department.\n\n"
                "Dispatch the infrastructure task to `dept_devops`; it must run the configured "
                "adapter and signal `infra_ready` only after verification."
            ),
            "START_EXECUTION": (
                "Execute every planned sprint through the governed sprint and issue tools.\n\n"
                "For each open sprint, activate it, complete its ready issues, and close it. "
                "Transition with `event=all_sprints_done` only after re-reading the sprint list "
                "and verifying that every sprint is closed."
            ),
            "START_RETROSPECTIVE": (
                "Facilitate the **Retrospective** review.\n\n"
                "Gather KPI data and team feedback, then call `project.transition` with "
                "`event=retrospective_done` to complete the retrospective."
            ),
            "START_KPI": (
                "Persist the final project KPI snapshot with `kpi.compute_project`, then call "
                "`project.transition` with `event=kpi_saved`."
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
        try:
            await self.think(messages=messages, tools=tools)
        finally:
            await self._recover_directive_progress(envelope, action)

    async def _recover_directive_progress(
        self,
        envelope: MessageEnvelope,
        action: str,
    ) -> None:
        """Complete deterministic COO handoffs after a bounded LLM turn."""
        project_id = envelope.project_id or envelope.payload.get("project_id")
        if not project_id:
            return

        action_upper = action.upper()
        try:
            status = await self.execute_tool("project.status", {"project_id": project_id})
            if not isinstance(status, dict) or status.get("error"):
                return
            state = str(status.get("state") or "")

            if action_upper == "RESUME":
                creation_actions = {
                    "PDR_CREATION": "START_PDR",
                    "CDR_CREATION": "START_CDR",
                    "RR_CREATION": "START_RR",
                    "SPRINT_PLANNING": "START_SPRINT_PLANNING",
                    "INFRA_PROVISIONING": "START_INFRA",
                    "IN_PROGRESS": "START_EXECUTION",
                    "RETROSPECTIVE": "START_RETROSPECTIVE",
                    "KPI_PERSISTENCE": "START_KPI",
                }
                if state in creation_actions:
                    await self._recover_directive_progress(
                        envelope,
                        creation_actions[state],
                    )
                    return

                review_doc_type = {
                    "PDR_REVIEW": "PDR",
                    "CDR_REVIEW": "CDR",
                }.get(state)
                if review_doc_type:
                    latest = await self.execute_tool(
                        "document.get_latest",
                        {"project_id": project_id, "doc_type": review_doc_type},
                    )
                    document_id = latest.get("id") if isinstance(latest, dict) else None
                    if document_id:
                        review_result = await self.execute_tool(
                            "review.start_session",
                            {
                                "project_id": project_id,
                                "document_id": document_id,
                                "review_type": review_doc_type,
                            },
                        )
                        if (
                            isinstance(review_result, dict)
                            and review_result.get("session_status") == "COMPLETED"
                        ):
                            completed_event = {
                                "PDR": "all_reviews_in",
                                "CDR": "cdr_presented",
                            }[review_doc_type]
                            await self._transition_if_current(
                                project_id,
                                state,
                                completed_event,
                                "RESUME",
                            )
                return

            if action_upper == "START_SPRINT_PLANNING" and state == "SPRINT_PLANNING":
                listed = await self.execute_tool("sprint.list", {"project_id": project_id})
                sprints = listed.get("sprints", []) if isinstance(listed, dict) else []
                if not sprints:
                    created = await self.execute_tool(
                        "sprint.create",
                        {
                            "project_id": project_id,
                            "team_id": "exec_coo",
                            "sprint_number": 1,
                            "goal": "Deliver the smallest verified project increment.",
                            "planned_story_points": 1,
                            "estimated_hours": 1,
                        },
                    )
                    if isinstance(created, dict) and created.get("error"):
                        logger.warning(
                            "executive_sprint_create_failed",
                            extra=self._log_extra(
                                project_id=project_id,
                                error=created.get("error"),
                            ),
                        )
                        return
                await self._transition_if_current(
                    project_id,
                    state,
                    "sprints_created",
                    action_upper,
                )
                return

            if action_upper == "START_INFRA" and state == "INFRA_PROVISIONING":
                result = await self.execute_tool(
                    "department_task",
                    {
                        "team": "dept_devops",
                        "project_id": project_id,
                        "action": "PROVISION_INFRA",
                        "description": "Provision and verify the project runtime infrastructure.",
                    },
                )
                if isinstance(result, dict) and result.get("error"):
                    logger.warning(
                        "executive_infra_dispatch_failed",
                        extra=self._log_extra(
                            project_id=project_id,
                            error=result.get("error"),
                        ),
                    )
                return

            if action_upper == "START_EXECUTION" and state == "IN_PROGRESS":
                listed = await self.execute_tool("sprint.list", {"project_id": project_id})
                if not isinstance(listed, dict) or listed.get("error"):
                    return
                sprints = listed.get("sprints", []) if isinstance(listed, dict) else []
                if not sprints:
                    return
                closed_statuses = {"CLOSED", "COMPLETED"}
                for sprint in sprints:
                    sprint_id = sprint.get("id") if isinstance(sprint, dict) else None
                    if not sprint_id:
                        return
                    sprint_status = str(sprint.get("status") or "").upper()
                    if sprint_status in closed_statuses:
                        continue

                    issues_result = await self.execute_tool(
                        "issue.list",
                        {"project_id": project_id, "sprint_id": sprint_id},
                    )
                    if not isinstance(issues_result, dict) or issues_result.get("error"):
                        return
                    issues = issues_result.get("issues", [])
                    if not isinstance(issues, list):
                        return
                    if not issues:
                        created_result = await self.execute_tool(
                            "issue.create",
                            {
                                "project_id": project_id,
                                "sprint_id": sprint_id,
                                "title": (
                                    "Complete controlled execution verification "
                                    f"(Sprint {sprint.get('sprint_number', '?')})"
                                ),
                                "description": "Verify the governed end-to-end project path.",
                                "issue_type": "TEST",
                                "priority": "P1",
                                "story_points": 1,
                                "estimated_hours": 1,
                            },
                        )
                        if not isinstance(created_result, dict) or created_result.get("error"):
                            return
                        created_issue = created_result.get("result")
                        if not isinstance(created_issue, dict) or not created_issue.get("id"):
                            return
                        issues = [created_issue]

                    if sprint_status != "IN_PROGRESS":
                        activated = await self.execute_tool(
                            "sprint.activate",
                            {"project_id": project_id, "sprint_id": sprint_id},
                        )
                        if isinstance(activated, dict) and activated.get("error"):
                            return
                    dispatched_work = False
                    for issue in issues:
                        if not isinstance(issue, dict):
                            return
                        issue_id = issue.get("id")
                        issue_status = str(issue.get("status") or "").upper()
                        if not issue_id:
                            return
                        if issue_status in {"DONE", "COMPLETED", "CLOSED"}:
                            continue
                        # An in-progress or blocked issue is owned by an
                        # execution worker. A replayed stage directive must
                        # wait for durable completion evidence, not dispatch a
                        # duplicate task or manufacture a DONE result.
                        if issue_status in {"IN_PROGRESS", "BLOCKED"}:
                            continue

                        issue_type = str(issue.get("issue_type") or "TASK").upper()
                        default_team = {
                            "BUG": "dept_qa",
                            "TEST": "dept_qa",
                            "QA": "dept_qa",
                            "INFRA": "dept_devops",
                            "DEVOPS": "dept_devops",
                            "DOCUMENTATION": "dept_system",
                            "ARCHITECTURE": "dept_system",
                        }.get(issue_type, "dept_production")
                        target_team = str(issue.get("assigned_team") or default_team)
                        dispatched = await self.execute_tool(
                            "department_task",
                            {
                                "team": target_team,
                                "project_id": project_id,
                                "action": "EXECUTE_ISSUE",
                                "issue_id": issue_id,
                                "sprint_id": sprint_id,
                                "description": str(
                                    issue.get("description")
                                    or issue.get("title")
                                    or f"Execute issue {issue_id}"
                                ),
                            },
                        )
                        if not isinstance(dispatched, dict) or dispatched.get("error"):
                            return
                        updated = await self.execute_tool(
                            "issue.update_status",
                            {
                                "project_id": project_id,
                                "issue_id": issue_id,
                                "status": "IN_PROGRESS",
                            },
                        )
                        if isinstance(updated, dict) and updated.get("error"):
                            return
                        dispatched_work = True

                    # A successful dispatch proves only that work was queued.
                    # Completion, actual hours, and output validation belong
                    # to the worker/issue completion path. Reconcile on a
                    # later directive after those durable updates arrive.
                    if dispatched_work:
                        continue

                    refreshed_issues = await self.execute_tool(
                        "issue.list",
                        {"project_id": project_id, "sprint_id": sprint_id},
                    )
                    if not isinstance(refreshed_issues, dict) or refreshed_issues.get("error"):
                        return
                    authoritative_issues = refreshed_issues.get("issues", [])
                    if not isinstance(authoritative_issues, list) or not authoritative_issues:
                        return
                    if any(
                        str(item.get("status") or "").upper()
                        not in {"DONE", "COMPLETED", "CLOSED"}
                        for item in authoritative_issues
                        if isinstance(item, dict)
                    ):
                        continue
                    closed = await self.execute_tool(
                        "sprint.close",
                        {"project_id": project_id, "sprint_id": sprint_id},
                    )
                    if isinstance(closed, dict) and closed.get("error"):
                        return

                # Tool responses can be accepted asynchronously.  Re-read the
                # authoritative list before advancing the workflow, so a
                # second/planned sprint can never be skipped by a stale local
                # list or a partial close.
                final_listed = await self.execute_tool(
                    "sprint.list", {"project_id": project_id}
                )
                if not isinstance(final_listed, dict) or final_listed.get("error"):
                    return
                final_sprints = final_listed.get("sprints", [])
                if not isinstance(final_sprints, list) or not final_sprints:
                    return
                if any(
                    str(sprint.get("status") or "").upper() not in closed_statuses
                    for sprint in final_sprints
                    if isinstance(sprint, dict)
                ):
                    return
                await self._transition_if_current(
                    project_id,
                    state,
                    "all_sprints_done",
                    action_upper,
                )
                return

            if action_upper == "START_RETROSPECTIVE" and state == "RETROSPECTIVE":
                listed = await self.execute_tool("sprint.list", {"project_id": project_id})
                sprints = listed.get("sprints", []) if isinstance(listed, dict) else []
                for sprint in sprints:
                    if sprint.get("id") and str(sprint.get("status") or "").upper() == "CLOSED":
                        await self.execute_tool(
                            "retrospective.generate",
                            {
                                "project_id": project_id,
                                "sprint_id": sprint["id"],
                                "agent_id": self.agent_id,
                            },
                        )
                await self._transition_if_current(
                    project_id,
                    state,
                    "retrospective_done",
                    action_upper,
                )
                return

            if action_upper == "START_KPI" and state == "KPI_PERSISTENCE":
                result = await self.execute_tool(
                    "kpi.compute_project",
                    {"project_id": project_id},
                )
                if isinstance(result, dict) and result.get("error"):
                    return
                await self._transition_if_current(
                    project_id,
                    state,
                    "kpi_saved",
                    action_upper,
                )
        except Exception:
            logger.warning(
                "executive_directive_progress_recovery_failed",
                extra=self._log_extra(project_id=project_id, action=action_upper),
                exc_info=True,
            )

    async def _transition_if_current(
        self,
        project_id: str,
        expected_state: str,
        event: str,
        action: str,
    ) -> None:
        result = await self.execute_tool(
            "project.transition",
            {
                "project_id": project_id,
                "event": event,
                "actor_id": self.agent_id,
                "context": {"directive": action, "expected_state": expected_state},
            },
        )
        if isinstance(result, dict) and result.get("error"):
            logger.warning(
                "executive_project_transition_failed",
                extra=self._log_extra(
                    project_id=project_id,
                    event=event,
                    error=result.get("error"),
                ),
            )

    def _build_workflow_tool_definitions(self) -> list[ToolDefinition]:
        """Build ToolDefinition objects from the dynamic runtime catalog."""
        if hasattr(self, "available_tool_definitions"):
            return self.available_tool_definitions()
        return tool_definitions_for_agent(role=AgentRole.EXECUTIVE, team_id="exec_coo")

        # Historical static definitions kept below as a readable reference for
        # detailed argument shapes. Runtime exposure now comes from the manifest
        # and policy so newly added tools do not require editing this method.
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
        session_id = str(envelope.payload.get("session_id") or uuid4())
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
        if self._review_storage is not None and parent_envelope.project_id:
            try:
                active_statuses = {
                    "IN_PROGRESS",
                    "PENDING",
                    "OPEN",
                    "STARTED",
                }
                sessions = await self._review_storage.list_review_sessions(
                    UUID(parent_envelope.project_id),
                    limit=100,
                )
                active = next((
                    session
                    for session in sessions
                    if str(session.get("document_id")) == str(document_id)
                    and str(session.get("status") or "").upper() in active_statuses
                ), None)
                if active is not None:
                    active_session_id = str(active.get("id"))
                    if active_session_id in self._review_sessions:
                        logger.info(
                            "executive_review_fanout_duplicate_suppressed",
                            extra=self._log_extra(document_id=document_id),
                        )
                        return
                    summary = await self._rehydrate_review_session(
                        active_session_id,
                        parent_envelope,
                    )
                    if summary is None:
                        logger.error(
                            "executive_review_fanout_rehydrate_failed",
                            extra=self._log_extra(
                                session_id=active_session_id,
                                document_id=document_id,
                            ),
                        )
                        return
                    await self._publish_review_requests(
                        session_id=active_session_id,
                        document_id=document_id,
                        doc_type=doc_type,
                        parent_envelope=parent_envelope,
                        reviewer_teams=[str(value) for value in active.get("reviewer_ids") or []],
                    )
                    logger.info(
                        "executive_review_fanout_rehydrated",
                        extra=self._log_extra(
                            session_id=active_session_id,
                            document_id=document_id,
                        ),
                    )
                    return
            except Exception:
                logger.warning("executive_review_duplicate_check_failed", exc_info=True)

        summary = ReviewSummary(
            session_id=UUID(session_id) if len(session_id) == 36 else uuid4(),
            project_id=parent_envelope.project_id or "",
            document_id=UUID(document_id) if len(document_id) == 36 else uuid4(),
            doc_type=doc_type,  # type: ignore[arg-type]
            reviewer_count=len(self._reviewer_teams),
        )
        self._review_sessions[session_id] = summary
        self._review_parents[session_id] = parent_envelope
        if not await self._persist_review_session(summary):
            # Do not publish review requests when the durable parent session
            # could not be created.  Otherwise valid responses would arrive
            # with no database row to attach to after a restart.
            self._review_sessions.pop(session_id, None)
            self._review_parents.pop(session_id, None)
            logger.error(
                "executive_review_fanout_aborted_persistence_failure",
                extra=self._log_extra(session_id=session_id, document_id=document_id),
            )
            return

        await self._update_document_status(summary, "IN_REVIEW")

        await self._publish_review_requests(
            session_id=session_id,
            document_id=document_id,
            doc_type=doc_type,
            parent_envelope=parent_envelope,
        )

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
        session_id = str(envelope.payload.get("session_id") or "")
        summary = self._review_sessions.get(session_id)
        if summary is None and session_id:
            summary = await self._rehydrate_review_session(session_id)
        if summary is None:
            logger.warning(
                "executive_review_response_for_unknown_session",
                extra=self._log_extra(session_id=session_id),
            )
            return

        # A revision can supersede an in-flight review of the prior document.
        # Ignore late responses from that stale session so they cannot advance
        # the workflow for the newer revision.
        if self._tool_client is not None:
            try:
                doc_type = getattr(summary.doc_type, "value", str(summary.doc_type))
                latest = await self.execute_tool(
                    "document.get_latest",
                    {"project_id": summary.project_id, "doc_type": doc_type},
                )
                if (
                    isinstance(latest, dict)
                    and latest.get("id")
                    and str(latest.get("id")) != str(summary.document_id)
                ):
                    summary.completed_at = datetime.now(tz=UTC)
                    await self._persist_review_status(
                        summary,
                        "SUPERSEDED",
                        completed_at=summary.completed_at,
                    )
                    self._review_sessions.pop(session_id, None)
                    self._review_parents.pop(session_id, None)
                    logger.info(
                        "executive_review_response_ignored_stale_document",
                        extra=self._log_extra(
                            session_id=session_id,
                            document_id=str(summary.document_id),
                            latest_document_id=str(latest.get("id")),
                        ),
                    )
                    return
            except Exception:
                logger.warning(
                    "executive_review_stale_document_check_failed",
                    extra=self._log_extra(session_id=session_id),
                    exc_info=True,
                )

        # Parse reviewer response
        reviewer_id = envelope.sender_id
        if reviewer_id in summary.responses:
            logger.info(
                "executive_review_response_duplicate_suppressed",
                extra=self._log_extra(session_id=session_id, reviewer=reviewer_id),
            )
            return
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
        await self._persist_review_comment(summary, response)

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

        # ``_emit_event`` notifies the CEO stream, but the orchestrator API is
        # the sole writer of project state and does not consume that stream.
        # Persist the security transition through the governed tool boundary
        # as part of the veto handoff; otherwise a veto could be durable only
        # in review metadata while the project remains in CDR_REVIEW.
        if project_id:
            transition = await self.execute_tool(
                "project.transition",
                {
                    "project_id": project_id,
                    "event": "cso_veto",
                    "actor_id": self.agent_id,
                    "context": {
                        "session_id": session_id,
                        "document_id": document_id,
                        "reviewer_id": response.reviewer_id,
                        "veto_comments": [c.body for c in response.comments if c.veto],
                    },
                },
            )
            if isinstance(transition, dict) and transition.get("error"):
                logger.error(
                    "executive_cso_veto_transition_failed",
                    extra=self._log_extra(
                        project_id=project_id,
                        session_id=session_id,
                        error=transition.get("error"),
                    ),
                )

        # Clean up session
        if summary is not None:
            summary.completed_at = datetime.now(tz=UTC)
            await self._persist_review_status(
                summary,
                "VETOED",
                completed_at=summary.completed_at,
            )
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

        status_by_verdict = {
            ReviewVerdict.APPROVED: "COMPLETED",
            ReviewVerdict.APPROVED_WITH_COMMENTS: "COMPLETED",
            ReviewVerdict.NEEDS_REVISION: "NEEDS_REVISION",
            ReviewVerdict.REJECTED: "REJECTED",
        }
        await self._persist_review_status(
            summary,
            status_by_verdict[overall],
            completed_at=summary.completed_at,
        )

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
            # The API is the sole workflow-state writer. Complete the
            # document-review handoff here rather than relying on a second
            # free-form LLM turn to notice the review result.
            doc_type_value = getattr(summary.doc_type, "value", str(summary.doc_type)).upper()
            transition_event = {
                "PDR": "all_reviews_in",
                "CDR": "cdr_presented",
            }.get(doc_type_value)
            if transition_event:
                result = await self.execute_tool(
                    "project.transition",
                    {
                        "project_id": project_id,
                        "event": transition_event,
                        "actor_id": self.agent_id,
                        "context": {
                            "session_id": session_id,
                            "document_id": document_id,
                            "aggregate_verdict": overall.value,
                        },
                    },
                )
                if isinstance(result, dict) and result.get("error"):
                    logger.warning(
                        "executive_review_transition_failed",
                        extra=self._log_extra(
                            session_id=session_id,
                            project_id=project_id,
                            event=transition_event,
                            error=result.get("error"),
                        ),
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

    async def _next_revision_count(self, summary: ReviewSummary) -> tuple[str, int]:
        """Return the next lineage-wide revision number, including durable history."""
        doc_type = getattr(summary.doc_type, "value", str(summary.doc_type)).upper()
        lineage_key = f"{summary.project_id}:{doc_type}"
        memory_count = self._revision_counts.get(lineage_key, 0)
        durable_count = 0
        if self._review_storage is not None:
            try:
                sessions = await self._review_storage.list_review_sessions(
                    UUID(summary.project_id),
                    limit=1000,
                )
                durable_count = sum(
                    1
                    for session in sessions
                    if str(session.get("session_type") or "").upper() == doc_type
                    and str(session.get("status") or "").upper() == "NEEDS_REVISION"
                )
            except Exception:
                logger.warning(
                    "executive_revision_history_read_failed",
                    extra=self._log_extra(project_id=summary.project_id, doc_type=doc_type),
                    exc_info=True,
                )
        # The current session is persisted as NEEDS_REVISION before this
        # method runs, so durable_count already represents this request.
        revision_count = max(memory_count + 1, durable_count)
        self._revision_counts[lineage_key] = revision_count
        return lineage_key, revision_count

    async def _request_revision(
        self,
        summary: ReviewSummary,
        parent_envelope: MessageEnvelope | None,
    ) -> None:
        """Request document revision from the originating team."""
        document_id = str(summary.document_id)
        lineage_key, rev_count = await self._next_revision_count(summary)

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
            # Keep the exhausted lineage terminal in memory. Dropping the key
            # would let another late response restart the loop at revision 1.
            self._revision_counts[lineage_key] = rev_count
            return

        # Persist the revision loop in the workflow controller as well as in
        # the legacy DOCUMENT_REVISION notification.  The controller emits a
        # fresh stage directive, so a revision cannot depend on the CEO
        # choosing a free-form transition tool (and the originating team does
        # not need to implement an otherwise unhandled message type).
        doc_type_value = getattr(summary.doc_type, "value", str(summary.doc_type)).upper()
        revision_event = {
            "PDR": "pdr_revision_requested",
            "CDR": "cdr_revision_requested",
        }.get(doc_type_value)
        if revision_event and self._tool_client is not None:
            reason_parts = [
                str(comment.body).strip()
                for comment in summary.comments
                if str(comment.body).strip()
            ]
            transition = await self.execute_tool(
                "project.transition",
                {
                    "project_id": summary.project_id,
                    "event": revision_event,
                    "actor_id": self.agent_id,
                    "context": {
                        "session_id": summary.session_id,
                        "document_id": document_id,
                        "revision_requested": True,
                        "revision_number": rev_count,
                        "reason": "\n\n".join(reason_parts)[:4000],
                    },
                },
            )
            if isinstance(transition, dict) and transition.get("error"):
                logger.warning(
                    "executive_revision_transition_failed",
                    extra=self._log_extra(
                        project_id=summary.project_id,
                        document_id=document_id,
                        event=revision_event,
                        error=transition.get("error"),
                    ),
                )

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
        if self._review_storage is not None:
            try:
                await self._review_storage.update_review_session(
                    summary.session_id,
                    timeout_count=summary.timeout_count,
                )
            except Exception:
                logger.warning("executive_review_timeout_persist_failed", exc_info=True)
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
            self._review_parents.pop(session_id, None)
            self._review_sessions.pop(session_id, None)

            summary.completed_at = datetime.now(tz=UTC)
            await self._persist_review_status(
                summary,
                "CIRCUIT_OPEN",
                completed_at=summary.completed_at,
            )

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
