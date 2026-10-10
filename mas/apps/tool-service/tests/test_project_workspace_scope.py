from __future__ import annotations

import hashlib
from types import SimpleNamespace
from uuid import uuid4

import pytest
from tool_service.tools.file import FilePatchTool, FileReadTool, FileWriteTool
from tool_service.tools.infra import BlobDeleteTool, BlobDownloadTool, BlobListTool, BlobUploadTool
from tool_service.tools.project import ProjectRepositoryTool

from mas_core.protocols.enums import AgentRole
from mas_core.protocols.tool import ToolRequest

# The shared conftest stubs local file/blob methods for unrelated tool tests.
# Keep these originals so this module covers the real implementations through
# ToolRegistry's authenticated request context.
_FILE_READ = FileReadTool.execute
_FILE_WRITE = FileWriteTool.execute
_FILE_PATCH = FilePatchTool.execute
_BLOB_UPLOAD = BlobUploadTool.execute
_BLOB_DOWNLOAD = BlobDownloadTool.execute
_BLOB_LIST = BlobListTool.execute
_BLOB_DELETE = BlobDeleteTool.execute
_PROJECT_REPOSITORY = ProjectRepositoryTool.execute


class _MemoryBlob:
    """Deterministic S3 boundary for upload/readback and scope tests."""

    _bucket = "test-artifacts"

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.calls: list[tuple[str, str, str]] = []

    async def upload(self, *, project_id, key, data, content_type):
        self.calls.append(("upload", project_id, key))
        self.objects[(project_id, key)] = data
        return SimpleNamespace(
            bucket=self._bucket,
            key=f"{project_id}/{key}",
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )

    async def download_by_key(self, *, project_id, key):
        self.calls.append(("download", project_id, key))
        return self.objects[(project_id, key)]

    async def list_objects(self, *, project_id, prefix):
        self.calls.append(("list", project_id, prefix))
        return [key for (owner, key) in self.objects if owner == project_id and key.startswith(prefix)]

    async def delete_by_key(self, *, project_id, key):
        self.calls.append(("delete", project_id, key))
        del self.objects[(project_id, key)]


def _worker_request(*, tool_name: str, project_id: str | None, kwargs: dict) -> ToolRequest:
    return ToolRequest(
        caller_id="coding-worker",
        caller_role=AgentRole.WORKER,
        caller_team="dept_system",
        project_id=project_id,
        worker_run_id=uuid4(),
        tool_name=tool_name,
        kwargs=kwargs,
    )


def _restore_real_tools(monkeypatch) -> None:
    monkeypatch.setattr(FileReadTool, "execute", _FILE_READ)
    monkeypatch.setattr(FileWriteTool, "execute", _FILE_WRITE)
    monkeypatch.setattr(FilePatchTool, "execute", _FILE_PATCH)
    monkeypatch.setattr(BlobUploadTool, "execute", _BLOB_UPLOAD)
    monkeypatch.setattr(BlobDownloadTool, "execute", _BLOB_DOWNLOAD)
    monkeypatch.setattr(BlobListTool, "execute", _BLOB_LIST)
    monkeypatch.setattr(BlobDeleteTool, "execute", _BLOB_DELETE)
    monkeypatch.setattr(ProjectRepositoryTool, "execute", _PROJECT_REPOSITORY)


@pytest.mark.anyio
async def test_worker_file_tools_are_bound_to_authenticated_project(
    make_registry, monkeypatch, tmp_path
) -> None:
    _restore_real_tools(monkeypatch)
    monkeypatch.setenv("TOOL_WORKSPACE_ROOT", str(tmp_path))
    target = tmp_path / "other-project"
    target.mkdir()
    (target / "private.txt").write_text("private", encoding="utf-8")
    registry = make_registry()

    read = await registry.execute(
        _worker_request(
            tool_name="file_read",
            project_id="assigned-project",
            kwargs={"project_id": "other-project", "path": "private.txt"},
        )
    )
    write = await registry.execute(
        _worker_request(
            tool_name="file_write",
            project_id="assigned-project",
            kwargs={"project_id": "other-project", "path": "private.txt", "content": "changed"},
        )
    )
    patch = await registry.execute(
        _worker_request(
            tool_name="file.patch",
            project_id="assigned-project",
            kwargs={
                "project_id": "other-project",
                "path": "private.txt",
                "find": "private",
                "replace": "changed",
            },
        )
    )
    repository_read = await registry.execute(
        _worker_request(
            tool_name="repo.read",
            project_id="assigned-project",
            kwargs={"project_id": "other-project", "path": "private.txt"},
        )
    )

    assert read.success is False
    assert "authenticated project scope" in (read.error or "")
    assert write.success is False
    assert "authenticated project scope" in (write.error or "")
    assert patch.success is False
    assert "authenticated project scope" in (patch.error or "")
    assert repository_read.success is False
    assert "authenticated project scope" in (repository_read.error or "")
    assert (target / "private.txt").read_text(encoding="utf-8") == "private"

    write_own = await registry.execute(
        _worker_request(
            tool_name="file_write",
            project_id="assigned-project",
            kwargs={"path": "src/solution.py", "content": "VALUE = 42\n"},
        )
    )
    read_own = await registry.execute(
        _worker_request(
            tool_name="file_read",
            project_id="assigned-project",
            kwargs={"path": "src/solution.py"},
        )
    )
    assert write_own.success is True
    assert read_own.success is True
    assert read_own.result["content"] == "VALUE = 42\n"


