"""Target-specific infrastructure and monitoring adapter executable."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml


def _run_bounded(command: list[str], *, timeout: int, limit: int = 200_000) -> dict[str, Any]:
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(command, stdout=stdout_file, stderr=stderr_file)
        try:
            returncode: int | None = process.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            returncode = None
            timed_out = True
        stdout_size = stdout_file.tell()
        stderr_size = stderr_file.tell()
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read(limit).decode("utf-8", errors="replace")
        stderr = stderr_file.read(limit).decode("utf-8", errors="replace")
    return {
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": stdout_size > limit,
        "stderr_truncated": stderr_size > limit,
        "timed_out": timed_out,
    }


def _safe_output(base: Path, relative: str) -> Path:
    target = (base / relative).resolve()
    target.relative_to(base.resolve())
    return target


def _run_tofu_plan(config: dict[str, Any]) -> dict[str, Any]:
    binary = shutil.which("tofu") or shutil.which("opentofu")
    if binary is None:
        return {"available": False, "reason": "opentofu_binary_not_found"}
    args = [str(value) for value in config.get("args", ["-input=false", "-no-color"])]
    result = _run_bounded(
        [binary, "plan", *args], timeout=min(int(config.get("timeout_seconds", 120)), 900)
    )
    return {
        "available": True,
        "configured": True,
        "target": "opentofu_plan_only",
        "mutated": False,
        **result,
        "verified": result["returncode"] == 0,
    }


def _local_docker_plan(resource: str, config: dict[str, Any]) -> dict[str, Any]:
    image = str(config.get("image") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:@-]{1,255}", image):
        raise ValueError("local_docker requires a pinned image name")
    service_name = re.sub(r"[^a-z0-9_-]", "-", resource.lower()).strip("-") or "preview"
    compose = {
        "services": {
            service_name: {
                "image": image,
                "network_mode": "none",
                "read_only": True,
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                "pids_limit": 256,
                "mem_limit": "512m",
            }
        }
    }
    if config.get("command"):
        command = config["command"]
        if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
            raise ValueError("command must be a string array")
        compose["services"][service_name]["command"] = command
    output = _safe_output(Path.cwd(), str(config.get("output") or "infra/docker-compose.preview.yml"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(compose, sort_keys=False), encoding="utf-8")
    if config.get("apply"):
        if not str(config.get("approval_id") or "").strip():
            raise ValueError("local_docker apply requires approval_id")
        docker = shutil.which("docker")
        if docker is None:
            return {
                "available": False,
                "target": "local_docker",
                "reason": "docker_binary_not_found",
                "plan_file": str(output.relative_to(Path.cwd())),
            }
        result = _run_bounded(
            [docker, "compose", "-f", str(output), "up", "-d"], timeout=120, limit=64_000
        )
        return {
            "available": True,
            "configured": True,
            "target": "local_docker",
            "mutated": result["returncode"] == 0,
            "approval_id": config["approval_id"],
            **result,
            "plan_file": str(output.relative_to(Path.cwd())),
        }
    return {
        "available": True,
        "configured": True,
        "target": "local_docker",
        "mutated": False,
        "plan_file": str(output.relative_to(Path.cwd())),
        "verified": output.is_file(),
    }


def provision(payload: dict[str, Any]) -> dict[str, Any]:
    resource = str(payload.get("resource") or "").strip()
    if not resource:
        raise ValueError("resource is required")
    config = dict(payload.get("config") or {})
    target = str(config.get("target") or "opentofu_plan_only")
    if target == "opentofu_plan_only":
        return _run_tofu_plan(config)
    if target == "local_docker":
        return _local_docker_plan(resource, config)
    raise ValueError(f"unsupported infrastructure target: {target}")


def monitoring(payload: dict[str, Any]) -> dict[str, Any]:
    config = dict(payload.get("config") or {})
    target = str(config.get("target") or "prometheus")
    if target != "prometheus":
        raise ValueError("only prometheus monitoring target is supported")
    output_dir = _safe_output(Path.cwd(), str(config.get("output_dir") or "monitoring"))
    output_dir.mkdir(parents=True, exist_ok=True)
    services = config.get(
        "services",
        {
            "orchestrator-api": "orchestrator-api:8000",
            "message-router": "message-router:8001",
            "tool-service": "tool-service:8002",
        },
    )
    if not isinstance(services, dict) or not services:
        raise ValueError("services must be a non-empty object")
    scrape_configs = [
        {"job_name": str(name), "static_configs": [{"targets": [str(target_value)]}]}
        for name, target_value in services.items()
    ]
    rules = payload.get("rules") or []
    files = {
        "prometheus.yml": {"global": {"scrape_interval": "15s"}, "scrape_configs": scrape_configs},
        "alert_rules.yml": {"groups": [{"name": "aiat", "rules": rules}]},
        "synthetic_checks.yml": {
            "checks": [
                {"name": str(name), "url": f"http://{target_value}/health"}
                for name, target_value in services.items()
            ]
        },
    }
    written = []
    for name, content in files.items():
        path = output_dir / name
        path.write_text(yaml.safe_dump(content, sort_keys=False), encoding="utf-8")
        written.append(str(path.relative_to(Path.cwd())))
    return {
        "available": True,
        "configured": True,
        "target": "prometheus",
        "files_written": written,
        "checks": [{"name": str(name), "status": "configured"} for name in services],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("infra", "monitoring"))
    parser.add_argument("--json-stdin", action="store_true", required=True)
    args = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("adapter payload must be a JSON object")
        result = provision(payload) if args.operation == "infra" else monitoring(payload)
    except Exception as exc:
        result = {"available": False, "error": str(exc)}
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
