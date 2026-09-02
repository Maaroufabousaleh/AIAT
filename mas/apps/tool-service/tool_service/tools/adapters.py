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


def _decode_docling_output(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize the Docling runner boundary without leaking parser errors.

    The runner is an optional external-process boundary.  A successful process
    exit does not guarantee that its stdout is valid JSON, so malformed output
    must be reported as a bounded degraded result rather than escaping through
    the tool registry as an opaque ``TOOL_ERROR``.
    """
    result["configured"] = True
    raw_output = result.get("stdout")
    if result.get("returncode") != 0:
        return result
    if not isinstance(raw_output, str) or not raw_output.strip():
        result.update(
            {
                "degraded": True,
                "reason": "docling_empty_output",
                "document": None,
            }
        )
        return result
    try:
        parsed = json.loads(raw_output)
    except (TypeError, json.JSONDecodeError):
        result.update(
            {
                "degraded": True,
                "reason": "docling_invalid_json",
                "document": None,
            }
        )
        return result
    if not isinstance(parsed, dict):
        result.update(
            {
                "degraded": True,
                "reason": "docling_invalid_result_shape",
                "document": None,
            }
        )
        return result
    result["document"] = parsed
    return result


def _normalize_diagram_result(
    result: dict[str, Any], *, output_path: Path, output_name: str
) -> dict[str, Any]:
    """Expose only bounded Mermaid render metadata after the subprocess exits."""
    result.update(
        {
            "backend": "mermaid",
            "configured": True,
            "output": output_name,
            "rendered": False,
            "output_exists": False,
        }
    )
    if result.get("available") is False:
        result.setdefault("degraded", True)
        result.setdefault("reason", "mermaid_adapter_unavailable")
        return result
    returncode = result.get("returncode")
    if returncode != 0:
        result.update(
            {
                "degraded": True,
                "reason": "mermaid_render_timed_out"
                if result.get("timed_out")
                else "mermaid_render_failed",
            }
        )
        return result
    try:
        output_stat = output_path.stat()
    except OSError:
        result.update({"degraded": True, "reason": "mermaid_output_missing"})
        return result
    if not output_path.is_file():
        result.update({"degraded": True, "reason": "mermaid_output_not_file"})
        return result
    if output_stat.st_size <= 0:
        result.update(
            {
                "degraded": True,
                "reason": "mermaid_output_empty",
                "output_exists": True,
                "output_size_bytes": output_stat.st_size,
            }
        )
        return result
    result.update(
        {
            "rendered": True,
            "output_exists": True,
            "output_size_bytes": output_stat.st_size,
        }
    )
    return result


def _decode_code_review_output(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize optional review-process JSON without tripping the registry."""
    result["configured"] = True
    if result.get("returncode") != 0:
        return result
    raw_output = result.get("stdout")
    if not isinstance(raw_output, str) or not raw_output.strip():
        result.update(
            {
                "degraded": True,
                "reason": "code_review_empty_output",
                "review": None,
            }
        )
        return result
    try:
        parsed = json.loads(raw_output)
    except (TypeError, json.JSONDecodeError):
        result.update(
            {
                "degraded": True,
                "reason": "code_review_invalid_json",
                "review": None,
            }
        )
        return result
    if not isinstance(parsed, dict):
        result.update(
            {
                "degraded": True,
                "reason": "code_review_invalid_result_shape",
                "review": None,
            }
        )
        return result
    result["review"] = parsed
    return result


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
    raw_output = adapter_result.get("stdout")
    if not isinstance(raw_output, str) or not raw_output.strip():
        return {
            "available": False,
            "configured": True,
            "backend": "sandbox_adapter",
            "sandbox_profile": "gvisor",
            "degraded": True,
            "reason": "sandbox_adapter_empty_output",
        }
    try:
        result = json.loads(raw_output)
    except (TypeError, json.JSONDecodeError):
        return {
            "available": False,
            "configured": True,
            "backend": "sandbox_adapter",
            "sandbox_profile": "gvisor",
            "degraded": True,
            "reason": "sandbox_adapter_invalid_json",
        }
    if not isinstance(result, dict):
        return {
            "available": False,
            "configured": True,
            "backend": "sandbox_adapter",
            "sandbox_profile": "gvisor",
            "degraded": True,
            "reason": "sandbox_adapter_invalid_result_shape",
        }
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
            return _decode_docling_output(result)
        return {
            "available": True,
            "configured": True,
            "degraded": True,
            "backend": "plain_text_fallback",
            "reason": "docling_binary_not_found",
            "path": path,
            "size_bytes": safe_path.stat().st_size,
            "text": safe_path.read_text(encoding="utf-8", errors="replace")[:50_000],
        }


class SecurityScanTool(BaseTool):
    name = "security.scan"
    group = ToolGroup.KPI_UTILITY
    description = "Run configured Semgrep/SkillSpector/TruffleHog checks through a bounded process adapter."
    allowed_roles = _WORKER
    cache_ttl_seconds = 0
    idempotent = False
    max_concurrency = 2

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        cwd = _workspace_cwd(project_id, kwargs.get("path", "."))
        scanner = str(kwargs.get("scanner") or "semgrep").strip().lower()
        if scanner not in {"semgrep", "skillspector", "trufflehog"}:
            return {
                "available": False,
                "configured": False,
                "scanner": scanner,
                "reason": "unsupported_scanner",
            }
        if scanner == "skillspector":
            # SkillSpector is an optional scanner selected through the same
            # hardened sandbox boundary as the other security tools.  The
            # command is operator-configurable because the upstream CLI
            # surface varies by installation; its absence is an honest
            # availability result, never a licence or provenance decision.
            raw_command = os.getenv("TOOL_SKILLSPECTOR_COMMAND", "").strip()
            argv = shlex.split(raw_command) if raw_command else ["skillspector", "scan", "--json", "."]
            if not argv:
                return {
                    "available": False,
                    "configured": False,
                    "scanner": scanner,
                    "reason": "TOOL_SKILLSPECTOR_COMMAND_empty",
                }
            result = await _run_sandboxed_process(
                argv,
                cwd=cwd,
                timeout=float(kwargs.get("timeout_seconds", 90)),
                max_output_bytes=int(kwargs.get("max_output_bytes", 512_000)),
            )
            findings_count: int | None = None
            raw_value = result.get("stdout")
            raw_output = raw_value if isinstance(raw_value, str) else ""
            if result.get("returncode") == 0 and not raw_output.strip():
                result["degraded"] = True
                result["reason"] = "skillspector_empty_output"
            elif raw_output:
                try:
                    parsed = json.loads(raw_output)
                except json.JSONDecodeError:
                    findings_count = len([line for line in raw_output.splitlines() if line.strip()])
                else:
                    if isinstance(parsed, dict):
                        findings = parsed.get("findings")
                        if isinstance(findings, list):
                            findings_count = len(findings)
                        else:
                            result["degraded"] = True
                            result["reason"] = "skillspector_invalid_result_shape"
                    elif isinstance(parsed, list):
                        findings_count = len(parsed)
                    else:
                        result["degraded"] = True
                        result["reason"] = "skillspector_invalid_result_shape"
            result["backend"] = "skillspector"
            result["scanner"] = scanner
            result["findings_count"] = findings_count
            result["command_configured"] = bool(raw_command)
            return result
        if scanner == "trufflehog":
            argv = [
                "sh",
                "-lc",
                (
                    "set -e; "
                    "rm -rf /tmp/aiat-trufflehog-src; "
                    "mkdir -p /tmp/aiat-trufflehog-src; "
                    "cp -R . /tmp/aiat-trufflehog-src/; "
                    "cd /tmp/aiat-trufflehog-src; "
                    "trufflehog filesystem --json ."
                ),
            ]
            result = await _run_sandboxed_process(
                argv,
                cwd=cwd,
                timeout=float(kwargs.get("timeout_seconds", 90)),
                max_output_bytes=int(kwargs.get("max_output_bytes", 512_000)),
            )
            findings = [
                line for line in str(result.get("stdout") or "").splitlines() if line.strip()
            ]
            result["backend"] = "trufflehog"
            result["scanner"] = scanner
            result["findings_count"] = len(findings) if result.get("available") else None
            return result
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
        raw_output = result.get("stdout")
        if result.get("available") and result.get("returncode") == 0 and (
            not isinstance(raw_output, str) or not raw_output.strip()
        ):
            result["findings_count"] = None
            result["degraded"] = True
            result["reason"] = "semgrep_empty_output"
        elif result.get("available") and isinstance(raw_output, str) and raw_output.strip():
            try:
                parsed = json.loads(raw_output)
            except json.JSONDecodeError:
                result["findings_count"] = None
                result["degraded"] = True
                result["reason"] = "semgrep_invalid_json"
            else:
                if isinstance(parsed, dict) and isinstance(parsed.get("results"), list):
                    result["findings_count"] = len(parsed["results"])
                else:
                    result["findings_count"] = None
                    result["degraded"] = True
                    result["reason"] = "semgrep_invalid_result_shape"
        result["backend"] = "semgrep"
        result["scanner"] = scanner
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
    description = "Run the bounded local diff reviewer or a pinned external adapter."
    allowed_roles = _WORKER
    cache_ttl_seconds = 0
    idempotent = False
    max_concurrency = 1

    async def execute(self, **kwargs: Any) -> Any:
        raw = os.getenv("TOOL_CODE_REVIEW_COMMAND", "").strip()
        cwd = _workspace_cwd(kwargs.get("project_id", ""), kwargs.get("cwd", "."))
        payload = {
            "mode": str(kwargs.get("mode") or "diff"),
            "base": str(kwargs.get("base") or ""),
            "head": str(kwargs.get("head") or "HEAD"),
            "severity_threshold": str(kwargs.get("severity_threshold") or "medium"),
        }
        if not raw:
            # The local deterministic reviewer is the default adapter.  An
            # external command remains an optional, explicitly configured
            # extension and never becomes an implicit network/provider call.
            from ..code_review_runner import review

            try:
                result = review(cwd, payload)
            except Exception as exc:
                return {
                    "available": False,
                    "backend": "aiat_deterministic_diff_review",
                    "reason": type(exc).__name__,
                }
            result["backend"] = "aiat_deterministic_diff_review"
            result["external_adapter_configured"] = False
            return result
        result = await _run_process(
            shlex.split(raw),
            cwd=cwd,
            input_text=json.dumps(payload),
            timeout=float(kwargs.get("timeout_seconds", 120)),
            max_output_bytes=int(kwargs.get("max_output_bytes", 256_000)),
        )
        result = _decode_code_review_output(result)
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
        result = await _run_process(
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
        return _normalize_diagram_result(result, output_path=out_path, output_name=output)


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
