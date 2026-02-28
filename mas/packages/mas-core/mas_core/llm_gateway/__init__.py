"""
llm_gateway — Scalable multi-provider LLM client.

Exports
-------
LLMGatewayClient      Async HTTP client with automatic provider routing.
                      Supports chat-completions, responses, and CLI API styles.
                      Retry: exponential backoff on 429 / 5xx.
                      Tracks token usage per call; feeds BudgetTracker.
LLMConfig             Pydantic settings model (url, default_model, timeout_s).
ChatResponse          Normalised response with usage stats.
MODEL_REGISTRY        Singleton model catalog; register custom models at startup.
ModelRegistry         Registry class (for typing / custom instances).
ModelEntry            Metadata for a single model.
ProviderConfig        Shared settings for a provider (auth, headers, base URL).
ApiStyle              Enum: CHAT_COMPLETIONS | RESPONSES | CLI.
CopilotModelScanner   Discovers and registers free Copilot CLI models.
COPILOT_COST_MAP      Known cost multipliers for Copilot models.
"""

from .client import LLMGatewayClient, LLMGatewayError, LLMRateLimited
from .providers.cli.copilot import COPILOT_COST_MAP, CopilotModelScanner
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
from .providers import (
    MODEL_REGISTRY,
    ApiStyle,
    ModelCapabilities,
    ModelEntry,
    ModelRegistry,
    ProviderConfig,
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
    "MODEL_REGISTRY",
    "ModelRegistry",
    "ModelCapabilities",
    "ModelEntry",
    "ProviderConfig",
    "ApiStyle",
    "CopilotModelScanner",
    "COPILOT_COST_MAP",
]
