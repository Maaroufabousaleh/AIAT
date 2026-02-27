"""Domain models for the MAS corporate-hierarchy project lifecycle.

Covers org-architecture plan §3.4:
  - ProjectDocument, ReviewComment, ReviewSummary
  - FeasibilityReport
  - HumanDecision
  - Sprint, Issue
  - KPISnapshot
  - AgentProfile

All models use Pydantic v2.  Instances are stored in Postgres; large bodies
(document content, diagrams) are uploaded to MinIO and referenced via
``BlobRef`` (see envelope.py).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from .enums import (
    DocumentState,
    DocumentType,
    IssueStatus,
    IssueType,
    IssuePriority,
    ProjectState,
    ReviewSessionStatus,
    ReviewSeverity,
    ReviewVerdict,
)
from .envelope import BlobRef


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


class ProjectDocument(BaseModel):
    """Metadata row for a formal project document (PDR, CDR, RR, etc.).

    The actual document body lives in MinIO; agents fetch it via ``blob_ref``.
    """

    document_id: UUID = Field(default_factory=uuid4)
    project_id: str
    doc_type: DocumentType
    version: int = Field(default=1, ge=1)
    state: DocumentState = Field(default=DocumentState.DRAFT)
    title: str | None = Field(default=None, description="Human-readable document title.")
    sections: dict[str, Any] = Field(default_factory=dict)
    content_ref: str | None = Field(default=None, description="MinIO key for large document body.")
    parent_document_id: UUID | None = None

    # Author & ownership
    created_by: str = Field(..., description="Agent ID that created this document.")
    team_id: str = Field(..., description="Team that produced this document.")

    # Content reference (MinIO)
    blob_ref: BlobRef | None = Field(
        default=None,
        description="Pointer to the full document body in MinIO.",
    )

    # Inline summary (≤ 2 KB) — used by reviewers to decide if a BlobRef fetch is needed
    summary: str | None = Field(default=None, max_length=2048)

    # Audit
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    # Review tracking
    review_session_id: UUID | None = None
    revision_count: int = Field(default=0, ge=0)


# ---------------------------------------------------------------------------
# Review models
# ---------------------------------------------------------------------------


class ReviewComment(BaseModel):
    """A single reviewer comment inside a ReviewResponse."""

    comment_id: UUID = Field(default_factory=uuid4)
    reviewer_id: str = Field(..., description="Agent ID of the reviewer.")
    reviewer_role: str | None = Field(default=None, description="Display role label, e.g. CFO.")
    reviewer_team: str
    section: str | None = Field(
        default=None,
        description="Document section this comment refers to (e.g. 'security', 'budget').",
    )
    severity: ReviewSeverity = Field(default=ReviewSeverity.INFO)
    body: str = Field(..., description="Review comment text.")
    comment: str | None = Field(default=None, description="Alias field matching plan text.")
    suggested_change: str | None = None
    suggested_fix: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    # CSO veto — only applicable when reviewer_role == CSuiteAgent(CSO)
    veto: bool = Field(
        default=False,
        description=(
            "If True AND severity == BLOCKER, triggers SECURITY_BLOCKED sub-state. "
            "Only the CSO may set this flag."
        ),
    )


class ReviewSummary(BaseModel):
    """Aggregated result of a fan-out review cycle managed by the COO."""

    session_id: UUID = Field(default_factory=uuid4)
    project_id: str
    document_id: UUID
    document_version: int | None = Field(default=None, ge=1)
    doc_type: DocumentType

    # Reviewer responses keyed by agent_id
    responses: dict[str, "ReviewResponse"] = Field(default_factory=dict)
    reviews: list[ReviewComment] = Field(default_factory=list)
    overall_verdict: ReviewVerdict | None = None

    # Aggregated comment list across all reviewers
    comments: list[ReviewComment] = Field(default_factory=list)

    # Circuit-breaker counters
    timeout_count: int = Field(default=0, ge=0)
    reviewer_count: int = Field(default=0, ge=0)  # 0 = not yet known (set once session starts)
    responses_received: int = Field(default=0, ge=0)

    completed_at: datetime | None = None

    @property
    def is_complete(self) -> bool:
        # reviewer_count may be 0 before fan-out starts; that state is not complete.
        return self.reviewer_count > 0 and self.responses_received >= self.reviewer_count

    @property
    def circuit_open(self) -> bool:
        """True when ≥ 2 timeout events make continued review unreliable."""
        return self.timeout_count >= 2


class ReviewResponse(BaseModel):
    """A single reviewer's full response to a REVIEW_REQUEST."""

    reviewer_id: str
    reviewer_role: str
    reviewer_team: str
    verdict: ReviewVerdict
    comments: list[ReviewComment] = Field(default_factory=list)
    veto: bool = False
    submitted_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


