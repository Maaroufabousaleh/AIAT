"""MEMORY group tools: shared_memory_read, shared_memory_write."""

from __future__ import annotations

from typing import Any

from mas_core.protocols.enums import AgentRole
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup


class SharedMemoryReadTool(BaseTool):
    name = "shared_memory_read"
    group = ToolGroup.KPI_UTILITY
    description = "Read a value from the shared agent memory store."
    allowed_roles = [
        AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE,
        AgentRole.C_SUITE, AgentRole.ADMIN, AgentRole.WORKER,
    ]
    cache_ttl_seconds = 5
    idempotent = True
    max_concurrency = 10

    async def execute(self, **kwargs: Any) -> Any:
        key = kwargs.get("key", "")
        namespace = kwargs.get("namespace", "default")
        return {"key": key, "namespace": namespace, "value": None, "found": False}


class SharedMemoryWriteTool(BaseTool):
    name = "shared_memory_write"
    group = ToolGroup.KPI_UTILITY
    description = "Write a value to the shared agent memory store."
    allowed_roles = [
        AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE,
        AgentRole.C_SUITE, AgentRole.ADMIN, AgentRole.WORKER,
    ]
    cache_ttl_seconds = 0
    idempotent = False
    max_concurrency = 5

    async def execute(self, **kwargs: Any) -> Any:
        key = kwargs.get("key", "")
        value = kwargs.get("value")
        namespace = kwargs.get("namespace", "default")
        return {"key": key, "namespace": namespace, "written": True}
