"""AgentConfig — per-agent Pydantic settings.

Loaded from environment variables (prefixed ``MAS_AGENT_``) or injected
directly in tests. Each team-runner passes a populated AgentConfig instance
when constructing each agent.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any
from uuid import UUID  # noqa: TC003 - Pydantic resolves UUID annotations at runtime.

if TYPE_CHECKING:
    from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ..protocols.enums import AgentRole  # noqa: TC001 - Pydantic resolves this at runtime.
from ..protocols.envelope import TaskBudget


class GovernanceModelBinding(BaseModel):
    """Immutable model decision supplied to a governance-agent invocation.

    Specialist worker runs receive the full model-resolution snapshot from the
    orchestrator.  Governance agents currently have a separate runtime plane,
    so this small projection is the explicit hand-off point when a caller has
    resolved a model for an AgentBase invocation.  It is intentionally not a
    resolver, scheduler, or authority store.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    resolution_snapshot_id: UUID
    profile_id: str
    profile_version: str | None = None
    provider_id: str
    exact_model_id: str

    @field_validator("profile_id", "provider_id", "exact_model_id")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("governance model binding values must not be blank")
        return value

    @field_validator("profile_version")
    @classmethod
    def _normalise_version(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def _reject_unmanaged_model(self) -> GovernanceModelBinding:
        if self.exact_model_id.lower() in {"auto", "default", "latest"}:
            raise ValueError("governance model binding requires an exact model ID")
        return self

    @classmethod
    def from_snapshot(
        cls,
        snapshot_id: UUID,
        snapshot: Mapping[str, Any],
    ) -> GovernanceModelBinding:
        """Build the runtime projection from a persisted AIAT snapshot.

        Resolution and project-scope authorization remain control-plane
        responsibilities.  This helper only performs the narrow, immutable
        projection that governance runtimes are allowed to consume.
        """
        return cls(
            resolution_snapshot_id=snapshot_id,
            profile_id=str(snapshot.get("resolved_profile_id") or ""),
            profile_version=(
                str(snapshot["resolved_profile_version"])
                if snapshot.get("resolved_profile_version") is not None
                else None
            ),
            provider_id=str(snapshot.get("provider_id") or ""),
            exact_model_id=str(snapshot.get("exact_model_id") or ""),
        )


class AgentConfig(BaseSettings):
    """Configuration for a single agent instance.

    All fields can be set via environment variables prefixed with
    ``MAS_AGENT_`` (e.g. ``MAS_AGENT_ID=ceo_agent``).
    """

    model_config = SettingsConfigDict(
        env_prefix="MAS_AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Identity ---
    agent_id: str = Field(..., description="Unique agent identifier, e.g. 'ceo_agent'.")
    team_id: str = Field(..., description="Team this agent belongs to, e.g. 'exec_ceo'.")
    worker_manifest_ref: str | None = Field(
        default=None,
        description=(
            "Explicit checked-in worker manifest identity for this team-runner agent. "
            "The reference is metadata for runtime provenance; it never registers or "
            "activates a worker."
        ),
    )
    agent_role: AgentRole = Field(..., description="Role in the corporate hierarchy.")
    agent_secret: str = Field(
        ...,
        description=(
            "Shared secret used to authenticate the WS subscription with the router. "
            "Passed as 'Bearer {agent_id}:{agent_secret}' in the Authorization header."
        ),
    )

    # --- Router connection ---
    router_url: str = Field(
        default="http://message-router:8001",
        description="Base HTTP URL of the message-router service.",
    )

    # --- Default budget caps (can be overridden per-task via MessageEnvelope.budget) ---
    budget_defaults: TaskBudget = Field(
        default_factory=TaskBudget,
        description="Default resource caps applied when a TASK message has no budget.",
    )
    tool_names: list[str] = Field(
        default_factory=list,
        description=(
            "Canonical tool names declared by team configuration. These are additive "
            "hints; runtime tool exposure is derived from the manifest and policy."
        ),
    )
    tool_definitions: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Runtime-discovered OpenAI-format tool definitions for this agent.",
    )

    # --- Think-loop tuning ---
    max_think_iterations: int = Field(
        default=20,
        ge=1,
        description="Maximum LLM call iterations per think() loop before forcing a stop.",
    )
    checkpoint_interval: int = Field(
        default=1,
        ge=1,
        description=(
            "Save a checkpoint every N iterations inside think(). "
            "1 = after every LLM call (safest). Higher values reduce DB writes."
        ),
    )

    # --- LLM defaults for think() ---
    llm_model: str = Field(
        default="auto",
        description=(
            "Default LiteLLM/OmniRoute alias passed to "
            "LLMGatewayClient.chat_completion()."
        ),
    )
    model_resolution_binding: GovernanceModelBinding | None = Field(
        default=None,
        description=(
            "Optional immutable AIAT model decision for this governance-agent "
            "runtime. When present, AgentBase uses the exact model and records "
            "the resolution snapshot; when absent, the legacy direct-gateway "
            "path remains visible as unresolved until TeamRunner supplies a "
            "per-invocation binding."
        ),
    )
    require_model_resolution_binding: bool = Field(
        default=False,
        description=(
            "If True, every AgentBase model call requires a per-dispatch or "
            "static GovernanceModelBinding. TeamRunner enables this when its "
            "control-plane storage boundary is active; direct compatibility "
            "fixtures may leave it disabled."
        ),
    )

    @field_validator("llm_model", mode="before")
    @classmethod
    def _resolve_llm_model(cls, v: str) -> str:
        """Fall back to LLM_DEFAULT_MODEL env var if llm_model is the built-in default."""
        env_model = os.environ.get("LLM_DEFAULT_MODEL", "").strip()
        # If an explicit MAS_AGENT_LLM_MODEL was set, honour it.
        # Otherwise use LLM_DEFAULT_MODEL if available.
        if v in {"auto", "gemini-2.5-flash"} and env_model:
            return env_model
        return v

    llm_temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="Default sampling temperature for think() LLM calls.",
    )
    llm_max_tokens: int | None = Field(
        default=None,
        ge=1,
        description="Optional max_tokens cap for think() LLM calls.",
    )
    llm_stream: bool = Field(
        default=False,
        description="If True, think() uses streaming mode on the LLM gateway by default.",
    )
    llm_use_fallback: bool = Field(
        default=True,
        description=(
            "If True, think() uses chat_completion_with_fallback for automatic model "
            "fallback on rate limits/errors."
        ),
    )
    llm_fallback_task: str | None = Field(
        default=None,
        description=(
            "Optional task hint for fallback chain (e.g., 'reasoning', "
            "'code-generation', 'tool-calling')."
        ),
    )
    llm_fallback_chain_length: int = Field(
        default=4,
        ge=1,
        le=10,
        description="Maximum number of models to try in fallback chain.",
    )

    # --- LRU dedup ---
    lru_size: int = Field(
        default=1000,
        ge=1,
        description="Capacity of the consume-side LRU idempotency set (message_id values).",
    )

    # --- Structured logging ---
    log_level: str = Field(default="INFO", description="Log level for structlog.")
