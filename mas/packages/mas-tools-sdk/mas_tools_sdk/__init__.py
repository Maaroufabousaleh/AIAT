"""mas-tools-sdk — tool abstractions and HTTP client for the MAS tool-service.

Public API
----------
BaseTool           Abstract base class for tool implementations.
ToolGroup          Enum of the 7 canonical tool groups.
GROUP_RATE_LIMITS  Default rate limits per group.
TOOL_MANIFEST      Canonical dict of all registered tools.
ToolServiceClient  Async HTTP client agents use to call tools.
"""

from .base import BaseTool
from .client import ToolServiceClient
from .groups import GROUP_RATE_LIMITS, ToolGroup
from .manifest import TOOL_MANIFEST

__all__ = [
    "BaseTool",
    "GROUP_RATE_LIMITS",
    "TOOL_MANIFEST",
    "ToolGroup",
    "ToolServiceClient",
]
