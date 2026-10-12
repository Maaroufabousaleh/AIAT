#!/usr/local/bin/python3
"""Minimal stdlib-only process entrypoint for the AIAT file-write candidate.

The process has no workspace, database, network, or provider interface. It
accepts one WorkerRun JSON line, asks AIAT for one project-scoped ``file_write``
through the opt-in ProcessAdapter JSONL protocol, and reports success only
after the correlated Tool Service response confirms the same path.
"""

from __future__ import annotations

import json
import re
import sys
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

WORKER_ID = "aiat_deterministic_file_worker"
TASK_TYPE = "deterministic_file_generation"
MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_PATH_LENGTH = 240
MAX_CONTENT_BYTES = 64 * 1024
_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class WorkerInputError(ValueError):
    """Safe validation failure that can be returned without exposing input."""


def _read_bounded_line(stream: object, limit: int) -> bytes:
    line = stream.readline(limit + 1)  # type: ignore[attr-defined]
    if len(line) > limit or (line and not line.endswith(b"\n")):
        raise WorkerInputError("protocol message exceeds the configured limit")
    if not line:
        raise WorkerInputError("protocol message is missing")
    return line


def _validated_request(request: object) -> tuple[str, str, str, str]:
    if not isinstance(request, dict):
        raise WorkerInputError("WorkerRun request must be a JSON object")
    run_id = request.get("run_id")
    worker_id = request.get("worker_id")
    project_id = request.get("project_id")
    try:
        UUID(str(run_id))
        UUID(str(project_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise WorkerInputError("request must contain valid run and project identifiers") from exc
    if worker_id != WORKER_ID:
        raise WorkerInputError("request targets a different worker")
    if request.get("task_type") != TASK_TYPE:
        raise WorkerInputError("unsupported task type")
    tool_grants = request.get("tool_grants")
    if not isinstance(tool_grants, list) or "file_write" not in tool_grants:
        raise WorkerInputError("file_write is not granted to this WorkerRun")
    task_input = request.get("task_input")
    if not isinstance(task_input, dict) or set(task_input) != {"path", "content"}:
        raise WorkerInputError("task input must contain exactly path and content")
    path = task_input.get("path")
    content = task_input.get("content")
    if not isinstance(path, str) or not path or len(path) > MAX_PATH_LENGTH:
        raise WorkerInputError("path is empty or exceeds the configured limit")
    if (
        path.startswith("/")
        or "\\" in path
        or "\x00" in path
        or any(not _PATH_SEGMENT.fullmatch(part) for part in path.split("/"))
    ):
        raise WorkerInputError("path must contain safe relative workspace segments")
    if not isinstance(content, str) or not content:
        raise WorkerInputError("content must be a non-empty string")
    if "\x00" in content:
        raise WorkerInputError("content contains a null byte or exceeds 64 KiB")
    try:
        content_bytes = content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise WorkerInputError("content must be valid UTF-8") from exc
    if len(content_bytes) > MAX_CONTENT_BYTES:
        raise WorkerInputError("content contains a null byte or exceeds 64 KiB")
    return str(run_id), str(worker_id), path, content


def _emit(value: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":"), ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _emit_failure(code: str, message: str) -> None:
    result: dict[str, object] = {
        "success": False,
        "output": {"file_write_confirmed": False},
        "error": {
            "code": code,
            "message": message,
            "retryable": False,
            "terminal": True,
            "category": "validation" if code == "WORKER_INPUT_INVALID" else "tool_mediation",
        },
        "completion_criteria": {"file_write_confirmed_by_tool_service": False},
    }
    # ``ProcessAdapter`` supplies its authoritative run/worker IDs to the final
    # WorkerResult. It is safer to omit them from failure lines before parsing.
    _emit(result)


def main() -> int:
    try:
        request_line = _read_bounded_line(sys.stdin.buffer, MAX_REQUEST_BYTES)
        request = json.loads(request_line)
        run_id, worker_id, path, content = _validated_request(request)
    except (WorkerInputError, json.JSONDecodeError, UnicodeDecodeError):
        _emit_failure("WORKER_INPUT_INVALID", "WorkerRun input failed bounded validation")
        return 2

    request_id = str(uuid4())
    stable_effect_id = str(
        uuid5(NAMESPACE_URL, f"aiat:{worker_id}:{run_id}:file_write:{path}")
    )
    tool_request = {
        "request_id": request_id,
        "run_id": run_id,
        "tool_name": "file_write",
        "arguments": {"path": path, "content": content},
        "permission_scope": ["file_write"],
        "approval_required": False,
        "idempotency_key": stable_effect_id,
    }
    _emit(
        {
            "run_id": run_id,
            "worker_id": worker_id,
            "event_type": "tool_request",
            "tool_request": tool_request,
        }
    )

    try:
        response_line = _read_bounded_line(sys.stdin.buffer, MAX_RESPONSE_BYTES)
        envelope = json.loads(response_line)
    except (WorkerInputError, json.JSONDecodeError, UnicodeDecodeError):
        _emit_failure("TOOL_RESPONSE_INVALID", "AIAT returned no bounded tool response")
        return 2

    response = envelope.get("payload") if isinstance(envelope, dict) else None
    if (
        not isinstance(envelope, dict)
        or envelope.get("message_type") != "WorkerToolResponse"
        or not isinstance(response, dict)
        or response.get("request_id") != request_id
        or response.get("run_id") != run_id
        or response.get("tool_name") != "file_write"
    ):
        _emit_failure("TOOL_RESPONSE_INVALID", "AIAT tool response did not match the pending request")
        return 2

    if response.get("success") is not True:
        error = response.get("error")
        code = error.get("code") if isinstance(error, dict) else "FILE_WRITE_FAILED"
        if not isinstance(code, str) or not re.fullmatch(r"[A-Z0-9_]{1,64}", code):
            code = "FILE_WRITE_FAILED"
        _emit_failure(code, "AIAT Tool Service did not confirm the file write")
        return 1

    effect_result = response.get("result")
    if (
        not isinstance(effect_result, dict)
        or effect_result.get("success") is not True
        or effect_result.get("path") != path
    ):
        _emit_failure("TOOL_WRITE_RESULT_INVALID", "AIAT did not confirm the requested file path")
        return 1

    bytes_written = effect_result.get("bytes_written")
    if isinstance(bytes_written, bool) or not isinstance(bytes_written, int) or not 0 <= bytes_written <= MAX_CONTENT_BYTES:
        _emit_failure("TOOL_WRITE_RESULT_INVALID", "AIAT returned an invalid file-write size")
        return 1

    _emit(
        {
            "success": True,
            "output": {
                "file_write_confirmed": True,
                "tool_name": "file_write",
                "path": path,
                "bytes_written": bytes_written,
            },
            "completion_criteria": {"file_write_confirmed_by_tool_service": True},
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