@pytest.mark.anyio
async def test_blob_artifact_upload_readback_and_mutations_are_project_scoped(
    make_registry, monkeypatch
) -> None:
    import tool_service.tools.infra as infra_module

    _restore_real_tools(monkeypatch)
    blob = _MemoryBlob()

    async def get_blob_client():
        return blob

    monkeypatch.setattr(infra_module, "get_blob_client", get_blob_client)
    registry = make_registry()

    uploaded = await registry.execute(
        _worker_request(
            tool_name="blob.upload",
            project_id="assigned-project",
            kwargs={"key": "artifacts/solution.py", "content": "VALUE = 42\n"},
        )
    )
    downloaded = await registry.execute(
        _worker_request(
            tool_name="blob.download",
            project_id="assigned-project",
            kwargs={"key": "artifacts/solution.py"},
        )
    )

    assert uploaded.success is True
    assert uploaded.result["uploaded"] is True
    assert uploaded.result["sha256"] == hashlib.sha256(b"VALUE = 42\n").hexdigest()
    assert downloaded.success is True
    assert downloaded.result["content"] == "VALUE = 42\n"
    assert blob.objects == {("assigned-project", "artifacts/solution.py"): b"VALUE = 42\n"}

    for tool_name, kwargs in (
        ("blob.upload", {"project_id": "other-project", "key": "leak.txt", "content": "x"}),
        ("blob.download", {"project_id": "other-project", "key": "artifacts/solution.py"}),
        ("blob.list", {"project_id": "other-project", "prefix": ""}),
        ("blob.delete", {"project_id": "other-project", "key": "artifacts/solution.py"}),
    ):
        response = await registry.execute(
            _worker_request(
                tool_name=tool_name,
                project_id="assigned-project",
                kwargs=kwargs,
            )
        )
        assert response.success is False
        assert "authenticated project scope" in (response.error or "")

    assert blob.objects == {("assigned-project", "artifacts/solution.py"): b"VALUE = 42\n"}
    assert blob.calls == [
        ("upload", "assigned-project", "artifacts/solution.py"),
        ("download", "assigned-project", "artifacts/solution.py"),
    ]


@pytest.mark.anyio
async def test_worker_cannot_self_assign_project_scope_for_files_or_artifacts(
    make_registry, monkeypatch, tmp_path
) -> None:
    import tool_service.tools.infra as infra_module

    _restore_real_tools(monkeypatch)
    monkeypatch.setenv("TOOL_WORKSPACE_ROOT", str(tmp_path))
    blob = _MemoryBlob()

    async def get_blob_client():
        return blob

    monkeypatch.setattr(infra_module, "get_blob_client", get_blob_client)
    registry = make_registry()
    requests = (
        _worker_request(
            tool_name="file_write",
            project_id=None,
            kwargs={"project_id": "claimed-project", "path": "note.txt", "content": "x"},
        ),
        _worker_request(
            tool_name="blob.upload",
            project_id=None,
            kwargs={"project_id": "claimed-project", "key": "note.txt", "content": "x"},
        ),
    )
    responses = [await registry.execute(request) for request in requests]

    assert all(response.success is False for response in responses)
    assert all("authenticated project scope" in (response.error or "") for response in responses)
    assert not (tmp_path / "claimed-project").exists()
    assert not blob.calls


@pytest.mark.anyio
async def test_git_workspace_uses_authenticated_project_for_target(
    make_registry, monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("TOOL_WORKSPACE_ROOT", str(tmp_path))
    registry = make_registry()

    response = await registry.execute(
        ToolRequest(
            caller_id="operator",
            caller_role=AgentRole.ORCHESTRATOR,
            caller_team="exec_ceo",
            project_id="assigned-project",
            tool_name="project.repository",
            kwargs={
                "operation": "init",
                "project_id": "other-project",
                "branch": "main",
            },
        )
    )

    assert response.success is False
    assert "authenticated project scope" in (response.error or "")
    assert not (tmp_path / "other-project").exists()


@pytest.mark.anyio
async def test_worker_test_and_search_cannot_target_another_project(
    make_registry, monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("TOOL_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "assigned-project").mkdir()
    (tmp_path / "other-project").mkdir()
    calls = []

    async def fake_sandbox(*_args, **_kwargs):
        calls.append("sandbox")
        return {"available": True, "returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr("tool_service.tools.adapters._run_sandboxed_process", fake_sandbox)
    registry = make_registry()
    test_response = await registry.execute(
        _worker_request(
            tool_name="test.run",
            project_id="assigned-project",
            kwargs={"project_id": "other-project", "runner": "pytest", "args": ["tests"]},
        )
    )
    search_response = await registry.execute(
        _worker_request(
            tool_name="repo.search",
            project_id="assigned-project",
            kwargs={"project_id": "other-project", "query": "needle"},
        )
    )

    assert test_response.success is False
    assert search_response.success is False
    assert all("authenticated project scope" in (response.error or "") for response in (test_response, search_response))
    assert calls == []


@pytest.mark.anyio
async def test_project_workspace_symlink_cannot_cross_into_another_project(
    make_registry, monkeypatch, tmp_path
) -> None:
    _restore_real_tools(monkeypatch)
    monkeypatch.setenv("TOOL_WORKSPACE_ROOT", str(tmp_path))
    source = tmp_path / "assigned-project"
    target = tmp_path / "other-project"
    source.mkdir()
    target.mkdir()
    (target / "private.txt").write_text("private", encoding="utf-8")
    link = source / "linked-project"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable in this environment")

    response = await make_registry().execute(
        _worker_request(
            tool_name="file_read",
            project_id="assigned-project",
            kwargs={"path": "linked-project/private.txt"},
        )
    )

    assert response.success is False
    assert "Path traversal escapes the project workspace" in (response.error or "")
