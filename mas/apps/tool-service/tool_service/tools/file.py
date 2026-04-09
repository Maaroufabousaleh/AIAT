"""FILE group tools: file_read, file_write."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from mas_core.protocols.enums import AgentRole
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup

logger = logging.getLogger(__name__)


class FileReadTool(BaseTool):
    name = "file_read"
    group = ToolGroup.KPI_UTILITY
    description = "Read a file from the project workspace."
    allowed_roles = [
        AgentRole.ORCHESTRATOR,
        AgentRole.EXECUTIVE,
        AgentRole.C_SUITE,
        AgentRole.ADMIN,
        AgentRole.WORKER,
    ]
    cache_ttl_seconds = 10
    idempotent = True
    max_concurrency = 10

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        path = kwargs.get("path", "")

        if not path:
            raise ValueError("path is required")

        workspace_root = Path(os.getenv("TOOL_WORKSPACE_ROOT", "/tmp/workspace"))
        if project_id:
            safe_path = workspace_root / project_id / path
        else:
            safe_path = workspace_root / path

        try:
            safe_path = safe_path.resolve()
            if not str(safe_path).startswith(str(workspace_root.resolve())):
                raise ValueError("Path traversal detected")

            content = safe_path.read_text(encoding="utf-8")
            return {
                "path": path,
                "project_id": project_id,
                "content": content,
                "size_bytes": len(content),
                "success": True,
            }
        except FileNotFoundError:
            raise ValueError(f"File not found: {path}")
        except PermissionError:
            raise ValueError(f"Permission denied: {path}")
        except Exception as e:
            logger.error("file_read_error", extra={"path": path, "error": str(e)}, exc_info=True)
            raise RuntimeError(f"Failed to read file: {e}")


class FileWriteTool(BaseTool):
    name = "file_write"
    group = ToolGroup.KPI_UTILITY
    description = "Write content to a file in the project workspace."
    allowed_roles = [
        AgentRole.ORCHESTRATOR,
        AgentRole.EXECUTIVE,
        AgentRole.C_SUITE,
        AgentRole.ADMIN,
        AgentRole.WORKER,
    ]
    cache_ttl_seconds = 0
    idempotent = False
    max_concurrency = 5

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        path = kwargs.get("path", "")
        content = kwargs.get("content", "")

        if not path:
            raise ValueError("path is required")

        workspace_root = Path(os.getenv("TOOL_WORKSPACE_ROOT", "/tmp/workspace"))
        if project_id:
            safe_path = workspace_root / project_id / path
        else:
            safe_path = workspace_root / path

        try:
            safe_path = safe_path.resolve()
            if not str(safe_path).startswith(str(workspace_root.resolve())):
                raise ValueError("Path traversal detected")

            safe_path.parent.mkdir(parents=True, exist_ok=True)
            safe_path.write_text(content, encoding="utf-8")

            return {
                "path": path,
                "project_id": project_id,
                "bytes_written": len(content),
                "success": True,
            }
        except Exception as e:
            logger.error("file_write_error", extra={"path": path, "error": str(e)}, exc_info=True)
            raise RuntimeError(f"Failed to write file: {e}")