# ---------------------------------------------------------------------------
# Feasibility
# ---------------------------------------------------------------------------


class FeasibilityReport(BaseModel):
    """Aggregated feasibility findings presented to Human at step 2."""

    project_id: str
    financial_viable: bool | None = None    # Set by CFO
    technical_viable: bool | None = None    # Set by CIO
    resource_viable: bool | None = None     # Set by CHRM
    security_viable: bool | None = None     # Set by CSO
    estimated_cost: float | None = Field(default=None, ge=0.0)
    estimated_duration_days: int | None = Field(default=None, ge=0)
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] | None = None

    financial_summary: str | None = None
    technical_summary: str | None = None
    resource_summary: str | None = None
    security_summary: str | None = None

    overall_recommendation: str | None = None
    risks: list[str] = Field(default_factory=list)

    assembled_by: str = Field(..., description="CEO agent ID that assembled this report.")
    assembled_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    @property
    def is_viable(self) -> bool:
        """All four dimensions must pass for the project to proceed."""
        return all(
            [
                self.financial_viable is True,
                self.technical_viable is True,
                self.resource_viable is True,
                self.security_viable is True,
            ]
        )


# ---------------------------------------------------------------------------
# Human-in-the-loop
# ---------------------------------------------------------------------------


class HumanDecision(BaseModel):
    """Human approval / rejection response received via the orchestrator API."""

    decision_id: UUID = Field(default_factory=uuid4)
    project_id: str
    gate: str = Field(
        ...,
        description="Approval gate identifier (e.g. 'FEASIBILITY', 'CDR_APPROVAL').",
    )
    gate_type: Literal["FEASIBILITY", "CDR_APPROVAL"] | None = None

    # Decision
    approved: bool
    action: str = Field(
        ...,
        description=(
            "Explicit action taken: 'approve', 'reject', 'edit', 'cancel'. "
            "The controller uses this to drive state transitions."
        ),
    )
    comment: str | None = None
    comments: str | None = None
    edit_instructions: str | None = Field(
        default=None,
        description="Free-text revision instructions when action == 'edit'.",
    )
    edits: list[str] | None = None
    decision: Literal["APPROVE", "REJECT", "EDIT", "CANCEL"] | None = None

    # Audit
    decided_by: str = Field(default="human", description="Human user or API token identifier.")
    decided_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


# ---------------------------------------------------------------------------
# Sprint & Issue
# ---------------------------------------------------------------------------


class Issue(BaseModel):
    """A single sprint issue (feature, test, QA, docs, infra, bug)."""

    issue_id: UUID = Field(default_factory=uuid4)
    project_id: str
    sprint_id: UUID | None = None
    parent_issue_id: UUID | None = Field(
        default=None, description="Set when this is a sub-issue decomposed from a parent."
    )

    title: str
    description: str | None = None
    issue_type: IssueType
    priority: IssuePriority = IssuePriority.MEDIUM
    status: IssueStatus = IssueStatus.BACKLOG

    # Assignment
    assigned_team: str | None = None
    assigned_agent: str | None = None
    assignee_team: str | None = None
    assignee_agent: str | None = None

    # Estimation
    estimated_hours: float | None = Field(default=None, ge=0.0)
    estimate_hours: float | None = Field(default=None, ge=0.0)
    actual_hours: float | None = Field(default=None, ge=0.0)
    story_points: int | None = Field(default=None, ge=0)

    # Sub-issues
    sub_issues: list[UUID] = Field(default_factory=list)

    # Dependencies
    blocked_by: list[UUID] = Field(default_factory=list)
    dependencies: list[UUID] = Field(default_factory=list)

    # Audit
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    completed_at: datetime | None = None


