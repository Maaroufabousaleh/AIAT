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
    "project.repository",
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
    "privileged_ops.request",
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

MUTABLE_READ_TOOLS = {
    "blob.download",
    "blob.list",
    "capability.list_workers",
    "capability.search",
    "document.get_latest",
    "document.ingest",
    "document.list",
    "file_read",
    "flow.list",
    "flow.recommend",
    "flow.status",
    "issue.list",
    "kpi.compute",
    "kpi.compute_project",
    "kpi.query_history",
    "project.list",
    "project.status",
    "repo.read",
    "repo.search",
    "shared_memory_read",
    "velocity.report",
}


def test_documented_default_tools_are_in_static_manifest():
    missing = DOCUMENTED_DEFAULT_TOOLS - set(TOOL_MANIFEST)
    assert missing == set()


def test_documented_default_tools_are_registered(make_registry):
    registry = make_registry()
    missing = DOCUMENTED_DEFAULT_TOOLS - set(registry.tool_names)
    assert missing == set()


def test_mutable_reads_are_not_cached_without_invalidation(make_registry):
    manifest = {entry["tool_name"]: entry for entry in make_registry().get_manifest()}
    assert manifest.keys() >= MUTABLE_READ_TOOLS
    assert {
        name: manifest[name]["cache_ttl_seconds"]
        for name in MUTABLE_READ_TOOLS
        if manifest[name]["cache_ttl_seconds"] != 0
    } == {}


def test_manifest_reports_unconfigured_adapters_as_unavailable(make_registry, monkeypatch):
    monkeypatch.delenv("TOOL_SANDBOX_COMMAND", raising=False)
    monkeypatch.delenv("TOOL_CODE_REVIEW_COMMAND", raising=False)
    manifest = {entry["tool_name"]: entry for entry in make_registry().get_manifest()}

    assert manifest["security.scan"]["available"] is False
    assert manifest["security.scan"]["configured"] is False
    assert manifest["code.review"]["available"] is True
    assert manifest["code.review"]["backend"] == "aiat_deterministic_diff_review"
    assert manifest["code.review"]["configured"] is False
    assert manifest["document.ingest"]["available"] is True


@pytest.mark.anyio
async def test_tools_endpoint_exposes_documented_defaults(client):
    response = await client.get("/tools")
    assert response.status_code == 200
    names = {tool["tool_name"] for tool in response.json()["tools"]}
    assert names >= DOCUMENTED_DEFAULT_TOOLS


def test_oss_compatibility_aliases_resolve_to_guarded_wrappers():
    assert resolve_tool_name("semgrep") == "security.scan"
    assert resolve_tool_name("skillspector") == "security.scan"
    assert resolve_tool_name("trufflehog") == "security.scan"
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
    assert captured["argv"][:2] == ["sh", "-lc"]
    assert "cp -R . /tmp/aiat-semgrep-src/" in captured["argv"][2]
    assert "semgrep scan --json --metrics=off --disable-version-check" in captured["argv"][2]
    assert "--no-git-ignore --include=*.py --exclude=.git" in captured["argv"][2]
    assert "/workspace/mas/apps/tool-service/tool_service/semgrep-default.yml" in captured["argv"][2]
    assert captured["cwd"] == tmp_path
    assert response.result["findings_count"] == 0


