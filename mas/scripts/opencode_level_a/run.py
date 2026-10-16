"""Run a disposable, no-egress OpenCode + LiteLLM fixture smoke.

This only exercises the real pinned OpenCode process through an isolated
LiteLLM configuration whose sole upstream is a local deterministic fixture.
It is explicitly not an AIAT WorkerRun, tool-mediation, artifact, or live
provider certificate.  Docker execution is opt-in via ``--run-live``.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
PROJECT_PREFIX = "aiat-level-a-opencode-"
EXPECTED_OPENCODE_VERSION = "1.17.13"
GATEWAY_KEY = "sk-aiat-level-a-gateway-dummy"


class HarnessError(RuntimeError):
    def __init__(self, message: str, *, code: str = "HARNESS_VALIDATION_FAILED") -> None:
        super().__init__(message)
        self.code = code


def _docker_environment(opencode_image: str, litellm_image: str, subnet: str = "") -> dict[str, str]:
    """Pass Docker connection settings, never ambient provider credentials."""

    allowed = {
        "PATH",
        "HOME",
        "DOCKER_CONFIG",
        "DOCKER_CONTEXT",
        "DOCKER_HOST",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
    }
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update(
        {
            "COMPOSE_DISABLE_ENV_FILE": "1",
            "AIAT_LEVEL_A_OPENCODE_IMAGE": opencode_image,
            "AIAT_LEVEL_A_LITELLM_IMAGE": litellm_image,
            "AIAT_LEVEL_A_SUBNET": subnet,
        }
    )
    return env


def _select_subnet(occupied: list[str]) -> str:
    """Choose an isolated /29 from private space without overlapping routes."""

    occupied_networks: list[ipaddress.IPv4Network] = []
    for raw in occupied:
        try:
            network = ipaddress.ip_network(raw, strict=False)
        except ValueError:
            continue
        if isinstance(network, ipaddress.IPv4Network):
            occupied_networks.append(network)
    pools = (
        "172.28.240.0/20",
        "172.29.240.0/20",
        "172.30.240.0/20",
        "172.31.240.0/20",
        "10.254.240.0/20",
        "10.253.240.0/20",
        "192.168.240.0/20",
    )
    for raw_pool in pools:
        pool = ipaddress.ip_network(raw_pool)
        for candidate in pool.subnets(new_prefix=29):
            if not any(candidate.overlaps(network) for network in occupied_networks):
                return str(candidate)
    raise HarnessError("no non-overlapping private /29 is available for the isolated Compose network")


def _service_addresses(subnet: str) -> dict[str, str]:
    """Choose stable service IPs inside the selected isolated /29.

    OpenCode runs under runsc, where Docker's embedded DNS resolver is not
    reachable.  The production Compose stack uses explicit ``extra_hosts`` for
    the same reason.  Reserve the first three usable addresses after Docker's
    gateway for the fixture, gateway, and OpenCode runtime respectively.
    """

    try:
        network = ipaddress.ip_network(subnet, strict=True)
    except ValueError as exc:
        raise HarnessError("isolated service subnet is invalid") from exc
    if not isinstance(network, ipaddress.IPv4Network) or network.prefixlen != 29 or not network.is_private:
        raise HarnessError("isolated service addresses require a private IPv4 /29")
    hosts = list(network.hosts())
    if len(hosts) != 6:
        raise HarnessError("isolated service subnet does not have the expected host capacity")
    return {
        "AIAT_LEVEL_A_FIXTURE_IP": str(hosts[1]),
        "AIAT_LEVEL_A_LITELLM_IP": str(hosts[2]),
        "AIAT_LEVEL_A_OPENCODE_IP": str(hosts[3]),
    }


def _available_subnet(env: dict[str, str]) -> str:
    networks = _run(["docker", "network", "ls", "-q"], env=env, timeout=15)
    identifiers = networks.stdout.split()
    occupied: list[str] = []
    if identifiers:
        inspected = _run(["docker", "network", "inspect", *identifiers], env=env, timeout=20)
        try:
            for network in json.loads(inspected.stdout):
                for entry in ((network.get("IPAM") or {}).get("Config") or []):
                    if entry.get("Subnet"):
                        occupied.append(str(entry["Subnet"]))
        except (json.JSONDecodeError, TypeError) as exc:
            raise HarnessError("Docker network inspection did not return valid subnet data") from exc
    ip_binary = shutil.which("ip")
    if not ip_binary:
        raise HarnessError("iproute2 is required to avoid overlapping host routes")
    routes = _run([ip_binary, "-j", "route", "show"], env=env, timeout=10)
    try:
        route_rows = json.loads(routes.stdout)
    except json.JSONDecodeError as exc:
        raise HarnessError("host route table could not be read as JSON") from exc
    occupied.extend(str(row["dst"]) for row in route_rows if row.get("dst") and row["dst"] != "default")
    return _select_subnet(occupied)


def _run(
    argv: list[str],
    *,
    env: dict[str, str],
    cwd: Path = HERE,
    timeout: int = 30,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if check and result.returncode:
        # Container and Docker diagnostics can contain ambient configuration.
        # Keep the evidence reason stable and do not retain stderr.
        raise HarnessError("bounded command failed", code="COMMAND_FAILED")
    return result


def _compose(project: str, env: dict[str, str], *args: str, timeout: int = 30, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            "docker",
            "compose",
            "--env-file",
            os.devnull,
            "-p",
            project,
            "-f",
            str(HERE / "docker-compose.yml"),
            *args,
        ],
        env=env,
        cwd=HERE,
        timeout=timeout,
        check=check,
    )


def _image_id(image: str, env: dict[str, str]) -> str:
    result = _run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        env=env,
        timeout=15,
    )
    image_id = result.stdout.strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise HarnessError("a requested image did not resolve to a local immutable image ID")
    return image_id


def _assert_runsc(env: dict[str, str]) -> None:
    result = _run(
        ["docker", "info", "--format", "{{json .Runtimes}}"],
        env=env,
        timeout=15,
    )
    try:
        runtimes = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise HarnessError("Docker did not return runtime registration data") from exc
    if "runsc" not in runtimes:
        raise HarnessError("Docker runtime runsc is not registered; refusing any isolation downgrade")


def _assert_opencode_version(project: str, env: dict[str, str]) -> None:
    result = _compose(project, env, "exec", "-T", "opencode-runtime", "opencode", "--version", timeout=30)
    if result.stdout.strip() != EXPECTED_OPENCODE_VERSION:
        raise HarnessError("local OpenCode image does not report the exact expected version")


def _validate_compose_config(project: str, env: dict[str, str]) -> dict[str, Any]:
    output = _compose(project, env, "config", "--format", "json", timeout=20).stdout
    try:
        config = json.loads(output)
    except json.JSONDecodeError as exc:
        raise HarnessError("isolated Compose configuration was not valid JSON") from exc
    _assert_compose_config(config)
    return config


def _assert_compose_config(config: dict[str, Any]) -> None:
    services = config.get("services", {})
    networks = config.get("networks", {})
    if set(services) != {"provider-fixture", "litellm", "opencode-runtime"}:
        raise HarnessError("isolated Compose service set changed unexpectedly")
    if set(networks) != {"isolated"} or networks["isolated"].get("internal") is not True:
        raise HarnessError("isolated Compose must have exactly one internal network")
    ipam_configs = ((networks["isolated"].get("ipam") or {}).get("config") or [])
    if len(ipam_configs) != 1:
        raise HarnessError("isolated Compose must have one unique subnet")
    try:
        subnet = ipaddress.ip_network(str(ipam_configs[0].get("subnet")), strict=True)
    except ValueError as exc:
        raise HarnessError("isolated Compose subnet is invalid") from exc
    if not isinstance(subnet, ipaddress.IPv4Network) or subnet.prefixlen != 29 or not subnet.is_private:
        raise HarnessError("isolated Compose subnet must be a private IPv4 /29")
    service_addresses: dict[str, ipaddress.IPv4Address] = {}
    if config.get("volumes"):
        raise HarnessError("isolated Compose must not declare persistent volumes")
    if config.get("secrets"):
        raise HarnessError("isolated Compose must not declare external secrets")
    for name, service in services.items():
        if service.get("ports"):
            raise HarnessError(f"isolated service {name!r} unexpectedly publishes a host port")
        if service.get("env_file") or service.get("secrets"):
            raise HarnessError(f"isolated service {name!r} imports ambient configuration")
        if service.get("build"):
            raise HarnessError(f"isolated service {name!r} must use a pre-existing local image")
        attached = service.get("networks", [])
        if set(attached) != {"isolated"}:
            raise HarnessError(f"isolated service {name!r} has an unexpected network")
        network_config = attached.get("isolated") if isinstance(attached, dict) else None
        raw_address = network_config.get("ipv4_address") if isinstance(network_config, dict) else None
        try:
            address = ipaddress.ip_address(str(raw_address))
        except ValueError as exc:
            raise HarnessError(f"isolated service {name!r} has no fixed IPv4 address") from exc
        if not isinstance(address, ipaddress.IPv4Address) or address not in subnet or address in {
            subnet.network_address,
            subnet.broadcast_address,
            next(subnet.hosts()),
        }:
            raise HarnessError(f"isolated service {name!r} has an invalid fixed IPv4 address")
        service_addresses[name] = address
    if len(set(service_addresses.values())) != len(services):
        raise HarnessError("isolated services must have unique fixed IPv4 addresses")

    def host_mappings(service: dict[str, Any]) -> dict[str, str]:
        mappings: dict[str, str] = {}
        for entry in service.get("extra_hosts", []) or []:
            if isinstance(entry, str):
                for separator in (":", "="):
                    if separator in entry:
                        host, address = entry.split(separator, maxsplit=1)
                        mappings[host] = address
                        break
            elif isinstance(entry, dict):
                host = entry.get("host")
                address = entry.get("ip")
                if isinstance(host, str) and isinstance(address, str):
                    mappings[host] = address
        return mappings

    expected_mappings = {
        "litellm": str(service_addresses["litellm"]),
    }
    if host_mappings(services["opencode-runtime"]) != expected_mappings:
        raise HarnessError("runsc OpenCode runtime must resolve only the fixed internal LiteLLM gateway address")
    if host_mappings(services["litellm"]) != {"provider-fixture": str(service_addresses["provider-fixture"])}:
        raise HarnessError("local LiteLLM must resolve only the fixed provider-fixture address")
    runtime = services["opencode-runtime"]
    if runtime.get("runtime") != "runsc":
        raise HarnessError("OpenCode runtime must use runsc; refusing any isolation downgrade")
    if runtime.get("privileged") or "/var/run/docker.sock" in json.dumps(runtime.get("volumes", [])):
        raise HarnessError("OpenCode runtime has a privileged or Docker socket mount")
    if runtime.get("environment", {}).get("AIAT_GATEWAY_URL") != "http://litellm:4000":
        raise HarnessError("OpenCode must use the isolated local LiteLLM service")
    if runtime.get("environment", {}).get("AIAT_GATEWAY_API_KEY") != GATEWAY_KEY:
        raise HarnessError("OpenCode must use the isolated dummy gateway key")


def _service_container(project: str, service: str, env: dict[str, str]) -> str:
    result = _compose(project, env, "ps", "-q", service, timeout=15)
    container_id = result.stdout.strip().splitlines()[-1:] or [""]
    if not container_id[0]:
        raise HarnessError(f"isolated service {service!r} has no container")
    return container_id[0]


def _wait_healthy(project: str, services: tuple[str, ...], env: dict[str, str], timeout: int = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        states: dict[str, str] = {}
        for service in services:
            result = _compose(project, env, "ps", "-q", service, check=False, timeout=10)
            container_id = result.stdout.strip().splitlines()[-1:] or [""]
            if not container_id[0]:
                states[service] = "missing"
                continue
            inspected = _run(
                ["docker", "inspect", "--format", "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}", container_id[0]],
                env=env,
                timeout=10,
                check=False,
            )
            states[service] = inspected.stdout.strip() if inspected.returncode == 0 else "inspect_error"
        if all(state == "healthy" for state in states.values()):
            return
        if any(state in {"exited", "dead"} for state in states.values()):
            raise HarnessError("an isolated container exited before becoming healthy")
        time.sleep(1)
    raise HarnessError("isolated services did not become healthy before timeout")


def _container_network(project: str, service: str, env: dict[str, str]) -> tuple[str, str]:
    container_id = _service_container(project, service, env)
    result = _run(
        ["docker", "inspect", "--format", "{{json .NetworkSettings.Networks}}", container_id],
        env=env,
        timeout=10,
    )
    try:
        attached = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise HarnessError("Docker did not return isolated network attachment data") from exc
    expected = f"{project}_isolated"
    if set(attached) != {expected}:
        raise HarnessError(f"service {service!r} is attached outside its unique isolated network")
    address = str(attached[expected].get("IPAddress") or "")
    if not address:
        raise HarnessError(f"service {service!r} has no isolated-network address")
    return container_id, address


def _assert_gateway_reachable(project: str, env: dict[str, str]) -> None:
    """Prove runsc can reach LiteLLM before attempting an OpenCode prompt."""

    probe = (
        "fetch('http://litellm:4000/health/liveliness',{signal:AbortSignal.timeout(3000)})"
        ".then(r=>process.stdout.write(JSON.stringify({status:r.status})))"
        ".catch(e=>{process.stdout.write(JSON.stringify({error:e.cause?.code||e.name}));process.exitCode=1})"
    )
    result = _compose(
        project,
        env,
        "exec",
        "-T",
        "opencode-runtime",
        "node",
        "-e",
        probe,
        timeout=8,
        check=False,
    )
    try:
        outcome = json.loads(result.stdout.splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise HarnessError("runsc gateway probe returned invalid output", code="GATEWAY_PROBE_INVALID") from exc
    if result.returncode != 0 or outcome.get("status") != 200:
        raise HarnessError("runsc OpenCode runtime cannot reach the approved local model gateway", code="GATEWAY_UNREACHABLE")


def _fixture_report(project: str, env: dict[str, str]) -> dict[str, Any]:
    result = _compose(
        project,
        env,
        "exec",
        "-T",
        "provider-fixture",
        "node",
        "-e",
        "fetch('http://127.0.0.1:8000/__fixture/report').then(r=>r.json()).then(v=>process.stdout.write(JSON.stringify(v)))",
        timeout=15,
    )
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise HarnessError(
            "local provider fixture did not return its sanitized request report",
            code="FIXTURE_REPORT_INVALID",
        ) from exc
    if not isinstance(report, dict):
        raise HarnessError("local provider fixture report has an invalid shape", code="FIXTURE_REPORT_INVALID")
    return report


def _fixture_summary(fixture: dict[str, Any], gateway_ip: str) -> dict[str, Any]:
    """Reduce fixture state to bounded counters; never retain request content."""

    raw_requests = fixture.get("requests")
    requests = raw_requests if isinstance(raw_requests, list) else []
    bounded_requests = [row for row in requests[:100] if isinstance(row, dict)]
    sources = {
        str(row.get("source", "")).removeprefix("::ffff:")
        for row in bounded_requests
        if isinstance(row.get("source"), str)
    }
    contract_count = sum(
        row.get("method") == "POST"
        and row.get("path") == "/v1/chat/completions"
        and row.get("model") == "fixture-model"
        and row.get("authorized") is True
        for row in bounded_requests
    )
    authorized_count = sum(row.get("authorized") is True for row in bounded_requests)
    count = fixture.get("request_count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        count = len(bounded_requests)
    return {
        "request_count": min(count, 100),
        "retained_request_count": len(bounded_requests),
        "report_truncated": count > 100 or len(requests) > 100,
        "authorized_request_count": authorized_count,
        "contract_valid_request_count": contract_count,
        "all_requests_from_gateway": bool(bounded_requests) and sources == {gateway_ip},
        "gateway_source_match": sources == {gateway_ip} if bounded_requests else None,
    }


def _opencode_probe_failure(opencode: dict[str, Any] | None, exit_code: int) -> tuple[str, str]:
    """Return a stable stage/code pair from the test probe's allowlisted report."""

    if opencode is None:
        return "opencode.process", "OPENCODE_PROBE_OUTPUT_INVALID"
    stage = opencode.get("failure_stage")
    code = opencode.get("failure_code")
    allowed = {
        "opencode.health",
        "opencode.session_create",
        "opencode.event_stream",
        "opencode.prompt_submit",
        "opencode.await_idle",
        "opencode.read_response",
        "opencode.cleanup",
    }
    if isinstance(stage, str) and stage in allowed and isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", code):
        return stage, code
    if exit_code != 0:
        return "opencode.process", "OPENCODE_PROBE_FAILED"
    return "opencode.process", "OPENCODE_PROBE_INCOMPLETE"


