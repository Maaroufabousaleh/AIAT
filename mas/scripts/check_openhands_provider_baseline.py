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
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable

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
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_RETRY_DELAY_SECONDS = 1.0
BASELINE_MAX_COMPLETION_TOKENS = 64
BASELINE_REASONING_EFFORT = "low"

_SAFE_PROVIDER_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


class BaselineProbeError(RuntimeError):
    """A deterministic baseline probe failed at a known boundary."""

    def __init__(
        self,
        reason: str,
        *,
        stage: str = "provider",
        http_status: int | None = None,
        exception_type: str | None = None,
        provider_error_code: str | None = None,
        provider_error_type: str | None = None,
        attempt_history: list[dict[str, Any]] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(reason)
        self.stage = stage
        self.http_status = http_status
        self.exception_type = exception_type
        self.provider_error_code = provider_error_code
        self.provider_error_type = provider_error_type
        self.attempt_history = list(attempt_history or [])
        self.retryable = retryable


def _safe_code(value: object) -> str | None:
    """Retain only bounded scalar provider codes, never arbitrary messages."""

    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if _SAFE_PROVIDER_CODE.fullmatch(value) else None


def _response_error_metadata(response: httpx.Response) -> tuple[str | None, str | None]:
    """Extract safe error code/type fields without retaining a response body."""

    try:
        body = response.json()
    except ValueError:
        return None, None
    if not isinstance(body, dict):
        return None, None
    error = body.get("error")
    if not isinstance(error, dict):
        return _safe_code(body.get("code")), _safe_code(body.get("type"))
    return _safe_code(error.get("code")), _safe_code(error.get("type"))


def _failure_is_retryable(error: BaselineProbeError) -> bool:
    """Retry only transient transport/server boundaries, never auth/model errors."""

    if error.exception_type in {"ReadTimeout", "ConnectTimeout", "ConnectError", "NetworkError"}:
        return True
    return error.http_status is not None and 500 <= error.http_status <= 599


def _attempt_record(
    *,
    attempt: int,
    status: str,
    http_status: int | None = None,
    failure_class: str | None = None,
    retryable: bool = False,
    provider_error_code: str | None = None,
    provider_error_type: str | None = None,
) -> dict[str, Any]:
    """Build a scalar-only attempt record for sanitized evidence."""

    record: dict[str, Any] = {
        "attempt": attempt,
        "status": status,
        "http_status": http_status,
        "retryable": retryable,
    }
    if failure_class:
        record["failure"] = failure_class
    if provider_error_code:
        record["provider_error_code"] = provider_error_code
    if provider_error_type:
        record["provider_error_type"] = provider_error_type
    return record


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
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    retry_delay_seconds: float = DEFAULT_RETRY_DELAY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if not gateway_key:
        raise BaselineProbeError("model_gateway_key_missing", stage="gateway_auth")
    if not url.startswith(("http://", "https://")):
        raise BaselineProbeError("provider_endpoint_missing", stage="provider")
    if max_attempts < 1 or max_attempts > 3:
        raise ValueError("max_attempts must be between 1 and 3")
    if retry_delay_seconds < 0 or retry_delay_seconds > 10:
        raise ValueError("retry_delay_seconds must be between 0 and 10")

    created_client = client is None
    client = client or httpx.Client(
        timeout=httpx.Timeout(120.0, connect=10.0),
        follow_redirects=False,
    )
    route = baseline_route()
    attempts: list[dict[str, Any]] = []
    try:
        last_error: BaselineProbeError | None = None
        for attempt in range(1, max_attempts + 1):
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
                        # Groq's GPT-OSS route documents this field as the current
                        # spelling; a small but non-trivial budget avoids consuming
                        # the entire completion on hidden reasoning tokens.
                        "max_completion_tokens": BASELINE_MAX_COMPLETION_TOKENS,
                        "reasoning_effort": BASELINE_REASONING_EFFORT,
                        "include_reasoning": False,
                        "stream": False,
                    },
                )
            except httpx.TimeoutException:
                error = BaselineProbeError(
                    "provider_route_timeout",
                    stage="provider",
                    exception_type="ReadTimeout",
                )
            except httpx.TransportError:
                error = BaselineProbeError(
                    "provider_route_transport_error",
                    stage="provider",
                    exception_type="ConnectError",
                )
            else:
                provider_error_code, provider_error_type = _response_error_metadata(response)
                if response.status_code == 200:
                    try:
                        body = response.json()
                    except ValueError:
                        error = BaselineProbeError(
                            "provider_baseline_invalid_json", stage="provider"
                        )
                    else:
                        choices = body.get("choices") if isinstance(body, dict) else None
                        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                            error = BaselineProbeError("provider_baseline_missing_choice", stage="provider")
                        else:
                            message = choices[0].get("message")
                            if not isinstance(message, dict) or not str(message.get("content") or "").strip():
                                error = BaselineProbeError("provider_baseline_empty_response", stage="provider")
                            else:
                                attempts.append(_attempt_record(attempt=attempt, status="PASS", http_status=200))
                                return {
                                    "schema_version": SCHEMA,
                                    "status": "PASS",
                                    "provider": CERTIFICATION_PROVIDER,
                                    "provider_model": CERTIFICATION_BASELINE_MODEL,
                                    "requested_provider_model": route,
                                    "endpoint": f"{url.rstrip('/')}/v1",
                                    "http_status": response.status_code,
                                    "response_success": True,
                                    "attempt_count": attempt,
                                    "attempts": attempts,
                                    "usage": _usage(body.get("usage")) if isinstance(body, dict) else {},
                                    "selected_connection_id_present": bool(
                                        response.headers.get("x-omniroute-selected-connection-id")
                                    ),
                                    "gateway_key_retained": False,
                                    "raw_response_retained": False,
                                }
                elif response.status_code in {401, 403}:
                    error = BaselineProbeError(
                        "model_gateway_auth_failed",
                        stage="gateway_auth",
                        http_status=response.status_code,
                        provider_error_code=provider_error_code,
                        provider_error_type=provider_error_type,
                    )
                elif response.status_code == 404:
                    error = BaselineProbeError(
                        "baseline_model_unavailable",
                        stage="provider",
                        http_status=404,
                        provider_error_code=provider_error_code,
                        provider_error_type=provider_error_type,
                    )
                elif response.status_code == 429:
                    error = BaselineProbeError(
                        "provider_rate_limit",
                        stage="provider",
                        http_status=429,
                        provider_error_code=provider_error_code,
                        provider_error_type=provider_error_type,
                    )
                else:
                    error = BaselineProbeError(
                        "provider_baseline_request_failed",
                        stage="provider",
                        http_status=response.status_code,
                        provider_error_code=provider_error_code,
                        provider_error_type=provider_error_type,
                    )

            failure = classify_failure(
                stage=error.stage,
                http_status=error.http_status,
                error_code=error.provider_error_code or str(error),
                exception_type=error.exception_type,
            )
            retryable = _failure_is_retryable(error)
            attempts.append(
                _attempt_record(
                    attempt=attempt,
                    status="BLOCKED",
                    http_status=error.http_status,
                    failure_class=failure.failure_class,
                    retryable=retryable,
                    provider_error_code=error.provider_error_code,
                    provider_error_type=error.provider_error_type,
                )
            )
            error.attempt_history = attempts
            error.retryable = retryable
            last_error = error
            if not retryable or attempt >= max_attempts:
                raise error
            sleep(retry_delay_seconds)

        assert last_error is not None  # max_attempts is validated above
        raise last_error
    finally:
        if created_client:
            client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.getenv("OMNIROUTE_BASE_URL", ""))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    parser.add_argument("--retry-delay-seconds", type=float, default=DEFAULT_RETRY_DELAY_SECONDS)
    args = parser.parse_args(argv)
    try:
        report = probe(
            url=args.url,
            gateway_key=os.getenv("OPENHANDS_MODEL_GATEWAY_API_KEY", "").strip(),
            max_attempts=args.max_attempts,
            retry_delay_seconds=args.retry_delay_seconds,
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
            "attempt_count": len(getattr(exc, "attempt_history", [])) or 1,
            "attempts": getattr(exc, "attempt_history", []),
            "provider": CERTIFICATION_PROVIDER,
            "provider_model": CERTIFICATION_BASELINE_MODEL,
            "requested_provider_model": baseline_route(),
            "gateway_key_retained": False,
            "raw_response_retained": False,
        }
        if getattr(exc, "provider_error_code", None):
            report["provider_error_code"] = exc.provider_error_code
        if getattr(exc, "provider_error_type", None):
            report["provider_error_type"] = exc.provider_error_type
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
