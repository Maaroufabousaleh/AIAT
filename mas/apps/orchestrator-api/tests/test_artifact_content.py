from __future__ import annotations

import hashlib
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from conftest import PROJECT_ID

from mas_core.memory.blob import BlobRef
from mas_core.worker_contract import WorkerArtifact

_app_dir = str(Path(__file__).resolve().parents[1])
if _app_dir not in sys.path:
    sys.path.insert(0, _app_dir)


class _Body:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.offset = 0
        self.closed = False

    async def read(self, limit: int) -> bytes:
        result = self.content[self.offset : self.offset + limit]
        self.offset += len(result)
        return result

    def close(self) -> None:
        self.closed = True


class _ObjectClient:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.requested: list[tuple[str, str]] = []
        self.body: _Body | None = None

    async def get_object(self, *, Bucket: str, Key: str) -> dict:
        self.requested.append((Bucket, Key))
        self.body = _Body(self.content)
        return {"Body": self.body, "ContentLength": len(self.content)}


class _FailingAsyncCloseBody:
    def __init__(self) -> None:
        self.closed = False

    async def read(self, _limit: int) -> bytes:
        raise OSError("simulated read interruption")

    async def close(self) -> None:
        self.closed = True


class _Blob:
    def __init__(self, content: bytes = b"") -> None:
        self.content = content
        self.client = _ObjectClient(content)
        self.uploads: list[tuple[str, str, bytes, str, str]] = []
        self.connected = False
        self.closed = False

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.closed = True

    async def upload(
        self,
        project_id: str,
        key: str,
        content: bytes,
        *,
        content_type: str,
        bucket: str,
    ) -> BlobRef:
        self.uploads.append((project_id, key, content, content_type, bucket))
        return BlobRef(
            bucket=bucket,
            key=f"{project_id}/{key}",
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            content_type=content_type,
        )


@pytest.mark.anyio
async def test_opencode_registrar_uploads_run_workspace_bytes_with_canonical_project_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orchestrator_api.artifact_content import make_opencode_artifact_registrar

    run_id = uuid.uuid4()
    worker_id = uuid.uuid4()
    workspace = tmp_path / "workspace"
    file_path = workspace / str(run_id) / "src" / "totals.py"
    file_path.parent.mkdir(parents=True)
    content = b"def total(values):\n    return sum(values)\n"
    file_path.write_bytes(content)
    monkeypatch.setenv("MINIO_ENDPOINT", "http://object-store.test")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "test-access")
    monkeypatch.setenv("MINIO_SECRET_KEY", "test-secret")
    monkeypatch.setenv("MINIO_BUCKET", "worker-artifacts")
    blob = _Blob()
    run = {
        "id": run_id,
        "project_id": PROJECT_ID,
        "worker_id": worker_id,
    }
    storage = SimpleNamespace(get_worker_run=AsyncMock(return_value=run))
    artifact = WorkerArtifact(
        kind="file",
        name="totals.py",
        uri="src/totals.py",
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        mime_type="text/x-python",
        metadata={"worker_run_id": str(run_id)},
    )

    registrar = make_opencode_artifact_registrar(
        storage,
        worker_id=worker_id,
        workspace_root=workspace,
        blob_client_factory=lambda *args, **kwargs: blob,
    )
    await registrar(artifact)

    assert blob.connected and blob.closed
    assert blob.uploads == [
        (
            str(PROJECT_ID),
            f"worker-runs/{run_id}/src/totals.py",
            content,
            "text/x-python",
            "worker-artifacts",
        )
    ]
    assert artifact.metadata["project_id"] == str(PROJECT_ID)
    assert artifact.metadata["blob_key"] == (
        f"{PROJECT_ID}/worker-runs/{run_id}/src/totals.py"
    )
    assert artifact.metadata["blob_sha256"] == hashlib.sha256(content).hexdigest()
    assert artifact.metadata["blob_size_bytes"] == len(content)


