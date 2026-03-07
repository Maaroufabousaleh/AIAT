"""Capability group tools.

Phase 6 requires capability tooling before full Phase 7 persistence wiring.
This module uses an in-memory registry as a compatibility bridge.
"""

from __future__ import annotations

from typing import Any

from mas_core.protocols.enums import AgentRole
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup

_ADMIN = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE, AgentRole.ADMIN]
_EXEC = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]

_REGISTRY: dict[str, dict[str, Any]] = {}


class CapabilitySearchTool(BaseTool):
    name = "capability.search"
    group = ToolGroup.CAPABILITY
    description = "Search workers by capability name."
    allowed_roles = _ADMIN
    cache_ttl_seconds = 15

    async def execute(self, **kwargs: Any) -> Any:
        name = str(kwargs.get("name", "")).strip().lower()
        role = kwargs.get("role")
        workers: list[dict[str, Any]] = []
        for worker in _REGISTRY.values():
            caps = worker.get("capabilities", [])
            has_cap = any(str(c).lower() == name for c in caps) if name else True
            if not has_cap:
                continue
            if role and worker.get("role") != role:
                continue
            workers.append(worker)
        return {"query": {"name": name, "role": role}, "workers": workers, "count": len(workers)}


class CapabilityListWorkersTool(BaseTool):
    name = "capability.list_workers"
    group = ToolGroup.CAPABILITY
    description = "List all registered workers and their capabilities."
    allowed_roles = _ADMIN
    cache_ttl_seconds = 15

    async def execute(self, **kwargs: Any) -> Any:
        return {"workers": list(_REGISTRY.values()), "count": len(_REGISTRY)}


class CapabilityRegisterTool(BaseTool):
    name = "capability.register"
    group = ToolGroup.CAPABILITY
    description = "Register worker capabilities."
    allowed_roles = _EXEC
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        worker_id = str(kwargs.get("worker_id", "")).strip()
        if not worker_id:
            raise ValueError("worker_id is required")
        record = {
            "worker_id": worker_id,
            "name": kwargs.get("name", worker_id),
            "role": kwargs.get("role"),
            "capabilities": list(kwargs.get("capabilities", [])),
            "sandbox_profile": kwargs.get("sandbox_profile", "standard"),
            "adapter_type": kwargs.get("adapter_type", "process"),
        }
        _REGISTRY[worker_id] = record
        return {"registered": True, "worker": record}


class CapabilityDeregisterTool(BaseTool):
    name = "capability.deregister"
    group = ToolGroup.CAPABILITY
    description = "Deregister worker capabilities."
    allowed_roles = _EXEC
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        worker_id = str(kwargs.get("worker_id", "")).strip()
        removed = _REGISTRY.pop(worker_id, None)
        return {"deregistered": removed is not None, "worker_id": worker_id}

