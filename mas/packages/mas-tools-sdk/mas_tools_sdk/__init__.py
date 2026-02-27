"""
mas-tools-sdk — Tool interface definition + async HTTP client for tool-service.

Exports (Phase 6)
-----------------
BaseTool           Abstract base class every tool implementation must inherit.
                   execute(request: ToolRequest) → ToolResponse
ToolServiceClient  Async HTTP client that wraps POST /tools/execute.
                   Handles 429 rate-limit back-off and circuit breaker detection.
ToolGroup          Enum of the six tool groups defined in the tool-service manifest:
                     WEB, FILE, MEMORY, PROJECT, SPRINT_KPI, INFRA
TOOL_MANIFEST      Dict of tool_name → ToolDefinition (name, group, min_role, description).
"""

# Populated in Phase 6.
