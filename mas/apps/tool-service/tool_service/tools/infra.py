"""DevOps and blob utility tools."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from mas_core.protocols.enums import AgentRole
from mas_core.memory.blob import BlobClient
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup
from ..config import get_settings

logger = logging.getLogger(__name__)

_blob_client: BlobClient | None = None


async def get_blob_client() -> BlobClient:
    """Get or create BlobClient connection."""
    global _blob_client
    if _blob_client is None:
        settings = get_settings()
        _blob_client = BlobClient(
            endpoint_url=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            bucket=settings.minio_bucket,
        )
        await _blob_client.connect()
    return _blob_client


async def close_blob_client() -> None:
    """Close BlobClient connection."""
    global _blob_client
    if _blob_client:
        await _blob_client.close()
        _blob_client = None


_ADMIN = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE, AgentRole.ADMIN]
_WORKER = [
    AgentRole.ORCHESTRATOR,
    AgentRole.EXECUTIVE,
    AgentRole.C_SUITE,
    AgentRole.ADMIN,
    AgentRole.WORKER,
]
_ALL = list(AgentRole)


class InfraProvisionTool(BaseTool):
    name = "infra.provision"
    group = ToolGroup.DEVOPS
    description = "Provision infrastructure resources."
    allowed_roles = _ADMIN
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        resource = kwargs.get("resource", "")
        config = kwargs.get("config", {})
        return {"resource": resource, "config": config, "provisioned": True}


class CICDConfigureTool(BaseTool):
    name = "cicd.configure"
    group = ToolGroup.DEVOPS
    description = "Configure CI/CD pipeline settings."
    allowed_roles = _ADMIN
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        pipeline = kwargs.get("pipeline", "")
        config = kwargs.get("config", {})
        return {"pipeline": pipeline, "config": config, "configured": True}


class MonitoringSetupTool(BaseTool):
    name = "monitoring.setup"
    group = ToolGroup.DEVOPS
    description = "Set up monitoring and alerting rules."
    allowed_roles = _ADMIN
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        rules = kwargs.get("rules", [])
        return {"rules": rules, "setup": True}


class SecretsManageTool(BaseTool):
    name = "secrets.manage"
    group = ToolGroup.DEVOPS
    description = "Manage secrets (create, rotate, revoke)."
    allowed_roles = _ADMIN
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        action = kwargs.get("action", "create")
        name = kwargs.get("name", "")
        value = kwargs.get("value", "")
        return {"action": action, "secret_name": name, "ok": True}


class InfraReadySignalTool(BaseTool):
    name = "infra.ready_signal"
    group = ToolGroup.DEVOPS
    description = "Signal that infrastructure is ready for deployment."
    allowed_roles = _ADMIN
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        target = kwargs.get("target", "")
        return {"signaled": True, "target": target}


class BlobUploadTool(BaseTool):
    name = "blob.upload"
    group = ToolGroup.KPI_UTILITY
    description = "Upload a file to MinIO blob storage."
    allowed_roles = _WORKER
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "default")
        key = kwargs.get("key", "")
        content = kwargs.get("content", "")
        content_type = kwargs.get("content_type", "text/plain")

        if not key:
            raise ValueError("key is required")
        if not content:
            raise ValueError("content is required")

        blob = await get_blob_client()
        try:
            ref = await blob.upload(
                project_id=project_id,
                key=key,
                data=content.encode("utf-8"),
                content_type=content_type,
            )
            return {
                "bucket": ref.bucket,
                "key": ref.key,
                "size_bytes": ref.size_bytes,
                "sha256": ref.sha256,
                "uploaded": True,
            }
        except Exception as e:
            logger.error("blob_upload_error", extra={"key": key, "error": str(e)}, exc_info=True)
            raise RuntimeError(f"Blob upload failed: {e}")


class BlobDownloadTool(BaseTool):
    name = "blob.download"
    group = ToolGroup.KPI_UTILITY
    description = "Download a file from MinIO blob storage."
    allowed_roles = _ALL
    cache_ttl_seconds = 30

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "default")
        key = kwargs.get("key", "")

        if not key:
            raise ValueError("key is required")

        blob = await get_blob_client()
        try:
            data = await blob.download_by_key(project_id=project_id, key=key)
            return {
                "bucket": blob._bucket,
                "key": f"{project_id}/{key}",
                "content": data.decode("utf-8"),
                "size_bytes": len(data),
            }
        except Exception as e:
            logger.error("blob_download_error", extra={"key": key, "error": str(e)}, exc_info=True)
            raise RuntimeError(f"Blob download failed: {e}")


class BlobListTool(BaseTool):
    name = "blob.list"
    group = ToolGroup.KPI_UTILITY
    description = "List objects in a MinIO bucket with optional prefix."
    allowed_roles = _WORKER
    cache_ttl_seconds = 15

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "default")
        prefix = kwargs.get("prefix", "")

        blob = await get_blob_client()
        try:
            objects = await blob.list_objects(project_id=project_id, prefix=prefix)
            return {
                "project_id": project_id,
                "prefix": prefix,
                "objects": objects,
                "count": len(objects),
            }
        except Exception as e:
            logger.error(
                "blob_list_error", extra={"project_id": project_id, "error": str(e)}, exc_info=True
            )
            raise RuntimeError(f"Blob list failed: {e}")
