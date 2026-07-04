"""Capability group tools backed by the orchestrator worker registry."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from mas_core.protocols.enums import AgentRole
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup

from ._orch_client import orch_delete, orch_get, orch_post

_ADMIN = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE, AgentRole.ADMIN]
_EXEC = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]

def _role_value(role: Any) -> str | None:
    if role is None:
        return None
    return str(getattr(role, "value", role))


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


async def _resolve_orchestrator_worker_id(worker_id: str) -> str | None:
    if _is_uuid(worker_id):
        return worker_id

    workers = await orch_get("/capabilities/workers")
    if not isinstance(workers, list):
        return None
    for worker in workers:
        if not isinstance(worker, dict):
            continue
        adapter_config = worker.get("adapter_config") or {}
        candidates = {
            str(worker.get("id") or ""),
            str(worker.get("name") or ""),
            str(worker.get("worker_id") or ""),
            str(adapter_config.get("worker_id") or ""),
        }
        if worker_id in candidates:
            resolved = worker.get("id")
            return str(resolved) if resolved else None
    return None


class CapabilitySearchTool(BaseTool):
    name = "capability.search"
    group = ToolGroup.CAPABILITY
    description = "Search workers by capability name."
    allowed_roles = _ADMIN
    cache_ttl_seconds = 0

    async def execute(self, **kwargs: Any) -> Any:
        name = str(kwargs.get("name", "")).strip().lower()
        role = _role_value(kwargs.get("role"))
        if name:
            body: dict[str, Any] = {"name": name}
            if role:
                body["role"] = role
            workers = await orch_post("/capabilities/search", body)
        else:
            workers = await orch_get("/capabilities/workers")
        if not isinstance(workers, list):
            raise RuntimeError("orchestrator capability search returned a non-list response")
        return {"query": {"name": name, "role": role}, "workers": workers, "count": len(workers)}


class CapabilityListWorkersTool(BaseTool):
    name = "capability.list_workers"
    group = ToolGroup.CAPABILITY
    description = "List all registered workers and their capabilities."
    allowed_roles = _ADMIN
    cache_ttl_seconds = 0

    async def execute(self, **kwargs: Any) -> Any:
        params: dict[str, Any] = {}
        if kwargs.get("team_id"):
            params["team_id"] = kwargs["team_id"]
        if kwargs.get("status"):
            params["status"] = kwargs["status"]
        workers = await orch_get("/capabilities/workers", params=params or None)
        if not isinstance(workers, list):
            raise RuntimeError("orchestrator worker listing returned a non-list response")
        return {"workers": workers, "count": len(workers)}


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
        adapter_config = dict(kwargs.get("adapter_config") or {})
        adapter_config.setdefault("worker_id", worker_id)
        role = _role_value(record["role"])
        worker = await orch_post(
            "/capabilities/workers",
            {
                "name": record["name"],
                "adapter_type": record["adapter_type"],
                "adapter_config": adapter_config,
                "sandbox_profile": record["sandbox_profile"],
                "role": role,
                "team_id": kwargs.get("team_id"),
                "source_repo": kwargs.get("source_repo"),
                "version_pin": kwargs.get("version_pin"),
                "update_policy": kwargs.get("update_policy") or "manual",
                "capability_names": record["capabilities"],
                "required_tools": list(kwargs.get("required_tools", [])),
            },
        )
        return {"registered": True, "worker": worker}


class CapabilityDeregisterTool(BaseTool):
    name = "capability.deregister"
    group = ToolGroup.CAPABILITY
    description = "Deregister worker capabilities."
    allowed_roles = _EXEC
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        worker_id = str(kwargs.get("worker_id", "")).strip()
        if not worker_id:
            raise ValueError("worker_id is required")
        orchestrator_worker_id = await _resolve_orchestrator_worker_id(worker_id)
        if orchestrator_worker_id is None:
            return {"deregistered": False, "worker_id": worker_id, "reason": "worker_not_found"}
        result = await orch_delete(f"/capabilities/workers/{orchestrator_worker_id}")
        return {
            "deregistered": True,
            "worker_id": worker_id,
            "orchestrator_worker_id": orchestrator_worker_id,
            "result": result,
        }
