"""MAS protocols package — unified message schema and domain models.

Public surface
--------------
Enums       : MessageType, AgentRole, ProjectState, ReviewSessionStatus,
              ReviewSeverity, ReviewVerdict, DocumentType, DocumentState,
              IssueType, IssueStatus, IssuePriority, FailureReason, SystemState
Envelope    : BlobRef, TaskBudget, MessageEnvelope, MAX_PAYLOAD_BYTES
Tool        : ToolRequest, ToolResponse, ToolManifestEntry, CircuitState
Domain      : ProjectDocument, ReviewComment, ReviewSummary, ReviewResponse,
              ReviewSession, FeasibilityReport, HumanDecision, Sprint, Issue,
              Milestone, ProjectSummary, KPISnapshot, AgentProfile
WS          : WSMessageFrame, WSPingFrame, WSAckFrame, WSNackFrame, WSPongFrame,
              AgentFrame, RouterFrame, parse_agent_frame
"""

from .domain import (
    AgentProfile,
    FeasibilityReport,
    HumanDecision,
    Issue,
    KPISnapshot,
    Milestone,
    ProjectDocument,
    ProjectSummary,
    ReviewComment,
    ReviewResponse,
    ReviewSession,
    ReviewSummary,
    Sprint,
)
from .enums import (
    AgentRole,
    DocumentState,
    DocumentType,
    FailureReason,
    IssueStatus,
    IssueType,
    IssuePriority,
    MessageType,
    ProjectState,
    ReviewSessionStatus,
    ReviewSeverity,
    ReviewVerdict,
    SystemState,
)
from .envelope import (
    MAX_PAYLOAD_BYTES,
    BlobRef,
    MessageEnvelope,
    TaskBudget,
)
from .tool import (
    CircuitState,
    ToolManifestEntry,
    ToolRequest,
    ToolResponse,
)
from .ws import (
    AgentFrame,
    RouterFrame,
    WSAckFrame,
    WSMessageFrame,
    WSNackFrame,
    WSPingFrame,
    WSPongFrame,
    parse_agent_frame,
)

__all__ = [
    # Enums
    "AgentRole",
    "MessageType",
    "ProjectState",
    "ReviewSessionStatus",
    "ReviewSeverity",
    "ReviewVerdict",
    "DocumentType",
    "DocumentState",
    "IssueType",
    "IssueStatus",
    "IssuePriority",
    "FailureReason",
    "SystemState",
    # Envelope
    "MAX_PAYLOAD_BYTES",
    "BlobRef",
    "TaskBudget",
    "MessageEnvelope",
    # Tool
    "CircuitState",
    "ToolRequest",
    "ToolResponse",
    "ToolManifestEntry",
    # Domain
    "ProjectDocument",
    "ReviewComment",
    "ReviewSummary",
    "ReviewResponse",
    "ReviewSession",
    "FeasibilityReport",
    "HumanDecision",
    "Sprint",
    "Issue",
    "Milestone",
    "ProjectSummary",
    "KPISnapshot",
    "AgentProfile",
    # WS protocol
    "WSMessageFrame",
    "WSPingFrame",
    "WSAckFrame",
    "WSNackFrame",
    "WSPongFrame",
    "AgentFrame",
    "RouterFrame",
    "parse_agent_frame",
]

