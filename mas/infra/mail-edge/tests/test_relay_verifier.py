"""Executable policy tests for the live Stalwart relay verifier."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "verify-stalwart-relay.sh"


def _route(name: str, kind: str = "Relay") -> dict[str, Any]:
    return {
        "id": f"route-{name}",
        "name": name,
        "@type": kind,
        "address": "smtp.resend.com",
        "port": 465,
        "protocol": "smtp",
        "implicitTls": True,
        "allowInvalidCerts": False,
        "authUsername": "resend",
        "authSecret": {
            "@type": "EnvironmentVariable",
            "variableName": "RESEND_API_KEY",
        },
    }


def _run_verifier(routes: list[dict[str, Any]]) -> subprocess.CompletedProcess[str]:
    paths: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
            paths.append(self.path)
            if self.path != "/jmap/session":
                self.send_error(404)
                return
            payload = json.dumps(
                {"apiUrl": f"http://localhost:{server.server_port}/jmap/"}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
            paths.append(self.path)
            if self.path != "/jmap/":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length))
            method = request["methodCalls"][0][0]
            if method == "x:MtaRoute/get":
                body = {
                    "methodResponses": [["x:MtaRoute/get", {"list": routes}, "routes"]],
                    "sessionState": "session-state",
                }
            else:
                body = {
                    "methodResponses": [[
                        "x:MtaOutboundStrategy/get",
                        {
                            "list": [{
                                "id": "singleton",
                                "route": {
                                    "match": {
                                        "0": {
                                            "if": "is_local_domain(rcpt_domain)",
                                            "then": "'local'",
                                        }
                                    },
                                    "else": "'resend-relay'",
                                },
                            }]
                        },
                        "strategy",
                    ]],
                    "sessionState": "session-state",
                }
            payload = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(
            [str(SCRIPT)],
            env={
                **os.environ,
                "STALWART_ADMIN_URL": f"http://127.0.0.1:{server.server_port}/api",
                "STALWART_API_KEY": "test-only-placeholder",
                "STALWART_ADMIN_INSECURE_TLS": "false",
            },
            text=True,
            capture_output=True,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
    assert paths and paths[0] == "/jmap/session"
    assert all(path == "/jmap/" for path in paths[1:])
    return result

pytestmark = pytest.mark.skipif(
    shutil.which("curl") is None or shutil.which("jq") is None,
    reason="relay verifier requires curl and jq",
)


def test_relay_verifier_accepts_exactly_one_safe_resend_route() -> None:
    result = _run_verifier([_route("local", "Local"), _route("resend-relay")])
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "extra_route",
    [_route("mx", "Mx"), _route("legacy-relay")],
)
def test_relay_verifier_rejects_every_other_remote_route(
    extra_route: dict[str, Any],
) -> None:
    result = _run_verifier([_route("resend-relay"), extra_route])
    assert result.returncode != 0
    assert "unapproved remote-delivery route" in result.stderr


def test_relay_verifier_rejects_non_environment_backed_secret() -> None:
    route = _route("resend-relay")
    route["authSecret"] = {"@type": "String", "value": "not-a-secret"}
    result = _run_verifier([route])
    assert result.returncode != 0
    assert "environment-backed Resend relay" in result.stderr