@pytest.mark.anyio
async def test_trufflehog_alias_delegates_to_the_shared_bounded_adapter(
    make_registry, tmp_path, monkeypatch
):
    captured = {}

    async def fake_run_sandboxed_process(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return {"available": True, "returncode": 0, "stdout": '{"path":"secret"}\n'}

    monkeypatch.setenv("TOOL_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "tool_service.tools.adapters._run_sandboxed_process", fake_run_sandboxed_process
    )
    response = await make_registry().execute(
        ToolRequest(
            caller_id="worker-alpha",
            caller_role=AgentRole.WORKER,
            caller_team="office_cso",
            tool_name="trufflehog",
            kwargs={"path": "."},
        )
    )

    assert response.success is True
    assert "trufflehog filesystem --json ." in captured["argv"][2]
    assert response.result["backend"] == "trufflehog"
    assert response.result["scanner"] == "trufflehog"
    assert response.result["findings_count"] == 1


@pytest.mark.anyio
async def test_skillspector_alias_delegates_to_the_shared_bounded_adapter(
    make_registry, tmp_path, monkeypatch
):
    captured = {}

    async def fake_run_sandboxed_process(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return {"available": True, "returncode": 0, "stdout": '{"findings": [{"rule": "fixture"}]}' }

    monkeypatch.setenv("TOOL_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("TOOL_SKILLSPECTOR_COMMAND", "skillspector scan --json .")
    monkeypatch.setattr(
        "tool_service.tools.adapters._run_sandboxed_process", fake_run_sandboxed_process
    )
    response = await make_registry().execute(
        ToolRequest(
            caller_id="worker-alpha",
            caller_role=AgentRole.WORKER,
            caller_team="office_cso",
            tool_name="skillspector",
            kwargs={"path": "."},
        )
    )

    assert response.success is True
    assert captured["argv"] == ["skillspector", "scan", "--json", "."]
    assert response.result["backend"] == "skillspector"
    assert response.result["scanner"] == "skillspector"
    assert response.result["findings_count"] == 1
    assert response.result["command_configured"] is True


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
    assert response.result["available"] is True
    assert response.result["configured"] is True
    assert response.result["degraded"] is True
    assert response.result["backend"] == "plain_text_fallback"
    assert response.result["reason"] == "docling_binary_not_found"
    assert "Body" in response.result["text"]


@pytest.mark.anyio
async def test_document_ingest_uses_docling_runner_when_installed(
    make_registry, tmp_path, monkeypatch
):
    source = tmp_path / "notes.md"
    source.write_text("# Notes", encoding="utf-8")
    monkeypatch.setenv("TOOL_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "tool_service.tools.adapters.shutil.which",
        lambda name: "/usr/local/bin/docling" if name == "docling" else None,
    )

    async def fake_run_process(argv, **kwargs):
        assert argv[1:3] == ["-m", "tool_service.docling_runner"]
        return {
            "available": True,
            "returncode": 0,
            "stdout": json.dumps({"document": {"name": "notes"}, "text": "# Notes"}),
            "stderr": "",
        }

    monkeypatch.setattr("tool_service.tools.adapters._run_process", fake_run_process)
    response = await make_registry().execute(
        ToolRequest(
            caller_id="writer",
            caller_role=AgentRole.WORKER,
            caller_team="dept_system",
            tool_name="document.ingest",
            kwargs={"path": "notes.md"},
        )
    )

    assert response.success is True
    assert response.result["backend"] == "docling"
    assert response.result["document"]["text"] == "# Notes"


@pytest.mark.anyio
async def test_cicd_configure_writes_real_github_actions_workflow(
    make_registry, tmp_path, monkeypatch
):
    monkeypatch.setenv("TOOL_WORKSPACE_ROOT", str(tmp_path))
    response = await make_registry().execute(
        ToolRequest(
            caller_id="devops-pm",
            caller_role=AgentRole.ADMIN,
            caller_team="dept_devops",
            tool_name="cicd.configure",
            kwargs={
                "pipeline": "ci",
                "config": {
                    "workflow": {
                        "name": "CI",
                        "on": ["push"],
                        "jobs": {"test": {"runs-on": "ubuntu-latest", "steps": []}},
                    }
                },
            },
        )
    )

    workflow = tmp_path / ".github" / "workflows" / "ci.yml"
    assert response.success is True
    assert response.result["backend"] == "github_actions"
    assert workflow.is_file()
    assert "ubuntu-latest" in workflow.read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_secrets_manage_uses_encrypted_credentials_boundary(make_registry, monkeypatch):
    calls = []

    async def fake_post(path, body=None):
        calls.append(("post", path, body))
        return {"name": body["name"], "secret_type": body["secret_type"]}

    async def fake_patch(path, body=None):
        calls.append(("patch", path, body))
        return {"name": path.rsplit("/", 1)[-1]}

    async def fake_delete(path):
        calls.append(("delete", path, None))
        return {}

    monkeypatch.setattr("tool_service.tools.infra.orch_post", fake_post)
    monkeypatch.setattr("tool_service.tools.infra.orch_patch", fake_patch)
    monkeypatch.setattr("tool_service.tools.infra.orch_delete", fake_delete)
    registry = make_registry()
    base = {
        "caller_id": "devops-pm",
        "caller_role": AgentRole.ADMIN,
        "caller_team": "dept_devops",
        "tool_name": "secrets.manage",
    }

    for kwargs in (
        {"action": "create", "name": "audit", "value": "one"},
        {"action": "rotate", "name": "audit", "value": "two"},
        {"action": "revoke", "name": "audit"},
    ):
        response = await registry.execute(ToolRequest(**base, kwargs=kwargs))
        assert response.success is True

    assert [call[:2] for call in calls] == [
        ("post", "/credentials"),
        ("patch", "/credentials/audit"),
        ("delete", "/credentials/audit"),
    ]


@pytest.mark.anyio
async def test_infra_ready_signal_transitions_real_project(make_registry, monkeypatch):
    captured = {}

    async def fake_post(path, body=None):
        captured.update(path=path, body=body)
        return {"next_state": "IN_PROGRESS"}

    monkeypatch.setattr("tool_service.tools.infra.orch_post", fake_post)
    response = await make_registry().execute(
        ToolRequest(
            caller_id="devops-pm",
            caller_role=AgentRole.ADMIN,
            caller_team="dept_devops",
            tool_name="infra.ready_signal",
            kwargs={"project_id": "project-1", "sprint_id": "sprint-1"},
        )
    )

    assert response.success is True
    assert captured["path"] == "/projects/project-1/transition"
    assert captured["body"]["event"] == "infra_ready"


@pytest.mark.anyio
async def test_infra_provision_fails_closed_when_unconfigured(make_registry, monkeypatch):
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


@pytest.mark.anyio
async def test_infra_provision_creates_project_workspace(make_registry, monkeypatch, tmp_path):
    captured = {}

    async def fake_adapter(env_name, payload, *, cwd=None):
        captured.update(env_name=env_name, payload=payload, cwd=cwd)
        return {"available": True, "configured": True, "verified": True}

    monkeypatch.setenv("TOOL_INFRA_PROVISION_COMMAND", "configured-adapter")
    monkeypatch.setenv("TOOL_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "tool_service.tools.infra._run_configured_adapter",
        fake_adapter,
    )

    response = await make_registry().execute(
        ToolRequest(
            caller_id="devops-pm",
            caller_role=AgentRole.ADMIN,
            caller_team="dept_devops",
            project_id="project-1",
            tool_name="infra.provision",
            kwargs={"resource": "preview-environment", "config": {}},
        )
    )

    assert response.success is True
    assert response.result["verified"] is True
    assert captured["cwd"] == tmp_path / "project-1"
    assert (tmp_path / "project-1").is_dir()


@pytest.mark.anyio
async def test_project_repository_initializes_and_reports_git_workspace(
    make_registry, monkeypatch, tmp_path
):
    monkeypatch.setenv("TOOL_WORKSPACE_ROOT", str(tmp_path))
    response = await make_registry().execute(
        ToolRequest(
            caller_id="orchestrator",
            caller_role=AgentRole.ORCHESTRATOR,
            caller_team="exec_ceo",
            project_id="project-1",
            tool_name="project.repository",
            kwargs={"operation": "init", "branch": "main"},
        )
    )

    assert response.success is True
    assert response.result["initialized"] is True
    assert response.result["branch"] == "main"
    assert response.result["clean"] is True
    assert response.result["workspace_relative_path"] == "project-1"
    assert (tmp_path / "project-1" / ".git").is_dir()
    assert (tmp_path / "project-1" / ".aiat" / "project.json").is_file()


@pytest.mark.anyio
async def test_mcp_invoke_reports_unconfigured_transport_without_fabricating_success(
    make_registry,
):
    response = await make_registry().execute(
        ToolRequest(
            caller_id="worker-alpha",
            caller_role=AgentRole.WORKER,
            caller_team="dept_qa",
            tool_name="mcp.invoke",
            kwargs={"server": "default", "tool": "ping", "arguments": {}},
        )
    )

    assert response.success is True
    assert response.result == {
        "available": False,
        "configured": False,
        "reason": "MCP transport endpoint not configured for mcp.invoke",
    }
