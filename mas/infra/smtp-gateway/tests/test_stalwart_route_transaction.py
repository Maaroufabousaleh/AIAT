from __future__ import annotations

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

GATEWAY = Path(__file__).resolve().parents[1]


def test_policy_matches_stalwart_v01615_route_and_list_shapes() -> None:
    policy = json.loads((GATEWAY / "../mail-edge/stalwart-relay-policy.json").read_text(encoding="utf-8"))
    assert policy["mta_route"] == {
        "@type": "Relay",
        "name": "resend-relay",
        "address": "smtp.resend.com",
        "port": 465,
        "protocol": "smtp",
        "implicitTls": True,
        "allowInvalidCerts": False,
        "authUsername": "resend",
        "authSecret": {"@type": "EnvironmentVariable", "variableName": "RESEND_API_KEY"},
    }
    assert policy["mta_outbound_strategy_patch"]["route"]["match"] == {
        "0": {"if": "is_local_domain(rcpt_domain)", "then": "'local'"}
    }


def test_route_preflight_certification_and_verifier_use_strict_jmap_validation() -> None:
    scripts = [
        GATEWAY / "scripts" / "configure-stalwart-resend-route.sh",
        GATEWAY / "scripts" / "preflight-resend-certification.sh",
        GATEWAY / "scripts" / "certify-resend.sh",
        GATEWAY / "../mail-edge/scripts/verify-stalwart-relay.sh",
    ]
    for script in scripts:
        source = script.read_text(encoding="utf-8")
        assert "stalwart_jmap_response.py" in source
        assert "--request-file" in source
        assert 'url = "http://127.0.0.1:18080/api"' not in source


def _json_response(handler: BaseHTTPRequestHandler, body: dict[str, Any], status: int = 200) -> None:
    if "methodResponses" in body and "sessionState" not in body:
        body = {**body, "sessionState": "session-state"}
    payload = json.dumps(body).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


