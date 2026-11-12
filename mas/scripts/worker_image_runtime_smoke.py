"""Exercise the deterministic worker protocol inside a restricted OCI runtime.

The response is a fixture supplied by this smoke harness. This checks the
worker image and ProcessAdapter wire contract; it is not a governed WorkerRun
or evidence of a real Tool Service file write.
"""

from __future__ import annotations

import argparse
import json
import selectors
import subprocess
from pathlib import Path
from typing import Any

RUN_ID = "00000000-0000-4000-8000-000000000041"
WORKER_ID = "00000000-0000-4000-8000-000000000042"
PROJECT_ID = "00000000-0000-4000-8000-000000000043"
TASK_PATH = "ci/sandboxed-smoke.txt"
TASK_CONTENT = "worker-image-runsc-smoke\n"


def _read_line(process: subprocess.Popen[str], selector: selectors.BaseSelector, timeout: float) -> str:
    if process.stdout is None:
        raise RuntimeError("worker stdout is unavailable")
    ready = selector.select(timeout)
    if not ready:
        raise TimeoutError("worker did not emit a protocol line before timeout")
    line = process.stdout.readline()
    if not line:
        raise RuntimeError("worker exited before completing its protocol response")
    return line


def run_smoke(image_ref: str, runtime: str, *, timeout: float) -> dict[str, Any]:
    inspect = subprocess.run(
        ["docker", "image", "inspect", image_ref],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    image = json.loads(inspect.stdout)[0]
    image_id = image.get("Id")
    config = image.get("Config") or {}
    labels = config.get("Labels") or {}
    if not isinstance(image_id, str) or not image_id.startswith("sha256:"):
        raise RuntimeError("candidate image has no immutable local config identity")
    if config.get("User") != "65532:65532":
        raise RuntimeError("candidate image does not default to the expected unprivileged user")

    command = [
        "docker",
        "run",
        "--rm",
        "--runtime",
        runtime,
        "--network",
        "none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit=32",
        "--memory=128m",
        "--cpus=1",
        "--user=65532:65532",
        "--interactive",
        image_ref,
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    if process.stdin is None or process.stdout is None or process.stderr is None:
        process.kill()
        raise RuntimeError("worker process pipes are unavailable")

    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        request = {
            "run_id": RUN_ID,
            "worker_id": WORKER_ID,
            "project_id": PROJECT_ID,
            "task_type": "deterministic_file_generation",
            "tool_grants": ["file_write"],
            "task_input": {"path": TASK_PATH, "content": TASK_CONTENT},
        }
        process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
        process.stdin.flush()
        event = json.loads(_read_line(process, selector, timeout))
        tool_request = event.get("tool_request") if isinstance(event, dict) else None
        if (
            not isinstance(event, dict)
            or event.get("event_type") != "tool_request"
            or event.get("run_id") != RUN_ID
            or event.get("worker_id") != WORKER_ID
            or not isinstance(tool_request, dict)
            or tool_request.get("tool_name") != "file_write"
            or tool_request.get("arguments", {}).get("path") != TASK_PATH
        ):
            raise RuntimeError("worker did not emit the expected correlated file_write request")

        response = {
            "message_type": "WorkerToolResponse",
            "payload": {
                "request_id": tool_request["request_id"],
                "run_id": RUN_ID,
                "tool_name": "file_write",
                "success": True,
                "result": {
                    "success": True,
                    "path": TASK_PATH,
                    "bytes_written": len(TASK_CONTENT.encode("utf-8")),
                },
            },
        }
        process.stdin.write(json.dumps(response, separators=(",", ":")) + "\n")
        process.stdin.flush()
        process.stdin.close()
        result = json.loads(_read_line(process, selector, timeout))
        return_code = process.wait(timeout=timeout)
        stderr = process.stderr.read()
        if not isinstance(result, dict) or return_code != 0 or result.get("success") is not True:
            result_success = result.get("success") if isinstance(result, dict) else None
            raise RuntimeError(
                f"worker failed protocol smoke (exit={return_code}, result_success={result_success})"
                + (f": {stderr[-1000:]}" if stderr else "")
            )
        if (
            result.get("output", {}).get("path") != TASK_PATH
            or result.get("output", {}).get("bytes_written") != len(TASK_CONTENT.encode("utf-8"))
        ):
            raise RuntimeError("worker result did not match the fixture response")

        return {
            "schema_version": "aiat.worker.image-runsc-smoke.v1",
            "status": "pass",
            "runtime": runtime,
            "image_ref": image_ref,
            "image_id": image_id,
            "source_revision": labels.get("org.opencontainers.image.revision"),
            "source_sha256": labels.get("com.aiat.worker.source-sha256"),
            "worker_id": WORKER_ID,
            "run_id": RUN_ID,
            "project_id": PROJECT_ID,
            "tool_name": "file_write",
            "path": TASK_PATH,
            "bytes_written": len(TASK_CONTENT.encode("utf-8")),
            "fixture_tool_response": True,
            "real_tool_service_write": False,
            "container_policy": {
                "network": "none",
                "read_only_root": True,
                "capabilities": "all_dropped",
                "no_new_privileges": True,
                "user": "65532:65532",
            },
        }
    except Exception:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=timeout)
        raise
    finally:
        selector.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-ref", required=True)
    parser.add_argument("--runtime", default="runsc")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        evidence = run_smoke(args.image_ref, args.runtime, timeout=args.timeout)
    except (OSError, RuntimeError, subprocess.SubprocessError, TimeoutError, json.JSONDecodeError) as exc:
        safe_error = str(exc).replace("\n", " ")[:1200]
        evidence = {
            "schema_version": "aiat.worker.image-runsc-smoke.v1",
            "status": "fail",
            "error": safe_error,
            "runtime": args.runtime,
            "image_ref": args.image_ref,
            "fixture_tool_response": True,
            "real_tool_service_write": False,
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": evidence["status"], "runtime": args.runtime, "real_tool_service_write": False}))
    return 0 if evidence["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