class Sprint(BaseModel):
    """A sprint within a project managed by the CTO."""

    sprint_id: UUID = Field(default_factory=uuid4)
    project_id: str
    sprint_number: int = Field(..., ge=1)
    name: str
    milestone: str | None = None
    status: Literal["PLANNED", "ACTIVE", "COMPLETED", "CANCELLED"] = "PLANNED"
    start_date: datetime | None = None
    end_date: datetime | None = None

    issues: list[UUID] = Field(default_factory=list, description="Issue IDs in this sprint.")

    # Scheduling
    planned_start: datetime | None = None
    planned_end: datetime | None = None
    actual_start: datetime | None = None
    actual_end: datetime | None = None

    # Metrics (populated at retrospective)
    total_story_points: int | None = None
    completed_story_points: int | None = None
    velocity: float | None = None  # story_points_completed / sprint_duration_days

    # Status
    infra_ready: bool = Field(
        default=False,
        description="True once DevOps PM has sent INFRA_READY for this sprint's requirements.",
    )
    blocked_until_infra: bool = Field(
        default=False,
        description="First dev sprint is blocked until infra_ready == True.",
    )

    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


# ---------------------------------------------------------------------------
# KPI
# ---------------------------------------------------------------------------


class KPISnapshot(BaseModel):
    """KPI data captured at the end of a sprint or project.

    Stored in Postgres ``kpi_snapshots`` and used by CTO's in-context learning
    to improve future estimates via ``AgentProfile.correction_factor``.
    """

    snapshot_id: UUID = Field(default_factory=uuid4)
    project_id: str
    sprint_id: UUID | None = None          # None == project-level KPI
    agent_id: str | None = None            # None == aggregated team KPI
    team_id: str | None = None

    captured_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    measured_at: datetime | None = None
    metric_type: Literal[
        "ESTIMATION_ACCURACY",
        "TASK_COMPLETION_RATE",
        "REVIEW_PASS_RATE",
        "VELOCITY",
        "DEFECT_RATE",
        "REWORK_RATE",
        "BUDGET_ADHERENCE",
        "RESOURCE_UTILIZATION",
        "INFRA_LEAD_TIME",
    ] | None = None
    value: float | None = None
    context: dict[str, Any] | None = None

    # Core metrics (all Optional so partial snapshots are valid)
    estimation_accuracy: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="1 - abs(estimated - actual) / estimated",
    )
    task_completion_rate: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="completed_issues / total_issues",
    )
    review_pass_rate: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="approved_first_try / total_reviews",
    )
    velocity: float | None = Field(
        default=None,
        ge=0.0,
        description="story_points_completed / sprint_duration_days",
    )
    defect_rate: float | None = Field(
        default=None,
        ge=0.0,
        description="bugs_found / features_delivered",
    )
    rework_rate: float | None = Field(
        default=None,
        ge=0.0,
        description="cdr_revision_count (lower == better)",
    )
    budget_adherence: float | None = Field(
        default=None,
        ge=0.0,
        description="actual_cost / estimated_cost (target: 1.0)",
    )

    # Org-architecture specific metrics
    resource_utilization: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="RESOURCE_UTILIZATION — agent capacity used / total available.",
    )
    infra_lead_time_minutes: float | None = Field(
        default=None,
        ge=0.0,
        description="INFRA_LEAD_TIME — minutes from INFRA_PROVISIONING start to INFRA_READY.",
    )

    # Raw counters for cross-project aggregation
    llm_calls_total: int | None = None
    tool_calls_total: int | None = None
    cost_usd_total: float | None = None

    # Arbitrary extra metrics for future extensibility
    extra: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# AgentProfile — per-agent learning record
# ---------------------------------------------------------------------------


class AgentProfile(BaseModel):
    """Per-agent estimation profile, updated at every retrospective.

    The CTO uses ``correction_factor`` and ``estimation_bias`` when creating
    sprint estimates on new projects to reduce systematic over/under-estimation.
    No separate ML model — uses LLM in-context reasoning over Postgres history.
    """

    agent_id: str = Field(..., description="Unique agent identifier.")
    team_id: str = Field(..., description="Team this agent belongs to.")
    agent_role: str | None = None

    # Learning state
    correction_factor: float = Field(
        default=1.0,
        ge=0.1,
        le=10.0,
        description=(
            "Multiply raw estimates by this factor. "
            "> 1.0 means agent typically underestimates; < 1.0 means overestimates."
        ),
    )
    estimation_bias: float = Field(
        default=0.0,
        description="Additive bias in hours (positive = tends to underestimate).",
    )
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description=(
            "Confidence in the correction_factor. Increases with more data points. "
            "Low confidence → CTO applies estimates conservatively."
        ),
    )

    # History aggregates (updated by CTO at KPI_PERSISTENCE step)
    tasks_completed: int = Field(default=0, ge=0)
    avg_estimation_accuracy: float | None = None
    avg_task_completion_rate: float | None = None
    avg_velocity: float | None = None
    budget_adherence_avg: float | None = None
    sample_count: int = Field(default=0, ge=0)

    # Metadata
    last_updated: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    kpi_snapshot_ids: list[UUID] = Field(
        default_factory=list,
        description="IDs of KPISnapshot rows used to compute current profile.",
    )


