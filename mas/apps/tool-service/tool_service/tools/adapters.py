"""Default OSS capability adapters behind the AIAT tool-service boundary."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import sys
from typing import TYPE_CHECKING, Any

from mas_core.protocols.enums import AgentRole
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup

from .file import _safe_workspace_path, _workspace_root

if TYPE_CHECKING:
    from pathlib import Path

_WORKER = [
    AgentRole.ORCHESTRATOR,
    AgentRole.EXECUTIVE,
    AgentRole.C_SUITE,
    AgentRole.ADMIN,
    AgentRole.WORKER,
]
_ADMIN = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE, AgentRole.ADMIN]
_DEVOPS = _ADMIN

_DEFAULT_ALLOWED_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("pytest",),
    ("python", "-m", "pytest"),
    ("python3", "-m", "pytest"),
    ("python", "-m", "compileall"),
    ("python3", "-m", "compileall"),
    ("ruff", "check"),
    ("semgrep", "scan"),
    ("semgrep", "ci"),
    ("opentofu", "plan"),
    ("tofu", "plan"),
    ("npm", "test"),
    ("npm", "run", "test"),
    ("npx", "playwright", "test"),
)


def _bounded_number(
    value: Any,
    *,
    name: str,
    minimum: float,
    maximum: float,
) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not minimum <= number <= maximum:
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}")
    return number


def _command_allowlist() -> tuple[tuple[str, ...], ...]:
    raw = os.getenv("TOOL_COMMAND_ALLOWLIST", "").strip()
    if not raw:
        return _DEFAULT_ALLOWED_COMMANDS
    entries = []
    for item in raw.split(";"):
        parts = tuple(shlex.split(item.strip()))
        if parts:
            entries.append(parts)
    return tuple(entries) or _DEFAULT_ALLOWED_COMMANDS


def _command_allowed(argv: list[str]) -> bool:
    return any(tuple(argv[: len(prefix)]) == prefix for prefix in _command_allowlist())


def _workspace_cwd(project_id: str = "", path: str = ".") -> Path:
    root = _workspace_root()
    if path in ("", "."):
        candidate = root / project_id if project_id else root
    else:
        candidate = _safe_workspace_path(path, project_id=project_id)
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("Path traversal detected") from exc
    return resolved


async def _run_process(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float = 30.0,
    input_text: str | None = None,
    max_output_bytes: int = 64_000,
) -> dict[str, Any]:
    if not argv:
        raise ValueError("command is required")
    timeout = _bounded_number(timeout, name="timeout_seconds", minimum=1, maximum=900)
    max_output_bytes = int(
        _bounded_number(
            max_output_bytes,
            name="max_output_bytes",
            minimum=1_024,
            maximum=2_000_000,
        )
    )
    binary = shutil.which(argv[0])
    if binary is None:
        return {"available": False, "binary": argv[0], "reason": "binary_not_found"}

    proc = await asyncio.create_subprocess_exec(
        binary,
        *argv[1:],
        cwd=cwd,
        stdin=asyncio.subprocess.PIPE if input_text is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def read_limited(stream: asyncio.StreamReader) -> tuple[bytes, bool]:
        retained = bytearray()
        truncated = False
        while chunk := await stream.read(64 * 1024):
            remaining = max_output_bytes - len(retained)
            if remaining > 0:
                retained.extend(chunk[:remaining])
            if len(chunk) > remaining:
                truncated = True
        return bytes(retained), truncated

    async def write_input() -> None:
        if proc.stdin is None or input_text is None:
            return
        try:
            proc.stdin.write(input_text.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
            await proc.stdin.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            pass

    assert proc.stdout is not None
    assert proc.stderr is not None
    combined = asyncio.gather(
        proc.wait(),
        read_limited(proc.stdout),
        read_limited(proc.stderr),
        write_input(),
    )
    try:
        _, stdout_result, stderr_result, _ = await asyncio.wait_for(
            asyncio.shield(combined), timeout=timeout
        )
    except TimeoutError:
        proc.kill()
        await combined
        return {
            "available": True,
            "timed_out": True,
            "timeout_seconds": timeout,
            "returncode": None,
        }

    stdout, stdout_truncated = stdout_result
    stderr, stderr_truncated = stderr_result
    return {
        "available": True,
        "returncode": proc.returncode,
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
    }


async def _run_sandboxed_process(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float,
    max_output_bytes: int,
    workspace_root: Path | None = None,
    workspace_read_only: bool = False,
) -> dict[str, Any]:
    """Delegate worker-controlled execution to the configured hardened sandbox adapter.

    ``workspace_root`` lets a caller narrow the bind mount to a single
    governed run rather than exposing the service-wide project workspace.
    """
    timeout = _bounded_number(timeout, name="timeout_seconds", minimum=1, maximum=900)
    max_output_bytes = int(
        _bounded_number(
            max_output_bytes,
            name="max_output_bytes",
            minimum=1_024,
            maximum=2_000_000,
        )
    )
    raw_adapter = os.getenv("TOOL_SANDBOX_COMMAND", "").strip()
    if not raw_adapter:
        return {
            "available": False,
            "configured": False,
            "reason": "TOOL_SANDBOX_COMMAND_not_configured",
        }

    root = (workspace_root or _workspace_root()).resolve()
    resolved_cwd = cwd.resolve()
    try:
        relative_cwd = resolved_cwd.relative_to(root)
    except ValueError as exc:
        raise ValueError("sandbox cwd must be inside workspace_root") from exc
    payload = {
        "argv": argv,
        "workspace_root": str(root),
        "cwd": str(relative_cwd),
        "workspace_read_only": bool(workspace_read_only),
        "profile": "gvisor",
        "network_mode": "egress-deny-all",
        "timeout_seconds": timeout,
        "max_output_bytes": max_output_bytes,
    }
    adapter_result = await _run_process(
        shlex.split(raw_adapter),
        cwd=root,
        input_text=json.dumps(payload),
        timeout=timeout,
        max_output_bytes=max_output_bytes,
    )
    if not adapter_result.get("available") or adapter_result.get("returncode") != 0:
        return {
            **adapter_result,
            "configured": True,
            "backend": "sandbox_adapter",
        }
    try:
        result = json.loads(adapter_result.get("stdout") or "")
    except json.JSONDecodeError as exc:
        raise RuntimeError("Sandbox adapter returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise RuntimeError("Sandbox adapter response must be a JSON object")
    result.setdefault("available", True)
    result["configured"] = True
    result["backend"] = "sandbox_adapter"
    result["sandbox_profile"] = "gvisor"
    return result


class CommandRunSafeTool(BaseTool):
    name = "command.run_safe"
    group = ToolGroup.KPI_UTILITY
    description = "Run a budgeted allowlisted command inside the project workspace."
    allowed_roles = _WORKER
    cache_ttl_seconds = 0
    idempotent = False
    max_concurrency = 2

    async def execute(self, **kwargs: Any) -> Any:
        command = kwargs.get("command")
        if isinstance(command, str):
            argv = shlex.split(command)
        elif isinstance(command, list):
            argv = [str(part) for part in command]
        else:
            raise ValueError("command must be a string or list")
        if not _command_allowed(argv):
            raise ValueError(f"Command is not allowlisted: {' '.join(argv[:3])}")
        cwd = _workspace_cwd(kwargs.get("project_id", ""), kwargs.get("cwd", "."))
        if not cwd.is_dir():
            raise ValueError("cwd must be an existing directory")
        return await _run_sandboxed_process(
            argv,
            cwd=cwd,
            timeout=float(kwargs.get("timeout_seconds", 30)),
            max_output_bytes=int(kwargs.get("max_output_bytes", 64_000)),
        )


class RepoReadTool(BaseTool):
    name = "repo.read"
    group = ToolGroup.KPI_UTILITY
    description = "Read a repository file through the workspace boundary."
    allowed_roles = _WORKER
    cache_ttl_seconds = 0
    max_concurrency = 10

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        path = kwargs.get("path", "")
        safe_path = _safe_workspace_path(path, project_id=project_id)
        content = safe_path.read_text(encoding="utf-8")
        return {
            "path": path,
            "project_id": project_id,
            "content": content,
            "size_bytes": len(content.encode("utf-8")),
        }


class RepoSearchTool(BaseTool):
    name = "repo.search"
    group = ToolGroup.KPI_UTILITY
    description = "Search repository text with rg when available."
    allowed_roles = _WORKER
    cache_ttl_seconds = 0
    max_concurrency = 5

    async def execute(self, **kwargs: Any) -> Any:
        query = str(kwargs.get("query") or "")
        if not query:
            raise ValueError("query is required")
        project_id = kwargs.get("project_id", "")
        cwd = _workspace_cwd(project_id, kwargs.get("path", "."))
        max_results = int(
            _bounded_number(
                kwargs.get("max_results", 50),
                name="max_results",
                minimum=1,
                maximum=1_000,
            )
        )

        rg = shutil.which("rg")
        if rg:
            result = await _run_process(
                [rg, "--line-number", "--no-heading", "--fixed-strings", query, "."],
                cwd=cwd,
                timeout=float(kwargs.get("timeout_seconds", 15)),
                max_output_bytes=128_000,
            )
            lines = (result.get("stdout") or "").splitlines()[:max_results]
            return {"query": query, "matches": lines, "count": len(lines), "engine": "rg"}

        matches: list[str] = []
        for file_path in cwd.rglob("*"):
            if len(matches) >= max_results:
                break
            try:
                resolved_path = file_path.resolve(strict=True)
                resolved_path.relative_to(cwd)
                if not resolved_path.is_file():
                    continue
                for lineno, line in enumerate(
                    resolved_path.read_text(encoding="utf-8").splitlines(), 1
                ):
                    if query in line:
                        rel = file_path.relative_to(cwd)
                        matches.append(f"{rel}:{lineno}:{line}")
                        if len(matches) >= max_results:
                            break
            except (FileNotFoundError, OSError, UnicodeDecodeError, ValueError):
                continue
        return {"query": query, "matches": matches, "count": len(matches), "engine": "python"}


class DocumentIngestTool(BaseTool):
    name = "document.ingest"
    group = ToolGroup.DOCUMENT
    description = "Parse a document with Docling when installed, otherwise return text/metadata."
    allowed_roles = _WORKER
    cache_ttl_seconds = 0
    max_concurrency = 2

    async def execute(self, **kwargs: Any) -> Any:
        path = kwargs.get("path", "")
        project_id = kwargs.get("project_id", "")
        safe_path = _safe_workspace_path(path, project_id=project_id)
        docling = shutil.which("docling")
        if docling:
            result = await _run_process(
                [sys.executable, "-m", "tool_service.docling_runner", str(safe_path)],
                cwd=safe_path.parent,
                timeout=float(kwargs.get("timeout_seconds", 60)),
                max_output_bytes=int(kwargs.get("max_output_bytes", 256_000)),
            )
            result["backend"] = "docling"
            if result.get("returncode") == 0 and result.get("stdout"):
                result["document"] = json.loads(result["stdout"])
            return result
        return {
            "available": False,
            "backend": "docling",
            "reason": "binary_not_found",
            "path": path,
            "size_bytes": safe_path.stat().st_size,
            "text": safe_path.read_text(encoding="utf-8", errors="replace")[:50_000],
        }


class SecurityScanTool(BaseTool):
    name = "security.scan"
    group = ToolGroup.KPI_UTILITY
    description = "Run Semgrep/SkillSpector-style static checks through a process adapter."
    allowed_roles = _WORKER
    cache_ttl_seconds = 0
    idempotent = False
    max_concurrency = 2

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        cwd = _workspace_cwd(project_id, kwargs.get("path", "."))
        config = kwargs.get("config", "auto")
        if config in ("", "auto"):
            config = "/workspace/mas/apps/tool-service/tool_service/semgrep-default.yml"
        semgrep_cmd = " ".join(
            [
                "semgrep",
                "scan",
                "--json",
                "--metrics=off",
                "--disable-version-check",
                "--no-git-ignore",
                "--include=*.py",
                "--exclude=.git",
                "--config",
                shlex.quote(str(config)),
                ".",
            ]
        )
        argv = [
            "sh",
            "-lc",
            (
                "set -e; "
                "rm -rf /tmp/aiat-semgrep-src; "
                "mkdir -p /tmp/aiat-semgrep-src; "
                "cp -R . /tmp/aiat-semgrep-src/; "
                "cd /tmp/aiat-semgrep-src; "
                f"{semgrep_cmd}"
            ),
        ]
        result = await _run_sandboxed_process(
            argv,
            cwd=cwd,
            timeout=float(kwargs.get("timeout_seconds", 90)),
            max_output_bytes=int(kwargs.get("max_output_bytes", 512_000)),
        )
        if result.get("stdout"):
            try:
                parsed = json.loads(result["stdout"])
                result["findings_count"] = len(parsed.get("results", []))
            except json.JSONDecodeError:
                result["findings_count"] = None
        result["backend"] = "semgrep"
        return result


class TestRunTool(BaseTool):
    name = "test.run"
    group = ToolGroup.KPI_UTILITY
    description = "Run pytest or Playwright tests through the safe command boundary."
    allowed_roles = _WORKER
    cache_ttl_seconds = 0
    idempotent = False
    max_concurrency = 2

    async def execute(self, **kwargs: Any) -> Any:
        runner = str(kwargs.get("runner") or "pytest")
        args = [str(arg) for arg in kwargs.get("args", [])]
        if runner == "pytest":
            command = ["pytest", *args]
        elif runner == "playwright":
            command = ["npx", "playwright", "test", *args]
        else:
            raise ValueError("runner must be 'pytest' or 'playwright'")
        return await CommandRunSafeTool().execute(
            command=command,
            project_id=kwargs.get("project_id", ""),
            cwd=kwargs.get("cwd", "."),
            timeout_seconds=kwargs.get("timeout_seconds", 120),
            max_output_bytes=kwargs.get("max_output_bytes", 256_000),
        )


class CodeReviewTool(BaseTool):
    name = "code.review"
    group = ToolGroup.KPI_UTILITY
    description = "Run a pinned external code-review adapter command when configured."
    allowed_roles = _WORKER
    cache_ttl_seconds = 0
    idempotent = False
    max_concurrency = 1

    async def execute(self, **kwargs: Any) -> Any:
        raw = os.getenv("TOOL_CODE_REVIEW_COMMAND", "").strip()
        if not raw:
            return {"available": False, "reason": "TOOL_CODE_REVIEW_COMMAND_not_configured"}
        cwd = _workspace_cwd(kwargs.get("project_id", ""), kwargs.get("cwd", "."))
        payload = {
            "mode": str(kwargs.get("mode") or "diff"),
            "base": str(kwargs.get("base") or ""),
            "head": str(kwargs.get("head") or "HEAD"),
            "severity_threshold": str(kwargs.get("severity_threshold") or "medium"),
        }
        result = await _run_process(
            shlex.split(raw),
            cwd=cwd,
            input_text=json.dumps(payload),
            timeout=float(kwargs.get("timeout_seconds", 120)),
            max_output_bytes=int(kwargs.get("max_output_bytes", 256_000)),
        )
        if result.get("returncode") == 0 and result.get("stdout"):
            result["review"] = json.loads(result["stdout"])
        result["backend"] = "aiat_code_review"
        return result


class IaCPlanTool(BaseTool):
    name = "iac.plan"
    group = ToolGroup.DEVOPS
    description = "Run OpenTofu/tofu plan through the DevOps boundary."
    allowed_roles = _DEVOPS
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    cache_ttl_seconds = 0
    idempotent = False
    max_concurrency = 1

    async def execute(self, **kwargs: Any) -> Any:
        binary = "opentofu" if shutil.which("opentofu") else "tofu"
        args = [str(arg) for arg in kwargs.get("args", [])]
        command = [binary, "plan", *args]
        cwd = _workspace_cwd(kwargs.get("project_id", ""), kwargs.get("cwd", "."))
        return await _run_process(
            command,
            cwd=cwd,
            timeout=float(kwargs.get("timeout_seconds", 120)),
            max_output_bytes=int(kwargs.get("max_output_bytes", 256_000)),
        )


class DiagramRenderTool(BaseTool):
    name = "diagram.render"
    group = ToolGroup.DOCUMENT
    description = "Validate/render Mermaid diagrams when Mermaid CLI is installed."
    allowed_roles = _WORKER
    cache_ttl_seconds = 0
    max_concurrency = 2

    async def execute(self, **kwargs: Any) -> Any:
        source = str(kwargs.get("source") or "")
        if not source:
            raise ValueError("source is required")
        mmdc = shutil.which("mmdc")
        if mmdc is None:
            return {
                "available": False,
                "backend": "mermaid",
                "reason": "mmdc_binary_not_found",
                "source": source,
            }
        cwd = _workspace_cwd(kwargs.get("project_id", ""), kwargs.get("cwd", "."))
        output = str(kwargs.get("output", "diagram.svg"))
        out_path = _safe_workspace_path(output, project_id=kwargs.get("project_id", ""))
        return await _run_process(
            [
                mmdc,
                "-p",
                "/app/puppeteer-config.json",
                "-i",
                "-",
                "-o",
                str(out_path),
            ],
            cwd=cwd,
            input_text=source,
            timeout=float(kwargs.get("timeout_seconds", 30)),
            max_output_bytes=64_000,
        )


class MCPInvokeTool(BaseTool):
    name = "mcp.invoke"
    group = ToolGroup.CAPABILITY
    description = "Invoke a configured MCP bridge endpoint through the registry transport."
    allowed_roles = _WORKER
    cache_ttl_seconds = 0
    idempotent = False
    transport = "mcp"

    async def execute(self, **kwargs: Any) -> Any:
        raise RuntimeError("mcp.invoke must be configured through mcp transport")