@pytest.mark.anyio
async def test_opencode_registrar_rejects_traversal_and_symlink_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orchestrator_api.artifact_content import (
        ArtifactContentError,
        make_opencode_artifact_registrar,
    )

    run_id = uuid.uuid4()
    worker_id = uuid.uuid4()
    workspace = tmp_path / "workspace"
    run_workspace = workspace / str(run_id)
    run_workspace.mkdir(parents=True)
    outside = tmp_path / "outside.py"
    outside.write_text("secret", encoding="utf-8")
    (run_workspace / "escape.py").symlink_to(outside)
    monkeypatch.setenv("MINIO_ENDPOINT", "http://object-store.test")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "test-access")
    monkeypatch.setenv("MINIO_SECRET_KEY", "test-secret")
    run = {"id": run_id, "project_id": PROJECT_ID, "worker_id": worker_id}
    storage = SimpleNamespace(get_worker_run=AsyncMock(return_value=run))
    blob = _Blob()
    registrar = make_opencode_artifact_registrar(
        storage,
        worker_id=worker_id,
        workspace_root=workspace,
        blob_client_factory=lambda *args, **kwargs: blob,
    )

    for uri in ("../outside.py", "escape.py"):
        artifact = WorkerArtifact(
            kind="file",
            name="unsafe",
            uri=uri,
            sha256=hashlib.sha256(b"secret").hexdigest(),
            size_bytes=6,
            metadata={"worker_run_id": str(run_id)},
        )
        with pytest.raises(ArtifactContentError):
            await registrar(artifact)
    assert blob.uploads == []


@pytest.mark.anyio
async def test_download_closes_async_s3_body_when_read_fails() -> None:
    from orchestrator_api.artifact_content import download_artifact_bytes

    body = _FailingAsyncCloseBody()
    blob = SimpleNamespace(
        client=SimpleNamespace(
            get_object=AsyncMock(return_value={"Body": body, "ContentLength": 10})
        )
    )
    ref = BlobRef(
        bucket="worker-artifacts",
        key=f"{PROJECT_ID}/worker-runs/{uuid.uuid4()}/output.txt",
        sha256=hashlib.sha256(b"test").hexdigest(),
        size_bytes=4,
    )

    with pytest.raises(OSError, match="simulated read interruption"):
        await download_artifact_bytes(blob, ref)
    assert body.closed


@pytest.mark.anyio
async def test_certified_opencode_adapter_receives_orchestrator_artifact_registrar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import orchestrator_api.main as api

    import mas_core.worker_registry.runtime_adapters as adapters

    worker_id = uuid.uuid4()
    adapter_id = uuid.uuid4()
    captured: dict[str, object] = {}

    class CapturedAdapter:
        def __init__(self, *args, context, **kwargs) -> None:
            captured["context"] = context

    adapter_row = {
        "id": adapter_id,
        "worker_id": worker_id,
        "transport_type": "opencode",
        "status": "active",
        "conformance_status": "passed",
        "capabilities_json": {},
    }
    storage = SimpleNamespace(
        get_runtime_adapter=AsyncMock(return_value=adapter_row),
        get_worker_run=AsyncMock(),
    )
    worker = {
        "id": worker_id,
        "active_adapter_id": adapter_id,
        "adapter_type": "opencode",
        "adapter_config": {
            "workspace_path": str(tmp_path),
            "base_url": "http://opencode.test",
            "interface_verification": {},
        },
    }
    monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", "test-password")
    monkeypatch.setenv("TOOL_SECRET", "test-tool-secret")
    monkeypatch.setattr(adapters, "OpenCodeAdapter", CapturedAdapter)
    monkeypatch.setattr(
        adapters.OpenCodeInterfaceVerification,
        "from_report",
        classmethod(lambda cls, report: object()),
    )
    monkeypatch.setattr(api, "_worker_adapter_runtimes", {})

    adapter = await api._certified_worker_adapter(storage, worker)

    assert isinstance(adapter, CapturedAdapter)
    assert callable(captured["context"].artifact_registrar)


