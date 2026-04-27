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
from uuid import UUID

from ..llm_gateway.models import ToolDefinition, ToolFunction
from ..protocols.domain import (
    Issue,
    KPISnapshot,
    ReviewComment,
    Sprint,
)
from ..protocols.enums import (
    IssueType,
    MessageType,
    ReviewSeverity,
    ReviewVerdict,
)
from ..protocols.envelope import MessageEnvelope
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
            config,
            storage,
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
            "CEO": (
                "executive orchestration. You are the top-level executive and primary middleware "
                "between the human operator and the entire MAS. You coordinate all teams, supervise "
                "projects, steer workflows, aggregate status, send alerts, and act as the human's "
                "operational copilot. Your authority spans orchestration and executive decisions "
                "(Layer 1). Privileged infrastructure operations (Layer 2) are separately gated "
                "and require explicit human approval. Always be clear, decisive, and structured."
            ),
            "COO": (
                "operational coordination. You are responsible for project execution oversight, "
                "cross-team resource allocation, review orchestration, sprint health monitoring, "
                "workflow quality enforcement, and escalation management. You coordinate between "
                "the CEO and department teams. You conduct document review fan-outs, aggregate "
                "results, manage revision loops, and ensure projects progress through gates. "
                "You track KPIs, flag blockers, and initiate corrective actions. Your responses "
                "are operational, data-driven, and actionable."
            ),
            "CFO": (
                "financial governance. You provide rigorous financial analysis, cost estimation, "
                "budget review, ROI calculation, and fiscal risk assessment. For each project or "
                "document review: identify cost drivers, validate estimates against historical data "
                "using agent correction factors, flag budget overruns or unsustainable burn rates, "
                "and recommend cost optimization strategies. You enforce spend limits, generate "
                "financial summaries, and produce KPI snapshots. Refuse to APPROVE any financial "
                "document that lacks cost justification or violates budget policy."
            ),
            "CTO": (
                "technical architecture and engineering leadership. You review technical designs "
                "for architectural soundness, scalability, maintainability, security posture, "
                "technology stack choices, and integration complexity. You lead sprint planning: "
                "decompose requirements into FEATURE, TEST, QA, INFRA, and DOCS issues with "
                "accurate story point estimates (calibrated by agent correction factors). You "
                "coordinate DevOps and SRE teams for infrastructure provisioning and reliability. "
                "You track technical KPIs (velocity, defect density, test coverage, deployment "
                "frequency). VETO technically unsafe or unmaintainable designs with BLOCKER severity."
            ),
            "CSO": (
                "security governance and threat management. You are responsible for security "
                "review of all documents, architectures, and code changes. Evaluate threat models, "
                "attack surfaces, authentication and authorization designs, encryption standards, "
                "dependency vulnerabilities, and compliance requirements (SOC2, GDPR, etc.). "
                "You have VETO power: use BLOCKER severity to halt any project with unacceptable "
                "security risk. Your reviews must be specific, reference known vulnerabilities "
                "or standards violations, and provide remediation guidance. No security concern "
                "is too minor to document. Treat every unreviewed assumption as a potential threat."
            ),
            "CIO": (
                "information technology governance and enterprise architecture. You assess technology "
                "stack viability, system integration complexity, data architecture soundness, IT "
                "policy compliance, vendor risk, and digital transformation alignment. You review "
                "for data sovereignty, API governance, service mesh design, observability strategy, "
                "and platform consistency. You advise on build-vs-buy decisions, technology debt, "
                "and system lifecycle management. Your recommendations help the MAS evolve toward "
                "a coherent, maintainable, and observable enterprise platform."
            ),
            "CHRM": (
                "human resources, workforce planning, and organizational capacity management. "
                "You assess team capacity, skill gaps, resource availability, and workload "
                "distribution. For each project, evaluate whether the assigned team has the "
                "required skills, bandwidth, and motivation to succeed. Flag overallocation, "
                "under-resourced teams, or unrealistic sprint commitments. You manage agent "
                "profiles, track performance correction factors, and recommend hiring or "
                "retraining when capability gaps are identified. You ensure psychological "
                "safety, healthy team dynamics, and sustainable work practices across all teams."
            ),
        }
        focus = spec_context.get(self._specialization, "advisory review and analysis.")
        return (
            f"You are {self.agent_id}, the {self._specialization} "
            f"(C-Suite executive) for team {self.team_id}.\n\n"
            f"## Your Domain\n{focus}\n\n"
            "## Operating Principles\n"
            "1. Be decisive and structured in all responses.\n"
            "2. Ground recommendations in data and evidence.\n"
            "3. Escalate blockers immediately — do not let projects stall.\n"
            "4. Use the available tools to take action, not just advise.\n"
            "5. Keep context concise — other agents and the human operator read your output.\n"
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
            MessageType.SYSTEM_EVENT: self._handle_system_event,
            MessageType.DIRECTIVE: self._handle_directive,
        }
        return csuite_handlers.get(msg_type) or super()._get_handler(msg_type)

    # ------------------------------------------------------------------
    # Directive dispatch — action-driven think() loop
    # ------------------------------------------------------------------

    async def _handle_directive(self, envelope: MessageEnvelope) -> None:
        """Dispatch directive actions to the think() loop for CEO/COO.

        Actions that require LLM reasoning trigger think() with appropriate
        tools. Passive actions (RESUME for non-CEO roles) fall through to
        the parent AdminAgent broadcast.
        """
        action = envelope.payload.get("action", "")
        project_id = envelope.project_id or envelope.payload.get("project_id", "")

        logger.info(
            "csuite_directive_%s",
            action.lower(),
            extra=self._log_extra(action=action, specialization=self._specialization),
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
                        "csuite_directive_skip_terminal_project",
                        extra=self._log_extra(
                            action=action,
                            project_id=project_id,
                            state=project.get("state"),
                        ),
                    )
                    return
            except Exception:
                pass  # If we can't check, proceed normally

        # Actions that trigger LLM work
        actionable = {
            "START_FEASIBILITY",
            "START_PDR",
            "START_CDR",
            "START_RR",
            "RESUME",
            # COO-specific
            "COORDINATE_REVIEW",
            "ALLOCATE_RESOURCES",
            "CHECK_SPRINT_HEALTH",
            # CFO-specific
            "COST_REVIEW",
            "BUDGET_CHECK",
            "KPI_REPORT",
            # CTO-specific
            "ARCHITECTURE_REVIEW",
            "SPRINT_DECOMPOSE",
            # CSO-specific
            "SECURITY_AUDIT",
            "THREAT_MODEL",
            # CIO-specific
            "TECH_ASSESSMENT",
            "IT_GOVERNANCE_REVIEW",
            # CHRM-specific
            "CAPACITY_CHECK",
            "WORKFORCE_REVIEW",
        }
        if action.upper() in actionable:
            await self._directive_think(envelope, action)
        else:
            # Unknown actions — broadcast to team (default AdminAgent behaviour)
            await super()._handle_directive(envelope)

    async def _directive_think(self, envelope: MessageEnvelope, action: str) -> None:
        """Run think() in response to a directive action.

        Builds a task-oriented prompt from the directive payload, then runs
        the LLM loop with the agent's workflow tools so it can call
        project.transition / review.aggregate / etc.
        """
        project_id = envelope.project_id or envelope.payload.get("project_id", "unknown")
        project_name = envelope.payload.get("project_name", "")
        description = envelope.payload.get("description", "")
        state = envelope.payload.get("state", "")
        context_str = envelope.payload.get("context", "")

        action_instructions: dict[str, str] = {
            "START_FEASIBILITY": (
                "You have been tasked to perform a **feasibility assessment** for a new project.\n\n"
                "## Your Task\n"
                "1. Analyse the project description for technical feasibility, resource requirements, "
                "risks, and strategic alignment.\n"
                "2. Produce a concise feasibility recommendation (APPROVED, NEEDS_REVISION, or REJECTED).\n"
                "3. Call `review.aggregate` with your aggregate verdict to advance the project "
                "from FEASIBILITY_CHECK to FEASIBILITY_REPORT.\n\n"
                "Use the `project.status` tool first to confirm the project is in FEASIBILITY_CHECK, "
                "then call `review.aggregate` with `project_id`, `verdict`, and `actor_id`."
            ),
            "START_PDR": (
                "The project has been approved for a **Preliminary Design Review (PDR)**.\n\n"
                "Your task: coordinate with the CTO to produce the PDR document, then call "
                "`review.aggregate` once all reviewers respond."
            ),
            "START_CDR": (
                "The project has reached **Critical Design Review (CDR)**.\n\n"
                "Your task: verify CDR document is ready, fan-out review requests to C-Suite, "
                "then call `review.aggregate` when all reviews are in."
            ),
            "RESUME": (
                f"The system is resuming from a restart. Project is currently in state: **{state}**.\n\n"
                "Check the current project status with `project.status`, then determine and "
                "execute the next action required based on the current state.\n"
                "If in FEASIBILITY_CHECK: run feasibility and call `review.aggregate`.\n"
                "If in PDR_REVIEW or CDR_REVIEW: check if all reviews are in and call "
                "`review.aggregate`.\n"
            ),
            # COO
            "COORDINATE_REVIEW": (
                "Coordinate a full C-Suite review of the current project document.\n\n"
                "1. Use `project.status` to get the current state and pending review IDs.\n"
                "2. Fan out REVIEW_REQUEST messages to all assigned reviewers.\n"
                "3. Wait for all responses, then call `review.aggregate` to advance the state.\n"
                "4. If any reviewer times out (>120s), count it as APPROVED with a note.\n"
                "5. If CSO VETOs, call `project.transition` with event `cso_veto`."
            ),
            "ALLOCATE_RESOURCES": (
                "Review team capacity and allocate resources for the current project.\n\n"
                "1. List all teams involved in the project.\n"
                "2. Assess current workload vs. capacity for each team.\n"
                "3. Identify any overallocated or under-resourced teams.\n"
                "4. Recommend resource adjustments or hiring if needed.\n"
                "5. Document your allocation decision in the project context."
            ),
            "CHECK_SPRINT_HEALTH": (
                "Perform a sprint health assessment for the current project.\n\n"
                "1. Use `project.status` to get current sprint data.\n"
                "2. Calculate velocity, completion rate, and blockers.\n"
                "3. Identify at-risk issues or missed milestones.\n"
                "4. Send targeted QUERY messages to relevant PMs for status.\n"
                "5. Publish a SPRINT_REPORT with your health assessment."
            ),
            # CFO
            "COST_REVIEW": (
                "Perform a detailed cost review of the current project.\n\n"
                "1. Use `project.status` to gather budget data and estimates.\n"
                "2. Compare actual vs. planned spend.\n"
                "3. Calculate burn rate and projected cost at completion.\n"
                "4. Identify cost overruns and their root causes.\n"
                "5. Recommend cost optimization measures.\n"
                "6. Submit a REVIEW_RESPONSE with financial verdict (APPROVED/NEEDS_REVISION/REJECTED)."
            ),
            "BUDGET_CHECK": (
                "Validate that the project's financial commitments are within budget policy.\n\n"
                "1. Check total committed budget vs. approved limit.\n"
                "2. Review any unplanned expenses or scope creep costs.\n"
                "3. Flag any line items that violate financial policy.\n"
                "4. Approve or reject the budget with a clear rationale."
            ),
            "KPI_REPORT": (
                "Generate a comprehensive KPI report for the project.\n\n"
                "1. Collect velocity, quality, cost, and timeline metrics.\n"
                "2. Compare against targets and historical baselines.\n"
                "3. Identify trends (improving, stable, declining) for each KPI.\n"
                "4. Produce actionable recommendations for each underperforming KPI.\n"
                "5. Publish the report as a SPRINT_REPORT message."
            ),
            # CTO
            "ARCHITECTURE_REVIEW": (
                "Conduct a technical architecture review of the project design.\n\n"
                "1. Assess scalability, maintainability, and security of the architecture.\n"
                "2. Identify single points of failure and technical debt.\n"
                "3. Evaluate technology choices against platform standards.\n"
                "4. Provide specific, actionable improvement recommendations.\n"
                "5. VETO with BLOCKER if the architecture has critical safety issues."
            ),
            "SPRINT_DECOMPOSE": (
                "Decompose project requirements into sprint-ready issues.\n\n"
                "1. Parse the requirements document for user stories and technical tasks.\n"
                "2. Categorize each as FEATURE, TEST, QA, INFRA, or DOCS.\n"
                "3. Estimate story points (calibrate using historical agent correction factors).\n"
                "4. Assign priorities (P0/P1/P2/P3) and dependencies.\n"
                "5. Publish the sprint plan as a SPRINT_PLAN message to the COO."
            ),
            # CSO
            "SECURITY_AUDIT": (
                "Conduct a comprehensive security audit of the project.\n\n"
                "1. Review authentication and authorization design.\n"
                "2. Check for known vulnerability patterns (OWASP Top 10).\n"
                "3. Validate encryption at rest and in transit.\n"
                "4. Review dependency and supply chain security.\n"
                "5. Check compliance requirements (SOC2, GDPR, HIPAA as applicable).\n"
                "6. VETO any critical security findings with BLOCKER severity."
            ),
            "THREAT_MODEL": (
                "Build a threat model for the project.\n\n"
                "1. Identify assets, actors, and trust boundaries.\n"
                "2. Enumerate threats using STRIDE methodology.\n"
                "3. Assess likelihood and impact for each threat.\n"
                "4. Recommend mitigations prioritized by risk score.\n"
                "5. Document findings in the project context."
            ),
            # CIO
            "TECH_ASSESSMENT": (
                "Perform a technology stack assessment for the project.\n\n"
                "1. Evaluate technology choices against enterprise standards.\n"
                "2. Assess integration complexity and API governance.\n"
                "3. Review data architecture and storage strategy.\n"
                "4. Check observability and monitoring coverage.\n"
                "5. Provide a technology viability verdict with specific recommendations."
            ),
            "IT_GOVERNANCE_REVIEW": (
                "Review the project for IT governance compliance.\n\n"
                "1. Check alignment with enterprise architecture roadmap.\n"
                "2. Verify data sovereignty and privacy compliance.\n"
                "3. Review vendor risk and dependency management.\n"
                "4. Assess technical debt implications.\n"
                "5. Document governance findings and required remediations."
            ),
            # CHRM
            "CAPACITY_CHECK": (
                "Assess team capacity for the current project workload.\n\n"
                "1. Review assigned agents and their current workload.\n"
                "2. Check agent profiles for correction factors and historical performance.\n"
                "3. Identify overallocated or underperforming agents.\n"
                "4. Recommend workload redistribution or capacity adjustments.\n"
                "5. Flag any unsustainable commitments to the COO."
            ),
            "WORKFORCE_REVIEW": (
                "Conduct a workforce planning review for the project.\n\n"
                "1. Map required skills against available agent capabilities.\n"
                "2. Identify skill gaps that could block delivery.\n"
                "3. Recommend training, hiring, or role changes.\n"
                "4. Review team health indicators (sentiment, velocity, blocker rate).\n"
                "5. Produce a workforce readiness assessment."
            ),
        }

        task_instruction = action_instructions.get(
            action.upper(),
            f"Execute the required action for directive: {action}. "
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
                    f"- Name: {project_name}\n"
                    f"- Description: {description}\n"
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
                        "Aggregate all reviews and advance project state from "
                        "FEASIBILITY_CHECK → FEASIBILITY_REPORT, or "
                        "PDR_REVIEW → CDR_CREATION, etc. "
                        "Call this when all reviews are complete."
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
                            "session_id": {
                                "type": "string",
                                "description": "Optional review session ID.",
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
                                    "human_approved, human_rejected."
                                ),
                            },
                            "actor_id": {
                                "type": "string",
                                "description": "Agent or user performing the transition.",
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
            ToolDefinition(
                function=ToolFunction(
                    name="human.notify",
                    description="Send a notification to the human operator.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "project_id": {
                                "type": "string",
                                "description": "UUID of the project.",
                            },
                            "message": {
                                "type": "string",
                                "description": "Notification message for the human.",
                            },
                            "notification_type": {
                                "type": "string",
                                "enum": ["INFO", "WARNING", "ERROR", "APPROVAL_REQUIRED"],
                                "description": "Type of notification.",
                            },
                        },
                        "required": ["project_id", "message"],
                    },
                )
            ),
            ToolDefinition(
                function=ToolFunction(
                    name="flow.list",
                    description="List all available orchestration flows. Returns flow ID, name, version, and active status.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "is_active": {
                                "type": "boolean",
                                "description": "Filter by active status.",
                            },
                        },
                    },
                )
            ),
            ToolDefinition(
                function=ToolFunction(
                    name="flow.invoke",
                    description="Start or resume a flow for a project. The flow must be attached to the project first via the UI.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "project_id": {
                                "type": "string",
                                "description": "UUID of the project.",
                            },
                            "action": {
                                "type": "string",
                                "enum": ["start", "pause", "resume", "cancel"],
                                "description": "Action to perform on the flow.",
                            },
                        },
                        "required": ["project_id", "action"],
                    },
                )
            ),
            ToolDefinition(
                function=ToolFunction(
                    name="flow.status",
                    description="Get the current status of a flow attached to a project.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "project_id": {
                                "type": "string",
                                "description": "UUID of the project.",
                            },
                        },
                        "required": ["project_id"],
                    },
                )
            ),
            ToolDefinition(
                function=ToolFunction(
                    name="flow.advance",
                    description="Manually advance a flow node (complete or fail a task node). Use when a task is done or failed.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "project_id": {
                                "type": "string",
                                "description": "UUID of the project.",
                            },
                            "node_id": {
                                "type": "string",
                                "description": "ID of the node to advance.",
                            },
                            "action": {
                                "type": "string",
                                "enum": ["complete", "fail"],
                                "description": "Complete or fail the node.",
                            },
                            "output": {
                                "type": "object",
                                "description": "Output from the node (optional).",
                            },
                            "approved": {
                                "type": "boolean",
                                "description": "For approval nodes: true to approve, false to reject.",
                            },
                        },
                        "required": ["project_id", "node_id", "action"],
                    },
                )
            ),
        ]

    # ------------------------------------------------------------------
    # SYSTEM_EVENT handler — react to orchestrator state transitions
    # ------------------------------------------------------------------

    async def _handle_system_event(self, envelope: MessageEnvelope) -> None:
        """React to workflow state transition notifications from the orchestrator.

        For CEO specialization: notify human on major milestones.
        For other C-Suite: no-op (informational only).
        """
        event = envelope.payload.get("event", "")
        to_state = envelope.payload.get("to_state", "")
        project_id = envelope.project_id or envelope.payload.get("project_id", "")

        logger.info(
            "csuite_system_event_%s",
            event.lower(),
            extra=self._log_extra(
                event=event,
                to_state=to_state,
                specialization=self._specialization,
            ),
        )

        # Skip LLM calls for projects already in a terminal state
        if project_id and self._storage is not None:
            try:
                from uuid import UUID as _UUID

                project = await self._storage.get_project(_UUID(project_id))
                if project is not None and project.get("state") in (
                    "ARCHIVED",
                    "COMPLETED",
                    "FAILED",
                ):
                    logger.info(
                        "csuite_system_event_skip_terminal_project",
                        extra=self._log_extra(
                            event=event,
                            project_id=project_id,
                            state=project.get("state"),
                        ),
                    )
                    return
            except Exception:
                pass  # If we can't check, proceed normally

        # CEO reacts to key state transitions
        if self._specialization == "CEO" and to_state in {
            "FEASIBILITY_REPORT",
            "HUMAN_APPROVAL",
            "COMPLETED",
            "FAILED",
            "SECURITY_BLOCKED",
        }:
            await self._ceo_react_to_state(envelope, event, to_state, project_id)

    async def _ceo_react_to_state(
        self,
        envelope: MessageEnvelope,
        event: str,
        to_state: str,
        project_id: str,
    ) -> None:
        """CEO reacts to a state transition by running the think() loop."""
        state_instructions = {
            "FEASIBILITY_REPORT": (
                "The feasibility assessment is complete. "
                "Call `human.notify` to inform the human that the feasibility report is ready "
                "for their review and decision. Include a brief summary of what happened."
            ),
            "HUMAN_APPROVAL": (
                "The project has reached the human approval gate. "
                "Call `human.notify` to ask the human to review the CDR and approve/reject/edit."
            ),
            "COMPLETED": (
                "The project has been successfully completed! "
                "Call `human.notify` with a congratulatory summary of the completed project."
            ),
            "FAILED": (
                "The project has failed. "
                "Call `human.notify` with the failure reason and any recommendations."
            ),
            "SECURITY_BLOCKED": (
                "The project is blocked due to a CSO security veto. "
                "Call `human.notify` to inform the human and ask if they want to override or accept."
            ),
        }
        instruction = state_instructions.get(
            to_state,
            f"Project transitioned to {to_state}. Determine appropriate next action.",
        )

        messages = [
            {"role": "system", "content": self._system_prompt},
            {
                "role": "user",
                "content": (
                    f"## Project State Change\n"
                    f"- Project ID: {project_id}\n"
                    f"- Event: {event}\n"
                    f"- New State: {to_state}\n\n"
                    f"{instruction}"
                ),
            },
        ]
        tools = self._build_workflow_tool_definitions()
        await self.think(messages=messages, tools=tools)

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
