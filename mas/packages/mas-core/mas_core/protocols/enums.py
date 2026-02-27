"""Enumerations for the MAS protocol layer."""

from enum import Enum


class AgentRole(str, Enum):
    """Corporate hierarchy roles mapped to MAS communication policy roles."""

    ORCHESTRATOR = "orchestrator"   # CEO — human interface, top-level lifecycle
    EXECUTIVE = "executive"         # COO — operational coordination, doc lifecycle
    C_SUITE = "c_suite"             # CFO, CIO, CHRM, CSO, CTO — advisory/review
    ADMIN = "admin"                 # Department PMs — manage workers within dept
    WORKER = "worker"               # Executes tasks assigned by PM
    SUB_AGENT = "sub_agent"         # Spawned for subtasks by a parent agent


class MessageType(str, Enum):
    """All legal message types in the MAS unified MessageEnvelope."""

    # --- Core ---
    TASK = "TASK"                           # Assign a task to an agent / team
    RESULT = "RESULT"                       # Task result returned to requester
    QUERY = "QUERY"                         # Request information / data
    RESPONSE = "RESPONSE"                   # Reply to a QUERY
    BROADCAST = "BROADCAST"                 # One-to-many informational message
    ADMIN_TASK = "ADMIN_TASK"               # Admin-to-worker delegation
    ADMIN_REPLY = "ADMIN_REPLY"             # Worker-to-admin completion reply
    SHUTDOWN = "SHUTDOWN"                   # Ordered shutdown signal

    # --- Document lifecycle ---
    DOCUMENT_SUBMIT = "DOCUMENT_SUBMIT"     # Agent submits a completed document
    DOCUMENT_REVISION = "DOCUMENT_REVISION" # Request revision of a document

    # --- Review workflow ---
    REVIEW_REQUEST = "REVIEW_REQUEST"       # Fan-out review request to reviewers
    REVIEW_RESPONSE = "REVIEW_RESPONSE"     # Reviewer submits findings

    # --- Human-in-the-loop ---
    APPROVAL_REQUEST = "APPROVAL_REQUEST"   # Gate requiring human decision
    APPROVAL_RESPONSE = "APPROVAL_RESPONSE" # Human decision returned via API

    # --- Sprint management ---
    SPRINT_PLAN = "SPRINT_PLAN"             # CTO issues sprint plan to departments
    SPRINT_REPORT = "SPRINT_REPORT"         # Department PM submits sprint report
    ISSUE_ASSIGN = "ISSUE_ASSIGN"           # Assign individual issue to a worker
    ISSUE_COMPLETE = "ISSUE_COMPLETE"       # Worker signals issue completion

    # --- Hierarchy ---
    ESCALATION = "ESCALATION"               # Skip-one-level escalation upward
    DIRECTIVE = "DIRECTIVE"                 # High-priority directive from above

    # --- Infrastructure ---
    INFRA_READY = "INFRA_READY"             # DevOps PM signals provisioning done

    # --- System ---
    HEARTBEAT = "HEARTBEAT"                 # Liveness / keepalive pulse
    ACK = "ACK"                             # Explicit message acknowledgment (WS)
    SYSTEM_EVENT = "SYSTEM_EVENT"           # Internal system / lifecycle event


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

    PDR = "PDR"    # Primary Design Review
    CDR = "CDR"    # Critical Design Review
    RR = "RR"      # Requirements Review
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


class ProjectState(str, Enum):
    """All states in the 14-step deterministic project state machine.

    Only the orchestrator-api workflow controller may write ``projects.state``.
    Agents emit typed events; the controller validates transitions against the
    table in org-architecture §2.2 and persists the new state atomically.
    """

    # --- Lifecycle start ---
    INIT = "INIT"                           # Project record created; CEO kicks off feasibility

    # --- Feasibility gate (steps 1–2) ---
    FEASIBILITY_CHECK = "FEASIBILITY_CHECK" # CEO fans out REVIEW_REQUEST to CFO/CIO/CHRM/CSO
    FEASIBILITY_REPORT = "FEASIBILITY_REPORT" # CEO presents findings; human approval gate

    # --- Document creation & review (steps 3–6) ---
    PDR_CREATION = "PDR_CREATION"           # COO tasks Production dept to create PDR
    PDR_REVIEW = "PDR_REVIEW"               # COO fans out REVIEW_REQUEST for PDR
    SECURITY_BLOCKED = "SECURITY_BLOCKED"   # CSO veto sub-state; blocks all downstream work
    CDR_CREATION = "CDR_CREATION"           # COO tasks System dept to create CDR
    CDR_REVIEW = "CDR_REVIEW"               # COO aggregates CDR, presents to CEO for Human
    HUMAN_APPROVAL = "HUMAN_APPROVAL"       # Human approves / edits / cancels CDR

    # --- Execution planning (steps 9–10) ---
    RR_CREATION = "RR_CREATION"             # COO transforms CDR into Requirements Review
    SPRINT_PLANNING = "SPRINT_PLANNING"     # CTO decomposes RR into sprints/issues
    INFRA_PROVISIONING = "INFRA_PROVISIONING" # CTO → DevOps; blocked until INFRA_READY

    # --- Sprint execution (steps 11–12) ---
    IN_PROGRESS = "IN_PROGRESS"             # CTO monitors sprint lifecycle

    # --- Wrap-up (step 13) ---
    RETROSPECTIVE = "RETROSPECTIVE"         # CTO computes sprint KPIs, identifies issues
    KPI_PERSISTENCE = "KPI_PERSISTENCE"     # CTO saves KPIs, updates agent_profiles → CEO

    # --- Terminal states ---
    COMPLETED = "COMPLETED"                 # Project done; all data preserved
    ARCHIVED = "ARCHIVED"                   # Cancelled or completed + archived
    FAILED = "FAILED"                       # Systemic failure; human decides RETRY or ARCHIVE


class ReviewSessionStatus(str, Enum):
    """Lifecycle states of a parallel review fan-out session (org-architecture §7.4)."""

    IN_PROGRESS = "IN_PROGRESS"     # Waiting for all reviewers to respond
    COMPLETED = "COMPLETED"         # All reviewers submitted; verdict aggregated
    TIMED_OUT = "TIMED_OUT"         # One or more reviewers timed out (< circuit threshold)
    CIRCUIT_OPEN = "CIRCUIT_OPEN"   # ≥ 2 timeouts — session aborted, project → FAILED


class FailureReason(str, Enum):
    """Reasons a project can enter FAILED state."""

    WATCHDOG_TIMEOUT = "WATCHDOG_TIMEOUT"
    REVIEW_CIRCUIT_OPEN = "REVIEW_CIRCUIT_OPEN"
    DLQ_OVERFLOW = "DLQ_OVERFLOW"
    INFRA_FAILURE = "INFRA_FAILURE"
    AGENT_BUDGET_EXHAUSTED = "AGENT_BUDGET_EXHAUSTED"
    UNRECOVERABLE_ERROR = "UNRECOVERABLE_ERROR"


class SystemState(str, Enum):
    """Overall system / orchestrator lifecycle state."""

    RUNNING = "RUNNING"
    SHUTTING_DOWN = "SHUTTING_DOWN"
    STARTING = "STARTING"
    STOPPED = "STOPPED"
