"""FILE group tools: file_read, file_write."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from mas_core.protocols.enums import AgentRole
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup

logger = logging.getLogger(__name__)


def _workspace_root() -> Path:
    return Path(os.getenv("TOOL_WORKSPACE_ROOT", "/tmp/workspace")).resolve()


def _safe_workspace_path(path: str, *, project_id: str = "") -> Path:
    if not path:
        raise ValueError("path is required")
    if "\x00" in path:
        raise ValueError("path contains null byte")

    root = _workspace_root()
    candidate = root / project_id / path if project_id else root / path
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("Path traversal detected") from exc
    return resolved


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

        try:
            safe_path = _safe_workspace_path(path, project_id=project_id)
            content = safe_path.read_text(encoding="utf-8")
            return {
                "path": path,
                "project_id": project_id,
                "content": content,
                "size_bytes": len(content),
                "success": True,
            }
        except FileNotFoundError as exc:
            raise ValueError(f"File not found: {path}") from exc
        except PermissionError as exc:
            raise ValueError(f"Permission denied: {path}") from exc
        except Exception as e:
            logger.error("file_read_error", extra={"path": path, "error": str(e)}, exc_info=True)
            raise RuntimeError(f"Failed to read file: {e}") from e


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

        try:
            safe_path = _safe_workspace_path(path, project_id=project_id)
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
            raise RuntimeError(f"Failed to write file: {e}") from e


class FilePatchTool(BaseTool):
    name = "file.patch"
    group = ToolGroup.KPI_UTILITY
    description = "Apply a safe patch inside the project workspace."
    allowed_roles = [
        AgentRole.ORCHESTRATOR,
        AgentRole.EXECUTIVE,
        AgentRole.C_SUITE,
        AgentRole.ADMIN,
        AgentRole.WORKER,
    ]
    cache_ttl_seconds = 0
    idempotent = False
    max_concurrency = 3

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        unified_diff = kwargs.get("unified_diff")
        path = kwargs.get("path", "")
        find = kwargs.get("find")
        replace = kwargs.get("replace")

        if unified_diff:
            return await self._apply_unified_diff(str(unified_diff), project_id=project_id)

        safe_path = _safe_workspace_path(path, project_id=project_id)
        if find is None or replace is None:
            raise ValueError("Either unified_diff or path/find/replace is required")

        old_text = safe_path.read_text(encoding="utf-8")
        find_text = str(find)
        if find_text not in old_text:
            raise ValueError("find text was not present in target file")
        new_text = old_text.replace(find_text, str(replace), 1)
        safe_path.write_text(new_text, encoding="utf-8")
        return {
            "path": path,
            "project_id": project_id,
            "patched": True,
            "bytes_written": len(new_text.encode("utf-8")),
        }

    async def _apply_unified_diff(self, unified_diff: str, *, project_id: str = "") -> dict[str, Any]:
        root = _workspace_root()
        cwd = (root / project_id).resolve() if project_id else root
        try:
            cwd.relative_to(root)
        except ValueError as exc:
            raise ValueError("Path traversal detected") from exc
        cwd.mkdir(parents=True, exist_ok=True)

        git = shutil.which("git")
        if git is None:
            raise RuntimeError("git binary is required for unified diff patches")

        check = subprocess.run(
            [git, "apply", "--check", "--whitespace=nowarn", "-"],
            input=unified_diff,
            text=True,
            cwd=cwd,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if check.returncode != 0:
            raise ValueError(check.stderr.strip() or "patch failed validation")

        applied = subprocess.run(
            [git, "apply", "--whitespace=nowarn", "-"],
            input=unified_diff,
            text=True,
            cwd=cwd,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if applied.returncode != 0:
            raise RuntimeError(applied.stderr.strip() or "patch application failed")
        return {"project_id": project_id, "patched": True, "mode": "unified_diff"}
