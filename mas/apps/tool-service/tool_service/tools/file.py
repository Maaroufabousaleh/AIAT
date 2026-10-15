"""FILE group tools: file_read, file_write."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any
from uuid import UUID

from mas_core.protocols.enums import AgentRole
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup

logger = logging.getLogger(__name__)
MAX_FILE_WRITE_BYTES = 10 * 1024 * 1024


def _workspace_root() -> Path:
    return Path(os.getenv("TOOL_WORKSPACE_ROOT", "/tmp/workspace")).resolve()


def _resolve_project_id(kwargs: dict[str, Any], *, default: str = "") -> str:
    """Resolve project scope from authenticated tool context.

    Tool kwargs are caller-controlled.  When the gateway has attached the
    authenticated request context, its project ID is authoritative for
    project-scoped file and artifact operations. Workers must not select a
    project solely by supplying a ``project_id`` argument.
    """
    supplied_project_id = str(kwargs.get("project_id") or "").strip()
    context = kwargs.get("_aiat_context")
    if not isinstance(context, dict):
        return supplied_project_id or default

    caller_role = str(context.get("caller_role") or "")
    authenticated_project_id = str(context.get("project_id") or "").strip()
    if caller_role == AgentRole.WORKER.value and not authenticated_project_id:
        raise ValueError("worker tool access requires an authenticated project scope")
    if authenticated_project_id:
        if supplied_project_id and supplied_project_id != authenticated_project_id:
            raise ValueError("project_id does not match authenticated project scope")
        return authenticated_project_id
    return supplied_project_id or default


def _safe_workspace_path(
    path: str,
    *,
    project_id: str = "",
    workspace_root: Path | None = None,
) -> Path:
    if not path:
        raise ValueError("path is required")
    if "\x00" in path:
        raise ValueError("path contains null byte")

    root = (workspace_root or _workspace_root()).resolve()
    if project_id:
        if (
            project_id in {".", ".."}
            or "\x00" in project_id
            or "/" in project_id
            or "\\" in project_id
        ):
            raise ValueError("project_id must be a single workspace directory name")
        untrusted_project_root = root / project_id
        if untrusted_project_root.is_symlink():
            raise ValueError("project workspace symlinks are denied")
        project_root = untrusted_project_root.resolve()
        try:
            project_root.relative_to(root)
        except ValueError as exc:
            raise ValueError("project workspace escapes the configured workspace root") from exc
        allowed_root = project_root
        candidate = project_root / path
    else:
        allowed_root = root
        candidate = root / path
    resolved = candidate.resolve()
    try:
        resolved.relative_to(allowed_root)
    except ValueError as exc:
        raise ValueError("Path traversal escapes the project workspace") from exc
    return resolved


def _worker_run_workspace_path(path: str, kwargs: dict[str, Any]) -> Path | None:
    """Resolve a worker's output into its shared, run-scoped artifact area.

    The authenticated ToolRequest context supplies the run ID.  It is never
    accepted from tool arguments.  The run workspace is shared with the
    orchestrator's bounded artifact registrar, while normal non-worker file
    operations retain the existing project workspace layout.
    """

    context = kwargs.get("_aiat_context")
    if not isinstance(context, dict) or context.get("caller_role") != AgentRole.WORKER.value:
        return None
    raw_run_id = str(context.get("worker_run_id") or "").strip()
    if not raw_run_id:
        raise ValueError("worker file access requires an authenticated WorkerRun ID")
    try:
        run_id = UUID(raw_run_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("worker file access requires a valid authenticated run ID") from exc
    shared_root = Path(
        os.getenv("OPENCODE_WORKSPACE_ROOT") or str(_workspace_root())
    ).resolve()
    return _safe_workspace_path(path, project_id=str(run_id), workspace_root=shared_root)


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
    cache_ttl_seconds = 0
    idempotent = True
    max_concurrency = 10

    async def execute(self, **kwargs: Any) -> Any:
        project_id = _resolve_project_id(kwargs)
        path = kwargs.get("path", "")

        try:
            safe_path = _worker_run_workspace_path(path, kwargs) or _safe_workspace_path(
                path,
                project_id=project_id,
            )
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
        project_id = _resolve_project_id(kwargs)
        path = kwargs.get("path", "")
        content = kwargs.get("content", "")

        try:
            encoded_content = content.encode("utf-8") if isinstance(content, str) else None
            if encoded_content is None:
                raise ValueError("content must be a UTF-8 string")
            if len(encoded_content) > MAX_FILE_WRITE_BYTES:
                raise ValueError(
                    "FILE_WRITE_TOO_LARGE: content exceeds the 10485760-byte limit"
                )
            safe_path = _worker_run_workspace_path(path, kwargs) or _safe_workspace_path(
                path,
                project_id=project_id,
            )
            safe_path.parent.mkdir(parents=True, exist_ok=True)
            safe_path.write_bytes(encoded_content)

            return {
                "path": path,
                "project_id": project_id,
                "bytes_written": len(encoded_content),
                "sha256": hashlib.sha256(encoded_content).hexdigest(),
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
        project_id = _resolve_project_id(kwargs)
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