# ---------------------------------------------------------------------------
# Re-export ReviewResponse at module level (circular ref workaround)
# ---------------------------------------------------------------------------
ReviewSummary.model_rebuild()


# ---------------------------------------------------------------------------
# ReviewSession — tracks a single fan-out review cycle (org-architecture §7.4)
# ---------------------------------------------------------------------------


class ReviewSession(BaseModel):
    """One parallel review fan-out cycle orchestrated by the COO.

    Created by the workflow controller before REVIEW_REQUEST messages are sent.
    Persisted in Postgres ``review_sessions`` table throughout the cycle.
    """

    session_id: UUID = Field(default_factory=uuid4)
    project_id: str
    document_id: UUID
    doc_type: DocumentType

    # Participants (agent IDs of reviewers invited)
    reviewer_ids: list[str] = Field(default_factory=list)
    reviewer_count: int = Field(default=0, ge=0)

    # Progress tracking
    responses_received: int = Field(default=0, ge=0)
    timeout_count: int = Field(default=0, ge=0)
    status: ReviewSessionStatus = Field(default=ReviewSessionStatus.IN_PROGRESS)

    # Aggregated output (populated on completion)
    overall_verdict: ReviewVerdict | None = None
    comments: list[ReviewComment] = Field(default_factory=list)
    cso_veto: bool = Field(
        default=False,
        description="True if the CSO submitted a BLOCKER veto during this session.",
    )
    cso_veto_comment: str | None = None

    # Timing
    review_timeout_seconds: int = Field(
        default=300,
        ge=30,
        description="Per-reviewer timeout in seconds. Reminder sent at 1x, CB fires at 2x.",
    )
    started_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    completed_at: datetime | None = None

    @property
    def is_complete(self) -> bool:
        # reviewer_count may be 0 before fan-out starts; that state is not complete.
        return self.reviewer_count > 0 and self.responses_received >= self.reviewer_count

    @property
    def circuit_open(self) -> bool:
        return self.timeout_count >= 2


# ---------------------------------------------------------------------------
# Milestone — sprint hierarchy level above Sprint
# ---------------------------------------------------------------------------


class Milestone(BaseModel):
    """A project milestone grouping one or more sprints.

    Created by the CTO during SPRINT_PLANNING when decomposing the RR.
    Hierarchy: Milestone → Sprint → Issue → sub-Issue.
    """

    milestone_id: UUID = Field(default_factory=uuid4)
    project_id: str
    name: str = Field(..., description="Short milestone name, e.g. 'MVP Release'.")
    description: str | None = None
    order: int = Field(default=1, ge=1, description="Display/execution order within the project.")

    # Child sprints (ordered)
    sprint_ids: list[UUID] = Field(default_factory=list)

    # Success criteria
    acceptance_criteria: list[str] = Field(default_factory=list)

    # Scheduling
    planned_start: datetime | None = None
    planned_end: datetime | None = None
    actual_start: datetime | None = None
    actual_end: datetime | None = None

    # Completion
    completed: bool = False
    completed_at: datetime | None = None

    # Audit
    created_by: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


# ---------------------------------------------------------------------------
# Project — lightweight summary row (backed by Postgres `projects` table)
# ---------------------------------------------------------------------------


class ProjectSummary(BaseModel):
    """Lightweight project record as managed by the workflow controller.

    The full project state lives in Postgres; this model is used to pass
    project context in messages and API responses.
    """

    project_id: str
    name: str
    description: str | None = None
    state: ProjectState = ProjectState.INIT
    failure_reason: str | None = None      # FailureReason value when state == FAILED
    failed_from_state: ProjectState | None = None  # State active when failure occurred

    # Requestor info (CEO proxies for human)
    requested_by: str = Field(..., description="Human user or CEO agent ID.")

    # Timing
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    shutdown_at: datetime | None = None
    boot_at: datetime | None = None
