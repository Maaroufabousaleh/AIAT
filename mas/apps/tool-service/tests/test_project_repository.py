from __future__ import annotations

from pathlib import Path

import pytest

from tool_service.tools import project
from tool_service.tools.project import ProjectRepositoryTool


@pytest.mark.anyio
async def test_git_command_scopes_safe_directory_to_exact_workspace(tmp_path, monkeypatch):
    captured: dict[str, object] = {}

    async def fake_run_process(argv, *, cwd, timeout, max_output_bytes):
        captured.update(
            argv=argv,
            cwd=cwd,
            timeout=timeout,
            max_output_bytes=max_output_bytes,
        )
        return {"available": True, "returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(project, "_run_process", fake_run_process)

    await project._git_command(["status", "--porcelain"], cwd=tmp_path)

    workspace = tmp_path.resolve()
    assert captured["argv"] == [
        "git",
        "-c",
        f"safe.directory={workspace}",
        "status",
        "--porcelain",
    ]
    assert captured["cwd"] == workspace


@pytest.mark.anyio
async def test_git_status_and_commit_keep_safe_directory_on_nonfatal_calls(tmp_path, monkeypatch):
    invocations: list[list[str]] = []

    async def fake_run_process(argv, *, cwd, timeout, max_output_bytes):
        invocations.append(argv)
        if argv[-3:] == ["remote", "get-url", "origin"]:
            return {"available": True, "returncode": 2, "stdout": "", "stderr": "no remote"}
        if argv[-3:] == ["commit", "-m", "message"]:
            return {"available": True, "returncode": 1, "stdout": "nothing to commit", "stderr": ""}
        if argv[-2:] == ["branch", "--show-current"]:
            return {"available": True, "returncode": 0, "stdout": "main\n", "stderr": ""}
        if argv[-2:] == ["rev-parse", "HEAD"]:
            return {"available": True, "returncode": 0, "stdout": "abc\n", "stderr": ""}
        return {"available": True, "returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(project, "_run_process", fake_run_process)
    workspace = tmp_path / "workspace"
    (workspace / ".git").mkdir(parents=True)

    status = await project._git_status(
        workspace=workspace,
        project_id="workspace",
        remote_name="origin",
    )
    commit = await project._git_command(
        ["commit", "-m", "message"],
        cwd=workspace,
        check=False,
    )

    safe_prefix = ["git", "-c", f"safe.directory={workspace.resolve()}"]
    assert status["remote"] is None
    assert commit["returncode"] == 1
    assert all(argv[:3] == safe_prefix for argv in invocations)


@pytest.mark.anyio
async def test_project_repository_initializes_idempotent_managed_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("TOOL_WORKSPACE_ROOT", str(tmp_path))
    tool = ProjectRepositoryTool()

    initialized = await tool.execute(operation="init", project_id="project-123")
    repeated = await tool.execute(operation="init", project_id="project-123")

    workspace = Path(initialized["workspace_path"])
    assert workspace == tmp_path / "project-123"
    assert (workspace / ".git").is_dir()
    assert (workspace / ".aiat" / "project.json").is_file()
    assert initialized["initialized"] is True
    assert initialized["branch"] == "main"
    assert initialized["clean"] is True
    assert initialized["head"]
    assert repeated["head"] == initialized["head"]


@pytest.mark.anyio
async def test_project_repository_uses_inline_identity_for_bind_mount_init(tmp_path, monkeypatch):
    """Git identity setup must not require a bind-mounted .git/config chmod."""
    monkeypatch.setenv("TOOL_WORKSPACE_ROOT", str(tmp_path))
    calls: list[list[str]] = []

    async def fake_git_command(argv, *, cwd, timeout=120, check=True):
        calls.append(list(argv))
        return {"available": True, "returncode": 0, "stdout": "", "stderr": ""}

    async def fake_git_status(*, workspace, project_id, remote_name):
        return {
            "initialized": True,
            "project_id": project_id,
            "workspace_path": str(workspace),
            "branch": "main",
            "head": "inline-identity",
            "clean": True,
        }

    monkeypatch.setattr(project, "_git_command", fake_git_command)
    monkeypatch.setattr(project, "_git_status", fake_git_status)

    result = await ProjectRepositoryTool().execute(operation="init", project_id="bind-mounted")

    assert result["head"] == "inline-identity"
    assert ["config", "user.name", "AIAT"] not in calls
    assert ["config", "user.email", "aiat@local.invalid"] not in calls
    assert [
        "-c",
        "user.name=AIAT",
        "-c",
        "user.email=aiat@local.invalid",
        "commit",
        "-m",
        "Initialize AIAT project workspace",
    ] in calls
