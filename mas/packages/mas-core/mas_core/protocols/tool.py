"""Tool-service request / response models for agent ↔ tool-service communication.

Design notes
------------
* Agents call the tool-service over HTTP (POST /tools/{tool_name}/run).
* The tool-service enforces role-based access before executing any tool.
* ``ToolRequest.idempotency_key`` maps to the originating ``MessageEnvelope.message_id``
  so the shared Redis cache (``tool_cache:{hash}``) can deduplicate concurrent calls.
* Circuit-breaker state is surfaced in ``ToolResponse`` so agents can adapt.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .enums import AgentRole

# ---------------------------------------------------------------------------
# Circuit-breaker state (mirrored from tool-service)
# ---------------------------------------------------------------------------


class CircuitState(str, Enum):
    CLOSED = "CLOSED"           # Normal operation
    OPEN = "OPEN"               # Failing — returns errors immediately
    HALF_OPEN = "HALF_OPEN"     # Probe call allowed


# ---------------------------------------------------------------------------
# ToolRequest
# ---------------------------------------------------------------------------


class ToolRequest(BaseModel):
    """Sent by an agent to POST /tools/{tool_name}/run on the tool-service."""

    model_config = ConfigDict(populate_by_name=True)

    protocol_version: Literal["aiat.v1"] = Field(
        default="aiat.v1",
        description="Non-breaking protocol version for cross-runtime contract validation.",
    )

    # Caller identity (used for role-based access gating)
    caller_id: str = Field(
        ...,
        alias="agent_id",
        serialization_alias="agent_id",
        description="Agent ID making the request.",
    )
    caller_role: AgentRole = Field(
        ...,
        alias="sender_role",
        serialization_alias="sender_role",
        description="Caller's role — enforced against tool manifest.",
    )
    caller_team: str | None = Field(
        default=None,
        alias="sender_team",
        serialization_alias="sender_team",
        description="Caller's team for team-scoped permission checks.",
    )
    project_id: str | None = Field(default=None, description="Project context for audit logging.")
    worker_run_id: UUID | None = Field(default=None, description="Durable worker run that authorized this tool call.")
    permission_scope: list[str] = Field(default_factory=list, description="AIAT-approved permission scopes.")
    budget_snapshot: dict[str, Any] | None = Field(default=None, description="Budget state at authorization time.")
    audit_context: dict[str, Any] = Field(default_factory=dict, description="Trace and provenance references for audit.")

    # Tool to invoke
    tool_name: str = Field(
        ...,
        description=(
            "Dot-namespaced tool identifier, e.g. 'web.search', 'project.transition', "
            "'sprint.create'. Must match a registered tool in the tool manifest."
        ),
    )
    tool_kwargs: dict[str, Any] = Field(
        default_factory=dict,
        alias="kwargs",
        serialization_alias="kwargs",
        description="Keyword arguments forwarded verbatim to the tool implementation.",
    )

    # Idempotency / caching
    idempotency_key: UUID | None = Field(
        default=None,
        description=(
            "Set to the originating MessageEnvelope.message_id to enable cache deduplication. "
            "Concurrent calls with the same (tool_name, tool_kwargs, idempotency_key) "
            "return the cached result from the first call."
        ),
    )

    # Observability
    trace_id: str | None = Field(default=None, description="Distributed trace ID for log correlation.")
    span_id: str | None = Field(default=None, description="Trace span ID.")


# ---------------------------------------------------------------------------
# ToolResponse
# ---------------------------------------------------------------------------


class ToolResponse(BaseModel):
    """Returned by the tool-service to the calling agent."""

    protocol_version: Literal["aiat.v1"] = Field(
        default="aiat.v1",
        description="Non-breaking protocol version for cross-runtime contract validation.",
    )

    # Request echo — always set
    tool_name: str = Field(..., description="Tool that was (attempted to be) called.")
    idempotency_key: UUID | None = Field(default=None)
    worker_run_id: UUID | None = Field(default=None, description="Durable worker run associated with this response.")

    # Outcome
    success: bool = Field(..., description="True if the tool executed successfully.")
    result: Any | None = Field(
        default=None,
        description="Tool output on success. May be any JSON-serialisable value.",
    )
    error: str | None = Field(
        default=None,
        description="Human-readable error message when success=False.",
    )
    error_code: str | None = Field(
        default=None,
        description=(
            "Machine-readable error code: 'FORBIDDEN', 'CIRCUIT_OPEN', 'RATE_LIMITED', "
            "'TOOL_NOT_FOUND', 'TOOL_ERROR', 'VALIDATION_ERROR'."
        ),
    )

    # Cache / circuit metadata
    cached: bool = Field(
        default=False,
        description="True if this result was served from the Redis tool cache.",
    )
    circuit_state: CircuitState = Field(
        default=CircuitState.CLOSED,
        description="Circuit-breaker state of the tool at response time.",
    )

    # Rate limiting headers (mirrored from HTTP 429)
    rate_limit_remaining: int | None = Field(
        default=None,
        description="Remaining calls in the current rate-limit window for this tool group.",
    )
    rate_limit_reset_at: datetime | None = Field(
        default=None,
        description="UTC time when the rate-limit bucket resets.",
    )

    # Timing
    duration_ms: float | None = Field(
        default=None, description="Wall-clock execution time of the tool in milliseconds."
    )
    responded_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC),
        description="UTC timestamp when the tool-service generated this response.",
    )

    # Observability
    trace_id: str | None = Field(default=None)
    span_id: str | None = Field(default=None)


# ---------------------------------------------------------------------------
# ToolManifestEntry — static description of a single tool (used by policy engine)
# ---------------------------------------------------------------------------


class ToolManifestEntry(BaseModel):
    """Metadata record describing one registered tool."""

    tool_name: str = Field(..., description="Dot-namespaced tool identifier.")
    tool_group: str = Field(
        ..., description="Group the tool belongs to (e.g., 'project', 'sprint', 'infra', 'web')."
    )
    description: str = Field(..., description="Human-readable description of what the tool does.")
    allowed_roles: list[AgentRole] = Field(
        ...,
        description="Roles permitted to call this tool. Enforced by the tool-service.",
    )
    blocked_roles: list[AgentRole] = Field(
        default_factory=list,
        description="Roles explicitly denied regardless of group defaults.",
    )
    rate_limit_calls_per_min: int = Field(
        default=60,
        description="Token-bucket capacity for this tool's group (calls per minute).",
    )
    cache_ttl_seconds: int = Field(
        default=30,
        description="How long to cache results in Redis. 0 disables caching for this tool.",
    )
    idempotent: bool = Field(
        default=True,
        description="If True, identical kwargs always produce the same result (safe to cache/retry).",
    )
    transport: str = Field(
        default="internal",
        description="Tool transport backend: internal | http | mcp | process.",
    )
    deprecated_alias_of: str | None = Field(
        default=None,
        description="If set, this entry is a legacy alias of the canonical tool name.",
    )