def _artifact_fixture(run_id: uuid.UUID, content: bytes) -> dict:
    digest = hashlib.sha256(content).hexdigest()
    return {
        "id": 73,
        "agent_id": "worker-1",
        "path": "src/totals.py",
        "sha256": digest,
        "size_bytes": len(content),
        "metadata": {
            "project_id": str(PROJECT_ID),
            "worker_run_id": str(run_id),
            "blob_bucket": "worker-artifacts",
            "blob_key": f"{PROJECT_ID}/worker-runs/{run_id}/src/totals.py",
            "blob_sha256": digest,
            "blob_size_bytes": len(content),
            "blob_content_type": "text/x-python",
        },
    }


@pytest.mark.anyio
async def test_artifact_download_returns_verified_bytes_only_to_operator_for_linked_project(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import orchestrator_api.artifact_content as artifact_content
    from orchestrator_api.main import app

    run_id = uuid.uuid4()
    content = b"def total(values):\n    return sum(values)\n"
    artifact = _artifact_fixture(run_id, content)
    storage = MagicMock()
    storage.get_project = AsyncMock(return_value={"id": PROJECT_ID})
    storage.get_artifact = AsyncMock(return_value=artifact)
    storage.get_worker_run = AsyncMock(
        return_value={"id": run_id, "project_id": PROJECT_ID, "worker_id": "worker-1"}
    )
    storage.list_worker_artifacts = AsyncMock(return_value=[
        {
            "artifact_id": artifact["id"],
            "uri": artifact["path"],
            "sha256": artifact["sha256"],
            "size_bytes": artifact["size_bytes"],
        }
    ])
    app.state.storage = storage
    blob = _Blob(content)
    monkeypatch.setattr(artifact_content, "new_artifact_blob_client", lambda: blob)
    monkeypatch.setenv("MINIO_ENDPOINT", "http://object-store.test")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "test-access")
    monkeypatch.setenv("MINIO_SECRET_KEY", "test-secret")
    monkeypatch.setenv("MINIO_BUCKET", "worker-artifacts")
    path = f"/projects/{PROJECT_ID}/artifacts/{artifact['id']}/download"

    unauthenticated = await client.get(path, headers={"X-API-Key": ""})
    assert unauthenticated.status_code == 401
    denied = await client.get(path)
    assert denied.status_code == 403
    assert blob.client.requested == []

    response = await client.get(path, headers={"X-API-Key": "test-operator-key"})
    assert response.status_code == 200
    assert response.content == content
    assert response.headers["x-aiat-artifact-sha256"] == artifact["sha256"]
    assert response.headers["x-aiat-artifact-size"] == str(len(content))
    assert response.headers["content-disposition"] == 'attachment; filename="totals.py"'
    assert blob.client.requested == [
        ("worker-artifacts", artifact["metadata"]["blob_key"])
    ]
    assert blob.client.body is not None and blob.client.body.closed
    storage.list_worker_artifacts.assert_awaited_once_with(run_id, limit=1_000)


