"""Small, secret-safe command line client for the AIAT control plane.

The Compose wrapper remains responsible for container lifecycle.  This client
is intentionally narrower: it gives an operator a repeatable API-facing
``status``/``diagnostics``/``bootstrap`` read path and explicit ``resume`` and
``shutdown`` commands without requiring curl or an SDK checkout.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_URL = "http://localhost:8000"
DEFAULT_TIMEOUT = 5.0


@dataclass(frozen=True)
class Response:
    """Safe response envelope used by command handlers and tests."""

    status_code: int | None
    payload: dict[str, Any] | None
    error_type: str | None = None

    @property
    def ok(self) -> bool:
        return self.status_code is not None and 200 <= self.status_code < 300


def _base_url(value: str | None) -> str:
    return (value or os.getenv("AIAT_ORCHESTRATOR_URL") or DEFAULT_URL).rstrip("/")


def _api_key(value: str | None) -> str | None:
    return value or os.getenv("AIAT_OPERATOR_API_KEY") or os.getenv("MAS_API_KEY")


def request_json(
    *,
    base_url: str,
    path: str,
    method: str = "GET",
    api_key: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Response:
    """Make one control-plane request without exposing response/error text."""

    headers = {"Accept": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    request = Request(f"{base_url.rstrip('/')}{path}", headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            decoded = json.loads(raw.decode("utf-8")) if raw else {}
            payload = decoded if isinstance(decoded, dict) else {"value": decoded}
            return Response(status_code=response.status, payload=payload)
    except HTTPError as exc:
        # Do not print or retain the body: an upstream error may contain
        # credentials, provider details, or a traceback.
        return Response(status_code=exc.code, payload=None, error_type="HTTPError")
    except (TimeoutError, URLError, OSError):
        return Response(status_code=None, payload=None, error_type="ConnectionError")
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return Response(status_code=None, payload=None, error_type="InvalidResponse")


def _record(response: Response) -> dict[str, Any]:
    """Convert a response to a stable, payload-safe CLI record."""

    record: dict[str, Any] = {"http_status": response.status_code}
    if response.payload is not None:
        record["payload"] = response.payload
    if response.error_type:
        record["error_type"] = response.error_type
    return record


def run(
    command: str,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[int, dict[str, Any]]:
    """Run one CLI command and return ``(exit_code, safe_record)``."""

    url = _base_url(base_url)
    key = _api_key(api_key)

    if command == "status":
        response = request_json(base_url=url, path="/system/status", api_key=key, timeout=timeout)
        return (0 if response.ok else 1), {"command": command, **_record(response)}
    if command == "diagnostics":
        response = request_json(
            base_url=url,
            path="/system/diagnostics",
            api_key=key,
            timeout=timeout,
        )
        payload_status = (response.payload or {}).get("status")
        return (0 if response.ok else 1), {
            "command": command,
            **_record(response),
            **({"status": payload_status} if isinstance(payload_status, str) else {}),
        }
    if command == "bootstrap":
        health = request_json(base_url=url, path="/health", api_key=key, timeout=timeout)
        diagnostics = request_json(
            base_url=url,
            path="/system/diagnostics",
            api_key=key,
            timeout=timeout,
        )
        diagnostic_status = (diagnostics.payload or {}).get("status")
        ready = health.ok and diagnostics.ok and diagnostic_status == "ok"
        return (0 if ready else 1), {
            "command": command,
            "status": "ready" if ready else "degraded",
            "health": _record(health),
            "diagnostics": _record(diagnostics),
        }
    if command in {"resume", "shutdown"}:
        response = request_json(
            base_url=url,
            path=f"/system/{command}",
            method="POST",
            api_key=key,
            timeout=timeout,
        )
        return (0 if response.ok else 1), {"command": command, **_record(response)}
    raise ValueError(f"unsupported command: {command}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AIAT control-plane operator CLI")
    parser.add_argument(
        "command",
        choices=("status", "diagnostics", "bootstrap", "resume", "shutdown"),
        help="readiness or explicit system-control operation",
    )
    parser.add_argument("--url", help="orchestrator URL (default: AIAT_ORCHESTRATOR_URL or localhost)")
    parser.add_argument(
        "--api-key",
        help="operator API key (default: AIAT_OPERATOR_API_KEY or MAS_API_KEY)",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="request timeout in seconds")
    parser.add_argument("--compact", action="store_true", help="emit one-line JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        exit_code, record = run(
            args.command,
            base_url=args.url,
            api_key=args.api_key,
            timeout=args.timeout,
        )
    except (OSError, ValueError) as exc:
        record = {"command": args.command, "error_type": type(exc).__name__}
        exit_code = 1
    print(json.dumps(record, sort_keys=True, separators=(",", ":") if args.compact else (",", ": ")))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
