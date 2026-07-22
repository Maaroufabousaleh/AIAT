from __future__ import annotations

import json
import subprocess
import sys

import pytest
from tool_service.code_review_runner import review
from tool_service.config import Settings
from tool_service.devops_adapter import monitoring
from tool_service.mcp_client import invoke_mcp_tool
from tool_service.rate_limiter import RateLimiterPool
from tool_service.registry import ToolRegistry
from tool_service.sandbox_runner import execute as execute_sandbox
from tool_service.tools.all_tools import get_all_tools

from mas_core.protocols.enums import AgentRole
from mas_core.protocols.tool import ToolRequest


def test_sandbox_runner_never_falls_back_when_runsc_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("tool_service.sandbox_runner.shutil.which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr("tool_service.sandbox_runner._runtime_available", lambda _docker: False)

    result = execute_sandbox(
        {
            "argv": ["pytest"],
            "workspace_root": str(tmp_path),
            "cwd": ".",
            "profile": "gvisor",
            "network_mode": "egress-deny-all",
        }
    )

    assert result["available"] is False
    assert result["reason"] == "gvisor_runsc_runtime_not_available"
    assert result["sandbox_profile"] == "gvisor"


def test_sandbox_runner_scrubs_service_environment_and_can_mount_read_only(tmp_path, monkeypatch):
    captured = {}

    class CompletedProcess:
        def wait(self, timeout=None):
            return 0

    monkeypatch.setenv("TOOL_SECRET", "must-not-reach-generated-tests")
    monkeypatch.setattr("tool_service.sandbox_runner.shutil.which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr("tool_service.sandbox_runner._runtime_available", lambda _docker: True)
    monkeypatch.setattr(
        "tool_service.sandbox_runner.subprocess.Popen",
        lambda command, **_kwargs: captured.setdefault("command", command) and CompletedProcess(),
    )

    result = execute_sandbox(
        {
            "argv": ["python", "-m", "pytest", "-q", "test_solution.py"],
            "workspace_root": str(tmp_path),
            "cwd": ".",
            "workspace_read_only": True,
            "profile": "gvisor",
            "network_mode": "egress-deny-all",
        }
    )

    command = captured["command"]
    assert result["available"] is True
    assert "--network=none" in command
    assert "--user=10001:10001" in command
    assert "PYTHONNOUSERSITE=1" in command
    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1" in command
    assert "must-not-reach-generated-tests" not in command
    assert any(value.endswith(",readonly") for value in command)


def test_code_review_reports_structured_findings_without_secret_text(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "audit@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "AIAT Audit"], cwd=tmp_path, check=True)
    source = tmp_path / "app.py"
    source.write_text("safe = True\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=tmp_path, check=True, capture_output=True)
    source.write_text('safe = True\napi_key = "do-not-leak"\n', encoding="utf-8")

    result = review(tmp_path, {"head": "HEAD", "severity_threshold": "medium"})

    assert result["findings_count"] == 1
    assert result["findings"][0]["rule_id"] == "hardcoded-secret"
    assert "do-not-leak" not in json.dumps(result)


def test_monitoring_adapter_writes_prometheus_and_synthetic_configs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = monitoring({"rules": [], "config": {}})

    assert result["target"] == "prometheus"
    assert (tmp_path / "monitoring" / "prometheus.yml").is_file()
    assert (tmp_path / "monitoring" / "alert_rules.yml").is_file()
    assert (tmp_path / "monitoring" / "synthetic_checks.yml").is_file()
    assert {check["name"] for check in result["checks"]} == {
        "orchestrator-api",
        "message-router",
        "tool-service",
    }


def test_settings_parse_reviewed_mcp_server_registry():
    settings = Settings(
        aiat_mcp_servers_json=json.dumps(
            {
                "workspace": {
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": ["-m", "tool_service.mcp_workspace_server"],
                }
            }
        )
    )

    assert settings.mcp_servers["workspace"]["transport"] == "stdio"


@pytest.mark.anyio
async def test_registry_dispatches_mcp_through_server_registry(monkeypatch):
    settings = Settings(
        aiat_mcp_servers_json=json.dumps(
            {"workspace": {"transport": "stdio", "command": "python", "args": []}}
        )
    )
    captured = {}

    async def fake_invoke(servers, kwargs, *, timeout):
        captured.update(servers=servers, kwargs=kwargs, timeout=timeout)
        return {"server": "workspace", "tools": [{"name": "workspace_read"}]}

    monkeypatch.setattr("tool_service.mcp_client.invoke_mcp_tool", fake_invoke)
    registry = ToolRegistry(settings, rate_limiter=RateLimiterPool())
    registry.register_all(get_all_tools())
    response = await registry.execute(
        ToolRequest(
            caller_id="worker-alpha",
            caller_role=AgentRole.WORKER,
            caller_team="dept_qa",
            tool_name="mcp.invoke",
            kwargs={"server": "workspace", "operation": "list_tools"},
        )
    )

    assert response.success is True
    assert response.result["tools"][0]["name"] == "workspace_read"
    assert "workspace" in captured["servers"]


@pytest.mark.anyio
async def test_real_mcp_workspace_stdio_round_trip(tmp_path):
    (tmp_path / "probe.txt").write_text("live MCP content\n", encoding="utf-8")
    servers = {
        "workspace": {
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-m", "tool_service.mcp_workspace_server"],
            "env": {"TOOL_WORKSPACE_ROOT": str(tmp_path)},
        }
    }

    listed = await invoke_mcp_tool(
        servers,
        {"server": "workspace", "operation": "list_tools"},
        timeout=20,
    )
    called = await invoke_mcp_tool(
        servers,
        {
            "server": "workspace",
            "operation": "call",
            "tool": "workspace_read",
            "arguments": {"path": "probe.txt"},
        },
        timeout=20,
    )

    assert {tool["name"] for tool in listed["tools"]} == {"workspace_read", "workspace_search"}
    assert called["is_error"] is False
    assert "live MCP content" in called["content"][0]["text"]
