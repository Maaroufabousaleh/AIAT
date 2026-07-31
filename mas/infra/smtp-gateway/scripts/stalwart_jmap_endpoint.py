#!/usr/bin/env python3
"""Discover and validate the local Stalwart JMAP endpoint.

This module is deliberately small so shell lifecycle scripts and the
certification-key provisioner share one endpoint-validation contract.  The
only value printed by the CLI on success is the sanitized endpoint URL.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from urllib import error, parse, request


class JmapEndpointError(ValueError):
    """Raised when Stalwart does not advertise a safe local JMAP endpoint."""


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        raise JmapEndpointError("JMAP session redirects are not permitted")


LOCAL_HOSTS = {"127.0.0.1", "localhost"}
JMAP_PATH = "/jmap/"
SESSION_PATH = "/jmap/session"
ALLOWED_BASE_PATHS = {"", "/", "/api", "/api/", "/jmap", "/jmap/", SESSION_PATH}


def _parts(value: str, *, label: str) -> parse.SplitResult:
    if not isinstance(value, str) or not value:
        raise JmapEndpointError(f"{label} URL is missing")
    try:
        result = parse.urlsplit(value)
        port = result.port
    except (TypeError, ValueError):
        raise JmapEndpointError(f"{label} URL is malformed") from None
    if (
        result.scheme != "http"
        or result.hostname is None
        or result.hostname.lower() not in LOCAL_HOSTS
        or result.username is not None
        or result.password is not None
        or result.query
        or result.fragment
        or port is None
    ):
        raise JmapEndpointError(f"{label} URL must be HTTP loopback without credentials")
    if result.path not in ALLOWED_BASE_PATHS and label == "Stalwart base":
        raise JmapEndpointError("Stalwart base URL has an unexpected path")
    return result


def _authority(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def session_url(base_url: str) -> str:
    """Return the local session URL for a base, JMAP, or session URL."""
    base = _parts(base_url, label="Stalwart base")
    return f"{_authority(base.port)}{SESSION_PATH}"


def resolve_jmap_api_url(base_url: str, advertised_url: str) -> str:
    """Validate the session apiUrl and normalize it to the local /jmap/ URL."""
    base = _parts(base_url, label="Stalwart base")
    advertised = _parts(advertised_url, label="JMAP session apiUrl")
    advertised_path = advertised.path.rstrip("/") or "/"
    if advertised_path != "/jmap":
        raise JmapEndpointError("JMAP session apiUrl must identify /jmap/")
    if advertised.port != base.port:
        raise JmapEndpointError("JMAP session apiUrl uses an unexpected port")
    return f"{_authority(base.port)}{JMAP_PATH}"


def _read_session(base_url: str, authorization: str, *, timeout: float) -> dict:
    if not isinstance(authorization, str) or not authorization:
        raise JmapEndpointError("JMAP session authorization is missing")
    url = session_url(base_url)
    req = request.Request(
        url,
        headers={"Accept": "application/json", "Authorization": authorization},
        method="GET",
    )
    opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(req, timeout=timeout) as response:
            payload = response.read(1_048_577)
    except JmapEndpointError:
        raise
    except error.HTTPError:
        raise JmapEndpointError("JMAP session request failed") from None
    except (error.URLError, TimeoutError, OSError):
        raise JmapEndpointError("JMAP session request could not reach local Stalwart") from None
    if len(payload) > 1_048_576:
        raise JmapEndpointError("JMAP session response is too large")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise JmapEndpointError("JMAP session response is malformed") from None
    if not isinstance(value, dict):
        raise JmapEndpointError("JMAP session response is malformed")
    return value


def discover_jmap_api_url(
    base_url: str,
    authorization: str,
    *,
    timeout: float = 10.0,
) -> str:
    """GET /jmap/session and return a validated, normalized JMAP endpoint."""
    session = _read_session(base_url, authorization, timeout=timeout)
    advertised = session.get("apiUrl")
    if not isinstance(advertised, str) or not advertised:
        raise JmapEndpointError("JMAP session did not contain apiUrl")
    return resolve_jmap_api_url(base_url, advertised)


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.validate_only:
            session_url(args.base_url)
            print("VALID")
        else:
            authorization = os.environ.get("STALWART_JMAP_AUTHORIZATION", "")
            print(discover_jmap_api_url(args.base_url, authorization))
    except JmapEndpointError as exc:
        print(f"Stalwart JMAP endpoint discovery refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
