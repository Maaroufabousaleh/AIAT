"""DevOps and blob utility tools."""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
from pathlib import Path
from typing import Any

import yaml

from mas_core.memory.blob import BlobClient
from mas_core.protocols.enums import AgentRole
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup

from ..config import get_settings
from ._orch_client import orch_delete, orch_patch, orch_post
from .adapters import _run_process, _workspace_cwd
from .file import _safe_workspace_path

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


async def _run_configured_adapter(
    env_name: str,
    payload: dict[str, Any],
    *,
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Run a pinned operator-configured adapter without shell interpretation."""
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return {"available": False, "configured": False, "reason": f"{env_name}_not_configured"}
    argv = shlex.split(raw)
    result = await _run_process(
        argv,
        cwd=cwd or Path.cwd(),
        input_text=json.dumps(payload),
        timeout=120,
        max_output_bytes=256_000,
    )
    result["configured"] = True
    if result.get("returncode") == 0 and result.get("stdout"):
        try:
            parsed = json.loads(result["stdout"])
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(parsed, dict):
                parsed.setdefault("configured", True)
                parsed["adapter_process"] = {
                    "returncode": result.get("returncode"),
                    "stdout_truncated": result.get("stdout_truncated", False),
                    "stderr": result.get("stderr", ""),
                    "stderr_truncated": result.get("stderr_truncated", False),
                }
                return parsed
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
        config = dict(kwargs.get("config") or {})
        configured = _adapter_configured("TOOL_INFRA_PROVISION_COMMAND")
        if configured and not resource:
            raise ValueError("resource is required")
        if not configured:
            return await _run_configured_adapter(
                "TOOL_INFRA_PROVISION_COMMAND", {"resource": resource, "config": config}
            )
        cwd = _workspace_cwd(str(kwargs.get("project_id") or ""), str(config.pop("cwd", ".")))
        return await _run_configured_adapter(
            "TOOL_INFRA_PROVISION_COMMAND",
            {"resource": resource, "config": config},
            cwd=cwd,
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
        pipeline = str(kwargs.get("pipeline") or "").strip()
        config = dict(kwargs.get("config") or {})
        workflow = config.get("workflow")
        if not pipeline or not re.fullmatch(r"[A-Za-z0-9._-]+", pipeline):
            raise ValueError("pipeline must contain only letters, numbers, dot, underscore, or dash")
        if not isinstance(workflow, dict) or "jobs" not in workflow:
            raise ValueError("config.workflow must be a GitHub Actions workflow containing jobs")

        relative_path = str(config.get("path") or f".github/workflows/{pipeline}.yml")
        output_path = _safe_workspace_path(
            relative_path,
            project_id=str(kwargs.get("project_id") or ""),
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        content = yaml.safe_dump(workflow, sort_keys=False)
        output_path.write_text(content, encoding="utf-8")
        return {
            "available": True,
            "configured": True,
            "backend": "github_actions",
            "pipeline": pipeline,
            "path": relative_path,
            "bytes_written": len(content.encode("utf-8")),
        }


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
        config = dict(kwargs.get("config") or {})
        cwd = _workspace_cwd(str(kwargs.get("project_id") or ""), str(config.pop("cwd", ".")))
        return await _run_configured_adapter(
            "TOOL_MONITORING_SETUP_COMMAND",
            {"rules": rules, "config": config},
            cwd=cwd,
        )


class SecretsManageTool(BaseTool):
    name = "secrets.manage"
    group = ToolGroup.DEVOPS
    description = "Manage secrets (create, rotate, revoke)."
    allowed_roles = _ADMIN
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        action = str(kwargs.get("action") or "create")
        name = str(kwargs.get("name") or "").strip()
        if action not in {"create", "rotate", "revoke"}:
            raise ValueError("action must be create, rotate, or revoke")
        if not name:
            raise ValueError("name is required")
        if action == "revoke":
            await orch_delete(f"/credentials/{name}")
            return {"action": action, "name": name, "revoked": True}

        value = kwargs.get("value")
        if not isinstance(value, str) or not value:
            raise ValueError("value is required for create and rotate")
        if action == "rotate":
            metadata = await orch_patch(f"/credentials/{name}", {"value": value})
        else:
            metadata = await orch_post(
                "/credentials",
                {
                    "name": name,
                    "value": value,
                    "description": str(kwargs.get("description") or ""),
                    "secret_type": str(kwargs.get("secret_type") or "other"),
                    "policy": dict(kwargs.get("policy") or {}),
                    "created_by": str(kwargs.get("actor_id") or "tool-service"),
                },
            )
        return {
            "action": action,
            "name": name,
            "stored": True,
            "metadata": metadata,
        }


class InfraReadySignalTool(BaseTool):
    name = "infra.ready_signal"
    group = ToolGroup.DEVOPS
    description = "Signal that infrastructure is ready for deployment."
    allowed_roles = _ADMIN
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        project_id = str(kwargs.get("project_id") or kwargs.get("target") or "").strip()
        if not project_id:
            raise ValueError("project_id is required")
        transition = await orch_post(
            f"/projects/{project_id}/transition",
            {
                "event": "infra_ready",
                "actor_id": str(kwargs.get("actor_id") or "devops_eng"),
                "context": {"sprint_id": kwargs.get("sprint_id")},
            },
        )
        return {
            "project_id": project_id,
            "signalled": True,
            "transition": transition,
        }


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
    cache_ttl_seconds = 0

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
    cache_ttl_seconds = 0

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
