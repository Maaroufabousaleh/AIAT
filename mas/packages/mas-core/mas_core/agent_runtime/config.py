"""AgentConfig — per-agent Pydantic settings.

Loaded from environment variables (prefixed ``MAS_AGENT_``) or injected
directly in tests. Each team-runner passes a populated AgentConfig instance
when constructing each agent.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ..protocols.envelope import TaskBudget
from ..protocols.enums import AgentRole


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
        default="http://message-router:8000",
        description="Base HTTP URL of the message-router service.",
    )

    # --- Default budget caps (can be overridden per-task via MessageEnvelope.budget) ---
    budget_defaults: TaskBudget = Field(
        default_factory=TaskBudget,
        description="Default resource caps applied when a TASK message has no budget.",
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
        default="gpt-4o",
        description="Default model name passed to LLMGatewayClient.chat_completion().",
    )
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

    # --- LRU dedup ---
    lru_size: int = Field(
        default=1000,
        ge=1,
        description="Capacity of the consume-side LRU idempotency set (message_id values).",
    )

    # --- Structured logging ---
    log_level: str = Field(default="INFO", description="Log level for structlog.")
