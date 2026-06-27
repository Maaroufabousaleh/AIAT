#!/usr/bin/env python3
"""Idempotently provision AIAT's OmniRoute providers and embedded services."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BASE_URL = os.environ.get("OMNIROUTE_BOOTSTRAP_URL", "http://localhost:20128").rstrip("/")
MANAGEMENT_KEY = os.environ.get("OMNIROUTE_MANAGEMENT_KEY", "").strip()


@dataclass(frozen=True)
class ProviderSpec:
    env_name: str
    provider: str
    name: str
    default_model: str


PROVIDERS = (
    ProviderSpec("OPENAI_API_KEY", "openai", "AIAT OpenAI", "gpt-4o-mini"),
    ProviderSpec("GEMINI_API_KEY", "gemini", "AIAT Gemini", "gemini-2.5-flash"),
    ProviderSpec("OPENROUTER_API_KEY", "openrouter", "AIAT OpenRouter", "openrouter/free"),
    ProviderSpec("GROQ_API_KEY", "groq", "AIAT Groq", "llama-3.3-70b-versatile"),
    ProviderSpec("CEREBRAS_API_KEY", "cerebras", "AIAT Cerebras", "zai-glm-4.7"),
    ProviderSpec("MISTRAL_API_KEY", "mistral", "AIAT Mistral", "devstral-latest"),
    ProviderSpec(
        "CLOUDFLARE_API_TOKEN",
        "cloudflare-ai",
        "AIAT Cloudflare Workers AI",
        "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
    ),
    ProviderSpec(
        "NVIDIA_API_KEY",
        "nvidia",
        "AIAT NVIDIA",
        "meta/llama-3.1-70b-instruct",
    ),
    ProviderSpec("MINIMAX_API_KEY", "minimax", "AIAT MiniMax", "MiniMax-M2.7"),
)


def request_json(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    expected: tuple[int, ...] = (200, 201, 204),
) -> Any:
    headers = {"Accept": "application/json"}
    if MANAGEMENT_KEY:
        headers["Authorization"] = f"Bearer {MANAGEMENT_KEY}"
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            body = response.read()
            if response.status not in expected:
                raise RuntimeError(f"{method} {path} returned HTTP {response.status}")
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")[:500]
        if exc.code in (401, 403) and not MANAGEMENT_KEY:
            raise RuntimeError(
                "OmniRoute management authentication is enabled. Set "
                "OMNIROUTE_MANAGEMENT_KEY to a manage-scoped OmniRoute key."
            ) from exc
        raise RuntimeError(f"{method} {path} failed with HTTP {exc.code}: {message}") from exc


def wait_for_omniroute() -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE_URL}/dashboard", timeout=5) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(2)
    raise RuntimeError("OmniRoute did not become ready within 90 seconds")


def configure_providers(*, test_connections: bool) -> None:
    current = request_json("GET", "/api/providers").get("connections", [])
    by_name = {item.get("name"): item for item in current}

    for spec in PROVIDERS:
        api_key = os.environ.get(spec.env_name, "").strip()
        if not api_key:
            print(f"[OmniRoute] {spec.provider}: skipped ({spec.env_name} is empty)")
            continue

        existing = by_name.get(spec.name)
        payload = {
            "name": spec.name,
            "apiKey": api_key,
            "defaultModel": spec.default_model,
            "priority": 1,
            "isActive": True,
        }
        if existing:
            result = request_json("PUT", f"/api/providers/{existing['id']}", payload)
            connection = result.get("connection", existing)
            action = "updated"
        else:
            create_payload = {"provider": spec.provider, **payload}
            create_payload.pop("isActive", None)
            result = request_json("POST", "/api/providers", create_payload)
            connection = result["connection"]
            by_name[spec.name] = connection
            action = "created"

        print(f"[OmniRoute] {spec.provider}: {action}; default={spec.default_model}")
        if test_connections:
            tested = request_json("POST", f"/api/providers/{connection['id']}/test", {})
            if not tested.get("valid"):
                diagnosis = (tested.get("diagnosis") or {}).get("type", "unknown")
                raise RuntimeError(f"Provider validation failed for {spec.provider}: {diagnosis}")
            print(f"[OmniRoute] {spec.provider}: validation passed")


def configure_model_intelligence() -> None:
    request_json("PATCH", "/api/settings", {"modelsDevSyncEnabled": True})
    request_json("POST", "/api/settings/models-dev", {"action": "start"})
    print("[OmniRoute] models.dev capability and pricing sync enabled")


def compose_exec_repair() -> None:
    compose_dir = Path(__file__).resolve().parent
    command = [
        "docker",
        "compose",
        "-f",
        "docker-compose.yml",
        "-f",
        "docker-compose.dev.yml",
        "exec",
        "-T",
        "omniroute",
        "/bin/sh",
        "/opt/aiat/repair-cliproxyapi.sh",
    ]
    subprocess.run(command, cwd=compose_dir, check=True)


def configure_service(name: str) -> dict[str, Any]:
    status = request_json("GET", f"/api/services/{name}/status")
    version_env = "NINEROUTER_VERSION" if name == "9router" else "CLIPROXYAPI_VERSION"
    desired_version = os.environ.get(version_env, "latest").strip() or "latest"
    installed_version = status.get("installedVersion")
    version_mismatch = desired_version != "latest" and installed_version != desired_version
    if status.get("state") == "not_installed" or not installed_version or version_mismatch:
        request_json(
            "POST",
            f"/api/services/{name}/install",
            {"version": desired_version},
        )
        status = request_json("GET", f"/api/services/{name}/status")
        print(f"[OmniRoute] {name}: installed {status.get('installedVersion')}")

    # OmniRoute 3.8.38 can create duplicate supervisors when its internal
    # auto-start and the management route run during the same boot. AIAT owns
    # service startup from mas.sh, so keep the upstream toggle disabled.
    request_json("POST", f"/api/services/{name}/auto-start", {"enabled": False})
    if name == "9router":
        request_json("POST", "/api/services/9router/provider-expose", {"enabled": True})
    if name == "cliproxy":
        compose_exec_repair()

    # Auto-start runs asynchronously during OmniRoute bootstrap. Give an
    # already-starting service time to settle before issuing an explicit start;
    # otherwise two child processes can race for the same loopback port.
    for _ in range(15):
        status = request_json("GET", f"/api/services/{name}/status")
        if status.get("state") == "running" and status.get("health") == "healthy":
            print(f"[OmniRoute] {name}: already running and healthy; AIAT-managed startup")
            return status
        if status.get("state") not in ("starting", "unknown"):
            break
        time.sleep(2)

    if status.get("state") == "running" and status.get("health") == "healthy":
        print(f"[OmniRoute] {name}: already running and healthy; AIAT-managed startup")
        return status

    request_json("POST", f"/api/services/{name}/start")

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        status = request_json("GET", f"/api/services/{name}/status")
        if status.get("state") == "running" and status.get("health") == "healthy":
            print(f"[OmniRoute] {name}: running and healthy; AIAT-managed startup")
            return status
        if status.get("state") == "error":
            raise RuntimeError(f"{name} failed to start: {status.get('lastError', 'unknown error')}")
        time.sleep(2)
    raise RuntimeError(f"{name} did not become healthy within 60 seconds")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-providers", action="store_true")
    parser.add_argument("--skip-services", action="store_true")
    args = parser.parse_args()

    wait_for_omniroute()
    configure_providers(test_connections=args.test_providers)
    configure_model_intelligence()
    if not args.skip_services:
        configure_service("9router")
        configure_service("cliproxy")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[OmniRoute] configuration failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
