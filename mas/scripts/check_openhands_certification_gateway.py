"""Run scalar-only health and route probes for the disposable model gateway."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

import httpx

try:
    from openhands_gateway_errors import classify_failure
except ImportError:  # pragma: no cover - package invocation fallback
    from scripts.openhands_gateway_errors import classify_failure  # type: ignore

try:
    from openhands_model_routing import (
        AIAT_MODEL_ID,
        AUTO_ROUTER_MODEL,
        CERTIFICATION_BASELINE_MODEL,
        CERTIFICATION_NOAUTH_PROVIDER_BLOCKLIST,
        CERTIFICATION_PROVIDER,
        LITELLM_AUTO_ROUTER_MODEL,
    )
except ImportError:  # pragma: no cover - package invocation fallback
    from scripts.openhands_model_routing import (  # type: ignore
        AIAT_MODEL_ID,
        AUTO_ROUTER_MODEL,
        CERTIFICATION_BASELINE_MODEL,
        CERTIFICATION_NOAUTH_PROVIDER_BLOCKLIST,
        CERTIFICATION_PROVIDER,
        LITELLM_AUTO_ROUTER_MODEL,
    )

SCHEMA = "aiat.openhands-certification-gateway-health.v1"
OMNIROUTE_HEALTH_PATH = "/api/monitoring/health"
AIAT_MODEL = AIAT_MODEL_ID
PROVIDER = CERTIFICATION_PROVIDER
PROVIDER_MODEL = CERTIFICATION_BASELINE_MODEL


class GatewayProbeError(RuntimeError):
    """A required gateway health or deterministic route probe failed."""

    def __init__(
        self,
        reason: str,
        *,
        stage: str = "gateway_response",
        http_status: int | None = None,
        exception_type: str | None = None,
        response_error_code: str | None = None,
    ) -> None:
        super().__init__(reason)
        self.stage = stage
        self.http_status = http_status
        self.exception_type = exception_type
        self.response_error_code = response_error_code


_SAFE_ERROR_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


def _response_error_code(response: httpx.Response) -> str | None:
    """Extract one bounded error code without retaining a response payload."""

    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    candidates: tuple[object, ...]
    if isinstance(error, dict):
        candidates = (error.get("code"), error.get("type"), body.get("code"))
    else:
        candidates = (body.get("code"), body.get("type"))
    for candidate in candidates:
        if isinstance(candidate, str):
            value = candidate.strip()
            if _SAFE_ERROR_CODE.fullmatch(value):
                return value
    return None


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _usage(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, int] = {}
    for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        number = value.get(name)
        if isinstance(number, int) and number >= 0:
            result[name] = number
    return result


def _health(client: httpx.Client, path: str, *, stage: str) -> dict[str, Any]:
    try:
        response = client.get(path)
    except httpx.TimeoutException as exc:
        raise GatewayProbeError(
            "gateway_health_timeout",
            stage=stage,
            exception_type="ReadTimeout",
        ) from exc
    except httpx.TransportError as exc:
        raise GatewayProbeError(
            "gateway_health_transport_error",
            stage=stage,
            exception_type="ConnectError",
        ) from exc
    return {
        "path": path,
        "http_status": response.status_code,
        "passed": 200 <= response.status_code < 300,
    }


def probe(
    *,
    litellm_url: str,
    omniroute_url: str,
    gateway_key: str,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    if not gateway_key:
        raise GatewayProbeError("model_gateway_key_missing", stage="gateway_auth")
    created_client = client is None
    client = client or httpx.Client(
        timeout=httpx.Timeout(120.0, connect=10.0), follow_redirects=False
    )
    try:
        if not omniroute_url or not litellm_url:
            raise GatewayProbeError("gateway_endpoint_missing", stage="gateway_response")
        omniroute = _health(
            client,
            f"{omniroute_url.rstrip('/')}{OMNIROUTE_HEALTH_PATH}",
            stage="omniroute_health",
        )
        if not omniroute["passed"]:
            raise GatewayProbeError(
                "omniroute_health_failed",
                stage="omniroute_health",
                http_status=int(omniroute["http_status"]),
            )
        litellm = _health(
            client,
            f"{litellm_url.rstrip('/')}/health/readiness",
            stage="litellm_health",
        )
        if not litellm["passed"]:
            raise GatewayProbeError(
                "litellm_health_failed",
                stage="litellm_health",
                http_status=int(litellm["http_status"]),
            )

        try:
            response = client.post(
                f"{litellm_url.rstrip('/')}/v1/chat/completions",
                headers={"Authorization": f"Bearer {gateway_key}"},
                json={
                    "model": AIAT_MODEL,
                    "messages": [
                        {"role": "user", "content": "Reply with one short confirmation token."}
                    ],
                    "temperature": 0,
                    # Keep the auto/coding smoke request compatible with the
                    # governed Groq GPT-OSS baseline: use the current token
                    # spelling and leave room for hidden reasoning tokens.
                    "max_completion_tokens": 64,
                    "reasoning_effort": "low",
                    "include_reasoning": False,
                    "stream": False,
                },
            )
        except httpx.TimeoutException as exc:
            raise GatewayProbeError(
                "gateway_route_timeout",
                stage="litellm_to_omniroute",
                exception_type="ReadTimeout",
            ) from exc
        except httpx.TransportError as exc:
            raise GatewayProbeError(
                "gateway_route_transport_error",
                stage="litellm_to_omniroute",
                exception_type="ConnectError",
            ) from exc
        if response.status_code != 200:
            error_code = _response_error_code(response)
            if response.status_code in {401, 403}:
                raise GatewayProbeError(
                    "model_gateway_auth_failed",
                    stage="gateway_auth",
                    http_status=response.status_code,
                    response_error_code=error_code,
                )
            if response.status_code == 429:
                raise GatewayProbeError(
                    "provider_rate_limit",
                    stage="provider",
                    http_status=429,
                    response_error_code=error_code,
                )
            if response.status_code == 404:
                # A 404 is not sufficient to identify an auto-router failure:
                # LiteLLM can also return it for an unknown alias or malformed
                # upstream route.  Only a bounded, explicit gateway code may
                # claim that no auto-router candidates were available.
                if error_code in {"auto_no_valid_providers", "no_valid_providers"}:
                    raise GatewayProbeError(
                        "auto_no_valid_providers",
                        stage="provider",
                        http_status=404,
                        response_error_code=error_code,
                    )
                raise GatewayProbeError(
                    "litellm_route_not_found",
                    stage="litellm_to_omniroute",
                    http_status=404,
                    response_error_code=error_code,
                )
            raise GatewayProbeError(
                "litellm_route_probe_failed",
                stage="litellm_to_omniroute",
                http_status=response.status_code,
                response_error_code=error_code,
            )
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
                "resolved_route_model": AUTO_ROUTER_MODEL,
                "litellm_upstream_model": LITELLM_AUTO_ROUTER_MODEL,
                "routing_mode": "omniroute_auto_coding",
                # Provider provisioning is an exact-one governed connection
                # in this disposable certification wave.  Recording the
                # bounded provider identity makes a successful auto/coding
                # response attributable without retaining a connection ID,
                # model payload, or credential.
                "provider_attribution": {
                    "provider": PROVIDER,
                    "baseline_model": PROVIDER_MODEL,
                    "basis": "single_governed_certification_connection",
                },
                "provider_scope": {
                    "mode": "explicit_connection_only",
                    "noauth_provider_blocklist": list(CERTIFICATION_NOAUTH_PROVIDER_BLOCKLIST),
                    "verification": "provider_provisioning_readback_required",
                    "credential_values_retained": False,
                },
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
    parser.add_argument(
        "--auto-routing-output",
        type=Path,
        help="Optional second path for the governed auto/coding scalar evidence.",
    )
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
        http_status = None
        if isinstance(exc, GatewayProbeError):
            stage = exc.stage
            http_status = exc.http_status
            exception_type = exc.exception_type
        elif "omniroute_health" in reason:
            stage = "omniroute_health"
            exception_type = None
        elif "litellm_health" in reason:
            stage = "litellm_health"
            exception_type = None
        elif "route_probe" in reason:
            stage = "litellm_to_omniroute"
            exception_type = None
        else:
            exception_type = None
        if isinstance(exc, httpx.TimeoutException):
            failure = classify_failure(stage=stage, exception_type="ReadTimeout")
        elif isinstance(exc, httpx.TransportError):
            failure = classify_failure(stage=stage, exception_type="ConnectError")
        else:
            failure = classify_failure(
                stage=stage,
                http_status=http_status,
                error_code=reason,
                exception_type=exception_type,
            )
        report = {
            "schema_version": SCHEMA,
            "status": "BLOCKED",
            "failure": reason,
            "failure_class": failure.failure_class,
            "failure_stage": failure.stage,
            "failure_http_status": failure.http_status,
            "failure_retryable": failure.retryable,
            "requested_aiat_model": AIAT_MODEL,
            "resolved_route_model": AUTO_ROUTER_MODEL,
            "litellm_upstream_model": LITELLM_AUTO_ROUTER_MODEL,
            "routing_mode": "omniroute_auto_coding",
            "gateway_key_retained": False,
            "raw_response_retained": False,
        }
        if isinstance(exc, GatewayProbeError) and exc.response_error_code:
            report["response_error_code"] = exc.response_error_code
        _write_report(args.output, report)
        if args.auto_routing_output:
            _write_report(args.auto_routing_output, report)
        print(json.dumps({"status": "BLOCKED", "failure": report["failure"]}, sort_keys=True))
        return 2
    _write_report(args.output, report)
    if args.auto_routing_output:
        _write_report(args.auto_routing_output, report)
    print(json.dumps({"status": report["status"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
