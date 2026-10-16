from __future__ import annotations

import importlib.util
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "opencode_level_a"
SCRIPT = HARNESS / "run.py"


def _module():
    spec = importlib.util.spec_from_file_location("opencode_level_a_run", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_disposable_compose_is_internal_ephemeral_and_gvisor_only() -> None:
    compose = yaml.safe_load((HARNESS / "docker-compose.yml").read_text())
    level_a = _module()
    subnet = "172.31.255.0/29"
    compose["networks"]["isolated"]["ipam"]["config"][0]["subnet"] = subnet
    addresses = level_a._service_addresses(subnet)
    compose["services"]["provider-fixture"]["networks"] = {
        "isolated": {"ipv4_address": addresses["AIAT_LEVEL_A_FIXTURE_IP"]}
    }
    compose["services"]["litellm"]["networks"] = {
        "isolated": {"ipv4_address": addresses["AIAT_LEVEL_A_LITELLM_IP"]}
    }
    compose["services"]["litellm"]["extra_hosts"] = [
        f"provider-fixture:{addresses['AIAT_LEVEL_A_FIXTURE_IP']}"
    ]
    compose["services"]["opencode-runtime"]["networks"] = {
        "isolated": {"ipv4_address": addresses["AIAT_LEVEL_A_OPENCODE_IP"]}
    }
    compose["services"]["opencode-runtime"]["extra_hosts"] = [
        f"litellm:{addresses['AIAT_LEVEL_A_LITELLM_IP']}"
    ]
    level_a._assert_compose_config(compose)

    assert compose["networks"]["isolated"]["internal"] is True
    assert "volumes" not in compose
    assert {service: data["pull_policy"] for service, data in compose["services"].items()} == {
        "provider-fixture": "never",
        "litellm": "never",
        "opencode-runtime": "never",
    }
    assert compose["services"]["opencode-runtime"]["runtime"] == "runsc"
    assert compose["services"]["opencode-runtime"]["extra_hosts"] == ["litellm:172.31.255.3"]
    assert compose["services"]["litellm"]["extra_hosts"] == ["provider-fixture:172.31.255.2"]
    assert any(
        isinstance(volume, dict) and volume.get("source") == "./probe_helpers.mjs"
        for volume in compose["services"]["opencode-runtime"]["volumes"]
    )
    assert all("ports" not in data for data in compose["services"].values())
    assert all(not data.get("env_file") for data in compose["services"].values())
    assert all("/var/run/docker.sock" not in json.dumps(data.get("volumes", [])) for data in compose["services"].values())


def test_subnet_selection_skips_existing_docker_or_host_ranges() -> None:
    level_a = _module()
    assert level_a._select_subnet(["172.28.240.0/20"]) == "172.29.240.0/29"
    assert level_a._select_subnet(["172.28.240.0/20", "172.29.240.0/20"]) == "172.30.240.0/29"


def test_report_keeps_runtime_smoke_distinct_from_canonical_worker_execution() -> None:
    report = _module()._initial_report("disposable-level-a-project")

    assert report["level"] == "A_LOCAL_DETERMINISTIC_FIXTURE"
    assert report["promotable"] is False
    assert report["external_provider_call_performed"] is False
    assert report["scope"] == {
        "opencode_runtime": "real_pinned_process",
        "sandbox": "runsc_required",
        "provider_boundary": "local_deterministic_fixture",
        "canonical_worker_run": "not_exercised",
        "aiat_tool_mediation": "not_exercised",
        "artifact_persistence": "not_exercised",
    }
    assert report["canonical_worker_run"] == "not_run"
    assert report["artifact_and_tool_mediation"] == "not_run"


def test_fixed_service_addresses_use_unique_non_gateway_hosts() -> None:
    level_a = _module()
    assert level_a._service_addresses("172.31.255.0/29") == {
        "AIAT_LEVEL_A_FIXTURE_IP": "172.31.255.2",
        "AIAT_LEVEL_A_LITELLM_IP": "172.31.255.3",
        "AIAT_LEVEL_A_OPENCODE_IP": "172.31.255.4",
    }
    with pytest.raises(level_a.HarnessError, match="private IPv4 /29"):
        level_a._service_addresses("192.168.1.0/24")


def test_runsc_gateway_preflight_requires_http_success(monkeypatch: pytest.MonkeyPatch) -> None:
    level_a = _module()
    calls: list[tuple[str, ...]] = []

    def compose(_project: str, _env: dict[str, str], *args: str, **_kwargs: object) -> SimpleNamespace:
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout='{"status":200}\n', stderr="")

    monkeypatch.setattr(level_a, "_compose", compose)
    level_a._assert_gateway_reachable("fixture-project", {})
    assert calls[0][:4] == ("exec", "-T", "opencode-runtime", "node")


