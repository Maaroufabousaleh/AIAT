from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from mas_core.protocols.enums import AgentRole
from mas_core.protocols.tool import ToolRequest
from mas_tools_sdk.manifest import TOOL_MANIFEST, resolve_tool_name

DOCUMENTED_DEFAULT_TOOLS = {
    "project.create",
    "project.status",
    "project.transition",
    "project.list",
    "document.create_draft",
    "document.submit",
    "document.revise",
    "document.get_latest",
    "document.list",
    "document.ingest",
    "review.start_session",
    "review.submit",
    "review.aggregate",
    "review.submit_veto",
    "sprint.create",
    "sprint.activate",
    "issue.create",
    "issue.decompose",
    "issue.update_status",
    "issue.list",
    "kpi.compute",
    "kpi.compute_project",
    "kpi.query_history",
    "kpi.update_agent_profile",
    "blob.upload",
    "blob.download",
    "blob.list",
    "blob.delete",
    "command.run_safe",
    "file.patch",
    "repo.read",
    "repo.search",
    "web_search",
    "web_fetch",
    "security.scan",
    "test.run",
    "code.review",
    "iac.plan",
    "diagram.render",
    "mcp.invoke",
    "infra.provision",
    "cicd.configure",
    "monitoring.setup",
    "secrets.manage",
    "infra.ready_signal",
}


def test_documented_default_tools_are_in_static_manifest():
    missing = DOCUMENTED_DEFAULT_TOOLS - set(TOOL_MANIFEST)
    assert missing == set()


def test_documented_default_tools_are_registered(make_registry):
    registry = make_registry()
    missing = DOCUMENTED_DEFAULT_TOOLS - set(registry.tool_names)
    assert missing == set()


@pytest.mark.anyio
async def test_tools_endpoint_exposes_documented_defaults(client):
    response = await client.get("/tools")
    assert response.status_code == 200
    names = {tool["tool_name"] for tool in response.json()["tools"]}
    assert names >= DOCUMENTED_DEFAULT_TOOLS


def test_oss_compatibility_aliases_resolve_to_guarded_wrappers():
    assert resolve_tool_name("semgrep") == "security.scan"
    assert resolve_tool_name("skillspector") == "security.scan"
    assert resolve_tool_name("docling") == "document.ingest"
    assert resolve_tool_name("playwright.test") == "test.run"
    assert resolve_tool_name("opentofu.plan") == "iac.plan"


@pytest.mark.anyio
async def test_command_run_safe_rejects_unallowlisted_commands(
    make_registry, tmp_path, monkeypatch
):
    monkeypatch.setenv("TOOL_WORKSPACE_ROOT", str(tmp_path))
    registry = make_registry()
    request = ToolRequest(
        caller_id="worker-alpha",
        caller_role=AgentRole.WORKER,
        caller_team="dept_qa",
        tool_name="command.run_safe",
        kwargs={"command": "rm -rf ."},
    )

    response = await registry.execute(request)

    assert response.success is False
    assert response.error_code == "TOOL_ERROR"
    assert "allowlisted" in response.error


