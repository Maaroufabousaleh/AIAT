"""Run the mandatory live OpenCode Phase 0B certification gates.

This program deliberately executes the runtime portion inside the canonical
orchestrator container.  Secrets therefore stay in the Compose secret
boundary and are never copied into command arguments, stdout, or evidence.
The only persisted payload is a compact, content-free gate summary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PINNED_BINARY_SHA256 = "f8c45bae73a8f1e2088023fdd34dc2fe0a7f93f505f073e0703e4e1a19afe8ff"
PINNED_OPENAPI_SHA256 = "03d773f1ff66b1c2dc0b000fc541fb6955a8cb924cc5816dc6d0e67c31974a78"
EXPECTED_MIGRATION_HEAD = "0019_block_universal_contract"
SENSITIVE = re.compile(
    r"(?:password|secret|token|authorization|cookie|api[_-]?key|private[_-]?key|credential)",
    re.I,
)


def _run(argv: list[str], *, timeout: int = 120, input_text: str | None = None) -> str:
    completed = subprocess.run(
        argv,
        input=input_text,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if completed.returncode:
        detail = completed.stderr.strip().splitlines()[-1:] or ["no diagnostic"]
        raise RuntimeError(f"command failed ({argv[0]}): {detail[0]}")
    return completed.stdout.strip()


def _compose(compose_dir: Path, *args: str, timeout: int = 120) -> str:
    return _run(
        [
            "docker",
            "compose",
            "--env-file",
            "../../.env",
            "-f",
            "docker-compose.yml",
            *args,
        ],
        timeout=timeout,
    )


def _container_id(compose_dir: Path, service: str) -> str:
    previous = Path.cwd()
    try:
        import os

        os.chdir(compose_dir)
        identifier = _compose(compose_dir, "ps", "-q", service)
    finally:
        os.chdir(previous)
    if not identifier:
        raise RuntimeError(f"canonical Compose service {service!r} is not running")
    return identifier.splitlines()[-1]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sanitize(value: Any, key: str = "") -> Any:
    if SENSITIVE.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _sanitize(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(item, key) for item in value]
    if isinstance(value, str) and re.search(r"(?i)\b(?:bearer|basic)\s+[a-z0-9+/_.=-]+", value):
        return "[REDACTED]"
    return value


def _assert_sanitized(value: Any) -> None:
    encoded = json.dumps(value, sort_keys=True)
    forbidden = [
        r"(?i)authorization",
        r"(?i)bearer\s+",
        r"(?i)basic\s+[a-z0-9+/=]{8,}",
        r"(?i)(?:password|secret|token|api[_-]?key)\s*[=:]",
        r"X-AIAT-OpenCode-Grant",
        r"VALUE\s*=|def\s+add\(",
    ]
    if any(re.search(pattern, encoded) for pattern in forbidden):
        raise RuntimeError("sanitized evidence contains a forbidden secret or workspace-content pattern")


def _write_json(path: Path, value: Any) -> None:
    """Write cross-platform deterministic JSON without leaking runtime payloads."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


