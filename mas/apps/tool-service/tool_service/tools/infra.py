"""INFRA group tools — infra, cicd, monitoring, secrets, blob."""

from __future__ import annotations

from typing import Any

from mas_core.protocols.enums import AgentRole
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup

_ADMIN = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE, AgentRole.ADMIN]
_WORKER = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE, AgentRole.ADMIN, AgentRole.WORKER]
_ALL = list(AgentRole)


# ── Infrastructure provisioning ────────────────────────────────────────────

class InfraProvisionTool(BaseTool):
    name = "infra.provision"
    group = ToolGroup.INFRA
    description = "Provision infrastructure resources."
    allowed_roles = _ADMIN
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        return {"resource": kwargs.get("resource", ""), "provisioned": True}


class CICDConfigureTool(BaseTool):
    name = "cicd.configure"
    group = ToolGroup.INFRA
    description = "Configure CI/CD pipeline settings."
    allowed_roles = _ADMIN
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        return {"pipeline": kwargs.get("pipeline", ""), "configured": True}


class MonitoringSetupTool(BaseTool):
    name = "monitoring.setup"
    group = ToolGroup.INFRA
    description = "Set up monitoring and alerting rules."
    allowed_roles = _ADMIN
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        return {"rules": kwargs.get("rules", []), "setup": True}


class SecretsManageTool(BaseTool):
    name = "secrets.manage"
    group = ToolGroup.INFRA
    description = "Manage secrets (create, rotate, revoke)."
    allowed_roles = _ADMIN
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        return {"action": kwargs.get("action", "create"), "secret_name": kwargs.get("name", ""), "ok": True}


class InfraReadySignalTool(BaseTool):
    name = "infra.ready_signal"
    group = ToolGroup.INFRA
    description = "Signal that infrastructure is ready for deployment."
    allowed_roles = _ADMIN
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        return {"signaled": True, "target": kwargs.get("target", "")}


# ── Blob storage ───────────────────────────────────────────────────────────

class BlobUploadTool(BaseTool):
    name = "blob.upload"
    group = ToolGroup.INFRA
    description = "Upload a file to MinIO blob storage."
    allowed_roles = _WORKER
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        return {
            "bucket": kwargs.get("bucket", "default"),
            "key": kwargs.get("key", ""),
            "size_bytes": len(kwargs.get("content", "")),
            "uploaded": True,
        }


class BlobDownloadTool(BaseTool):
    name = "blob.download"
    group = ToolGroup.INFRA
    description = "Download a file from MinIO blob storage."
    allowed_roles = _ALL  # Even sub-agents can download
    cache_ttl_seconds = 30

    async def execute(self, **kwargs: Any) -> Any:
        return {
            "bucket": kwargs.get("bucket", "default"),
            "key": kwargs.get("key", ""),
            "content": "[stub] blob content",
        }


class BlobListTool(BaseTool):
    name = "blob.list"
    group = ToolGroup.INFRA
    description = "List objects in a MinIO bucket with optional prefix."
    allowed_roles = _WORKER
    cache_ttl_seconds = 15

    async def execute(self, **kwargs: Any) -> Any:
        return {
            "bucket": kwargs.get("bucket", "default"),
            "prefix": kwargs.get("prefix", ""),
            "objects": [],
        }