@pytest.mark.anyio
async def test_command_run_safe_fails_closed_without_sandbox(make_registry, tmp_path, monkeypatch):
    monkeypatch.setenv("TOOL_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("TOOL_SANDBOX_COMMAND", raising=False)
    response = await make_registry().execute(
        ToolRequest(
            caller_id="worker-alpha",
            caller_role=AgentRole.WORKER,
            caller_team="dept_qa",
            tool_name="command.run_safe",
            kwargs={"command": "pytest"},
        )
    )

    assert response.success is True
    assert response.result == {
        "available": False,
        "configured": False,
        "reason": "TOOL_SANDBOX_COMMAND_not_configured",
    }


@pytest.mark.anyio
async def test_command_run_safe_delegates_worker_command_to_gvisor_adapter(
    make_registry, tmp_path, monkeypatch
):
    captured = {}

    async def fake_run_process(argv, **kwargs):
        captured["adapter_argv"] = argv
        captured["payload"] = json.loads(kwargs["input_text"])
        return {
            "available": True,
            "returncode": 0,
            "stdout": json.dumps({"returncode": 0, "stdout": "ok", "stderr": ""}),
            "stderr": "",
        }

    monkeypatch.setenv("TOOL_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("TOOL_SANDBOX_COMMAND", "sandbox-runner --json-stdin")
    monkeypatch.setattr("tool_service.tools.adapters._run_process", fake_run_process)
    response = await make_registry().execute(
        ToolRequest(
            caller_id="worker-alpha",
            caller_role=AgentRole.WORKER,
            caller_team="dept_qa",
            tool_name="command.run_safe",
            kwargs={"command": ["pytest", "tests"]},
        )
    )

    assert response.success is True
    assert captured["adapter_argv"] == ["sandbox-runner", "--json-stdin"]
    assert captured["payload"]["argv"] == ["pytest", "tests"]
    assert captured["payload"]["profile"] == "gvisor"
    assert captured["payload"]["network_mode"] == "egress-deny-all"
    assert response.result["sandbox_profile"] == "gvisor"


@pytest.mark.anyio
async def test_run_process_retains_only_configured_output_limit(tmp_path):
    from tool_service.tools.adapters import _run_process

    result = await _run_process(
        [sys.executable, "-c", "import sys; sys.stdout.write('x' * 200000)"],
        cwd=tmp_path,
        max_output_bytes=1024,
    )

    assert result["returncode"] == 0
    assert len(result["stdout"].encode()) == 1024
    assert result["stdout_truncated"] is True


@pytest.mark.anyio
async def test_python_repo_search_skips_symlink_outside_workspace(
    make_registry, tmp_path, monkeypatch
):
    outside = tmp_path.parent / "outside-search.txt"
    outside.write_text("private needle", encoding="utf-8")
    link = tmp_path / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    monkeypatch.setenv("TOOL_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr("tool_service.tools.adapters.shutil.which", lambda _name: None)

    response = await make_registry().execute(
        ToolRequest(
            caller_id="worker-alpha",
            caller_role=AgentRole.WORKER,
            caller_team="dept_qa",
            tool_name="repo.search",
            kwargs={"query": "needle"},
        )
    )

    assert response.success is True
    assert response.result["matches"] == []


@pytest.mark.anyio
async def test_repo_read_rejects_path_traversal(make_registry, tmp_path, monkeypatch):
    monkeypatch.setenv("TOOL_WORKSPACE_ROOT", str(tmp_path))
    registry = make_registry()
    request = ToolRequest(
        caller_id="worker-alpha",
        caller_role=AgentRole.WORKER,
        caller_team="dept_qa",
        tool_name="repo.read",
        kwargs={"path": "../outside.txt"},
    )

    response = await registry.execute(request)

    assert response.success is False
    assert response.error_code == "TOOL_ERROR"
    assert "Path traversal" in response.error


@pytest.mark.anyio
async def test_file_patch_replaces_exact_text(make_registry, tmp_path, monkeypatch):
    monkeypatch.setenv("TOOL_WORKSPACE_ROOT", str(tmp_path))
    target = tmp_path / "app.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")
    registry = make_registry()

    response = await registry.execute(
        ToolRequest(
            caller_id="worker-alpha",
            caller_role=AgentRole.WORKER,
            caller_team="dept_qa",
            tool_name="file.patch",
            kwargs={"path": "app.txt", "find": "beta", "replace": "gamma"},
        )
    )

    assert response.success is True
    assert target.read_text(encoding="utf-8") == "alpha\ngamma\n"


@pytest.mark.anyio
async def test_security_scan_fails_closed_without_sandbox_adapter(
    make_registry, tmp_path, monkeypatch
):
    monkeypatch.setenv("TOOL_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("TOOL_SANDBOX_COMMAND", raising=False)
    registry = make_registry()

    response = await registry.execute(
        ToolRequest(
            caller_id="worker-alpha",
            caller_role=AgentRole.WORKER,
            caller_team="dept_qa",
            tool_name="security.scan",
            kwargs={"path": "."},
        )
    )

    assert response.success is True
    assert response.result["available"] is False
    assert response.result["configured"] is False
    assert response.result["reason"] == "TOOL_SANDBOX_COMMAND_not_configured"


@pytest.mark.anyio
async def test_security_scan_delegates_semgrep_to_gvisor_adapter(
    make_registry, tmp_path, monkeypatch
):
    captured = {}

    async def fake_run_sandboxed_process(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return {"available": True, "returncode": 0, "stdout": '{"results": []}'}

    monkeypatch.setenv("TOOL_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "tool_service.tools.adapters._run_sandboxed_process", fake_run_sandboxed_process
    )
    response = await make_registry().execute(
        ToolRequest(
            caller_id="worker-alpha",
            caller_role=AgentRole.WORKER,
            caller_team="office_cso",
            tool_name="security.scan",
            kwargs={"path": ".", "config": "auto"},
        )
    )

    assert response.success is True
    assert captured["argv"] == ["semgrep", "scan", "--json", "--config", "auto", "."]
    assert captured["cwd"] == tmp_path
    assert response.result["findings_count"] == 0


@pytest.mark.anyio
async def test_configured_infra_adapter_bounds_output_while_streaming(monkeypatch):
    from tool_service.tools.infra import _run_configured_adapter

    monkeypatch.setenv(
        "TEST_INFRA_ADAPTER_COMMAND",
        f'"{sys.executable}" -c "import sys; sys.stdin.read(); sys.stdout.write(\'x\' * 400000)"',
    )
    result = await _run_configured_adapter("TEST_INFRA_ADAPTER_COMMAND", {"key": "value"})

    assert result["returncode"] == 0
    assert len(result["stdout"].encode()) == 256_000
    assert result["stdout_truncated"] is True
    assert result["configured"] is True


@pytest.mark.anyio
async def test_document_ingest_falls_back_to_text_when_docling_missing(
    make_registry, tmp_path, monkeypatch
):
    monkeypatch.setenv("TOOL_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr("tool_service.tools.adapters.shutil.which", lambda _name: None)
    source = Path(tmp_path / "notes.md")
    source.write_text("# Notes\n\nBody", encoding="utf-8")
    registry = make_registry()

    response = await registry.execute(
        ToolRequest(
            caller_id="worker-alpha",
            caller_role=AgentRole.WORKER,
            caller_team="dept_qa",
            tool_name="document.ingest",
            kwargs={"path": "notes.md"},
        )
    )

    assert response.success is True
    assert response.result["available"] is False
    assert response.result["backend"] == "docling"
    assert "Body" in response.result["text"]


@pytest.mark.anyio
async def test_privileged_infra_adapter_fails_closed_when_unconfigured(make_registry, monkeypatch):
    monkeypatch.delenv("TOOL_INFRA_PROVISION_COMMAND", raising=False)
    registry = make_registry()

    response = await registry.execute(
        ToolRequest(
            caller_id="devops-pm",
            caller_role=AgentRole.ADMIN,
            caller_team="dept_devops",
            tool_name="infra.provision",
            kwargs={"resource": "preview-environment", "config": {"dry_run": True}},
        )
    )

    assert response.success is True
    assert response.result == {
        "available": False,
        "configured": False,
        "reason": "TOOL_INFRA_PROVISION_COMMAND_not_configured",
    }
