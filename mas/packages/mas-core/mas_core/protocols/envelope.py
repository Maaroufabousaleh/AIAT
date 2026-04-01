"""Unified MessageEnvelope — the single canonical message format for all MAS communication.

Design notes
------------
* All agents, the router, and the controller use **this one schema**. The previous
  dual-schema design (RouterEnvelope wrapping Message) is replaced entirely.
* Payloads must stay ≤ 64 KB when serialised. Anything larger must be stored in
  MinIO and referenced via ``BlobRef``.
* ``message_id`` serves as the idempotency key. The router deduplicates publishes
  within a 300 s window; consumers use an LRU set to guard against XAUTOCLAIM
  re-delivery races.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, PrivateAttr, model_validator

from .enums import AgentRole, MessageType

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_PAYLOAD_BYTES: int = 64 * 1024  # 64 KB hard limit for inline payloads


# ---------------------------------------------------------------------------
# BlobRef — pointer to an object stored in MinIO / S3-compatible storage
# ---------------------------------------------------------------------------


class BlobRef(BaseModel):
    """Reference to a large object stored outside Redis (MinIO / S3).

    When a payload would exceed ``MAX_PAYLOAD_BYTES``, agents upload the data to
    MinIO and include a ``BlobRef`` in ``MessageEnvelope.blob_ref`` instead of
    inlining the data in ``payload``.
    """

    bucket: str = Field(..., description="MinIO bucket name (e.g. 'mas-agents')")
    key: str = Field(
        ...,
        description="Object key within the bucket (e.g. '{project_id}/documents/pdr_v1.json')",
    )
    sha256: str = Field(..., description="Hex-encoded SHA-256 digest of the stored object")
    size_bytes: int = Field(..., ge=0, description="Size of the stored object in bytes")
    content_type: str = Field(
        default="application/json",
        description="MIME type of the stored object",
    )


# ---------------------------------------------------------------------------
# TaskBudget — per-task resource caps embedded in TASK / ADMIN_TASK messages
# ---------------------------------------------------------------------------


class TaskBudget(BaseModel):
    """Resource budget for a single task.

    Embedded in ``MessageEnvelope.budget`` for ``TASK`` and ``ADMIN_TASK``
    messages. The agent runtime enforces these caps during ``think()`` loops.
    ``None`` means uncapped.
    """

    max_llm_calls: int | None = Field(
        default=None, ge=1, description="Maximum number of LLM API calls allowed"
    )
    max_tool_calls: int | None = Field(
        default=None, ge=0, description="Maximum number of tool-service calls allowed"
    )
    max_subtasks: int | None = Field(
        default=None, ge=0, description="Maximum sub-agents / subtasks that may be spawned"
    )
    deadline: datetime | None = Field(
        default=None, description="Hard deadline (UTC). Agent must return partial result if hit."
    )
    max_cost_usd: float | None = Field(
        default=None, ge=0.0, description="Maximum estimated LLM cost in USD"
    )

    # Runtime-only counters — NOT serialised to Redis (Pydantic v2 PrivateAttr)
    _llm_calls_used: int = PrivateAttr(default=0)
    _tool_calls_used: int = PrivateAttr(default=0)
    _subtasks_used: int = PrivateAttr(default=0)
    _cost_usd_used: float = PrivateAttr(default=0.0)

    def llm_budget_remaining(self) -> bool:
        """Return True if another LLM call is permitted."""
        if self.max_llm_calls is None:
            return True
        return self._llm_calls_used < self.max_llm_calls

    def tool_budget_remaining(self) -> bool:
        """Return True if another tool call is permitted."""
        if self.max_tool_calls is None:
            return True
        return self._tool_calls_used < self.max_tool_calls

    def deadline_exceeded(self) -> bool:
        """Return True if the wall-clock deadline has passed."""
        if self.deadline is None:
            return False
        return datetime.now(tz=UTC) >= self.deadline


# ---------------------------------------------------------------------------
# MessageEnvelope — single canonical wire format
# ---------------------------------------------------------------------------


class MessageEnvelope(BaseModel):
    """Unified message envelope for all MAS inter-agent communication.

    Used by agents, the message router, Redis Streams storage, and the
    orchestrator-api workflow controller. Every message in the system, regardless
    of direction (horizontal, vertical, human-loop), must use this schema.

    Payload size rule
    -----------------
    ``payload`` must serialise to ≤ ``MAX_PAYLOAD_BYTES`` (64 KB). If the actual
    data is larger, upload to MinIO and reference it via ``blob_ref``. The
    validator below enforces this automatically.

    Idempotency
    -----------
    ``message_id`` is the idempotency key. The router deduplicates publishes with
    a 300 s Redis TTL; consumers maintain a 1 000-entry LRU set keyed on
    ``message_id`` to guard against XAUTOCLAIM re-delivery races.
    """

    # --- Identity & correlation ---
    message_id: UUID = Field(
        default_factory=uuid4,
        description="Globally unique idempotency key (UUIDv4). Set once by the publisher.",
    )
    correlation_id: UUID | None = Field(
        default=None,
        description="ID of the originating TASK/QUERY this message relates to.",
    )
    parent_id: UUID | None = Field(
        default=None,
        description="ID of the direct parent message (for request/reply threading).",
    )

    # --- Type & routing ---
    msg_type: MessageType = Field(..., description="Message type — governs routing and handling.")
    sender_id: str = Field(..., description="Agent ID of the sender (e.g. 'ceo_agent').")
    sender_role: AgentRole = Field(..., description="Sender's role in the corporate hierarchy.")
    sender_team: str = Field(..., description="Sender team ID (e.g. 'exec_ceo', 'dept_system').")
    recipient_id: str | None = Field(
        default=None,
        description="Target agent ID. Mutually exclusive with recipient_team for direct messages.",
    )
    recipient_team: str | None = Field(
        default=None,
        description="Target team ID (e.g. 'dept_production'). Router delivers to team stream.",
    )

    # --- Project context ---
    project_id: str | None = Field(
        default=None,
        description="Project this message belongs to. Mandatory for all messages after INIT.",
    )

    # --- Timing & delivery ---
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC),
        description="UTC timestamp when the message was created.",
    )
    ttl_seconds: int = Field(
        default=3600,
        ge=1,
        description="Time-to-live in seconds. Router discards / DLQs messages older than this.",
    )
    retry_count: int = Field(
        default=0,
        ge=0,
        description="Number of delivery attempts so far. Incremented by XAUTOCLAIM reclaims.",
    )
    ack_required: bool = Field(
        default=True,
        description="If True, the consumer must XACK after processing. If False, fire-and-forget.",
    )

    # --- Payload ---
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Inline message body (task parameters, results, etc.). "
            "Must serialise to ≤ 64 KB. Use blob_ref for larger data."
        ),
    )
    blob_ref: BlobRef | None = Field(
        default=None,
        description="Reference to large payload stored in MinIO. Use instead of inline payload.",
    )

    # --- Budget (TASK / ADMIN_TASK only) ---
    budget: TaskBudget | None = Field(
        default=None,
        description="Resource caps for this task. Only valid on TASK and ADMIN_TASK messages.",
    )

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def payload_size_check(self) -> MessageEnvelope:
        """Reject payloads that exceed MAX_PAYLOAD_BYTES unless blob_ref is set.

        When ``blob_ref`` is present the payload is expected to be a small
        metadata dict pointing at the real data in MinIO, so it's still capped.
        However, we only enforce the cap when there is **no** blob_ref —
        callers that exceed the limit must either shrink the payload or
        upload to MinIO and provide a ``BlobRef``.
        """
        encoded = json.dumps(self.payload, default=str).encode("utf-8")
        if len(encoded) > MAX_PAYLOAD_BYTES and self.blob_ref is None:
            raise ValueError(
                f"payload serialises to {len(encoded):,} bytes which exceeds "
                f"MAX_PAYLOAD_BYTES ({MAX_PAYLOAD_BYTES:,}). "
                "Upload the data to MinIO and supply a BlobRef instead."
            )
        return self

    @model_validator(mode="after")
    def routing_check(self) -> MessageEnvelope:
        """Validate recipient routing target shape."""
        exempt = {MessageType.HEARTBEAT, MessageType.ACK, MessageType.BROADCAST}
        if self.msg_type not in exempt:
            if self.recipient_id is None and self.recipient_team is None:
                raise ValueError(
                    "At least one of 'recipient_id' or 'recipient_team' must be set."
                )
        if (
            self.recipient_id is not None
            and self.recipient_team is not None
            and self.msg_type != MessageType.BROADCAST
        ):
            raise ValueError("Use either 'recipient_id' or 'recipient_team', not both.")
        return self

    @model_validator(mode="after")
    def budget_scope_check(self) -> MessageEnvelope:
        """Ensure budget is only set on TASK / ADMIN_TASK messages."""
        budget_allowed = {MessageType.TASK, MessageType.ADMIN_TASK}
        if self.budget is not None and self.msg_type not in budget_allowed:
            raise ValueError(
                f"'budget' is only valid on TASK and ADMIN_TASK messages, "
                f"not on {self.msg_type}."
            )
        return self

    @model_validator(mode="after")
    def correlation_default_check(self) -> MessageEnvelope:
        """Default root correlation_id to message_id."""
        if self.correlation_id is None:
            self.correlation_id = self.message_id
        return self

    @model_validator(mode="after")
    def project_scope_check(self) -> MessageEnvelope:
        """Require project context for all project-bound message types."""
        exempt = {
            MessageType.SHUTDOWN,
            MessageType.HEARTBEAT,
            MessageType.ACK,
            MessageType.SYSTEM_EVENT,
        }
        if self.msg_type not in exempt and self.project_id is None:
            raise ValueError("'project_id' is required for this message type.")
        return self

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def is_expired(self) -> bool:
        """Return True if the message has exceeded its TTL."""
        age = (datetime.now(tz=UTC) - self.timestamp).total_seconds()
        return age > self.ttl_seconds

    def reply(
        self,
        msg_type: MessageType,
        payload: dict[str, Any] | None = None,
        *,
        sender_id: str,
        sender_role: AgentRole,
        sender_team: str,
        **kwargs: Any,
    ) -> MessageEnvelope:
        """Construct a reply to this message with correlation wired up."""
        return MessageEnvelope(
            msg_type=msg_type,
            sender_id=sender_id,
            sender_role=sender_role,
            sender_team=sender_team,
            recipient_id=self.sender_id,
            correlation_id=self.correlation_id,
            parent_id=self.message_id,
            project_id=self.project_id,
            payload=payload or {},
            **kwargs,
        )
