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
Worker      : WorkerManifest, WORKER_SDK_VERSION
Domain      : ProjectDocument, ReviewComment, ReviewSummary, ReviewResponse,
              ReviewSession, FeasibilityReport, HumanDecision, Sprint, Issue,
              Milestone, ProjectSummary, KPISnapshot, AgentProfile
WS          : WSMessageFrame, WSPingFrame, WSAckFrame, WSNackFrame, WSPongFrame,
              AgentFrame, RouterFrame, parse_agent_frame
"""

from .capability import (
    CapabilityDef,
    CapabilitySearchRequest,
    CapabilitySearchResponse,
    WorkerCapabilityRecord,
)
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
    IssuePriority,
    IssueStatus,
    IssueType,
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
from .schema_export import (
    PROTOCOL_SCHEMA_VERSION,
    protocol_schema_bundle,
    write_protocol_schema_bundle,
)
from .tool import (
    CircuitState,
    ToolManifestEntry,
    ToolRequest,
    ToolResponse,
)
from .worker_manifest import WORKER_SDK_VERSION, WorkerManifest
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
    "PROTOCOL_SCHEMA_VERSION",
    "protocol_schema_bundle",
    "write_protocol_schema_bundle",
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
    "WORKER_SDK_VERSION",
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

