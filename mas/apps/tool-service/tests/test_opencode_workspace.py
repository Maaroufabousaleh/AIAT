from __future__ import annotations

from uuid import uuid4

import pytest

from tool_service.tools.opencode_workspace import (
    OpenCodeWorkspacePytestTool,
    OpenCodeWorkspaceReadTool,
    OpenCodeWorkspaceWriteTool,
)


@pytest.mark.anyio
async def test_opencode_workspace_is_run_scoped(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENCODE_WORKSPACE_ROOT", str(tmp_path))
    first_run = uuid4()
    second_run = uuid4()
    writer = OpenCodeWorkspaceWriteTool()
    reader = OpenCodeWorkspaceReadTool()

    result = await writer.execute(
        workspace_run_id=str(first_run),
        path="src/result.py",
        content="VALUE = 42\n",
    )

    assert result == {"path": "src/result.py", "bytes_written": 11}
    assert await reader.execute(workspace_run_id=str(first_run), path="src/result.py") == {
        "path": "src/result.py",
        "content": "VALUE = 42\n",
        "size_bytes": 11,
    }
    assert not (tmp_path / str(second_run) / "src" / "result.py").exists()


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["../escape", "nested/../../escape"])
async def test_opencode_workspace_rejects_traversal(monkeypatch, tmp_path, path) -> None:
    monkeypatch.setenv("OPENCODE_WORKSPACE_ROOT", str(tmp_path))

    with pytest.raises(ValueError, match="traversal denied"):
        await OpenCodeWorkspaceWriteTool().execute(
            workspace_run_id=str(uuid4()),
            path=path,
            content="blocked",
        )


@pytest.mark.anyio
async def test_opencode_workspace_rejects_absolute_paths(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENCODE_WORKSPACE_ROOT", str(tmp_path))

    with pytest.raises(ValueError, match="traversal denied"):
        await OpenCodeWorkspaceWriteTool().execute(
            workspace_run_id=str(uuid4()),
            path=str(tmp_path / "escape"),
            content="blocked",
        )


@pytest.mark.anyio
async def test_opencode_workspace_rejects_symlinks(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENCODE_WORKSPACE_ROOT", str(tmp_path))
    run_id = uuid4()
    run_root = tmp_path / str(run_id)
    run_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = run_root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not available for this test user")

    with pytest.raises(ValueError, match="symlinks are denied"):
        await OpenCodeWorkspaceWriteTool().execute(
            workspace_run_id=str(run_id),
            path="link/escape",
            content="blocked",
        )


@pytest.mark.anyio
async def test_opencode_workspace_runs_bounded_pytest(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENCODE_WORKSPACE_ROOT", str(tmp_path))
    run_id = uuid4()
    run_root = tmp_path / str(run_id)
    run_root.mkdir()
    (run_root / "test_solution.py").write_text(
        "def test_one():\n    assert 1 + 1 == 2\n\n"
        "def test_two():\n    assert 'aiat'.upper() == 'AIAT'\n",
        encoding="utf-8",
    )

    result = await OpenCodeWorkspacePytestTool().execute(
        workspace_run_id=str(run_id),
        path="test_solution.py",
    )

    assert result == {
        "path": "test_solution.py",
        "exit_code": 0,
        "certification_status": "PASSED",
        "passed": 2,
        "failed": 0,
        "skipped": 0,
    }
    assert not (run_root / ".pytest_cache").exists()
    assert not (run_root / "__pycache__").exists()