@pytest.mark.anyio
async def test_artifact_download_rejects_cross_project_before_object_store_read(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import orchestrator_api.artifact_content as artifact_content
    from orchestrator_api.main import app

    run_id = uuid.uuid4()
    content = b"safe"
    artifact = _artifact_fixture(run_id, content)
    storage = MagicMock()
    storage.get_project = AsyncMock(return_value={"id": uuid.uuid4()})
    storage.get_artifact = AsyncMock(return_value=artifact)
    storage.get_worker_run = AsyncMock(
        return_value={"id": run_id, "project_id": PROJECT_ID, "worker_id": "worker-1"}
    )
    storage.list_worker_artifacts = AsyncMock(return_value=[])
    app.state.storage = storage
    blob = _Blob(content)
    monkeypatch.setattr(artifact_content, "new_artifact_blob_client", lambda: blob)
    other_project_id = uuid.uuid4()
    response = await client.get(
        f"/projects/{other_project_id}/artifacts/{artifact['id']}/download",
        headers={"X-API-Key": "test-operator-key"},
    )

    assert response.status_code == 404
    storage.list_worker_artifacts.assert_not_awaited()
    assert blob.client.requested == []


@pytest.mark.anyio
async def test_artifact_download_rejects_worker_run_identity_mismatch(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import orchestrator_api.artifact_content as artifact_content
    from orchestrator_api.main import app

    run_id = uuid.uuid4()
    artifact = _artifact_fixture(run_id, b"worker bound")
    storage = MagicMock()
    storage.get_project = AsyncMock(return_value={"id": PROJECT_ID})
    storage.get_artifact = AsyncMock(return_value=artifact)
    storage.get_worker_run = AsyncMock(
        return_value={"id": run_id, "project_id": PROJECT_ID, "worker_id": "other-worker"}
    )
    storage.list_worker_artifacts = AsyncMock(return_value=[])
    app.state.storage = storage
    blob = _Blob(b"worker bound")
    monkeypatch.setattr(artifact_content, "new_artifact_blob_client", lambda: blob)

    response = await client.get(
        f"/projects/{PROJECT_ID}/artifacts/{artifact['id']}/download",
        headers={"X-API-Key": "test-operator-key"},
    )

    assert response.status_code == 404
    storage.list_worker_artifacts.assert_not_awaited()
    assert blob.client.requested == []


@pytest.mark.anyio
async def test_artifact_download_fails_closed_when_worker_run_identity_is_missing(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import orchestrator_api.artifact_content as artifact_content
    from orchestrator_api.main import app

    run_id = uuid.uuid4()
    artifact = _artifact_fixture(run_id, b"worker identity required")
    storage = MagicMock()
    storage.get_project = AsyncMock(return_value={"id": PROJECT_ID})
    storage.get_artifact = AsyncMock(return_value=artifact)
    storage.get_worker_run = AsyncMock(return_value={"id": run_id, "project_id": PROJECT_ID})
    storage.list_worker_artifacts = AsyncMock(return_value=[])
    app.state.storage = storage
    blob = _Blob(b"worker identity required")
    monkeypatch.setattr(artifact_content, "new_artifact_blob_client", lambda: blob)

    response = await client.get(
        f"/projects/{PROJECT_ID}/artifacts/{artifact['id']}/download",
        headers={"X-API-Key": "test-operator-key"},
    )

    assert response.status_code == 404
    storage.list_worker_artifacts.assert_not_awaited()
    assert blob.client.requested == []


@pytest.mark.anyio
async def test_artifact_download_rejects_corrupt_object_bytes(client, monkeypatch: pytest.MonkeyPatch) -> None:
    import orchestrator_api.artifact_content as artifact_content
    from orchestrator_api.main import app

    run_id = uuid.uuid4()
    content = b"canonical content"
    artifact = _artifact_fixture(run_id, content)
    storage = MagicMock()
    storage.get_project = AsyncMock(return_value={"id": PROJECT_ID})
    storage.get_artifact = AsyncMock(return_value=artifact)
    storage.get_worker_run = AsyncMock(
        return_value={"id": run_id, "project_id": PROJECT_ID, "worker_id": "worker-1"}
    )
    storage.list_worker_artifacts = AsyncMock(return_value=[
        {
            "artifact_id": artifact["id"],
            "uri": artifact["path"],
            "sha256": artifact["sha256"],
            "size_bytes": artifact["size_bytes"],
        }
    ])
    app.state.storage = storage
    blob = _Blob(b"tampered content!")
    monkeypatch.setattr(artifact_content, "new_artifact_blob_client", lambda: blob)
    monkeypatch.setenv("MINIO_ENDPOINT", "http://object-store.test")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "test-access")
    monkeypatch.setenv("MINIO_SECRET_KEY", "test-secret")
    monkeypatch.setenv("MINIO_BUCKET", "worker-artifacts")

    response = await client.get(
        f"/projects/{PROJECT_ID}/artifacts/{artifact['id']}/download",
        headers={"X-API-Key": "test-operator-key"},
    )

    assert response.status_code == 502
    assert response.json() == {"detail": "Artifact integrity verification failed"}