def test_runsc_gateway_preflight_fails_closed_on_dns_or_http_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    level_a = _module()
    monkeypatch.setattr(
        level_a,
        "_compose",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout='{"error":"EAI_AGAIN"}\n',
            stderr="private runtime diagnostics",
        ),
    )
    with pytest.raises(level_a.HarnessError) as raised:
        level_a._assert_gateway_reachable("fixture-project", {})
    assert raised.value.code == "GATEWAY_UNREACHABLE"
    assert "EAI_AGAIN" not in str(raised.value)
    assert "private runtime diagnostics" not in str(raised.value)


def test_litellm_has_one_local_fixture_upstream() -> None:
    config = yaml.safe_load((HARNESS / "litellm_config.yaml").read_text())
    models = config["model_list"]
    assert len(models) == 1
    assert models[0]["model_name"] == "omniroute-coding"
    assert models[0]["litellm_params"]["api_base"] == "http://provider-fixture:8000/v1"
    assert models[0]["litellm_params"]["model"] == "openai/fixture-model"


def test_docker_environment_drops_provider_and_dispatch_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-propagate")
    monkeypatch.setenv("LLM_API_KEY", "must-not-propagate")
    monkeypatch.setenv("AIAT_ALLOW_EXTERNAL_PROVIDER_DISPATCH", "true")
    monkeypatch.setenv("DOCKER_CONTEXT", "aiat-wsl")

    env = _module()._docker_environment("local-opencode", "local-litellm")

    assert env["DOCKER_CONTEXT"] == "aiat-wsl"
    assert env["AIAT_LEVEL_A_OPENCODE_IMAGE"] == "local-opencode"
    assert env["AIAT_LEVEL_A_LITELLM_IMAGE"] == "local-litellm"
    assert env["AIAT_LEVEL_A_SUBNET"] == ""
    assert "OPENAI_API_KEY" not in env
    assert "LLM_API_KEY" not in env
    assert "AIAT_ALLOW_EXTERNAL_PROVIDER_DISPATCH" not in env


def test_fixture_summary_retains_only_bounded_counters() -> None:
    level_a = _module()
    summary = level_a._fixture_summary(
        {
            "request_count": 1,
            "requests": [
                {
                    "method": "POST",
                    "path": "/v1/chat/completions",
                    "model": "fixture-model",
                    "authorized": True,
                    "source": "::ffff:10.10.0.3",
                    "prompt": "private prompt sentinel",
                    "authorization": "dummy-key sentinel",
                    "body": "provider body sentinel",
                }
            ],
        },
        "10.10.0.3",
    )

    assert summary == {
        "request_count": 1,
        "retained_request_count": 1,
        "report_truncated": False,
        "authorized_request_count": 1,
        "contract_valid_request_count": 1,
        "all_requests_from_gateway": True,
        "gateway_source_match": True,
    }
    encoded = json.dumps(summary)
    assert "private prompt sentinel" not in encoded
    assert "dummy-key sentinel" not in encoded
    assert "provider body sentinel" not in encoded


