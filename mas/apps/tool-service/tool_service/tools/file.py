"""FILE group tools: file_read, file_write."""

from __future__ import annotations

from typing import Any

from mas_core.protocols.enums import AgentRole
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup


class FileReadTool(BaseTool):
    name = "file_read"
    group = ToolGroup.KPI_UTILITY
    description = "Read a file from the project workspace."
    allowed_roles = [
        AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE,
        AgentRole.C_SUITE, AgentRole.ADMIN, AgentRole.WORKER,
    ]
    cache_ttl_seconds = 10
    idempotent = True
    max_concurrency = 10

    async def execute(self, **kwargs: Any) -> Any:
        path = kwargs.get("path", "")
        return {"path": path, "content": f"[stub] Contents of {path}", "size_bytes": 0}


class FileWriteTool(BaseTool):
    name = "file_write"
    group = ToolGroup.KPI_UTILITY
    description = "Write content to a file in the project workspace."
    allowed_roles = [
        AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE,
        AgentRole.C_SUITE, AgentRole.ADMIN, AgentRole.WORKER,
    ]
    cache_ttl_seconds = 0
    idempotent = False
    max_concurrency = 5

    async def execute(self, **kwargs: Any) -> Any:
        path = kwargs.get("path", "")
        content = kwargs.get("content", "")
        return {"path": path, "bytes_written": len(content), "success": True}