class RouteServer:
    def __init__(self, *, fail_destroy: bool = False, fail_strategy_once: bool = False) -> None:
        self.routes: list[dict[str, Any]] = [
            {"id": "local-id", "name": "local", "@type": "Local"},
            {"id": "mx-id", "name": "mx", "@type": "Mx"},
        ]
        self.strategy: dict[str, Any] = {
            "id": "singleton",
            "route": {
                "match": {"0": {"if": "is_local_domain(rcpt_domain)", "then": "'local'"}},
                "else": "'mx'",
            },
        }
        self.paths: list[str] = []
        self.requests: list[dict[str, Any]] = []
        self.fail_destroy = fail_destroy
        self.fail_strategy_once = fail_strategy_once
        self.strategy_failures = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _handler(self):
        state = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
                state.paths.append(self.path)
                if self.path != "/jmap/session":
                    self.send_error(404)
                    return
                _json_response(self, {"apiUrl": f"http://localhost:{state.server.server_port}/jmap/"})

            def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
                state.paths.append(self.path)
                if self.path != "/jmap/":
                    self.send_error(404)
                    return
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length))
                state.requests.append(payload)
                method, arguments, tag = payload["methodCalls"][0]
                if method == "x:MtaRoute/get":
                    _json_response(
                        self,
                        {"methodResponses": [[method, {"list": state.routes}, tag]]},
                    )
                    return
                if method == "x:MtaOutboundStrategy/get":
                    _json_response(
                        self,
                        {"methodResponses": [[method, {"list": [state.strategy]}, tag]]},
                    )
                    return
                if method == "x:MtaRoute/set":
                    if state.fail_destroy and arguments.get("destroy"):
                        _json_response(
                            self,
                            {
                                "methodResponses": [
                                    [
                                        "x:MtaRoute/set",
                                        {"notDestroyed": {str(arguments["destroy"][0]): {"type": "rejected"}}},
                                        tag,
                                    ]
                                ]
                            },
                        )
                        return
                    destroyed = arguments.get("destroy", [])
                    if destroyed:
                        state.routes = [route for route in state.routes if route["id"] not in destroyed]
                    created: dict[str, dict[str, str]] = {}
                    for key, route in arguments.get("create", {}).items():
                        created[key] = {"id": f"created-{key}"}
                        state.routes.append({**route, "id": f"created-{key}"})
                    _json_response(
                        self,
                        {
                            "methodResponses": [
                                [
                                    method,
                                    {
                                        **({"created": created} if created else {}),
                                        **({"destroyed": destroyed} if destroyed else {}),
                                    },
                                    tag,
                                ]
                            ]
                        },
                    )
                    return
                if method == "x:MtaOutboundStrategy/set":
                    if state.fail_strategy_once and state.strategy_failures == 0:
                        state.strategy_failures += 1
                        _json_response(
                            self,
                            {
                                "methodResponses": [
                                    [
                                        "x:MtaOutboundStrategy/set",
                                        {
                                            "notUpdated": {
                                                "singleton": {
                                                    "type": "invalidProperties",
                                                    "properties": ["route"],
                                                }
                                            }
                                        },
                                        tag,
                                    ]
                                ]
                            },
                        )
                        return
                    patch = arguments["update"]["singleton"]
                    if set(patch) != {"route"} or not isinstance(patch["route"], dict):
                        _json_response(
                            self,
                            {
                                "methodResponses": [
                                    [
                                        method,
                                        {
                                            "notUpdated": {
                                                "singleton": {
                                                    "type": "invalidProperties",
                                                    "properties": ["route"],
                                                }
                                            }
                                        },
                                        tag,
                                    ]
                                ]
                            },
                        )
                        return
                    state.strategy = {**state.strategy, "route": patch["route"]}
                    _json_response(
                        self,
                        {"methodResponses": [[method, {"updated": {"singleton": {}}}, tag]]},
                    )
                    return
                _json_response(
                    self,
                    {"methodResponses": [["error", {"type": "unknownMethod"}, tag]]},
                )

            def log_message(self, _format: str, *_args: object) -> None:
                return

        return Handler

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def stop(self) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()

    def backup(self) -> dict[str, Any]:
        return {
            "version": 1,
            "scope": "stalwart-remote-route-and-strategy",
            "routes": {
                "methodResponses": [["x:MtaRoute/get", {"list": self.routes}, "routes"]],
                "sessionState": "session-state",
            },
            "strategy": {
                "methodResponses": [
                    ["x:MtaOutboundStrategy/get", {"list": [self.strategy]}, "strategy"]
                ],
                "sessionState": "session-state",
            },
        }


def _write_inputs(tmp_path: Path, server: RouteServer) -> tuple[Path, Path, Path, Path, dict[str, str]]:
    profile = tmp_path / "profile.env"
    profile.write_text(
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
    relay = tmp_path / "relay.env"
    relay.write_text("RESEND_API_KEY=" + ("R" * 32) + "\n", encoding="utf-8")
    relay.chmod(0o600)
    credentials = tmp_path / "credentials.env"
    credentials.write_text("STALWART_API_KEY=" + ("M" * 24) + "\n", encoding="utf-8")
    credentials.chmod(0o600)
    backup = tmp_path / "route-backup.json"
    backup.write_text(json.dumps(server.backup()), encoding="utf-8")
    backup.chmod(0o600)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "case \"${1:-}\" in\n"
        " inspect) printf '%s\\n' true ;;\n"
        " exec) awk -F= '$1 == \"RESEND_API_KEY\" {printf \"%s\", $2; exit}' \"$TEST_RELAY_FILE\" | sha256sum | awk '{print $1}' ;;\n"
        " *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    (bin_dir / "docker").chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin", "TEST_RELAY_FILE": str(relay)}
    return profile, credentials, relay, backup, {"PATH": env["PATH"], "TEST_RELAY_FILE": env["TEST_RELAY_FILE"]}


