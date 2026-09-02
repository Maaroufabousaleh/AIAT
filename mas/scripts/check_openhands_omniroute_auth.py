"""Verify the disposable OmniRoute OpenAI-compatible API auth boundary."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable

SCHEMA = "aiat.openhands-certification-omniroute-auth.v1"


def _status(client: httpx.Client, url: str, headers: dict[str, str] | None = None) -> int | None:
    try:
        response = client.get(url, headers=headers or {})
    except httpx.HTTPError:
        return None
    return response.status_code


def _status_with_retry(
    client: httpx.Client,
    url: str,
    headers: dict[str, str] | None = None,
    *,
    attempts: int,
    interval_seconds: float,
    sleep: Callable[[float], None],
) -> tuple[int | None, int]:
    """Wait for the API bridge to bind without retrying HTTP decisions.

    OmniRoute starts its dashboard listener and OpenAI-compatible API bridge
    independently. The dashboard health endpoint can therefore return 200
    while the API bridge is still accepting connections. Transport failures
    during this bounded window are startup observations, not auth decisions.
    Once an HTTP status is observed, return it immediately so 401/403/200
    remain the authoritative authentication contract.
    """

    bounded_attempts = max(1, int(attempts))
    for attempt in range(1, bounded_attempts + 1):
        status = _status(client, url, headers)
        if status is not None or attempt == bounded_attempts:
            return status, attempt
        sleep(max(0.0, float(interval_seconds)))
    return None, bounded_attempts


def evaluate_statuses(
    *,
    unauthenticated_status: int | None,
    wrong_key_status: int | None,
    correct_key_status: int | None,
    endpoint: str,
) -> dict[str, Any]:
    unauthenticated_denied = unauthenticated_status in {401, 403}
    wrong_key_denied = wrong_key_status in {401, 403}
    correct_key_accepted = correct_key_status == 200
    return {
        "schema_version": SCHEMA,
        "status": "PASS" if unauthenticated_denied and wrong_key_denied and correct_key_accepted else "BLOCKED",
        "unauthenticated_provider_route_denied": unauthenticated_denied,
        "wrong_gateway_key_denied": wrong_key_denied,
        "correct_gateway_key_accepted": correct_key_accepted,
        "unauthenticated_http_status": unauthenticated_status,
        "wrong_key_http_status": wrong_key_status,
        "correct_key_http_status": correct_key_status,
        "endpoint": endpoint,
        "raw_response_retained": False,
        "credentials_retained": False,
    }


def _failure_class(report: dict[str, Any]) -> str:
    statuses = (
        report.get("unauthenticated_http_status"),
        report.get("wrong_key_http_status"),
        report.get("correct_key_http_status"),
    )
    if any(status is None for status in statuses):
        return "MODEL_GATEWAY_TRANSPORT_FAILURE"
    if any(isinstance(status, int) and status >= 500 for status in statuses):
        return "MODEL_GATEWAY_RESPONSE_INVALID"
    return "MODEL_GATEWAY_AUTH_FAILURE"


def check(
    *,
    url: str,
    gateway_key: str,
    client: httpx.Client | None = None,
    attempts: int = 1,
    interval_seconds: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if not gateway_key:
        return evaluate_statuses(
            unauthenticated_status=None,
            wrong_key_status=None,
            correct_key_status=None,
            endpoint=url,
        ) | {"failure_class": "MODEL_GATEWAY_AUTH_FAILURE"}
    created_client = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0), follow_redirects=False)
    try:
        wrong_key = "aiat-openhands-invalid-gateway-key"
        unauthenticated_status, unauthenticated_attempts = _status_with_retry(
            client,
            url,
            attempts=attempts,
            interval_seconds=interval_seconds,
            sleep=sleep,
        )
        wrong_key_status, wrong_key_attempts = _status_with_retry(
            client,
            url,
            {"Authorization": f"Bearer {wrong_key}"},
            attempts=attempts,
            interval_seconds=interval_seconds,
            sleep=sleep,
        )
        correct_key_status, correct_key_attempts = _status_with_retry(
            client,
            url,
            {"Authorization": f"Bearer {gateway_key}"},
            attempts=attempts,
            interval_seconds=interval_seconds,
            sleep=sleep,
        )
        report = evaluate_statuses(
            unauthenticated_status=unauthenticated_status,
            wrong_key_status=wrong_key_status,
            correct_key_status=correct_key_status,
            endpoint=url,
        )
        report["probe_attempts"] = {
            "unauthenticated": unauthenticated_attempts,
            "wrong_key": wrong_key_attempts,
            "correct_key": correct_key_attempts,
        }
        per_request_window = max(0, attempts - 1) * max(0.0, interval_seconds)
        report["per_request_transport_retry_window_seconds"] = round(per_request_window, 3)
        report["transport_retry_window_seconds"] = round(3 * per_request_window, 3)
        if report["status"] != "PASS":
            report["failure_class"] = _failure_class(report)
        return report
    finally:
        if created_client:
            client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--attempts",
        type=int,
        default=1,
        help="bounded transport attempts per request; HTTP statuses are not retried",
    )
    parser.add_argument("--interval-seconds", type=float, default=0.0)
    args = parser.parse_args(argv)
    report = check(
        url=args.url,
        gateway_key=os.getenv("OPENHANDS_MODEL_GATEWAY_API_KEY", "").strip(),
        attempts=args.attempts,
        interval_seconds=args.interval_seconds,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "failure_class": report.get("failure_class")}, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
