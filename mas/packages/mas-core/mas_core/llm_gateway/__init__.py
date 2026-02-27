"""
llm_gateway — Async LLM client targeting an OpenAI-compatible provider.

Exports (Phase 5)
-----------------
LLMGatewayClient   Async HTTP client; configured via LLM_GATEWAY_URL env var.
                   chat_completion(messages, model, **kwargs) → ChatResponse
                   Retry: exponential backoff on 429 / 5xx.
                   Tracks token usage per call; feeds BudgetTracker.
LLMConfig          Pydantic settings model (url, default_model, timeout_s).
ChatResponse       Normalised response with usage stats.
"""

from .client import LLMGatewayClient, LLMGatewayError, LLMRateLimited
from .models import (
    ChatMessage,
    ChatResponse,
    LLMConfig,
    ToolCall,
    ToolCallFunction,
    ToolDefinition,
    ToolFunction,
    UsageStats,
)

__all__ = [
    "LLMGatewayClient",
    "LLMGatewayError",
    "LLMRateLimited",
    "LLMConfig",
    "ChatMessage",
    "ChatResponse",
    "ToolCall",
    "ToolCallFunction",
    "ToolDefinition",
    "ToolFunction",
    "UsageStats",
]