RUNTIME_CERTIFIER = r'''
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import httpx

from mas_core.worker_contract.opencode_bridge import issue_opencode_tool_grant

BASE = "http://opencode-runtime:4096"
TOOL_BASE = "http://tool-service:8002"
USERNAME = os.getenv("OPENCODE_SERVER_USERNAME", "opencode")
PASSWORD = os.environ["OPENCODE_SERVER_PASSWORD"]
TOOL_SECRET = os.environ["TOOL_SECRET"]
AUTH = httpx.BasicAuth(USERNAME, PASSWORD)
MODEL = {"providerID": "aiat", "modelID": "omniroute-coding"}


def sanitized_openapi_hash(document):
    sensitive = re.compile(r"(?:password|secret|token|authorization|cookie|api[_-]?key|private[_-]?key)", re.I)
    def clean(value, key=""):
        if sensitive.search(key):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {str(k): clean(v, str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [clean(item, key) for item in value]
        return value
    canonical = json.dumps(clean(document), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def session_ids(value):
    found = set()
    def walk(item):
        if isinstance(item, dict):
            for key, nested in item.items():
                if key in {"sessionID", "sessionId", "session_id"} and isinstance(nested, str):
                    found.add(nested)
                walk(nested)
        elif isinstance(item, list):
            for nested in item:
                walk(nested)
    walk(value)
    return found


def tool_records(value):
    records = []
    def walk(item):
        if isinstance(item, dict):
            if item.get("type") == "tool" or "tool" in item or "toolName" in item:
                name = item.get("tool") or item.get("toolName") or item.get("name")
                state = item.get("state") if isinstance(item.get("state"), dict) else {}
                status = state.get("status") or item.get("status")
                output = state.get("output") or state.get("error") or item.get("output") or item.get("error") or ""
                input_value = state.get("input") or item.get("input") or {}
                records.append({"name": str(name or ""), "status": str(status or ""), "output": str(output), "input": input_value})
            for nested in item.values():
                walk(nested)
        elif isinstance(item, list):
            for nested in item:
                walk(nested)
    walk(value)
    unique = []
    seen = set()
    for record in records:
        marker = (
            record["name"],
            record["status"],
            json.dumps(record["input"], sort_keys=True, default=str),
            record["output"][:120],
        )
        if marker not in seen:
            seen.add(marker)
            unique.append(record)
    return unique


async def prompt_with_events(client, session_id, directory, text):
    events = []
    idle = asyncio.Event()
    async def consume():
        try:
            async with client.stream("GET", "/global/event", headers={"Accept": "text/event-stream"}) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    payload = event.get("payload") if isinstance(event, dict) else None
                    event_type = payload.get("type") if isinstance(payload, dict) else None
                    ids = session_ids(event)
                    if session_id in ids or event_type == "server.connected":
                        events.append(str(event_type or "unknown"))
                    if session_id in ids and event_type in {"session.idle", "session.status"}:
                        properties = payload.get("properties") if isinstance(payload, dict) else {}
                        status = properties.get("status") if isinstance(properties, dict) else None
                        if event_type == "session.idle" or (isinstance(status, dict) and status.get("type") == "idle"):
                            idle.set()
                            return
        except asyncio.CancelledError:
            pass
    task = asyncio.create_task(consume())
    await asyncio.sleep(0.25)
    response = await client.post(
        f"/session/{session_id}/message",
        params={"directory": directory},
        json={"model": MODEL, "parts": [{"type": "text", "text": text}]},
        timeout=180,
    )
    response.raise_for_status()
    try:
        await asyncio.wait_for(idle.wait(), timeout=10)
    except TimeoutError:
        pass
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return response.json(), events


async def add_bridge(client, run_id, tools):
    grant = issue_opencode_tool_grant(
        TOOL_SECRET,
        worker_id="opencode-phase0b-certifier",
        run_id=run_id,
        project_id=None,
        tool_names=tools,
        ttl_seconds=600,
    )
    name = f"aiat-{run_id.hex}"
    response = await client.post(
        "/mcp",
        params={"directory": "/workspace"},
        json={"name": name, "config": {"type": "remote", "url": f"{TOOL_BASE}/opencode/mcp", "headers": {"X-AIAT-OpenCode-Grant": grant}, "oauth": False, "enabled": True, "timeout": 60_000}},
    )
    response.raise_for_status()
    for _ in range(60):
        status = await client.get("/mcp", params={"directory": "/workspace"})
        status.raise_for_status()
        state = status.json().get(name, {})
        if state.get("status") == "connected":
            return name, grant
        await asyncio.sleep(0.25)
    raise RuntimeError("run-scoped MCP bridge did not connect")


async def make_session(client, name, title):
    response = await client.post(
        "/session",
        params={"directory": "/workspace"},
        json={
            "title": title,
            "permission": [
                {"permission": "*", "pattern": "*", "action": "deny"},
                {"permission": f"{name}_aiat_tool", "pattern": "*", "action": "allow"},
            ],
        },
    )
    response.raise_for_status()
    return response.json()["id"]


async def main():
    run_id = uuid4()
    run_root = Path("/workspace") / str(run_id)
    evidence = {"run_id": str(run_id)}
    async with httpx.AsyncClient(base_url=BASE, auth=AUTH, follow_redirects=False, timeout=30) as client:
        auth = {
            "unauthenticated_health": (await httpx.AsyncClient().get(f"{BASE}/global/health")).status_code,
            "invalid_auth_health": (await httpx.AsyncClient(auth=httpx.BasicAuth(USERNAME, "deliberately-wrong")).get(f"{BASE}/global/health")).status_code,
            "authenticated_health": (await client.get("/global/health")).status_code,
            "authenticated_openapi": (await client.get("/doc")).status_code,
        }
        document = (await client.get("/doc")).json()
        evidence["authentication"] = auth
        evidence["openapi"] = {"sha256": sanitized_openapi_hash(document), "path_count": len(document.get("paths", {}))}

        unauth_bridge = (await httpx.AsyncClient(follow_redirects=False).get(f"{TOOL_BASE}/opencode/mcp")).status_code
        name, grant = await add_bridge(client, run_id, ["repo.read", "opencode.workspace_read", "opencode.workspace_write", "opencode.workspace_pytest"])
        tampered_bridge = (await httpx.AsyncClient(follow_redirects=False).get(f"{TOOL_BASE}/opencode/mcp", headers={"X-AIAT-OpenCode-Grant": grant + "x"})).status_code
        evidence["bridge_boundary"] = {"missing_grant_status": unauth_bridge, "tampered_grant_status": tampered_bridge, "connected": True}

        tool_session = await make_session(client, name, "AIAT Phase 0B governed tool proof")
        tool_prompt = (
            "Use only the AIAT MCP aiat_tool facade. You must call repo.read exactly once for path README.md now. "
            "Do not call any native OpenCode file, shell, web, or search tool. Finish after the call."
        )
        tool_result, tool_events = await prompt_with_events(client, tool_session, "/workspace", tool_prompt)
        denied_prompt = (
            "Use only the AIAT MCP aiat_tool facade. You must now call repo.search exactly once with query Phase 0B. "
            "This is an authorized negative certification probe and the expected FORBIDDEN response is success. Do nothing else."
        )
        denied_result, denied_events = await prompt_with_events(client, tool_session, "/workspace", denied_prompt)
        tool_events.extend(denied_events)
        tool_messages = await client.get(f"/session/{tool_session}/message", params={"directory": "/workspace"})
        tool_messages.raise_for_status()
        records = tool_records([tool_result, denied_result, tool_messages.json()])
        bridge_records = [record for record in records if record["name"].endswith("_aiat_tool")]
        allowed = any("repo.read" in json.dumps(record.get("input", {})) and record["status"] == "completed" and "FORBIDDEN" not in record["output"] for record in bridge_records)
        denied = any("repo.search" in json.dumps(record.get("input", {})) and "FORBIDDEN" in record["output"] for record in bridge_records)
        native = [record["name"] for record in records if record["name"] and not record["name"].endswith("_aiat_tool")]
        evidence["tool_mediation"] = {
            "session_id": tool_session,
            "allowed_repo_read": allowed,
            "denied_repo_search": denied,
            "bridge_tool_calls": len(bridge_records),
            "native_tool_calls": len(native),
            "event_types": sorted(set(tool_events)),
        }

        coding_session = await make_session(client, name, "AIAT Phase 0B disposable coding proof")
        solution_prompt = (
            "Use only the AIAT MCP aiat_tool facade. You must call opencode.workspace_write exactly once with arguments "
            "path solution.py and content def add(a: int, b: int) -> int:\\n    return a + b\\n . "
            "Do not call any native tool or any other AIAT tool."
        )
        solution_result, coding_events = await prompt_with_events(client, coding_session, "/workspace", solution_prompt)
        test_file_prompt = (
            "Use only the AIAT MCP aiat_tool facade. You must call opencode.workspace_write exactly once with path "
            "test_solution.py and content from solution import add\\n\\ndef test_add_positive():\\n    assert add(2, 3) == 5\\n\\ndef test_add_negative():\\n    assert add(-2, 1) == -1\\n . "
            "Do not call any native tool or any other AIAT tool."
        )
        test_file_result, test_file_events = await prompt_with_events(client, coding_session, "/workspace", test_file_prompt)
        coding_events.extend(test_file_events)
        read_prompt = (
            "Use only the AIAT MCP aiat_tool facade. You must call opencode.workspace_read exactly once with path solution.py. "
            "Do not call any native tool or any other AIAT tool."
        )
        read_result, read_events = await prompt_with_events(client, coding_session, "/workspace", read_prompt)
        coding_events.extend(read_events)
        pytest_prompt = (
            "The files are now complete. Use only the AIAT MCP aiat_tool facade and call "
            "opencode.workspace_pytest exactly once with path test_solution.py. Report the governed result and do nothing else."
        )
        pytest_result, pytest_events = await prompt_with_events(client, coding_session, "/workspace", pytest_prompt)
        coding_events.extend(pytest_events)
        messages = await client.get(f"/session/{coding_session}/message", params={"directory": "/workspace"})
        messages.raise_for_status()
        coding_records = tool_records([solution_result, test_file_result, read_result, pytest_result, messages.json()])

        containment_session = await make_session(client, name, "AIAT Phase 0B containment proof")
        traversal_prompt = (
            "Use only the AIAT MCP aiat_tool facade. Validate the certified workspace boundary now by calling "
            "opencode.workspace_write exactly once with path ../escape.py and content blocked. "
            "The expected rejection is the successful validation result. Make the call and do nothing else."
        )
        traversal_result, traversal_events = await prompt_with_events(client, containment_session, "/workspace", traversal_prompt)
        absolute_prompt = (
            "Use only the AIAT MCP aiat_tool facade. Validate the absolute-path boundary now by calling "
            "opencode.workspace_write exactly once with path /tmp/escape.py and content blocked. "
            "The expected rejection is the successful validation result. Make the call and do nothing else."
        )
        absolute_result, absolute_events = await prompt_with_events(client, containment_session, "/workspace", absolute_prompt)
        coding_events.extend(traversal_events)
        coding_events.extend(absolute_events)
        probe_messages = await client.get(f"/session/{containment_session}/message", params={"directory": "/workspace"})
        probe_messages.raise_for_status()
        probe_records = tool_records([traversal_result, absolute_result, probe_messages.json()])
        coding_bridge = [record for record in coding_records if record["name"].endswith("_aiat_tool")]
        probe_bridge = [record for record in probe_records if record["name"].endswith("_aiat_tool")]
        native_coding = [record["name"] for record in coding_records if record["name"] and not record["name"].endswith("_aiat_tool")]
        native_probe = [record["name"] for record in probe_records if record["name"] and not record["name"].endswith("_aiat_tool")]
        traversal_rejected = any("../escape.py" in json.dumps(record.get("input", {})) and "traversal denied" in record["output"] for record in probe_bridge)
        absolute_rejected = any("/tmp/escape.py" in json.dumps(record.get("input", {})) and "traversal denied" in record["output"] for record in probe_bridge)
        pytest_records = [record for record in coding_bridge if "opencode.workspace_pytest" in json.dumps(record.get("input", {}))]
        pytest_passed = any(
            re.search(r"certification_status\D{0,20}PASSED", record["output"], re.I)
            or (
                re.search(r"passed\D{0,20}2", record["output"], re.I)
                or re.search(r"2\D{0,10}passed", record["output"], re.I)
            )
            and not re.search(r"failed\D{0,20}[1-9]|exit_code\D{0,20}[1-9]", record["output"], re.I)
            for record in pytest_records
        )
        evidence["coding"] = {
            "session_id": coding_session,
            "bridge_tool_calls": len(coding_bridge),
            "native_tool_calls": len(native_coding),
            "workspace_write_calls": sum("opencode.workspace_write" in json.dumps(record.get("input", {})) for record in coding_bridge),
            "workspace_read_calls": sum("opencode.workspace_read" in json.dumps(record.get("input", {})) for record in coding_bridge),
            "workspace_pytest_calls": len(pytest_records),
            "pytest_passed": 2 if pytest_passed else 0,
            "pytest_exit_code": 0 if pytest_passed else 1,
            "traversal_probe_rejected": traversal_rejected,
            "absolute_path_probe_rejected": absolute_rejected,
            "containment_bridge_tool_calls": len(probe_bridge),
            "event_types": sorted(set(coding_events)),
        }

        evidence["coding"]["message_count"] = len(messages.json()) if isinstance(messages.json(), list) else 0
        for session_id in (tool_session, coding_session, containment_session):
            deleted = await client.delete(f"/session/{session_id}", params={"directory": "/workspace"})
            deleted.raise_for_status()

    required = [run_root / "solution.py", run_root / "test_solution.py"]
    if not all(path.is_file() for path in required):
        diagnostic = []
        for record in coding_records:
            raw_input = record.get("input", {})
            requested = raw_input.get("tool_name") if isinstance(raw_input, dict) else None
            diagnostic.append({
                "name": record.get("name"),
                "status": record.get("status"),
                "requested_tool": requested,
                "forbidden": "FORBIDDEN" in record.get("output", ""),
                "traversal_denied": "traversal denied" in record.get("output", ""),
            })
        shutil.rmtree(run_root, ignore_errors=True)
        raise RuntimeError("OpenCode did not create both required artifacts: " + json.dumps(diagnostic, sort_keys=True))
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in required}
    evidence["coding"].update({
        "artifact_count": len(required),
        "artifact_sha256": hashes,
    })
    expected = run_root.resolve()
    evidence["containment"] = {
        "run_directory_is_uuid": run_root.name == str(run_id),
        "artifacts_within_run_directory": all(path.resolve().is_relative_to(expected) for path in required),
        "absolute_path_rejected": evidence["coding"]["absolute_path_probe_rejected"],
        "traversal_rejected": evidence["coding"]["traversal_probe_rejected"],
        "native_tools_denied": evidence["tool_mediation"]["native_tool_calls"] == 0 and evidence["coding"]["native_tool_calls"] == 0 and not native_probe,
    }
    shutil.rmtree(run_root)
    evidence["cleanup"] = {"workspace_removed": not run_root.exists()}
    print(json.dumps(evidence, sort_keys=True))


asyncio.run(main())
'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("mas/docs/opencode/phase0b/1.17.13/live-certification-evidence.json"),
    )
    parser.add_argument(
        "--binary",
        type=Path,
        default=Path(r"C:\Users\Maaro\AppData\Roaming\npm\node_modules\opencode-ai\bin\opencode.exe"),
    )
    args = parser.parse_args()
    repo_root = args.repo_root.resolve()
    compose_dir = repo_root / "mas" / "infra" / "compose"
    if not args.binary.is_file():
        raise SystemExit("the pinned local OpenCode binary does not exist")

    previous = Path.cwd()
    try:
        import os

        os.chdir(compose_dir)
        _compose(compose_dir, "config", "--quiet")
        service_ids = {
            name: _container_id(compose_dir, name)
            for name in ("postgres", "tool-service", "opencode-runtime", "orchestrator-api")
        }
    finally:
        os.chdir(previous)

    health: dict[str, str] = {}
    for name, identifier in service_ids.items():
        state = json.loads(_run(["docker", "inspect", identifier]))[0]["State"]
        health[name] = str((state.get("Health") or {}).get("Status") or state.get("Status"))
        if health[name] not in {"healthy", "running"}:
            raise RuntimeError(f"required service {name} is not healthy")

    runtime = json.loads(
        _run(["docker", "exec", service_ids["orchestrator-api"], "python", "-c", RUNTIME_CERTIFIER], timeout=420)
    )
    binary_hash = _sha256_file(args.binary)
    running_image_id = _run(
        ["docker", "inspect", service_ids["opencode-runtime"], "--format", "{{.Image}}"]
    )
    tagged_image_id = _run(
        ["docker", "image", "inspect", "mas/opencode-runtime:1.17.13", "--format", "{{.Id}}"]
    )

    source_head = _run(
        ["docker", "exec", service_ids["orchestrator-api"], "alembic", "heads"], timeout=30
    ).split()[0]
    live_head = _run(
        ["docker", "exec", service_ids["orchestrator-api"], "alembic", "current"], timeout=30
    ).split()[0]
    database = json.loads(
        _run(
            [
                "docker",
                "exec",
                service_ids["orchestrator-api"],
                "python",
                "-c",
                (
                    "import asyncio,json,os,asyncpg\n"
                    "async def m():\n"
                    " c=await asyncpg.connect(os.environ['PGBOUNCER_DSN'].replace('postgresql+asyncpg://','postgresql://'));\n"
                    " p=await c.fetchrow(\"SELECT p.logical_profile_id,p.status,v.version,v.exact_model_id,v.status AS version_status FROM model_profiles p JOIN model_profile_versions v ON v.profile_id=p.id WHERE p.logical_profile_id='opencode-phase0b-coding' ORDER BY v.created_at DESC LIMIT 1\");\n"
                    " u=await c.fetchval(\"SELECT COUNT(*) FROM worker_registry WHERE status='ACTIVE' AND source_repo IS NOT NULL AND source_repo <> 'local' AND (version_pin IS NULL OR active_adapter_id IS NULL OR active_skill_bundle_id IS NULL)\");\n"
                    " await c.close(); print(json.dumps({'model_profile_id':str(p['logical_profile_id']) if p else None,'model_status':str(p['status']) if p else None,'model_version':str(p['version']) if p else None,'model_exact_id':str(p['exact_model_id']) if p else None,'model_version_status':str(p['version_status']) if p else None,'unsafe_active_external_workers':int(u)}))\n"
                    "asyncio.run(m())"
                ),
            ],
            timeout=30,
        )
    )

    gates = {
        "services_healthy": all(value in {"healthy", "running"} for value in health.values()),
        "migration_head": source_head == live_head == EXPECTED_MIGRATION_HEAD,
        "model_governance": database.get("model_status") == "approved"
        and database.get("model_version_status") == "approved"
        and database.get("model_exact_id") == "aiat/omniroute-coding",
        "unsafe_worker_count": database.get("unsafe_active_external_workers") == 0,
        "binary_pin": binary_hash == PINNED_BINARY_SHA256,
        "image_pin": running_image_id == tagged_image_id,
        "openapi_pin": runtime["openapi"]["sha256"] == PINNED_OPENAPI_SHA256,
        "authentication": runtime["authentication"] == {
            "unauthenticated_health": 401,
            "invalid_auth_health": 401,
            "authenticated_health": 200,
            "authenticated_openapi": 200,
        },
        "allowed_tool": runtime["tool_mediation"]["allowed_repo_read"],
        "denied_tool": runtime["tool_mediation"]["denied_repo_search"],
        "bridge_boundary": runtime["bridge_boundary"]["missing_grant_status"] == 403
        and runtime["bridge_boundary"]["tampered_grant_status"] == 403,
        "native_tool_bypass": runtime["containment"]["native_tools_denied"],
        "coding": runtime["coding"]["pytest_exit_code"] == 0
        and runtime["coding"]["pytest_passed"] >= 2
        and runtime["coding"]["workspace_write_calls"] >= 2
        and runtime["coding"]["workspace_read_calls"] >= 1,
        "containment": runtime["containment"]["run_directory_is_uuid"]
        and runtime["containment"]["artifacts_within_run_directory"]
        and runtime["containment"]["absolute_path_rejected"]
        and runtime["containment"]["traversal_rejected"],
        "cleanup": runtime["cleanup"]["workspace_removed"],
        "event_evidence": bool(runtime["tool_mediation"]["event_types"])
        and bool(runtime["coding"]["event_types"]),
    }
    if not all(gates.values()):
        failed = sorted(name for name, passed in gates.items() if not passed)
        diagnostic = {
            "tool_mediation": runtime.get("tool_mediation"),
            "coding": runtime.get("coding"),
            "containment": runtime.get("containment"),
        }
        raise RuntimeError(
            f"mandatory Phase 0B gates failed: {', '.join(failed)}; "
            + json.dumps(diagnostic, sort_keys=True)
        )

    evidence = _sanitize(
        {
            "schema_version": "1.0",
            "release": "1.17.13",
            "captured_at": datetime.now(UTC).isoformat(),
            "services": health,
            "migration": {"source_head": source_head, "live_database_head": live_head},
            "database": database,
            "pins": {
                "binary_sha256": binary_hash,
                "running_image_digest": running_image_id,
                "tagged_image_digest": tagged_image_id,
                "openapi_sha256": runtime["openapi"]["sha256"],
                "openapi_path_count": runtime["openapi"]["path_count"],
            },
            "authentication": runtime["authentication"],
            "bridge_boundary": runtime["bridge_boundary"],
            "tool_mediation": runtime["tool_mediation"],
            "coding": runtime["coding"],
            "containment": runtime["containment"],
            "cleanup": runtime["cleanup"],
            "gates": gates,
            "sensitive_values_persisted": False,
            "workspace_contents_persisted": False,
        }
    )
    # Commit stable compatibility evidence, not raw prompts, headers,
    # messages, tool arguments, or workspace files.  These summaries prove
    # the event/request shapes seen by the live gate while keeping its
    # disposable coding workspace and secret boundary non-persistent.
    evidence_root = args.output.parent
    event_fixture = evidence_root / "event-fixtures" / "live-event-summary.json"
    interface_fixture = evidence_root / "request-response-fixtures" / "live-interface-summary.json"
    _write_json(
        event_fixture,
        _sanitize(
            {
                "schema_version": "1.0",
                "release": "1.17.13",
                "capture_kind": "normalized_live_event_summary",
                "event_types": {
                    "tool_mediation": sorted(set(runtime["tool_mediation"]["event_types"])),
                    "coding": sorted(set(runtime["coding"]["event_types"])),
                },
                "payload_content_persisted": False,
                "headers_persisted": False,
            }
        ),
    )
    _write_json(
        interface_fixture,
        _sanitize(
            {
                "schema_version": "1.0",
                "release": "1.17.13",
                "capture_kind": "sanitized_live_request_response_summary",
                "operations": {
                    "health": {"authenticated_status": runtime["authentication"]["authenticated_health"]},
                    "openapi": {"authenticated_status": runtime["authentication"]["authenticated_openapi"]},
                    "mcp_bridge": {
                        "missing_grant_status": runtime["bridge_boundary"]["missing_grant_status"],
                        "tampered_grant_status": runtime["bridge_boundary"]["tampered_grant_status"],
                    },
                    "workspace_pytest": {
                        "exit_code": runtime["coding"]["pytest_exit_code"],
                        "passed": runtime["coding"]["pytest_passed"],
                    },
                },
                "request_bodies_persisted": False,
                "response_bodies_persisted": False,
                "headers_persisted": False,
            }
        ),
    )
    evidence["fixtures"] = {
        "refs": [
            "event-fixtures/live-event-summary.json",
            "request-response-fixtures/live-interface-summary.json",
        ],
        "sha256": {
            "event-fixtures/live-event-summary.json": _sha256_file(event_fixture),
            "request-response-fixtures/live-interface-summary.json": _sha256_file(interface_fixture),
        },
    }
    _assert_sanitized(evidence)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Evidence is committed cross-platform; write exact LF bytes so Windows
    # newline translation cannot turn every JSON line into trailing whitespace
    # under Git's repository normalization rules.
    _write_json(args.output, evidence)
    print(json.dumps({"all_gates_passed": True, "gate_count": len(gates), "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
