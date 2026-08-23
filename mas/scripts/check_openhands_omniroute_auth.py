"""Verify the disposable OmniRoute OpenAI-compatible API auth boundary."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import httpx

SCHEMA = "aiat.openhands-certification-omniroute-auth.v1"


def _status(client: httpx.Client, url: str, headers: dict[str, str] | None = None) -> int | None:
    try:
        response = client.get(url, headers=headers or {})
    except httpx.HTTPError:
        return None
    return response.status_code


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


def check(*, url: str, gateway_key: str, client: httpx.Client | None = None) -> dict[str, Any]:
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
        report = evaluate_statuses(
            unauthenticated_status=_status(client, url),
            wrong_key_status=_status(client, url, {"Authorization": f"Bearer {wrong_key}"}),
            correct_key_status=_status(client, url, {"Authorization": f"Bearer {gateway_key}"}),
            endpoint=url,
        )
        if report["status"] != "PASS":
            report["failure_class"] = "MODEL_GATEWAY_AUTH_FAILURE"
        return report
    finally:
        if created_client:
            client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = check(url=args.url, gateway_key=os.getenv("OPENHANDS_MODEL_GATEWAY_API_KEY", "").strip())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "failure_class": report.get("failure_class")}, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