def _safe_opencode_probe_summary(opencode: dict[str, Any]) -> dict[str, Any]:
    """Retain only the probe's fixed status, lifecycle booleans, and safe code."""

    status = opencode.get("status")
    summary: dict[str, Any] = {"probe_status": status if isinstance(status, str) and status in {"PASS", "FAIL"} else "UNKNOWN"}
    for key in (
        "session_created",
        "prompt_request_started",
        "prompt_submitted",
        "prompt_completed",
        "session_idle_event_seen",
        "fixture_marker_seen",
    ):
        value = opencode.get(key)
        if isinstance(value, bool):
            summary[key] = value
    if summary["probe_status"] == "FAIL":
        stage, code = _opencode_probe_failure(opencode, 1)
        summary["failure_stage"] = stage
        summary["failure_code"] = code
    return summary


def _initial_report(project: str) -> dict[str, Any]:
    """Declare exactly which parts the isolated runtime smoke does not exercise."""

    return {
        "schema_version": "aiat.opencode-level-a-disposable-runtime.v1",
        "level": "A_LOCAL_DETERMINISTIC_FIXTURE",
        "promotable": False,
        "project": project,
        "scope": {
            "opencode_runtime": "real_pinned_process",
            "sandbox": "runsc_required",
            "provider_boundary": "local_deterministic_fixture",
            "canonical_worker_run": "not_exercised",
            "aiat_tool_mediation": "not_exercised",
            "artifact_persistence": "not_exercised",
        },
        "provider_fixture": "not_run",
        "egress_probe": "not_run",
        "opencode_process": "not_run",
        "canonical_worker_run": "not_run",
        "artifact_and_tool_mediation": "not_run",
        "external_provider_call_performed": False,
        "provider_dispatch": "UNVERIFIED",
        "provider_fixture_summary": {"status": "NOT_COLLECTED"},
    }


