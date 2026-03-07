"""MAS protocols package — unified message schema and domain models.

Public surface
--------------
Enums       : MessageType, AgentRole, ProjectState, ReviewSessionStatus,
              ReviewSeverity, ReviewVerdict, DocumentType, DocumentState,
              IssueType, IssueStatus, IssuePriority, FailureReason,
              KPIMetricType, SprintStatus, SystemState
Envelope    : BlobRef, TaskBudget, MessageEnvelope, MAX_PAYLOAD_BYTES
Tool        : ToolRequest, ToolResponse, ToolManifestEntry, CircuitState
Capability  : CapabilityDef, WorkerCapabilityRecord, CapabilitySearchRequest,
              CapabilitySearchResponse
Worker      : WorkerManifest
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
from .capability import (
    CapabilityDef,
    CapabilitySearchRequest,
    CapabilitySearchResponse,
    WorkerCapabilityRecord,
)
from .enums import (
    AgentRole,
    DocumentState,
    DocumentType,
    FailureReason,
    IssueStatus,
    IssueType,
    IssuePriority,
    KPIMetricType,
    MessageType,
    ProjectState,
    ReviewSessionStatus,
    ReviewSeverity,
    ReviewVerdict,
    SprintStatus,
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
from .worker_manifest import WorkerManifest
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
    "KPIMetricType",
    "FailureReason",
    "SprintStatus",
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
    # Capability / Worker Manifest
    "CapabilityDef",
    "WorkerCapabilityRecord",
    "CapabilitySearchRequest",
    "CapabilitySearchResponse",
    "WorkerManifest",
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