def _run(
    action: str,
    profile: Path,
    credentials: Path,
    relay: Path,
    backup: Path,
    server: RouteServer,
    env_values: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "sh",
            str(GATEWAY / "scripts" / "configure-stalwart-resend-route.sh"),
            action,
            str(profile),
            "--secret-file",
            str(credentials),
            "--relay-secret-file",
            str(relay),
            "--stalwart-container",
            "test-stalwart",
            "--backup",
            str(backup),
            "--admin-url",
            f"{server.base_url}/api",
        ],
        cwd=GATEWAY,
        env={**os.environ, **env_values},
        text=True,
        capture_output=True,
    )


def _assert_safe(result: subprocess.CompletedProcess[str], *secrets: str) -> None:
    output = result.stdout + result.stderr
    for secret in secrets:
        assert secret not in output
    assert "Authorization:" not in output
    assert "Bearer " not in output


def test_apply_is_transactional_and_reaches_exact_v01615_policy(tmp_path: Path) -> None:
    server = RouteServer()
    try:
        profile, credentials, relay, backup, env_values = _write_inputs(tmp_path, server)
        result = _run("apply", profile, credentials, relay, backup, server, env_values)
        assert result.returncode == 0, result.stderr
        assert "APPLY=PASS" in result.stdout
        assert all(path == "/jmap/session" or path == "/jmap/" for path in server.paths)
        assert "/api" not in server.paths
        assert [route["name"] for route in server.routes if route["@type"] == "Local"] == ["local"]
        assert not any(route["@type"] == "Mx" for route in server.routes)
        relays = [route for route in server.routes if route["@type"] == "Relay"]
        assert len(relays) == 1
        assert relays[0]["name"] == "resend-relay"
        assert relays[0]["address"] == "smtp.resend.com"
        assert relays[0]["port"] == 465
        assert relays[0]["protocol"] == "smtp"
        assert relays[0]["authSecret"] == {"@type": "EnvironmentVariable", "variableName": "RESEND_API_KEY"}
        assert server.strategy["route"] == {
            "match": {"0": {"if": "is_local_domain(rcpt_domain)", "then": "'local'"}},
            "else": "'resend-relay'",
        }
        verify_result = _run("verify", profile, credentials, relay, backup, server, env_values)
        assert verify_result.returncode == 0, verify_result.stderr
        assert "Local Stalwart Resend-only route verifies" in verify_result.stdout
        assert all(path == "/jmap/session" or path == "/jmap/" for path in server.paths)
        _assert_safe(result, "R" * 32, "M" * 24)
    finally:
        server.stop()


def test_partial_apply_performs_validated_automatic_rollback(tmp_path: Path) -> None:
    server = RouteServer(fail_strategy_once=True)
    try:
        profile, credentials, relay, backup, env_values = _write_inputs(tmp_path, server)
        original_routes = json.loads(json.dumps(server.routes))
        original_strategy = json.loads(json.dumps(server.strategy))
        result = _run("apply", profile, credentials, relay, backup, server, env_values)
        assert result.returncode != 0
        assert "APPLY=FAIL" in result.stderr
        assert "AUTOMATIC_ROLLBACK=PASS" in result.stderr
        assert [{key: value for key, value in route.items() if key != "id"} for route in server.routes] == [
            {key: value for key, value in route.items() if key != "id"} for route in original_routes
        ]
        assert server.strategy == original_strategy
        _assert_safe(result, "R" * 32, "M" * 24)
    finally:
        server.stop()


def test_failed_destroy_without_a_successful_mutation_does_not_rollback(tmp_path: Path) -> None:
    server = RouteServer(fail_destroy=True)
    try:
        profile, credentials, relay, backup, env_values = _write_inputs(tmp_path, server)
        result = _run("apply", profile, credentials, relay, backup, server, env_values)
        assert result.returncode != 0
        assert "APPLY=FAIL" in result.stderr
        assert "AUTOMATIC_ROLLBACK=NOT_NEEDED" in result.stderr
        assert sum(payload["methodCalls"][0][0].endswith("/set") for payload in server.requests) == 1
    finally:
        server.stop()


