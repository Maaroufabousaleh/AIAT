"""DevOps and blob utility tools."""

from __future__ import annotations

import json
import logging
import os
import shlex
from pathlib import Path
from typing import Any

from mas_core.memory.blob import BlobClient
from mas_core.protocols.enums import AgentRole
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup

from ..config import get_settings
from .adapters import _run_process

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


def _adapter_configured(env_name: str) -> bool:
    return bool(os.getenv(env_name, "").strip())


async def _run_configured_adapter(env_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Run a pinned operator-configured adapter without shell interpretation."""
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return {"available": False, "configured": False, "reason": f"{env_name}_not_configured"}
    argv = shlex.split(raw)
    result = await _run_process(
        argv,
        cwd=Path.cwd(),
        input_text=json.dumps(payload),
        timeout=120,
        max_output_bytes=256_000,
    )
    result["configured"] = True
    return result


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
        if _adapter_configured("TOOL_INFRA_PROVISION_COMMAND") and not resource:
            raise ValueError("resource is required")
        return await _run_configured_adapter(
            "TOOL_INFRA_PROVISION_COMMAND", {"resource": resource, "config": config}
        )


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
        if _adapter_configured("TOOL_CICD_CONFIGURE_COMMAND") and not pipeline:
            raise ValueError("pipeline is required")
        return await _run_configured_adapter(
            "TOOL_CICD_CONFIGURE_COMMAND", {"pipeline": pipeline, "config": config}
        )


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
        return await _run_configured_adapter("TOOL_MONITORING_SETUP_COMMAND", {"rules": rules})


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
        configured = _adapter_configured("TOOL_SECRETS_COMMAND")
        if configured and action not in {"create", "rotate", "revoke"}:
            raise ValueError("action must be create, rotate, or revoke")
        if configured and not name:
            raise ValueError("name is required")
        return await _run_configured_adapter(
            "TOOL_SECRETS_COMMAND",
            {"action": action, "name": name, "value": kwargs.get("value")},
        )


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
        if _adapter_configured("TOOL_INFRA_READY_COMMAND") and not target:
            raise ValueError("target is required")
        return await _run_configured_adapter("TOOL_INFRA_READY_COMMAND", {"target": target})


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
            raise RuntimeError(f"Blob upload failed: {e}") from e


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
            raise RuntimeError(f"Blob download failed: {e}") from e


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
            raise RuntimeError(f"Blob list failed: {e}") from e


class BlobDeleteTool(BaseTool):
    name = "blob.delete"
    group = ToolGroup.KPI_UTILITY
    description = "Delete an object from S3-compatible blob storage."
    allowed_roles = _WORKER
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "default")
        key = kwargs.get("key", "")

        if not key:
            raise ValueError("key is required")

        blob = await get_blob_client()
        try:
            await blob.delete_by_key(project_id=project_id, key=key)
            return {
                "project_id": project_id,
                "key": f"{project_id}/{key}",
                "deleted": True,
            }
        except Exception as e:
            logger.error("blob_delete_error", extra={"key": key, "error": str(e)}, exc_info=True)
            raise RuntimeError(f"Blob delete failed: {e}") from e
