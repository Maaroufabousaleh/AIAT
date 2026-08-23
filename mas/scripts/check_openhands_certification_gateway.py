"""Run scalar-only health and route probes for the disposable model gateway."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import httpx

try:
    from openhands_gateway_errors import classify_failure
except ImportError:  # pragma: no cover - package invocation fallback
    from scripts.openhands_gateway_errors import classify_failure  # type: ignore

SCHEMA = "aiat.openhands-certification-gateway-health.v1"
AIAT_MODEL = "omniroute-coding"
PROVIDER = "groq"
PROVIDER_MODEL = "llama-3.3-70b-versatile"


class GatewayProbeError(RuntimeError):
    """A required gateway health or deterministic route probe failed."""


def _usage(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, int] = {}
    for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        number = value.get(name)
        if isinstance(number, int) and number >= 0:
            result[name] = number
    return result


def _health(client: httpx.Client, path: str) -> dict[str, Any]:
    response = client.get(path)
    return {"path": path, "http_status": response.status_code, "passed": response.status_code == 200}


def probe(
    *,
    litellm_url: str,
    omniroute_url: str,
    gateway_key: str,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    if not gateway_key:
        raise GatewayProbeError("model_gateway_key_missing")
    created_client = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0), follow_redirects=False)
    try:
        omniroute = _health(client, f"{omniroute_url.rstrip('/')}/api/health/ping")
        if not omniroute["passed"]:
            raise GatewayProbeError("omniroute_health_failed")
        litellm = _health(client, f"{litellm_url.rstrip('/')}/health/readiness")
        if not litellm["passed"]:
            raise GatewayProbeError("litellm_health_failed")

        response = client.post(
            f"{litellm_url.rstrip('/')}/v1/chat/completions",
            headers={"Authorization": f"Bearer {gateway_key}"},
            json={
                "model": AIAT_MODEL,
                "messages": [
                    {"role": "user", "content": "Reply with one short confirmation token."}
                ],
                "temperature": 0,
                "max_tokens": 8,
                "stream": False,
            },
        )
        if response.status_code != 200:
            raise GatewayProbeError("litellm_route_probe_failed")
        try:
            body = response.json()
        except ValueError as exc:
            raise GatewayProbeError("litellm_route_probe_invalid_json") from exc
        choices = body.get("choices") if isinstance(body, dict) else None
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise GatewayProbeError("litellm_route_probe_missing_choice")
        message = choices[0].get("message")
        if not isinstance(message, dict) or not str(message.get("content") or "").strip():
            raise GatewayProbeError("litellm_route_probe_empty_response")
        usage = _usage(body.get("usage")) if isinstance(body, dict) else {}
        return {
            "schema_version": SCHEMA,
            "status": "PASS",
            "health": {"omniroute": omniroute, "litellm": litellm},
            "route": {
                "requested_aiat_model": AIAT_MODEL,
                "resolved_provider": PROVIDER,
                "resolved_provider_model": f"{PROVIDER}/{PROVIDER_MODEL}",
                "litellm_http_status": response.status_code,
                "response_success": True,
                "usage": usage,
                "selected_connection_id_present": bool(
                    response.headers.get("x-omniroute-selected-connection-id")
                ),
                "raw_response_retained": False,
            },
            "gateway_key_retained": False,
        }
    finally:
        if created_client:
            client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--litellm-url", default=os.getenv("LITELLM_BASE_URL", ""))
    parser.add_argument("--omniroute-url", default=os.getenv("OMNIROUTE_BASE_URL", ""))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = probe(
            litellm_url=args.litellm_url,
            omniroute_url=args.omniroute_url,
            gateway_key=os.getenv("OPENHANDS_MODEL_GATEWAY_API_KEY", "").strip(),
        )
    except (GatewayProbeError, httpx.HTTPError) as exc:
        reason = str(exc) if isinstance(exc, GatewayProbeError) else "gateway_transport_error"
        stage = "gateway_response"
        if "omniroute_health" in reason:
            stage = "omniroute_health"
        elif "litellm_health" in reason:
            stage = "litellm_health"
        elif "route_probe" in reason:
            stage = "litellm_to_omniroute"
        if isinstance(exc, httpx.TimeoutException):
            failure = classify_failure(stage="provider", exception_type="ReadTimeout")
        elif isinstance(exc, httpx.TransportError):
            failure = classify_failure(stage="provider", exception_type="ConnectError")
        else:
            failure = classify_failure(stage=stage, error_code=reason)
        report = {
            "schema_version": SCHEMA,
            "status": "BLOCKED",
            "failure": reason,
            "failure_class": failure.failure_class,
            "gateway_key_retained": False,
            "raw_response_retained": False,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": "BLOCKED", "failure": report["failure"]}, sort_keys=True))
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
