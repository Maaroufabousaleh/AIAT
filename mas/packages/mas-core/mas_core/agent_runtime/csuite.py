"""CSuiteAgent — C-Suite advisory agent (CFO, CIO, CHRM, CSO, CTO).

Extends ``AdminAgent`` with review/advisory capabilities:

* **Review handling**: responds to ``REVIEW_REQUEST`` with structured
  ``ReviewComment`` lists and a ``ReviewVerdict``.
* **CSO specialization**: can set ``veto=True`` on BLOCKER comments,
  halting the document lifecycle via the COO's veto handler.
* **CTO specialization**: sprint planning (uses ``KPISnapshot`` +
  ``AgentProfile.correction_factor``), issue decomposition (including
  INFRA issues), DevOps coordination, historical KPI queries.
* **Advisory**: responds to ``QUERY`` messages from the orchestrator
  or executive with domain-specific analysis.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID, uuid4

from ..protocols.domain import (
    ReviewComment,
    ReviewResponse,
    Issue,
    Sprint,
    KPISnapshot,
)
from ..protocols.envelope import MessageEnvelope
from ..protocols.enums import (
    AgentRole,
    IssueType,
    MessageType,
    ReviewSeverity,
    ReviewVerdict,
    SprintStatus,
)
from .admin import AdminAgent
from .config import AgentConfig

logger = logging.getLogger(__name__)


class CSuiteAgent(AdminAgent):
    """C-Suite advisory agent with review and domain specialization.

    The ``specialization`` parameter determines the agent's domain focus
    and unlocks role-specific behaviours:

    - ``"CSO"``: security review, veto power.
    - ``"CTO"``: sprint planning, DevOps coordination, KPI management.
    - ``"CFO"``: financial review, budget analysis.
    - ``"CIO"``: technology viability assessment.
    - ``"CHRM"``: resource availability and HR concerns.

    Parameters
    ----------
    config : AgentConfig
        Should have ``agent_role == AgentRole.C_SUITE``.
    specialization : str
        C-Suite role label. Determines which domain capabilities are activated.
    """

    def __init__(
        self,
        config: AgentConfig,
        storage: Any | None = None,
        *,
        specialization: str = "GENERIC",
        tool_client: Any | None = None,
        system_prompt: str | None = None,
        kpi_store: Any | None = None,
        **kwargs: Any,
    ) -> None:
        # Must set _specialization BEFORE super().__init__ because
        # AdminAgent.__init__ calls _default_system_prompt() which needs it.
        self._specialization = specialization.upper()
        super().__init__(
            config, storage,
            tool_client=tool_client,
            system_prompt=system_prompt,
            **kwargs,
        )
        self._kpi_store = kpi_store  # optional storage for KPI data

        # CTO-specific state
        self._active_sprints: dict[str, Sprint] = {}  # sprint_id -> Sprint
        self._kpi_history: list[KPISnapshot] = []

    def _default_system_prompt(self) -> str:
        spec_context = {
            "CSO": "security governance. You can VETO documents with BLOCKER severity.",
            "CTO": "technical architecture, sprint planning, DevOps, and KPI management.",
            "CFO": "financial analysis, budget review, and cost estimation.",
            "CIO": "technology assessment, system architecture viability.",
            "CHRM": "human resources, team capacity, and resource planning.",
        }
        focus = spec_context.get(self._specialization, "advisory review and analysis.")
        return (
            f"You are {self.agent_id}, the {self._specialization} "
            f"(C-Suite) for team {self.team_id}. "
            f"Your focus is {focus} "
            "Provide thorough, structured review comments."
        )

    # ------------------------------------------------------------------
    # Extended message routing
    # ------------------------------------------------------------------

    def _get_handler(self, msg_type: MessageType) -> Any:
        csuite_handlers = {
            MessageType.REVIEW_REQUEST: self._handle_review_request,
            MessageType.QUERY: self._handle_query,
            MessageType.SPRINT_PLAN: self._handle_sprint_plan,
            MessageType.SPRINT_REPORT: self._handle_sprint_report,
            MessageType.INFRA_READY: self._handle_infra_ready,
        }
        return csuite_handlers.get(msg_type) or super()._get_handler(msg_type)

    # ------------------------------------------------------------------
    # Review handling
    # ------------------------------------------------------------------

    async def _handle_review_request(self, envelope: MessageEnvelope) -> None:
        """Review a document and produce structured ReviewResponse.

        Uses the think() loop to analyze the document and generate comments.
        For CSO: may include veto=True on BLOCKER findings.
        """
        session_id = envelope.payload.get("session_id", "")
        document_id = envelope.payload.get("document_id", "")
        doc_type = envelope.payload.get("doc_type", "")
        document_payload = envelope.payload.get("document_payload", {})

        # Build review prompt
        messages = [
            {"role": "system", "content": self._system_prompt},
            {
                "role": "user",
                "content": self._build_review_prompt(doc_type, document_payload),
            },
        ]

        result_messages = await self.think(messages=messages)
        review_text = self._extract_result(result_messages)

        # Parse review into structured comments
        comments, verdict, has_veto = self._parse_review_output(review_text)

        # Build response envelope
        response_payload: dict[str, Any] = {
            "session_id": session_id,
            "document_id": document_id,
            "reviewer_role": self._specialization,
            "verdict": verdict.value,
            "veto": has_veto,
            "comments": [
                {
                    "section": c.section,
                    "severity": c.severity.value,
                    "body": c.body,
                    "suggested_change": c.suggested_change,
                    "veto": c.veto,
                }
                for c in comments
            ],
            "review_text": review_text,
        }

        reply = envelope.reply(
            msg_type=MessageType.REVIEW_RESPONSE,
            sender_id=self.agent_id,
            sender_role=self.role,
            sender_team=self.team_id,
            payload=response_payload,
        )
        await self.publish(reply)

        logger.info(
            "csuite_review_submitted",
            extra=self._log_extra(
                session_id=session_id,
                verdict=verdict.value,
                comment_count=len(comments),
                veto=has_veto,
                specialization=self._specialization,
            ),
        )

    def _build_review_prompt(
        self,
        doc_type: str,
        document_payload: dict[str, Any],
    ) -> str:
        """Build a review prompt based on specialization."""
        focus_areas = {
            "CSO": "security vulnerabilities, compliance, data protection, access control",
            "CTO": "technical feasibility, architecture quality, scalability, maintainability",
            "CFO": "budget impact, cost-effectiveness, financial risks, ROI",
            "CIO": "technology stack viability, integration risks, innovation alignment",
            "CHRM": "resource availability, skill gaps, team capacity, hiring needs",
        }
        focus = focus_areas.get(self._specialization, "general quality and completeness")

        task_desc = document_payload.get("task", "")
        summary = document_payload.get("summary", "")
        content = task_desc or summary or str(document_payload)[:2000]

        parts = [
            f"## Review Request — {doc_type}",
            f"\n## Document Content\n{content}",
            f"\n## Your Focus Areas\n{focus}",
            "\n## Instructions",
            "Analyze this document from your domain perspective. For each finding:",
            "1. Identify the section (if applicable)",
            "2. Assign a severity: INFO, SUGGESTION, MINOR, MAJOR, WARNING, CONCERN, or BLOCKER",
            "3. Provide clear feedback",
            "4. Suggest specific changes where appropriate",
        ]

        if self._specialization == "CSO":
            parts.append(
                "\nIMPORTANT: If you find a critical security issue, you may VETO "
                "the document by marking severity=BLOCKER and veto=true. "
                "This will halt the project until the issue is resolved."
            )

        parts.append(
            "\nReturn your verdict as one of: APPROVED, APPROVED_WITH_COMMENTS, "
            "NEEDS_REVISION, or REJECTED."
        )
        return "\n".join(parts)

    def _parse_review_output(
        self,
        review_text: str,
    ) -> tuple[list[ReviewComment], ReviewVerdict, bool]:
        """Parse LLM review output into structured comments.

        For now, creates a single comment from the full review text.
        More sophisticated parsing can be added when LLM output is structured.
        """
        # Default to approved with comments if there's review text
        verdict = ReviewVerdict.APPROVED_WITH_COMMENTS if review_text else ReviewVerdict.APPROVED
        has_veto = False

        # Try to detect verdict from text
        text_lower = review_text.lower()
        if "rejected" in text_lower:
            verdict = ReviewVerdict.REJECTED
        elif "needs_revision" in text_lower or "needs revision" in text_lower:
            verdict = ReviewVerdict.NEEDS_REVISION
        elif "approved" in text_lower and "comment" not in text_lower:
            verdict = ReviewVerdict.APPROVED

        # Detect CSO veto
        severity = ReviewSeverity.INFO
        if self._specialization == "CSO" and "veto" in text_lower:
            has_veto = True
            severity = ReviewSeverity.BLOCKER
            verdict = ReviewVerdict.REJECTED

        comments = []
        if review_text:
            comment = ReviewComment(
                reviewer_id=self.agent_id,
                reviewer_role=self._specialization,
                reviewer_team=self.team_id,
                severity=severity,
                body=review_text,
                veto=has_veto,
            )
            comments.append(comment)

        return comments, verdict, has_veto

    @staticmethod
    def _extract_result(messages: list[dict[str, Any]]) -> str:
        """Extract the final assistant response text."""
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                content = msg.get("content")
                if content:
                    return content
        return ""

    # ------------------------------------------------------------------
    # Query handling (advisory)
    # ------------------------------------------------------------------

    async def _handle_query(self, envelope: MessageEnvelope) -> None:
        """Respond to queries with domain-specific analysis."""
        query_text = envelope.payload.get("query", "")
        context = envelope.payload.get("context", "")

        messages = [
            {"role": "system", "content": self._system_prompt},
            {
                "role": "user",
                "content": f"## Query\n{query_text}\n\n## Context\n{context}",
            },
        ]

        result_messages = await self.think(messages=messages)
        response_text = self._extract_result(result_messages)

        reply = envelope.reply(
            msg_type=MessageType.RESPONSE,
            sender_id=self.agent_id,
            sender_role=self.role,
            sender_team=self.team_id,
            payload={
                "response": response_text,
                "specialization": self._specialization,
            },
        )
        await self.publish(reply)

    # ------------------------------------------------------------------
    # CTO: Sprint planning
    # ------------------------------------------------------------------

    async def _handle_sprint_plan(self, envelope: MessageEnvelope) -> None:
        """CTO: receives sprint plan or creates one from requirements."""
        if self._specialization != "CTO":
            logger.warning(
                "csuite_sprint_plan_ignored_non_cto",
                extra=self._log_extra(specialization=self._specialization),
            )
            return

        sprint_data = envelope.payload.get("sprint", {})
        requirements = envelope.payload.get("requirements", [])

        # Use think() to decompose requirements into issues
        messages = [
            {"role": "system", "content": self._system_prompt},
            {
                "role": "user",
                "content": (
                    "## Sprint Planning\n"
                    f"Decompose these requirements into actionable sprint issues.\n\n"
                    f"## Requirements\n{requirements}\n\n"
                    "Categorize each as: feature, test, qa, docs, infra, or bugfix.\n"
                    "Estimate hours and assign priorities."
                ),
            },
        ]
        result_messages = await self.think(messages=messages)
        plan_text = self._extract_result(result_messages)

        # Publish sprint plan to relevant departments
        plan_env = MessageEnvelope(
            msg_type=MessageType.SPRINT_PLAN,
            sender_id=self.agent_id,
            sender_role=self.role,
            sender_team=self.team_id,
            recipient_team=envelope.sender_team,
            project_id=envelope.project_id,
            correlation_id=envelope.correlation_id,
            parent_id=envelope.message_id,
            payload={
                "sprint_plan": plan_text,
                "sprint_data": sprint_data,
                "planned_by": self.agent_id,
            },
        )
        await self.publish(plan_env)

        logger.info(
            "cto_sprint_plan_created",
            extra=self._log_extra(sprint=sprint_data.get("name", "unknown")),
        )

    # ------------------------------------------------------------------
    # CTO: Sprint reports
    # ------------------------------------------------------------------

    async def _handle_sprint_report(self, envelope: MessageEnvelope) -> None:
        """CTO: process sprint report and compute KPIs."""
        if self._specialization != "CTO":
            # Non-CTO C-Suite agents forward sprint reports upstream
            await super()._handle_admin_reply(envelope)
            return

        sprint_number = envelope.payload.get("sprint_number")
        completed_points = envelope.payload.get("completed_story_points", 0)
        total_points = envelope.payload.get("total_story_points", 0)

        logger.info(
            "cto_sprint_report_received",
            extra=self._log_extra(
                sprint_number=sprint_number,
                velocity=f"{completed_points}/{total_points}",
            ),
        )

        # Store KPI snapshot
        velocity_val = completed_points / max(total_points, 1)
        kpi = KPISnapshot(
            project_id=envelope.project_id or "",
            agent_id=self.agent_id,
            team_id=self.team_id,
            velocity=velocity_val,
            task_completion_rate=completed_points / max(total_points, 1),
            context={
                "sprint_number": sprint_number,
                "completed_story_points": completed_points,
                "total_story_points": total_points,
            },
        )
        self._kpi_history.append(kpi)

        # Publish KPI upstream
        kpi_env = MessageEnvelope(
            msg_type=MessageType.SPRINT_REPORT,
            sender_id=self.agent_id,
            sender_role=self.role,
            sender_team=self.team_id,
            recipient_team=envelope.sender_team,
            project_id=envelope.project_id,
            correlation_id=envelope.correlation_id,
            parent_id=envelope.message_id,
            payload={
                "kpi": kpi.model_dump(mode="json"),
                "sprint_number": sprint_number,
                "computed_by": self.agent_id,
            },
        )
        await self.publish(kpi_env)

    # ------------------------------------------------------------------
    # CTO: Infrastructure coordination
    # ------------------------------------------------------------------

    async def _handle_infra_ready(self, envelope: MessageEnvelope) -> None:
        """CTO: DevOps PM signals infrastructure is provisioned."""
        if self._specialization != "CTO":
            logger.info(
                "csuite_infra_ready_ignored",
                extra=self._log_extra(specialization=self._specialization),
            )
            return

        sprint_id = envelope.payload.get("sprint_id")
        logger.info(
            "cto_infra_ready",
            extra=self._log_extra(sprint_id=sprint_id),
        )

        # Mark sprint as unblocked and forward notification
        if sprint_id and sprint_id in self._active_sprints:
            self._active_sprints[sprint_id].infra_ready = True

        ack = envelope.reply(
            msg_type=MessageType.ACK,
            sender_id=self.agent_id,
            sender_role=self.role,
            sender_team=self.team_id,
            payload={
                "acknowledged": "infra_ready",
                "sprint_id": sprint_id,
            },
        )
        await self.publish(ack)

    # ------------------------------------------------------------------
    # CTO: Issue decomposition
    # ------------------------------------------------------------------

    async def decompose_issues(
        self,
        *,
        requirements: list[dict[str, Any]],
        project_id: str,
        sprint_id: str | None = None,
    ) -> list[Issue]:
        """Decompose a requirements list into typed issues.

        Returns a list of ``Issue`` objects ready for ISSUE_ASSIGN dispatch.
        """
        _sprint_uuid: UUID | None = UUID(sprint_id) if sprint_id else None
        issues: list[Issue] = []
        for req in requirements:
            issue_type_str = req.get("type", "feature")
            try:
                issue_type = IssueType(issue_type_str)
            except ValueError:
                issue_type = IssueType.FEATURE

            issue = Issue(
                project_id=project_id,
                sprint_id=_sprint_uuid,
                title=req.get("title", "Untitled issue"),
                description=req.get("description"),
                issue_type=issue_type,
                estimated_hours=req.get("estimated_hours"),
                story_points=req.get("story_points"),
                assigned_team=req.get("assigned_team"),
            )
            issues.append(issue)

        return issues

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def specialization(self) -> str:
        return self._specialization

    @property
    def is_cso(self) -> bool:
        return self._specialization == "CSO"

    @property
    def is_cto(self) -> bool:
        return self._specialization == "CTO"

    @property
    def kpi_history(self) -> list[KPISnapshot]:
        return list(self._kpi_history)
