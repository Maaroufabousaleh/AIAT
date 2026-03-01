"""
llm_gateway — Scalable multi-provider LLM client.

Exports
-------
LLMGatewayClient      Async HTTP client with automatic provider routing.
                      Supports chat-completions, responses, and CLI API styles.
                      Retry: exponential backoff on 429 / 5xx.
                      Tracks token usage per call; feeds BudgetTracker.
                      Continuous audit, metrics, and rate-limit discovery.
LLMConfig             Pydantic settings model (url, default_model, timeout_s).
ChatResponse          Normalised response with usage stats.
MODEL_REGISTRY        Singleton model catalog; register custom models at startup.
ModelRegistry         Registry class (for typing / custom instances).
ModelEntry            Metadata for a single model.
ProviderConfig        Shared settings for a provider (auth, headers, base URL).
ApiStyle              Enum: CHAT_COMPLETIONS | RESPONSES | CLI.
CopilotModelScanner   Discovers and registers free Copilot CLI models.
MistralModelScanner   Discovers and refreshes Mistral models from /v1/models.
COPILOT_COST_MAP      Known cost multipliers for Copilot models.
AuditLog              Bounded in-memory audit trail with external sinks.
AuditEvent            Single auditable LLM gateway call.
AuditLevel            Detail level for audit capture.
MetricsCollector      Real-time sliding-window metrics per model.
RateLimitTracker      Empirical rate-limit discovery from 429 observations.
SmartRouter           Metrics-enhanced intelligent model selection.
ModelSelector         Task-aware automatic model selection with fallback chains.
ConversationContext   Stateful multi-turn conversation context manager.
create_observability_router  FastAPI router factory for all endpoints + UI.
DASHBOARD_HTML        Self-contained HTML/JS dashboard (served at /ui).
ObservabilityPersistence  Persist audit/metrics/rate-limit data across restarts.
"""

from .audit import AuditEvent, AuditLevel, AuditLog
from .client import (
    LLMGatewayClient,
    LLMGatewayError,
    LLMRateLimited,
    _ConversationContext as ConversationContext,
)
from .dashboard import DASHBOARD_HTML
from .metrics import MetricsCollector, Window as MetricsWindow
from .model_selector import ModelSelector
from .providers.api.mistral import MistralModelScanner
from .providers.cli.copilot import COPILOT_COST_MAP, CopilotModelScanner
from .rate_limits import RateLimitTracker, ModelRateLimits, ExperimentalLimit
from .persistence import ObservabilityPersistence
from .routes_observability import create_observability_router
from .smart_router import SmartRouter, ModelScore
from .thinking import Depth as ThinkingDepth, ThinkingChain, ThinkingResult
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
    "MistralModelScanner",
    "COPILOT_COST_MAP",
    "ThinkingChain",
    "ThinkingDepth",
    "ThinkingResult",
    # Observability
    "AuditLog",
    "AuditEvent",
    "AuditLevel",
    "MetricsCollector",
    "MetricsWindow",
    "RateLimitTracker",
    "ModelRateLimits",
    "ExperimentalLimit",
    "SmartRouter",
    "ModelScore",
    "ModelSelector",
    "ConversationContext",
    "create_observability_router",
    "DASHBOARD_HTML",
    "ObservabilityPersistence",
]