def test_backup_rejects_plaintext_route_credentials_without_writing_them(tmp_path: Path) -> None:
    server = RouteServer()
    secret = "P" * 48
    server.routes.append(
        {
            "id": "unsafe-id",
            "name": "unsafe",
            "@type": "Relay",
            "authSecret": {"@type": "PlainText", "value": secret},
        }
    )
    try:
        profile, credentials, relay, backup, env_values = _write_inputs(tmp_path, server)
        backup.unlink()
        result = _run("backup", profile, credentials, relay, backup, server, env_values)
        assert result.returncode != 0
        assert not backup.exists()
        assert secret not in result.stdout + result.stderr
    finally:
        server.stop()


def test_desired_state_is_idempotent_and_inspect_is_read_only(tmp_path: Path) -> None:
    server = RouteServer()
    server.routes = [
        {"id": "local-id", "name": "local", "@type": "Local"},
        {
            "id": "relay-id",
            "name": "resend-relay",
            "@type": "Relay",
            "address": "smtp.resend.com",
            "port": 465,
            "protocol": "smtp",
            "implicitTls": True,
            "allowInvalidCerts": False,
            "authUsername": "resend",
            "authSecret": {"@type": "EnvironmentVariable", "variableName": "RESEND_API_KEY"},
        },
    ]
    server.strategy["route"] = {
        "match": {"0": {"if": "is_local_domain(rcpt_domain)", "then": "'local'"}},
        "else": "'resend-relay'",
    }
    try:
        profile, credentials, relay, backup, env_values = _write_inputs(tmp_path, server)
        before = len(server.requests)
        apply_result = _run("apply", profile, credentials, relay, backup, server, env_values)
        assert apply_result.returncode == 0, apply_result.stderr
        assert "IDEMPOTENT=TRUE" in apply_result.stdout
        assert len(server.requests) == before + 2
        inspect_result = _run("inspect", profile, credentials, relay, backup, server, env_values)
        assert inspect_result.returncode == 0, inspect_result.stderr
        assert "ROUTE_NAME=resend-relay" in inspect_result.stdout
        assert "ROUTE_AUTH_SECRET_VARIABLE=RESEND_API_KEY" in inspect_result.stdout
        assert "R" * 32 not in inspect_result.stdout
        assert "M" * 24 not in inspect_result.stdout
    finally:
        server.stop()


def test_rollback_recreates_backed_up_remote_state_and_preserves_local(tmp_path: Path) -> None:
    server = RouteServer()
    profile, credentials, relay, backup, env_values = _write_inputs(tmp_path, server)
    server.routes = [
        {"id": "local-id", "name": "local", "@type": "Local"},
        {
            "id": "relay-id",
            "name": "resend-relay",
            "@type": "Relay",
            "address": "smtp.resend.com",
            "port": 465,
            "protocol": "smtp",
            "implicitTls": True,
            "allowInvalidCerts": False,
            "authUsername": "resend",
            "authSecret": {"@type": "EnvironmentVariable", "variableName": "RESEND_API_KEY"},
        },
    ]
    server.strategy["route"] = {
        "match": {"0": {"if": "is_local_domain(rcpt_domain)", "then": "'local'"}},
        "else": "'resend-relay'",
    }
    try:
        result = _run("rollback", profile, credentials, relay, backup, server, env_values)
        assert result.returncode == 0, result.stderr
        assert "ROLLBACK=PASS" in result.stdout
        assert {route["name"] for route in server.routes} == {"local", "mx"}
        assert server.strategy["route"]["else"] == "'mx'"
        assert server.strategy["route"]["match"]["0"]["then"] == "'local'"
    finally:
        server.stop()
