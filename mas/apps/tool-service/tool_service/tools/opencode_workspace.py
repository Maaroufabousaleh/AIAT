"""Run-bound filesystem tools for governed OpenCode coding workers."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

from mas_core.protocols.enums import AgentRole
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup

from .adapters import _run_sandboxed_process

_ALLOWED_ROLES = [AgentRole.WORKER]


def _run_path(workspace_run_id: str, relative_path: str) -> Path:
    run_id = UUID(str(workspace_run_id))
    if not relative_path or "\x00" in relative_path:
        raise ValueError("relative_path is required")
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("OpenCode workspace path traversal denied")
    root = Path(os.getenv("OPENCODE_WORKSPACE_ROOT", "/opencode-workspace")).resolve()
    run_root = (root / str(run_id)).resolve()
    try:
        run_root.relative_to(root)
    except ValueError as exc:
        raise ValueError("OpenCode run workspace is outside the configured root") from exc
    current = run_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("OpenCode workspace symlinks are denied")
    resolved = (run_root / relative).resolve(strict=False)
    try:
        resolved.relative_to(run_root)
    except ValueError as exc:
        raise ValueError("OpenCode workspace path traversal denied") from exc
    return resolved


def _run_root(workspace_run_id: str) -> Path:
    run_id = UUID(str(workspace_run_id))
    root = Path(os.getenv("OPENCODE_WORKSPACE_ROOT", "/opencode-workspace")).resolve()
    run_root = (root / str(run_id)).resolve()
    try:
        run_root.relative_to(root)
    except ValueError as exc:
        raise ValueError("OpenCode run workspace is outside the configured root") from exc
    return run_root


class OpenCodeWorkspaceReadTool(BaseTool):
    name = "opencode.workspace_read"
    group = ToolGroup.KPI_UTILITY
    description = "Read one UTF-8 file inside the current governed OpenCode run workspace."
    allowed_roles = _ALLOWED_ROLES
    cache_ttl_seconds = 0
    max_concurrency = 10

    async def execute(self, **kwargs: Any) -> Any:
        path = _run_path(str(kwargs.get("workspace_run_id") or ""), str(kwargs.get("path") or ""))
        content = path.read_text(encoding="utf-8")
        return {"path": str(kwargs.get("path")), "content": content, "size_bytes": len(content.encode())}


class OpenCodeWorkspaceWriteTool(BaseTool):
    name = "opencode.workspace_write"
    group = ToolGroup.KPI_UTILITY
    description = "Write one UTF-8 file inside the current governed OpenCode run workspace."
    allowed_roles = _ALLOWED_ROLES
    cache_ttl_seconds = 0
    idempotent = False
    max_concurrency = 5

    async def execute(self, **kwargs: Any) -> Any:
        path = _run_path(str(kwargs.get("workspace_run_id") or ""), str(kwargs.get("path") or ""))
        content = str(kwargs.get("content") or "")
        if len(content.encode("utf-8")) > 256_000:
            raise ValueError("OpenCode workspace write exceeds 256000 bytes")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"path": str(kwargs.get("path")), "bytes_written": len(content.encode("utf-8"))}


class OpenCodeWorkspacePytestTool(BaseTool):
    name = "opencode.workspace_pytest"
    group = ToolGroup.KPI_UTILITY
    description = "Run bounded pytest against one test file inside the current governed OpenCode run workspace."
    allowed_roles = _ALLOWED_ROLES
    cache_ttl_seconds = 0
    idempotent = False
    max_concurrency = 2

    async def execute(self, **kwargs: Any) -> Any:
        test_path = _run_path(
            str(kwargs.get("workspace_run_id") or ""),
            str(kwargs.get("path") or "test_solution.py"),
        )
        run_root = _run_root(str(kwargs.get("workspace_run_id") or ""))
        if not test_path.is_file() or test_path.suffix != ".py":
            raise ValueError("OpenCode pytest target must be an existing Python file")

        completed = await _run_sandboxed_process(
            [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", test_path.name],
            cwd=run_root,
            workspace_root=run_root,
            workspace_read_only=True,
            timeout=30,
            max_output_bytes=64_000,
        )
        if not completed.get("available"):
            return {
                "path": test_path.name,
                "exit_code": None,
                "certification_status": "SANDBOX_UNAVAILABLE",
                "passed": 0,
                "failed": 0,
                "skipped": 0,
                "sandbox_profile": "gvisor",
                "reason": str(completed.get("reason") or completed.get("error") or "sandbox unavailable"),
            }
        output = "\n".join(
            value
            for value in (completed.get("stdout"), completed.get("stderr"))
            if isinstance(value, str)
        )
        counts = {
            name: int(match.group(1)) if (match := re.search(rf"(\d+) {name}", output)) else 0
            for name in ("passed", "failed", "skipped")
        }
        exit_code = completed.get("returncode")
        return {
            "path": test_path.name,
            "exit_code": exit_code,
            "certification_status": "PASSED"
            if exit_code == 0 and counts["passed"] > 0 and counts["failed"] == 0
            else "FAILED",
            **counts,
            "sandbox_profile": "gvisor",
            "network_mode": "egress-deny-all",
        }
