"""Enumerations for the MAS protocol layer."""

from enum import Enum

# ProjectState is canonically defined in mas_core.workflow.states (StrEnum).
# Re-imported here so that ``from mas_core.protocols.enums import ProjectState``
# and ``from mas_core.workflow.states import ProjectState`` return the **same** class.
from mas_core.workflow.states import ProjectState as ProjectState  # noqa: F401


class AgentRole(str, Enum):
    """Corporate hierarchy roles mapped to MAS communication policy roles."""

    ORCHESTRATOR = "orchestrator"  # CEO — human interface, top-level lifecycle
    EXECUTIVE = "executive"  # COO — operational coordination, doc lifecycle
    C_SUITE = "c_suite"  # CFO, CIO, CHRM, CSO, CTO — advisory/review
    ADMIN = "admin"  # Department PMs — manage workers within dept
    WORKER = "worker"  # Executes tasks assigned by PM
    SUB_AGENT = "sub_agent"  # Spawned for subtasks by a parent agent


class MessageType(str, Enum):
    """All legal message types in the MAS unified MessageEnvelope."""

    # --- Core ---
    TASK = "TASK"  # Assign a task to an agent / team
    RESULT = "RESULT"  # Task result returned to requester
    QUERY = "QUERY"  # Request information / data
    RESPONSE = "RESPONSE"  # Reply to a QUERY
    BROADCAST = "BROADCAST"  # One-to-many informational message
    ADMIN_TASK = "ADMIN_TASK"  # Admin-to-worker delegation
    ADMIN_REPLY = "ADMIN_REPLY"  # Worker-to-admin completion reply
    SHUTDOWN = "SHUTDOWN"  # Ordered shutdown signal

    # --- Document lifecycle ---
    DOCUMENT_SUBMIT = "DOCUMENT_SUBMIT"  # Agent submits a completed document
    DOCUMENT_REVISION = "DOCUMENT_REVISION"  # Request revision of a document

    # --- Review workflow ---
    REVIEW_REQUEST = "REVIEW_REQUEST"  # Fan-out review request to reviewers
    REVIEW_RESPONSE = "REVIEW_RESPONSE"  # Reviewer submits findings

    # --- Human-in-the-loop ---
    APPROVAL_REQUEST = "APPROVAL_REQUEST"  # Gate requiring human decision
    APPROVAL_RESPONSE = "APPROVAL_RESPONSE"  # Human decision returned via API

    # --- Sprint management ---
    SPRINT_PLAN = "SPRINT_PLAN"  # CTO issues sprint plan to departments
    SPRINT_REPORT = "SPRINT_REPORT"  # Department PM submits sprint report
    ISSUE_ASSIGN = "ISSUE_ASSIGN"  # Assign individual issue to a worker
    ISSUE_COMPLETE = "ISSUE_COMPLETE"  # Worker signals issue completion

    # --- Hierarchy ---
    ESCALATION = "ESCALATION"  # Skip-one-level escalation upward
    DIRECTIVE = "DIRECTIVE"  # High-priority directive from above

    # --- Infrastructure ---
    INFRA_READY = "INFRA_READY"  # DevOps PM signals provisioning done

    # --- System ---
    HEARTBEAT = "HEARTBEAT"  # Liveness / keepalive pulse
    ACK = "ACK"  # Explicit message acknowledgment (WS)
    SHUTDOWN_ACK = "SHUTDOWN_ACK"  # Team confirms checkpoint + shutdown readiness
    SHUTDOWN_NACK = "SHUTDOWN_NACK"  # Team failed to shut down cleanly
    SYSTEM_EVENT = "SYSTEM_EVENT"  # Internal system / lifecycle event


class ReviewSeverity(str, Enum):
    """Severity levels for review responses."""

    APPROVED = "APPROVED"
    SUGGESTION = "SUGGESTION"
    MINOR = "MINOR"
    MAJOR = "MAJOR"
    INFO = "INFO"
    WARNING = "WARNING"
    CONCERN = "CONCERN"
    BLOCKER = "BLOCKER"


class ReviewVerdict(str, Enum):
    """Overall verdict from a reviewer."""

    APPROVED = "APPROVED"
    APPROVED_WITH_COMMENTS = "APPROVED_WITH_COMMENTS"
    NEEDS_REVISION = "NEEDS_REVISION"
    REJECTED = "REJECTED"


class DocumentType(str, Enum):
    """Types of formal documents in the project lifecycle."""

    PDR = "PDR"  # Primary Design Review
    CDR = "CDR"  # Critical Design Review
    RR = "RR"  # Requirements Review
    TEST_PLAN = "TEST_PLAN"
    SPRINT_REPORT = "SPRINT_REPORT"
    RETROSPECTIVE = "RETROSPECTIVE"
    KPI_REPORT = "KPI_REPORT"


class DocumentState(str, Enum):
    """Document lifecycle states."""

    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    NEEDS_REVISION = "NEEDS_REVISION"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"


class IssueType(str, Enum):
    """Types of sprint issues."""

    FEATURE = "feature"
    TEST = "test"
    QA = "qa"
    DOCS = "docs"
    INFRA = "infra"
    BUGFIX = "bugfix"
    BUG = "bug"
    REWORK = "rework"


class IssuePriority(str, Enum):
    """Issue priority levels."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class IssueStatus(str, Enum):
    """Issue lifecycle states."""

    BACKLOG = "backlog"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    DONE = "done"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class ReviewSessionStatus(str, Enum):
    """Lifecycle states of a parallel review fan-out session (org-architecture §7.4)."""

    IN_PROGRESS = "IN_PROGRESS"  # Waiting for all reviewers to respond
    COMPLETED = "COMPLETED"  # All reviewers submitted; verdict aggregated
    TIMED_OUT = "TIMED_OUT"  # One or more reviewers timed out (< circuit threshold)
    CIRCUIT_OPEN = "CIRCUIT_OPEN"  # ≥ 2 timeouts — session aborted, project → FAILED


class FailureReason(str, Enum):
    """Reasons a project can enter FAILED state."""

    WATCHDOG_TIMEOUT = "WATCHDOG_TIMEOUT"
    REVIEW_CIRCUIT_OPEN = "REVIEW_CIRCUIT_OPEN"
    DLQ_OVERFLOW = "DLQ_OVERFLOW"
    INFRA_FAILURE = "INFRA_FAILURE"
    AGENT_BUDGET_EXHAUSTED = "AGENT_BUDGET_EXHAUSTED"
    UNRECOVERABLE_ERROR = "UNRECOVERABLE_ERROR"


class SprintStatus(str, Enum):
    """Sprint lifecycle states."""

    PLANNED = "PLANNED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class KPIMetricType(str, Enum):
    """Enumeration of all KPI metric types collected by the CTO."""

    ESTIMATION_ACCURACY = "ESTIMATION_ACCURACY"
    TASK_COMPLETION_RATE = "TASK_COMPLETION_RATE"
    REVIEW_PASS_RATE = "REVIEW_PASS_RATE"
    VELOCITY = "VELOCITY"
    DEFECT_RATE = "DEFECT_RATE"
    REWORK_RATE = "REWORK_RATE"
    BUDGET_ADHERENCE = "BUDGET_ADHERENCE"
    RESOURCE_UTILIZATION = "RESOURCE_UTILIZATION"
    INFRA_LEAD_TIME = "INFRA_LEAD_TIME"


class SystemState(str, Enum):
    """Overall system / orchestrator lifecycle state."""

    RUNNING = "RUNNING"
    SHUTTING_DOWN = "SHUTTING_DOWN"
    STARTING = "STARTING"
    STOPPED = "STOPPED"