def _run_live(opencode_image: str, litellm_image: str) -> dict[str, Any]:
    if not opencode_image or not litellm_image:
        raise HarnessError("both local image references must be supplied explicitly")
    env = _docker_environment(opencode_image, litellm_image)
    project = PROJECT_PREFIX + uuid.uuid4().hex[:12]
    started = False
    cleanup_ok = True
    failure: Exception | None = None
    failure_stage: str | None = None
    stage = "preflight.runsc"
    stage_override: str | None = None
    gateway_ip = ""
    report = _initial_report(project)
    try:
        stage = "preflight.subnet"
        env["AIAT_LEVEL_A_SUBNET"] = _available_subnet(env)
        env.update(_service_addresses(env["AIAT_LEVEL_A_SUBNET"]))
        stage = "preflight.runsc"
        _assert_runsc(env)
        stage = "preflight.images"
        image_ids = {
            "opencode": _image_id(opencode_image, env),
            "litellm": _image_id(litellm_image, env),
        }
        stage = "preflight.compose_config"
        _validate_compose_config(project, env)
        started = True
        stage = "compose.start"
        _compose(project, env, "up", "-d", "--no-build", "--pull", "never", timeout=120)
        stage = "services.health"
        _wait_healthy(project, ("provider-fixture", "litellm", "opencode-runtime"), env)
        stage = "opencode.version"
        _assert_opencode_version(project, env)

        stage = "network.topology"
        _container_network(project, "opencode-runtime", env)
        _, gateway_ip = _container_network(project, "litellm", env)
        _container_network(project, "provider-fixture", env)
        network_name = f"{project}_isolated"
        network = _run(
            ["docker", "network", "inspect", "--format", "{{.Internal}}", network_name],
            env=env,
            timeout=10,
        ).stdout.strip().lower()
        if network != "true":
            raise HarnessError("isolated Docker network is not marked internal", code="NETWORK_NOT_INTERNAL")

        stage = "network.gateway"
        _assert_gateway_reachable(project, env)
        report["gateway_connectivity"] = "PASS"

        stage = "network.egress"
        probe = _compose(project, env, "exec", "-T", "opencode-runtime", "node", "/fixture/egress_probe.mjs", timeout=10)
        try:
            egress = json.loads(probe.stdout)
        except json.JSONDecodeError as exc:
            raise HarnessError("OpenCode egress probe did not return machine-readable output", code="EGRESS_OUTPUT_INVALID") from exc
        if probe.returncode != 0 or any(item.get("connected") for item in egress.get("results", [])):
            raise HarnessError("OpenCode runtime reached a public-IP egress probe target", code="EGRESS_NOT_DENIED")
        if len(egress.get("results", [])) != 2:
            raise HarnessError("OpenCode egress probe did not attempt both fixed public targets", code="EGRESS_PROBE_INCOMPLETE")
        report["egress_probe"] = "PASS"

        stage = "opencode.process"
        # A failed probe is still useful diagnostic evidence. Do not let the
        # Compose helper throw away its bounded JSON result before the fixture
        # request count is sampled.
        runtime_probe = _compose(
            project,
            env,
            "exec",
            "-T",
            "opencode-runtime",
            "node",
            "/fixture/open_code_probe.mjs",
            timeout=90,
            check=False,
        )
        report["opencode_probe_started"] = True
        report["opencode_probe_exit_code"] = runtime_probe.returncode
        opencode: dict[str, Any] | None = None
        try:
            opencode = json.loads(runtime_probe.stdout.splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            report["opencode_probe_report"] = "INVALID_OR_MISSING"
        else:
            if isinstance(opencode, dict):
                report["opencode_probe_report"] = "VALID"
                safe_probe = _safe_opencode_probe_summary(opencode)
                for key, value in safe_probe.items():
                    report[f"opencode_probe_{key}"] = value
                if runtime_probe.returncode != 0:
                    stage_override, probe_code = _opencode_probe_failure(opencode, runtime_probe.returncode)
                    report["opencode_probe_stage"] = stage_override
                    report["opencode_probe_code"] = probe_code
            else:
                opencode = None
                report["opencode_probe_report"] = "INVALID_OR_MISSING"

        stage = "fixture.report"
        fixture: dict[str, Any] | None = None
        try:
            fixture = _fixture_report(project, env)
            summary = _fixture_summary(fixture, gateway_ip)
            report["provider_fixture_summary"] = summary
            report["provider_request_count"] = summary["request_count"]
            report["provider_dispatch"] = "OBSERVED" if summary["request_count"] else "NOT_OBSERVED"
        except (HarnessError, subprocess.TimeoutExpired, OSError):
            # Keep the primary OpenCode failure and record only that the
            # sanitized local report was unavailable.
            report["provider_fixture_summary"] = {"status": "UNAVAILABLE"}

        if runtime_probe.returncode != 0:
            failed_stage, failed_code = _opencode_probe_failure(opencode, runtime_probe.returncode)
            stage_override = failed_stage
            report["opencode_process"] = "FAIL"
            if fixture is not None and report.get("provider_request_count") == 0:
                report["provider_dispatch"] = "NOT_OBSERVED"
            raise HarnessError("real OpenCode probe did not complete", code=failed_code)
        if opencode is None:
            raise HarnessError("OpenCode runtime probe did not return its sanitized report", code="OPENCODE_PROBE_OUTPUT_INVALID")
        if not all(opencode.get(key) for key in ("session_created", "prompt_submitted", "prompt_completed", "fixture_marker_seen")):
            stage_override, code = _opencode_probe_failure(opencode, 0)
            raise HarnessError("OpenCode runtime probe did not complete its local fixture exchange", code=code)
        if fixture is None:
            raise HarnessError("local provider fixture request report was unavailable", code="FIXTURE_REPORT_UNAVAILABLE")
        requests = fixture.get("requests", [])
        normalized_sources = {str(item.get("source", "")).removeprefix("::ffff:") for item in requests if isinstance(item, dict)}
        if not requests or not all(
            isinstance(item, dict)
            and
            item.get("method") == "POST"
            and item.get("path") == "/v1/chat/completions"
            and item.get("model") == "fixture-model"
            and item.get("authorized") is True
            for item in requests
        ):
            raise HarnessError("provider fixture saw a request outside its deterministic contract", code="FIXTURE_REQUEST_CONTRACT_INVALID")
        if normalized_sources != {gateway_ip}:
            raise HarnessError("provider requests did not originate exclusively from the isolated LiteLLM gateway", code="FIXTURE_SOURCE_NOT_GATEWAY")
        if fixture.get("request_count") != len(requests):
            raise HarnessError("provider fixture request report exceeded its bounded record set", code="FIXTURE_REPORT_COUNT_MISMATCH")

        report.update(
            {
                "provider_fixture": "PASS",
                "provider_request_count": len(requests),
                "provider_request_sources": sorted(normalized_sources),
                "egress_probe_results": egress.get("results", []),
                "opencode_process": "PASS",
                "opencode_session_created": opencode["session_created"],
                "opencode_prompt_submitted": opencode["prompt_submitted"],
                "opencode_prompt_completed": opencode["prompt_completed"],
                "opencode_idle_event_seen": opencode.get("session_idle_event_seen", False),
                "fixture_response_seen": opencode["fixture_marker_seen"],
                "runsc_runtime": "runsc",
                "network_internal": True,
                "network_subnet": env["AIAT_LEVEL_A_SUBNET"],
                "host_ports_published": False,
                "persistent_volumes": False,
                "image_ids": image_ids,
            }
        )
    except Exception as exc:  # evidence contains only a stable error class
        failure = exc
        report["failure_class"] = type(exc).__name__
        if isinstance(exc, HarnessError):
            report["failure_code"] = exc.code
        elif isinstance(exc, subprocess.TimeoutExpired):
            report["failure_code"] = "COMMAND_TIMEOUT"
        elif isinstance(exc, OSError):
            report["failure_code"] = "LOCAL_COMMAND_ERROR"
        else:
            report["failure_code"] = "HARNESS_UNEXPECTED_ERROR"
        failure_stage = stage_override or stage
        report["failure_stage"] = failure_stage
    finally:
        if started:
            try:
                teardown = _compose(
                    project,
                    env,
                    "down",
                    "--volumes",
                    "--remove-orphans",
                    "--timeout",
                    "10",
                    timeout=40,
                    check=False,
                )
                cleanup_ok = teardown.returncode == 0
                remaining = _run(
                    ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"],
                    env=env,
                    timeout=10,
                    check=False,
                )
                cleanup_ok = cleanup_ok and not remaining.stdout.strip()
                for resource_type in ("network", "volume"):
                    leftovers = _run(
                        ["docker", resource_type, "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"],
                        env=env,
                        timeout=10,
                        check=False,
                    )
                    cleanup_ok = cleanup_ok and not leftovers.stdout.strip()
            except (HarnessError, subprocess.TimeoutExpired, OSError):
                cleanup_ok = False
            report["cleanup"] = "PASS" if cleanup_ok else "FAIL"
            if not cleanup_ok and failure is None:
                report["failure_stage"] = "cleanup"
                report["failure_code"] = "CLEANUP_INCOMPLETE"
        else:
            report["cleanup"] = "NOT_NEEDED"
        report["status"] = "PASS" if cleanup_ok and failure is None and report.get("opencode_process") == "PASS" else "FAIL"
        print(json.dumps(report, sort_keys=True, indent=2))
        if not cleanup_ok:
            report["cleanup_error"] = True
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-live", action="store_true", help="explicitly allow the disposable local Docker runtime smoke")
    parser.add_argument("--opencode-image", default=os.getenv("AIAT_LEVEL_A_OPENCODE_IMAGE"))
    parser.add_argument("--litellm-image", default=os.getenv("AIAT_LEVEL_A_LITELLM_IMAGE"))
    args = parser.parse_args(argv)
    if not args.run_live:
        parser.error("live runtime execution is opt-in; pass --run-live after reviewing its isolated resource scope")
    try:
        report = _run_live(args.opencode_image or "", args.litellm_image or "")
    except (HarnessError, subprocess.TimeoutExpired, OSError) as exc:
        if isinstance(exc, HarnessError):
            code = exc.code
        elif isinstance(exc, subprocess.TimeoutExpired):
            code = "COMMAND_TIMEOUT"
        else:
            code = "LOCAL_COMMAND_ERROR"
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "failure_class": type(exc).__name__,
                    "failure_stage": "harness.initialize",
                    "failure_code": code,
                    "cleanup": "NOT_NEEDED",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    return 0 if report.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
