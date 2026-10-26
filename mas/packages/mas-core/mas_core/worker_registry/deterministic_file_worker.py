"""Bounded first-party worker for exercising AIAT's mediated file-write path.

This worker does not access the filesystem, Tool Service, database, or artifact
store directly. It emits one normal ``WorkerToolRequest`` and awaits the
controller's correlated response before reporting whether the write succeeded.
"""

from __future__ import annotations

import re
from uuid import NAMESPACE_URL, uuid5

from mas_core.worker_contract import (
    WorkerError,
    WorkerResult,
    WorkerRunRequest,
    WorkerToolRequest,
    WorkerToolResponse,
)

WORKER_ID = "aiat_deterministic_file_worker"
WORKER_VERSION = "0.1.0"
TASK_TYPE = "deterministic_file_generation"
MAX_PATH_LENGTH = 240
MAX_CONTENT_BYTES = 64 * 1024
TOOL_RESPONSE_TIMEOUT_SECONDS = 30.0
_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _validated_file_input(request: WorkerRunRequest) -> tuple[str, str]:
    if request.task_type != TASK_TYPE:
        raise ValueError(f"unsupported task type: {request.task_type}")
    if request.project_id is None:
        raise ValueError("deterministic file generation requires a project-scoped WorkerRun")
    if "file_write" not in request.tool_grants:
        raise ValueError("file_write is not granted to this WorkerRun")

    task_input = request.task_input
    if set(task_input) != {"path", "content"}:
        raise ValueError("task input must contain exactly path and content")
    path = task_input.get("path")
    content = task_input.get("content")
    if not isinstance(path, str) or not path or len(path) > MAX_PATH_LENGTH:
        raise ValueError("path must be a non-empty relative path within the configured limit")
    if (
        path.startswith("/")
        or "\\" in path
        or "\x00" in path
        or any(not _PATH_SEGMENT.fullmatch(segment) for segment in path.split("/"))
    ):
        raise ValueError("path must contain only safe relative workspace segments")
    if not isinstance(content, str) or not content:
        raise ValueError("content must be a non-empty string")
    if "\x00" in content or len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise ValueError("content contains a null byte or exceeds the 64 KiB limit")
    return path, content


async def generate_project_file(
    request: WorkerRunRequest,
    adapter: object,
) -> WorkerResult:
    """Write one project-scoped file through the controller-mediated tool API.

    ``task_input`` is deliberately limited to ``{"path", "content"}``. The
    authenticated project ID is carried by the WorkerRun and is not accepted
    from task input or copied into tool arguments.
    """

    path, content = _validated_file_input(request)
    tool_request = WorkerToolRequest(
        run_id=request.run_id,
        tool_name="file_write",
        arguments={"path": path, "content": content},
        permission_scope=["file_write"],
        approval_required=False,
        idempotency_key=str(
            uuid5(
                NAMESPACE_URL,
                f"aiat:{WORKER_ID}:{request.run_id}:file_write:{path}",
            )
        ),
    )
    request_tool = getattr(adapter, "request_tool", None)
    if not callable(request_tool):
        raise RuntimeError("worker adapter does not support governed tool request/response correlation")
    response: WorkerToolResponse = await request_tool(
        tool_request,
        timeout_seconds=TOOL_RESPONSE_TIMEOUT_SECONDS,
    )
    if not response.success:
        safe_code = response.error.code if response.error is not None else "TOOL_WRITE_FAILED"
        if not re.fullmatch(r"[A-Z0-9_]{1,64}", safe_code):
            safe_code = "TOOL_WRITE_FAILED"
        return WorkerResult(
            run_id=request.run_id,
            worker_id=request.worker_id,
            success=False,
            error=WorkerError(
                code=safe_code,
                message="AIAT Tool Service did not confirm the file write",
                retryable=bool(response.error.retryable) if response.error is not None else False,
                terminal=bool(response.error.terminal) if response.error is not None else False,
                category="tool_mediation",
            ),
            output={"file_write_confirmed": False, "path": path},
            completion_criteria={"file_write_confirmed_by_tool_service": False},
        )

    result = response.result
    if not isinstance(result, dict) or result.get("success") is not True or result.get("path") != path:
        return WorkerResult(
            run_id=request.run_id,
            worker_id=request.worker_id,
            success=False,
            error=WorkerError(
                code="TOOL_WRITE_RESULT_INVALID",
                message="AIAT Tool Service returned no matching successful file-write result",
                retryable=False,
                terminal=True,
                category="tool_mediation",
            ),
            output={"file_write_confirmed": False, "path": path},
            completion_criteria={"file_write_confirmed_by_tool_service": False},
        )

    return WorkerResult(
        run_id=request.run_id,
        worker_id=request.worker_id,
        success=True,
        output={
            "file_write_confirmed": True,
            "tool_name": "file_write",
            "path": path,
            "bytes_written": result.get("bytes_written"),
        },
        completion_criteria={
            "file_write_confirmed_by_tool_service": True,
        },
    )
