"""Probe the pinned OmniRoute application readiness endpoint safely.

The v3.8.38 image exposes two distinct HTTP contracts: the dashboard and
management routes on the dashboard port, and the OpenAI-compatible bridge on
the API port.  This helper probes only the documented public monitoring
health endpoint.  It retains scalar observations and never stores response
payloads.
"""

from __future__ import annotations

import argparse
import http.client
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

if TYPE_CHECKING:
    from collections.abc import Callable

SCHEMA = "aiat.openhands-certification-omniroute-readiness.v1"
EXPECTED_HEALTH_PATH = "/api/monitoring/health"


def _observation_class(status: int | None, error_type: str | None) -> str:
    if status is not None and 200 <= status < 300:
        return "PASS"
    if status in {401, 403}:
        return "OMNIROUTE_HEALTH_AUTH_CONTRACT_FAILURE"
    if status is not None and 500 <= status <= 599:
        return "OMNIROUTE_APPLICATION_HEALTH_FAILURE"
    if error_type in {"HTTPError", "RemoteDisconnected", "ConnectionRefusedError", "TimeoutError"}:
        return "STARTING"
    if error_type:
        return "STARTING"
    return "OMNIROUTE_APPLICATION_HEALTH_FAILURE"


def _request_status(url: str, timeout: float, opener: Callable[..., Any] = urlopen) -> tuple[int | None, str | None]:
    request = Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with opener(request, timeout=timeout) as response:
            # Do not read or retain the response body.  The HTTP status is the
            # only readiness evidence required here.
            return int(response.status), None
    except HTTPError as exc:
        return int(exc.code), "HTTPError"
    except (http.client.RemoteDisconnected, ConnectionRefusedError) as exc:
        return None, type(exc).__name__
    except (TimeoutError, URLError, OSError) as exc:
        return None, type(exc).__name__


def probe(
    *,
    url: str,
    attempts: int = 60,
    interval_seconds: float = 2.0,
    timeout_seconds: float = 5.0,
    run_exit_code: int = 0,
    container_running: bool = True,
    opener: Callable[..., Any] = urlopen,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Return a scalar readiness report and a process exit status.

    The returned ``status`` is ``PASS`` only for a 2xx response from the
    exact endpoint.  A persistent 401/403 is an authentication-contract
    failure, while persistent 5xx is an application-health failure.  A
    process that exits is a startup failure regardless of a prior response.
    """

    if not url.startswith(("http://", "https://")) or not url.endswith(EXPECTED_HEALTH_PATH):
        return {
            "schema_version": SCHEMA,
            "status": "BLOCKED",
            "health_status": "BLOCKED",
            "application_health_status": "BLOCKED",
            "failure_class": "OMNIROUTE_HEALTH_ENDPOINT_INVALID",
            "container_start_status": "PASS" if run_exit_code == 0 and container_running else "BLOCKED",
            "application_health_endpoint": url,
            "endpoint": url,
            "last_http_status": None,
            "attempt_count": 0,
            "run_exit_code": run_exit_code,
            "container_running": container_running,
            "raw_response_retained": False,
            "provider_count_expected": 1,
        }, 2

    # A failed launch or an already-exited container cannot become ready.  Do
    # not spend the bounded HTTP retry window probing a process that is gone;
    # retain the distinction between process startup and application health.
    if run_exit_code != 0 or not container_running:
        return {
            "schema_version": SCHEMA,
            "status": "BLOCKED",
            "health_status": "BLOCKED",
            "application_health_status": "BLOCKED",
            "failure_class": "OMNIROUTE_STARTUP_FAILURE",
            "container_start_status": "BLOCKED",
            "application_health_endpoint": url,
            "endpoint": url,
            "last_http_status": None,
            "attempt_count": 0,
            "run_exit_code": run_exit_code,
            "container_running": container_running,
            "raw_response_retained": False,
            "provider_count_expected": 1,
        }, 2

    attempts = max(1, int(attempts))
    last_status: int | None = None
    last_error: str | None = None
    last_class = "STARTING"
    consecutive_auth = 0

    for attempt in range(1, attempts + 1):
        last_status, last_error = _request_status(url, timeout_seconds, opener)
        last_class = _observation_class(last_status, last_error)
        if last_status is not None and 200 <= last_status < 300:
            if run_exit_code == 0 and container_running:
                return {
                    "schema_version": SCHEMA,
                    "status": "PASS",
                    "health_status": "PASS",
                    "application_health_status": "PASS",
                    "failure_class": None,
                    "container_start_status": "PASS",
                    "application_health_endpoint": url,
                    "endpoint": url,
                    "last_http_status": last_status,
                    "attempt_count": attempt,
                    "run_exit_code": run_exit_code,
                    "container_running": container_running,
                    "raw_response_retained": False,
                    "provider_count_expected": 1,
                }, 0
            break
        if last_status in {401, 403}:
            consecutive_auth += 1
            # Three identical auth responses are enough to distinguish a
            # contract mismatch from a transient cold-start observation.
            if consecutive_auth >= 3:
                break
        else:
            consecutive_auth = 0
        if attempt < attempts:
            sleep(max(0.0, interval_seconds))

    if run_exit_code != 0 or not container_running:
        failure_class = "OMNIROUTE_STARTUP_FAILURE"
        application_status = "BLOCKED"
    elif last_class == "OMNIROUTE_HEALTH_AUTH_CONTRACT_FAILURE":
        failure_class = last_class
        application_status = "AUTH_CONTRACT_FAILURE"
    elif last_class == "OMNIROUTE_APPLICATION_HEALTH_FAILURE":
        failure_class = last_class
        application_status = "APPLICATION_FAILURE"
    else:
        failure_class = "OMNIROUTE_HEALTH_TIMEOUT"
        application_status = "STARTING"

    return {
        "schema_version": SCHEMA,
        "status": "BLOCKED",
        "health_status": "BLOCKED",
        "application_health_status": application_status,
        "failure_class": failure_class,
        "container_start_status": "PASS" if run_exit_code == 0 and container_running else "BLOCKED",
        "application_health_endpoint": url,
        "endpoint": url,
        "last_http_status": last_status,
        "last_observation": last_class,
        "last_error_type": last_error,
        "attempt_count": attempts if consecutive_auth < 3 else min(attempts, 3),
        "run_exit_code": run_exit_code,
        "container_running": container_running,
        "raw_response_retained": False,
        "provider_count_expected": 1,
    }, 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--attempts", type=int, default=60)
    parser.add_argument("--interval-seconds", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--run-exit-code", type=int, default=0)
    parser.add_argument("--container-running", choices=("true", "false"), default="true")
    args = parser.parse_args(argv)
    report, exit_code = probe(
        url=args.url,
        attempts=args.attempts,
        interval_seconds=args.interval_seconds,
        timeout_seconds=args.timeout_seconds,
        run_exit_code=args.run_exit_code,
        container_running=args.container_running == "true",
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "failure_class": report["failure_class"]}, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