def test_opencode_probe_summary_keeps_only_allowlisted_booleans_and_failure_code() -> None:
    level_a = _module()
    summary = level_a._safe_opencode_probe_summary(
        {
            "status": "FAIL",
            "session_created": True,
            "prompt_request_started": True,
            "prompt_submitted": True,
            "prompt_completed": False,
            "session_idle_event_seen": False,
            "fixture_marker_seen": False,
            "failure_stage": "opencode.await_idle",
            "failure_code": "OPENCODE_REQUEST_TIMEOUT",
            "prompt": "private prompt sentinel",
            "error": "unredacted transport details sentinel",
            "authorization": "dummy auth sentinel",
        }
    )

    assert summary == {
        "probe_status": "FAIL",
        "session_created": True,
        "prompt_request_started": True,
        "prompt_submitted": True,
        "prompt_completed": False,
        "session_idle_event_seen": False,
        "fixture_marker_seen": False,
        "failure_stage": "opencode.await_idle",
        "failure_code": "OPENCODE_REQUEST_TIMEOUT",
    }
    encoded = json.dumps(summary)
    assert "private prompt sentinel" not in encoded
    assert "unredacted transport details sentinel" not in encoded
    assert "dummy auth sentinel" not in encoded


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for probe helper unit tests")
def test_probe_helpers_match_pinned_idle_event_shapes_and_classify_timeouts() -> None:
    node = shutil.which("node") or "node"
    result = subprocess.run(
        [
            node,
            "--input-type=module",
            "-e",
            "import {hasSessionIdle as idle, transportFailureCode as failure} from './probe_helpers.mjs'; "
            "const sid='ses_fixture'; "
            "const values=["
            "idle({type:'session.status',properties:{sessionID:sid,status:{type:'idle'}}},sid),"
            "idle({type:'session.status',data:{sessionId:sid,status:{type:'idle'}}},sid),"
            "idle({payload:{type:'session.status',properties:{sessionID:sid,status:{type:'idle'}}}},sid),"
            "idle({type:'session.idle',properties:{sessionID:sid}},sid),"
            "idle({type:'session.status',properties:{sessionID:sid,status:{type:'busy'}}},sid),"
            "idle({type:'session.status',properties:{sessionID:'other',status:{type:'idle'}}},sid),"
            "failure({name:'AbortError'}),failure({name:'TimeoutError'}),failure({name:'TypeError'})]; "
            "console.log(JSON.stringify(values))",
        ],
        cwd=HARNESS,
        capture_output=True,
        check=False,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == [
        True,
        True,
        True,
        True,
        False,
        False,
        "OPENCODE_REQUEST_TIMEOUT",
        "OPENCODE_REQUEST_TIMEOUT",
        "OPENCODE_REQUEST_TRANSPORT_ERROR",
    ]


def test_failed_opencode_probe_keeps_safe_stage_and_fixture_summary_before_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    level_a = _module()
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(level_a, "_docker_environment", lambda *_args: {})
    monkeypatch.setattr(level_a, "_available_subnet", lambda _env: "172.31.255.0/29")
    monkeypatch.setattr(level_a, "_assert_runsc", lambda _env: None)
    monkeypatch.setattr(level_a, "_image_id", lambda *_args: "sha256:" + "a" * 64)
    monkeypatch.setattr(level_a, "_validate_compose_config", lambda *_args: {})
    monkeypatch.setattr(level_a, "_wait_healthy", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(level_a, "_assert_opencode_version", lambda *_args: None)
    monkeypatch.setattr(level_a, "_assert_gateway_reachable", lambda *_args: None)

    def container_network(_project: str, service: str, _env: dict[str, str]) -> tuple[str, str]:
        addresses = {
            "opencode-runtime": "10.10.0.2",
            "litellm": "10.10.0.3",
            "provider-fixture": "10.10.0.4",
        }
        return f"{service}-id", addresses[service]

    monkeypatch.setattr(level_a, "_container_network", container_network)

    def compose(_project: str, _env: dict[str, str], *args: str, **_kwargs: object) -> SimpleNamespace:
        calls.append(args)
        if args[0] == "exec" and "/fixture/egress_probe.mjs" in args:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"results": [{"connected": False}, {"connected": False}]}),
                stderr="",
            )
        if args[0] == "exec" and "/fixture/open_code_probe.mjs" in args:
            return SimpleNamespace(
                returncode=1,
                stdout=json.dumps(
                    {
                        "schema_version": "aiat.opencode-level-a-runtime-probe.v1",
                        "status": "FAIL",
                        "failure_stage": "opencode.prompt_submit",
                        "failure_code": "OPENCODE_HTTP_400",
                    }
                )
                + "\n",
                stderr="must-not-be-retained",
            )
        if args[0] == "exec" and "provider-fixture" in args:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"request_count": 0, "requests": []}),
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(level_a, "_compose", compose)

    def run(argv: list[str], **_kwargs: object) -> SimpleNamespace:
        if argv[1:3] == ["network", "inspect"]:
            return SimpleNamespace(returncode=0, stdout="true", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(level_a, "_run", run)
    report = level_a._run_live("local-opencode", "local-litellm")
    emitted = json.loads(capsys.readouterr().out)

    assert report["status"] == emitted["status"] == "FAIL"
    assert report["failure_stage"] == "opencode.prompt_submit"
    assert report["failure_code"] == "OPENCODE_HTTP_400"
    assert report["provider_dispatch"] == "NOT_OBSERVED"
    assert report["provider_request_count"] == 0
    assert report["provider_fixture_summary"]["retained_request_count"] == 0
    assert report["opencode_probe_exit_code"] == 1
    assert report["cleanup"] == "PASS"
    fixture_index = next(i for i, call in enumerate(calls) if call[0] == "exec" and "provider-fixture" in call)
    teardown_index = next(i for i, call in enumerate(calls) if call[0] == "down")
    assert fixture_index < teardown_index
    assert "must-not-be-retained" not in json.dumps(report)


def test_live_harness_requires_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("Docker must not run without --run-live")

    level_a = _module()
    monkeypatch.setattr(level_a.subprocess, "run", unexpected_run)
    with pytest.raises(SystemExit) as raised:
        level_a.main([])
    assert raised.value.code == 2


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the local fixture unit test")
def test_provider_fixture_accepts_only_dummy_auth_and_returns_deterministic_response() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {
        "PATH": os.environ.get("PATH", ""),
        "AIAT_FIXTURE_HOST": "127.0.0.1",
        "AIAT_FIXTURE_PORT": str(port),
        "AIAT_FIXTURE_PROVIDER_KEY": "unit-test-dummy-key",
    }
    process = subprocess.Popen(
        [shutil.which("node") or "node", str(HARNESS / "fixture.mjs")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail("local provider fixture exited before it became ready")
            try:
                with urllib.request.urlopen(f"{base_url}/health", timeout=0.2) as response:
                    assert response.status == 200
                break
            except (OSError, urllib.error.URLError):
                time.sleep(0.05)
        else:
            pytest.fail("local provider fixture did not become ready")

        request = urllib.request.Request(
            f"{base_url}/v1/chat/completions",
            data=json.dumps({"model": "fixture-model", "messages": [{"role": "user", "content": "safe marker"}]}).encode(),
            headers={"Authorization": "Bearer unit-test-dummy-key", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.loads(response.read())
        assert payload["choices"][0]["message"]["content"] == "AIAT_LEVEL_A_FIXTURE_REPLY"
        assert payload["usage"]["total_tokens"] == 13

        stream_request = urllib.request.Request(
            f"{base_url}/v1/chat/completions",
            data=json.dumps({"model": "fixture-model", "stream": True}).encode(),
            headers={"Authorization": "Bearer unit-test-dummy-key", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(stream_request, timeout=2) as response:
            stream_body = response.read().decode()
            assert response.headers["Content-Type"].startswith("text/event-stream")
        assert "AIAT_LEVEL_A_FIXTURE_REPLY" in stream_body
        assert "data: [DONE]" in stream_body

        report = json.loads(urllib.request.urlopen(f"{base_url}/__fixture/report", timeout=2).read())
        assert report["request_count"] == 2
        record = report["requests"][0]
        assert record["method"] == "POST"
        assert record["path"] == "/v1/chat/completions"
        assert record["model"] == "fixture-model"
        assert record["authorized"] is True
        assert record["source"] in {"127.0.0.1", "::ffff:127.0.0.1"}
        assert 0 < record["body_bytes"] <= 1024 * 1024
        assert "safe marker" not in json.dumps(report)
        assert "unit-test-dummy-key" not in json.dumps(report)

        rejected = urllib.request.Request(
            f"{base_url}/v1/chat/completions",
            data=json.dumps({"model": "fixture-model"}).encode(),
            headers={"Authorization": "Bearer not-the-fixture-key", "Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as response_error:
            urllib.request.urlopen(rejected, timeout=2)
        assert response_error.value.code == 401
        report = json.loads(urllib.request.urlopen(f"{base_url}/__fixture/report", timeout=2).read())
        assert report["request_count"] == 3
        assert report["requests"][-1]["authorized"] is False
    finally:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
