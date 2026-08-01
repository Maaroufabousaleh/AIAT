from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error, request

import pytest

GATEWAY = Path(__file__).resolve().parents[1]
HELPER = GATEWAY / "scripts" / "stalwart_jmap_endpoint.py"
SPEC = importlib.util.spec_from_file_location("stalwart_jmap_endpoint", HELPER)
assert SPEC and SPEC.loader
jmap_endpoint = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(jmap_endpoint)


def _response(handler: BaseHTTPRequestHandler, body: dict, status: int = 200) -> None:
    if "methodResponses" in body and "sessionState" not in body:
        body = {**body, "sessionState": "session-state"}
    payload = json.dumps(body).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def _start_server(
    *,
    session: dict | None = None,
    redirect: str | None = None,
    fail_method: str | None = None,
):
    paths: list[str] = []
    routes: list[dict[str, object]] = []
    strategy: dict[str, object] = {"id": "singleton", "route": {"else": "'local'"}}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
            paths.append(self.path)
            if self.path != "/jmap/session":
                self.send_error(404)
                return
            if redirect is not None:
                self.send_response(302)
                self.send_header("Location", redirect)
                self.end_headers()
                return
            _response(self, session or {"apiUrl": f"http://localhost:{server.server_port}/jmap/"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
            paths.append(self.path)
            if self.path == "/api":
                self.send_error(404)
                return
            if self.path != "/jmap/":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            method = payload["methodCalls"][0][0]
            if method == fail_method:
                self.send_error(500)
                return
            if method == "x:MtaRoute/get":
                body = {"methodResponses": [[method, {"list": routes}, "routes"]]}
            elif method == "x:MtaRoute/set":
                destroyed = payload["methodCalls"][0][1].get("destroy", [])
                routes[:] = [route for route in routes if route.get("id") not in destroyed]
                created = {}
                for key, route in payload["methodCalls"][0][1].get("create", {}).items():
                    created[key] = {"id": f"created-{key}"}
                    routes.append({**route, "id": f"created-{key}"})
                arguments = {}
                if created:
                    arguments["created"] = created
                if destroyed:
                    arguments["destroyed"] = destroyed
                body = {"methodResponses": [[method, arguments, payload["methodCalls"][0][2]]]}
            elif method == "x:MtaOutboundStrategy/set":
                strategy["route"] = payload["methodCalls"][0][1]["update"]["singleton"].get("route", payload["methodCalls"][0][1]["update"]["singleton"])
                body = {"methodResponses": [[method, {"updated": {"singleton": {}}}, payload["methodCalls"][0][2]]]}
            else:
                body = {
                    "methodResponses": [
                        [method, {"list": [strategy]}, "strategy"]
                    ]
                }
            _response(self, body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, paths


def _stop_server(server, thread) -> None:
    server.shutdown()
    thread.join()
    server.server_close()


def test_discovery_normalizes_localhost_and_jmap_calls_do_not_need_api() -> None:
    server, thread, paths = _start_server()
    try:
        base = f"http://127.0.0.1:{server.server_port}/api"
        endpoint = jmap_endpoint.discover_jmap_api_url(base, "Bearer test-only")
        assert endpoint == f"http://127.0.0.1:{server.server_port}/jmap/"
        with pytest.raises(error.HTTPError):
            request.urlopen(request.Request(f"{base}", method="POST"), timeout=2)
        response = request.urlopen(
            request.Request(
                endpoint,
                data=b'{"methodCalls":[["x:MtaRoute/get",{},"call"]]}',
                method="POST",
            ),
            timeout=2,
        )
        assert response.status == 200
        assert paths == ["/jmap/session", "/api", "/jmap/"]
    finally:
        _stop_server(server, thread)


@pytest.mark.parametrize(
    "advertised",
    [
        "https://localhost:18080/jmap/",
        "http://mail.example.invalid:18080/jmap/",
        "http://localhost:18081/jmap/",
        "http://user:password@localhost:18080/jmap/",
        "not a URL",
        "http://localhost:18080/api",
        "http://localhost:18080/jmap/?unexpected=1",
    ],
)
def test_resolution_rejects_non_local_or_unexpected_session_urls(advertised: str) -> None:
    with pytest.raises(jmap_endpoint.JmapEndpointError):
        jmap_endpoint.resolve_jmap_api_url(
            "http://127.0.0.1:18080",
            advertised,
        )


def test_resolution_accepts_a_session_url_as_the_base() -> None:
    assert jmap_endpoint.session_url("http://localhost:18080/jmap/session") == (
        "http://127.0.0.1:18080/jmap/session"
    )
    assert jmap_endpoint.resolve_jmap_api_url(
        "http://localhost:18080/jmap/session",
        "http://localhost:18080/jmap",
    ) == "http://127.0.0.1:18080/jmap/"


def test_external_redirect_and_malformed_session_are_rejected_without_secret_echo() -> None:
    secret = "Bearer test-only-redacted"
    server, thread, _paths = _start_server(redirect="http://external.example.invalid/jmap/session")
    try:
        with pytest.raises(jmap_endpoint.JmapEndpointError) as exc_info:
            jmap_endpoint.discover_jmap_api_url(
                f"http://127.0.0.1:{server.server_port}", secret
            )
        assert secret not in str(exc_info.value)
    finally:
        _stop_server(server, thread)

    server, thread, _paths = _start_server(session={"apiUrl": {"bad": "shape"}})
    try:
        with pytest.raises(jmap_endpoint.JmapEndpointError) as exc_info:
            jmap_endpoint.discover_jmap_api_url(
                f"http://127.0.0.1:{server.server_port}", secret
            )
        assert secret not in str(exc_info.value)
    finally:
        _stop_server(server, thread)


def _write_profile(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "DEPLOYMENT_TOPOLOGY=smtp_gateway_vps_home_stalwart_resend",
                "AGENT_MAIL_DOMAIN=agents.aiat.ca",
                "DIRECT_MX_OUTBOUND_ENABLED=false",
                "DEFAULT_OUTBOUND_ENABLED=false",
                "OUTBOUND_RELAY_CERTIFIED=false",
                "OUTBOUND_RELAY_HOST=smtp.resend.com",
                "OUTBOUND_RELAY_PORT=465",
                "OUTBOUND_RELAY_TLS_MODE=implicit",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_docker_stub(directory: Path, relay_file: Path) -> None:
    (directory / "docker").write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "case \"${1:-}\" in\n"
        "  inspect) printf '%s\\n' true ;;\n"
        "  exec) awk -F= '$1 == \"RESEND_API_KEY\" {printf \"%s\", $2; exit}' \"$TEST_RELAY_FILE\" | sha256sum | awk '{print $1}' ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    (directory / "docker").chmod(0o755)


def _write_route_metadata(secret_file: Path) -> None:
    secret_file.with_name(secret_file.name.removesuffix(".env") + ".meta").write_text(
        json.dumps(
            {
                "version": 1,
                "purpose": "stalwart-route-lifecycle",
                "credentialId": "route-key-test",
                "owner": "admin@agents.aiat.local",
                "description": "AIAT Stalwart route lifecycle temporary",
                "expiresAt": "2026-08-01T12:00:00Z",
                "permissions": [
                    "authenticate",
                    "sysMtaRouteGet",
                    "sysMtaRouteCreate",
                    "sysMtaRouteUpdate",
                    "sysMtaRouteDestroy",
                    "sysMtaOutboundStrategyGet",
                    "sysMtaOutboundStrategyUpdate",
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    secret_file.with_name(secret_file.name.removesuffix(".env") + ".meta").chmod(0o600)


def test_route_backup_uses_discovered_jmap_and_writes_no_api_target(tmp_path: Path) -> None:
    server, thread, paths = _start_server()
    relay_file = tmp_path / "relay.env"
    relay_file.write_text("RESEND_API_KEY=" + ("R" * 32) + "\n", encoding="utf-8")
    relay_file.chmod(0o600)
    management_key = "API_" + ("M" * 24)
    secret_file = tmp_path / "cert.env"
    secret_file.write_text(f"STALWART_API_KEY={management_key}\n", encoding="utf-8")
    secret_file.chmod(0o600)
    _write_route_metadata(secret_file)
    profile = tmp_path / "profile.env"
    _write_profile(profile)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_docker_stub(bin_dir, relay_file)
    backup = tmp_path / "route-backup.json"
    try:
        result = subprocess.run(
            [
                "sh",
                str(GATEWAY / "scripts" / "configure-stalwart-resend-route.sh"),
                "backup",
                str(profile),
                "--secret-file",
                str(secret_file),
                "--relay-secret-file",
                str(relay_file),
                "--stalwart-container",
                "test-stalwart",
                "--backup",
                str(backup),
                "--admin-url",
                f"http://127.0.0.1:{server.server_port}/api",
            ],
            cwd=GATEWAY,
            env={
                **os.environ,
                "PATH": f"{bin_dir}:/usr/bin:/bin",
                "TEST_RELAY_FILE": str(relay_file),
            },
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr
        assert backup.exists() and backup.stat().st_mode & 0o777 == 0o600
        assert json.loads(backup.read_text(encoding="utf-8"))["version"] == 1
        assert paths[0] == "/jmap/session"
        assert all(path == "/jmap/" for path in paths[1:])
        assert "/api" not in result.stdout + result.stderr
        assert management_key not in result.stdout + result.stderr
    finally:
        _stop_server(server, thread)


def test_route_backup_failure_leaves_no_partial_backup(tmp_path: Path) -> None:
    server, thread, _paths = _start_server(fail_method="x:MtaOutboundStrategy/get")
    relay_file = tmp_path / "relay.env"
    relay_file.write_text("RESEND_API_KEY=" + ("R" * 32) + "\n", encoding="utf-8")
    relay_file.chmod(0o600)
    management_key = "API_" + ("M" * 24)
    secret_file = tmp_path / "cert.env"
    secret_file.write_text(f"STALWART_API_KEY={management_key}\n", encoding="utf-8")
    secret_file.chmod(0o600)
    _write_route_metadata(secret_file)
    profile = tmp_path / "profile.env"
    _write_profile(profile)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_docker_stub(bin_dir, relay_file)
    backup = tmp_path / "route-backup.json"
    try:
        result = subprocess.run(
            [
                "sh",
                str(GATEWAY / "scripts" / "configure-stalwart-resend-route.sh"),
                "backup",
                str(profile),
                "--secret-file",
                str(secret_file),
                "--relay-secret-file",
                str(relay_file),
                "--stalwart-container",
                "test-stalwart",
                "--backup",
                str(backup),
                "--admin-url",
                f"http://127.0.0.1:{server.server_port}",
            ],
            cwd=GATEWAY,
            env={
                **os.environ,
                "PATH": f"{bin_dir}:/usr/bin:/bin",
                "TEST_RELAY_FILE": str(relay_file),
            },
            text=True,
            capture_output=True,
        )
        assert result.returncode != 0
        assert "back up outbound strategy" in result.stderr
        assert not backup.exists()
        assert not list(tmp_path.glob("route-backup.json.tmp.*"))
        assert management_key not in result.stdout + result.stderr
    finally:
        _stop_server(server, thread)


def test_route_apply_requires_a_valid_backup_before_any_jmap_mutation(tmp_path: Path) -> None:
    server, thread, paths = _start_server()
    relay_file = tmp_path / "relay.env"
    relay_file.write_text("RESEND_API_KEY=" + ("R" * 32) + "\n", encoding="utf-8")
    relay_file.chmod(0o600)
    management_key = "API_" + ("M" * 24)
    secret_file = tmp_path / "cert.env"
    secret_file.write_text(f"STALWART_API_KEY={management_key}\n", encoding="utf-8")
    secret_file.chmod(0o600)
    _write_route_metadata(secret_file)
    profile = tmp_path / "profile.env"
    _write_profile(profile)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_docker_stub(bin_dir, relay_file)
    backup = tmp_path / "missing-backup.json"
    try:
        result = subprocess.run(
            [
                "sh",
                str(GATEWAY / "scripts" / "configure-stalwart-resend-route.sh"),
                "apply",
                str(profile),
                "--secret-file",
                str(secret_file),
                "--relay-secret-file",
                str(relay_file),
                "--stalwart-container",
                "test-stalwart",
                "--backup",
                str(backup),
                "--admin-url",
                f"http://127.0.0.1:{server.server_port}",
            ],
            cwd=GATEWAY,
            env={
                **os.environ,
                "PATH": f"{bin_dir}:/usr/bin:/bin",
                "TEST_RELAY_FILE": str(relay_file),
            },
            text=True,
            capture_output=True,
        )
        assert result.returncode != 0
        assert "backup validation failed" in result.stderr
        assert paths == ["/jmap/session"]
        assert management_key not in result.stdout + result.stderr
    finally:
        _stop_server(server, thread)


def test_route_rollback_uses_discovered_jmap_endpoint(tmp_path: Path) -> None:
    server, thread, paths = _start_server()
    relay_file = tmp_path / "relay.env"
    relay_file.write_text("RESEND_API_KEY=" + ("R" * 32) + "\n", encoding="utf-8")
    relay_file.chmod(0o600)
    management_key = "API_" + ("M" * 24)
    secret_file = tmp_path / "cert.env"
    secret_file.write_text(f"STALWART_API_KEY={management_key}\n", encoding="utf-8")
    secret_file.chmod(0o600)
    _write_route_metadata(secret_file)
    profile = tmp_path / "profile.env"
    _write_profile(profile)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_docker_stub(bin_dir, relay_file)
    backup = tmp_path / "route-backup.json"
    backup.write_text(
        json.dumps(
            {
                "version": 1,
                "scope": "stalwart-remote-route-and-strategy",
                "routes": {
                    "methodResponses": [
                        [
                            "x:MtaRoute/get",
                            {
                                "list": [
                                    {
                                        "id": "remote-id",
                                        "name": "legacy-relay",
                                        "@type": "Relay",
                                        "address": "smtp.example.invalid",
                                        "port": 465,
                                    }
                                ],
                            },
                            "routes",
                        ]
                    ],
                    "sessionState": "session-state"
                },
                "strategy": {
                    "methodResponses": [
                        [
                            "x:MtaOutboundStrategy/get",
                            {"list": [{"id": "singleton", "route": {"else": "'local'"}}]},
                            "strategy",
                        ]
                    ],
                    "sessionState": "session-state"
                },
            }
        ),
        encoding="utf-8",
    )
    backup.chmod(0o600)
    try:
        result = subprocess.run(
            [
                "sh",
                str(GATEWAY / "scripts" / "configure-stalwart-resend-route.sh"),
                "rollback",
                str(profile),
                "--secret-file",
                str(secret_file),
                "--relay-secret-file",
                str(relay_file),
                "--stalwart-container",
                "test-stalwart",
                "--backup",
                str(backup),
                "--admin-url",
                f"http://localhost:{server.server_port}/jmap/session",
            ],
            cwd=GATEWAY,
            env={
                **os.environ,
                "PATH": f"{bin_dir}:/usr/bin:/bin",
                "TEST_RELAY_FILE": str(relay_file),
            },
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr
        assert paths[0] == "/jmap/session"
        assert all(path == "/jmap/" for path in paths[1:])
        assert management_key not in result.stdout + result.stderr
    finally:
        _stop_server(server, thread)
