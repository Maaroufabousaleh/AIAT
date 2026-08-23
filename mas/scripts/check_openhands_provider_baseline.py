"""Run the deterministic provider/model baseline for OpenHands certification.

This is intentionally separate from the LiteLLM/OmniRoute ``auto/coding``
probe.  It proves one exact provider-qualified route and retains only scalar
status/usage metadata.  A missing or retired baseline is a precise block, not
an implementation failure and never silently falls back to another model.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import httpx

try:
    from openhands_gateway_errors import classify_failure
    from openhands_model_routing import (
        CERTIFICATION_BASELINE_MODEL,
        CERTIFICATION_PROVIDER,
        baseline_route,
    )
except ImportError:  # pragma: no cover - package invocation fallback
    from scripts.openhands_gateway_errors import classify_failure  # type: ignore
    from scripts.openhands_model_routing import (  # type: ignore
        CERTIFICATION_BASELINE_MODEL,
        CERTIFICATION_PROVIDER,
        baseline_route,
    )

SCHEMA = "aiat.openhands-certification-provider-baseline.v1"


class BaselineProbeError(RuntimeError):
    """A deterministic baseline probe failed at a known boundary."""

    def __init__(
        self,
        reason: str,
        *,
        stage: str = "provider",
        http_status: int | None = None,
        exception_type: str | None = None,
    ) -> None:
        super().__init__(reason)
        self.stage = stage
        self.http_status = http_status
        self.exception_type = exception_type


def _usage(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, int] = {}
    for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        number = value.get(name)
        if isinstance(number, int) and number >= 0:
            result[name] = number
    return result


def probe(
    *,
    url: str,
    gateway_key: str,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    if not gateway_key:
        raise BaselineProbeError("model_gateway_key_missing", stage="gateway_auth")
    if not url.startswith(("http://", "https://")):
        raise BaselineProbeError("provider_endpoint_missing", stage="provider")

    created_client = client is None
    client = client or httpx.Client(
        timeout=httpx.Timeout(120.0, connect=10.0),
        follow_redirects=False,
    )
    route = baseline_route()
    try:
        try:
            response = client.post(
                f"{url.rstrip('/')}/v1/chat/completions",
                headers={"Authorization": f"Bearer {gateway_key}"},
                json={
                    "model": route,
                    "messages": [
                        {"role": "user", "content": "Reply with one short confirmation token."}
                    ],
                    "temperature": 0,
                    "max_tokens": 8,
                    "stream": False,
                },
            )
        except httpx.TimeoutException as exc:
            raise BaselineProbeError(
                "provider_route_timeout",
                stage="provider",
                exception_type="ReadTimeout",
            ) from exc
        except httpx.TransportError as exc:
            raise BaselineProbeError(
                "provider_route_transport_error",
                stage="provider",
                exception_type="ConnectError",
            ) from exc

        if response.status_code != 200:
            if response.status_code in {401, 403}:
                raise BaselineProbeError(
                    "model_gateway_auth_failed",
                    stage="gateway_auth",
                    http_status=response.status_code,
                )
            if response.status_code == 404:
                raise BaselineProbeError(
                    "baseline_model_unavailable",
                    stage="provider",
                    http_status=404,
                )
            if response.status_code == 429:
                raise BaselineProbeError("provider_rate_limit", stage="provider", http_status=429)
            raise BaselineProbeError(
                "provider_baseline_request_failed",
                stage="provider",
                http_status=response.status_code,
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise BaselineProbeError("provider_baseline_invalid_json", stage="provider") from exc
        choices = body.get("choices") if isinstance(body, dict) else None
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise BaselineProbeError("provider_baseline_missing_choice", stage="provider")
        message = choices[0].get("message")
        if not isinstance(message, dict) or not str(message.get("content") or "").strip():
            raise BaselineProbeError("provider_baseline_empty_response", stage="provider")
        return {
            "schema_version": SCHEMA,
            "status": "PASS",
            "provider": CERTIFICATION_PROVIDER,
            "provider_model": CERTIFICATION_BASELINE_MODEL,
            "requested_provider_model": route,
            "endpoint": f"{url.rstrip('/')}/v1",
            "http_status": response.status_code,
            "response_success": True,
            "usage": _usage(body.get("usage")) if isinstance(body, dict) else {},
            "selected_connection_id_present": bool(
                response.headers.get("x-omniroute-selected-connection-id")
            ),
            "gateway_key_retained": False,
            "raw_response_retained": False,
        }
    finally:
        if created_client:
            client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.getenv("OMNIROUTE_BASE_URL", ""))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = probe(
            url=args.url,
            gateway_key=os.getenv("OPENHANDS_MODEL_GATEWAY_API_KEY", "").strip(),
        )
    except (BaselineProbeError, httpx.HTTPError) as exc:
        reason = str(exc) if isinstance(exc, BaselineProbeError) else "provider_route_transport_error"
        if isinstance(exc, BaselineProbeError):
            failure = classify_failure(
                stage=exc.stage,
                http_status=exc.http_status,
                error_code=reason,
                exception_type=exc.exception_type,
            )
        elif isinstance(exc, httpx.TimeoutException):
            failure = classify_failure(stage="provider", exception_type="ReadTimeout")
        else:
            failure = classify_failure(stage="provider", exception_type="ConnectError")
        report = {
            "schema_version": SCHEMA,
            "status": "BLOCKED",
            "failure": reason,
            "failure_class": failure.failure_class,
            "failure_stage": failure.stage,
            "failure_http_status": failure.http_status,
            "failure_retryable": failure.retryable,
            "provider": CERTIFICATION_PROVIDER,
            "provider_model": CERTIFICATION_BASELINE_MODEL,
            "requested_provider_model": baseline_route(),
            "gateway_key_retained": False,
            "raw_response_retained": False,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": report["status"], "failure": report["failure"]}, sort_keys=True))
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "provider": report["provider"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
