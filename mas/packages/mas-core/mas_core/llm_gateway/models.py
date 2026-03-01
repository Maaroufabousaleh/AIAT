"""LLM Gateway models — config and response types.

These are consumed by ``LLMGatewayClient`` and by ``AgentBase``
(which passes token counts to ``BudgetTracker``).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class LLMConfig(BaseSettings):
    """Settings for the LLM gateway client.

    Loaded from environment variables prefixed with ``LLM_``.
    """

    model_config = SettingsConfigDict(
        env_prefix="LLM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gateway_url: str = Field(
        default="http://llm-gateway:8080",
        description=(
            "Base URL of the OpenAI-compatible LLM provider, "
            "e.g. 'https://api.openai.com' or your custom proxy."
        ),
    )
    default_model: str = Field(
        default="gemma-3-27b-it",
        description="Model to use when no model is specified in the call.",
    )
    api_key: str = Field(
        default="",
        description=(
            "API key passed in the Authorization header. "
            "For custom proxies that use a different auth scheme, "
            "set to the appropriate token."
        ),
    )
    timeout_s: float = Field(
        default=120.0,
        ge=1.0,
        description="HTTP request timeout for chat completion calls (seconds).",
    )
    max_retries: int = Field(
        default=3,
        ge=0,
        description="Maximum number of retry attempts on 429 / 5xx responses.",
    )
    retry_min_wait_s: float = Field(
        default=1.0,
        description="Minimum wait between retries (seconds, exponential backoff).",
    )
    retry_max_wait_s: float = Field(
        default=60.0,
        description="Maximum wait between retries (seconds).",
    )


# ---------------------------------------------------------------------------
# OpenAI-compatible request fragments
# ---------------------------------------------------------------------------


class ToolFunction(BaseModel):
    """Describes a function that can be invoked by the LLM."""

    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


class ToolDefinition(BaseModel):
    """OpenAI-format tool definition (``tools`` list element)."""

    type: str = "function"
    function: ToolFunction


# ---------------------------------------------------------------------------
# OpenAI-compatible response fragments
# ---------------------------------------------------------------------------


class ToolCallFunction(BaseModel):
    name: str
    arguments: str  # JSON-encoded string from the LLM


class ToolCall(BaseModel):
    """One tool invocation requested by the LLM."""

    id: str
    type: str = "function"
    function: ToolCallFunction


class ChatMessage(BaseModel):
    """Single message in the conversation history."""

    role: str  # "system" | "user" | "assistant" | "tool"
    content: str | None = None
    tool_calls: list[ToolCall] | None = None  # set when role == "assistant"
    tool_call_id: str | None = None  # set when role == "tool"
    name: str | None = None  # set when role == "tool"


class UsageStats(BaseModel):
    """Token usage counts from the LLM response."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    @property
    def estimated_cost_usd(self) -> float:
        """Rough cost estimate using gpt-4o pricing ($5 / 1M in, $15 / 1M out).

        Override in subclasses or pass a real pricing table for accuracy.
        Defaults are intentionally conservative.
        """
        return (self.prompt_tokens / 1_000_000 * 5.0) + (self.completion_tokens / 1_000_000 * 15.0)


# ---------------------------------------------------------------------------
# Normalised response
# ---------------------------------------------------------------------------


class ChatResponse(BaseModel):
    """Normalised chat completion response returned by ``LLMGatewayClient``.

    Wraps the raw OpenAI response and exposes the fields agents actually need.
    """

    response_id: str = Field(default_factory=lambda: str(uuid4()))
    model: str = ""
    finish_reason: str = "stop"  # "stop" | "tool_calls" | "length" | "content_filter"

    # The assistant message (role="assistant") appended to the conversation
    message: ChatMessage

    # Tool calls requested by the assistant (convenience copy from message.tool_calls)
    tool_calls: list[ToolCall] = Field(default_factory=list)

    usage: UsageStats = Field(default_factory=UsageStats)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    @property
    def text(self) -> str:
        """Return the assistant's text content (empty string if tool-call-only response)."""
        return self.message.content or ""
