from __future__ import annotations

from dataclasses import asdict

import pytest

from mas_core.memory.optional_services import (
    OptionalServiceUnavailable,
    QdrantVectorAdapter,
    TemporalWorkflowAdapter,
    VectorPoint,
    VectorSearchHit,
    WorkflowCommand,
    WorkflowRunReference,
)

CHECKSUM = "a" * 64


class FakeQdrantBackend:
    def __init__(self) -> None:
        self.namespaces: list[str] = []
        self.points = ()
        self.healthy = True

    async def health(self) -> bool:
        return self.healthy

    async def upsert(self, *, namespace: str, points):
        self.namespaces.append(namespace)
        self.points = tuple(points)
        return len(self.points)

    async def search(self, *, namespace: str, query_vector, limit: int):
        self.namespaces.append(namespace)
        assert len(query_vector) == len(self.points[0].vector)
        return tuple(
            VectorSearchHit.build(
                point_id=point.point_id,
                source_ref=point.source_ref,
                checksum=point.checksum,
                score=0.9,
            )
            for point in self.points[:limit]
        )

    async def delete_namespace(self, *, namespace: str) -> int:
        self.namespaces.append(namespace)
        return len(self.points)


class FailingQdrantBackend(FakeQdrantBackend):
    async def health(self) -> bool:
        raise RuntimeError("provider response must not cross the boundary")

    async def upsert(self, *, namespace: str, points):
        raise RuntimeError("provider response must not cross the boundary")


class FakeTemporalBackend:
    def __init__(self) -> None:
        self.namespaces: list[str] = []
        self.commands: list[WorkflowCommand] = []

    async def health(self) -> bool:
        return True

    async def start(self, *, namespace: str, command: WorkflowCommand) -> WorkflowRunReference:
        self.namespaces.append(namespace)
        self.commands.append(command)
        return WorkflowRunReference.build(
            workflow_id=command.workflow_id,
            run_id="run-1",
            version=command.version,
            status="running",
        )

    async def describe(self, *, namespace: str, workflow_id: str) -> WorkflowRunReference:
        self.namespaces.append(namespace)
        return WorkflowRunReference.build(
            workflow_id=workflow_id,
            run_id="run-1",
            version="1.2.3",
            status="running",
        )

    async def signal(
        self, *, namespace: str, workflow_id: str, signal_name: str, payload_checksum: str
    ) -> WorkflowRunReference:
        self.namespaces.append(namespace)
        assert signal_name == "resume"
        assert payload_checksum == CHECKSUM
        return WorkflowRunReference.build(
            workflow_id=workflow_id,
            run_id="run-1",
            version="1.2.3",
            status="running",
        )

    async def cancel(self, *, namespace: str, workflow_id: str) -> WorkflowRunReference:
        self.namespaces.append(namespace)
        return WorkflowRunReference.build(
            workflow_id=workflow_id,
            run_id="run-1",
            version="1.2.3",
            status="cancelled",
        )

    async def delete_namespace(self, *, namespace: str) -> int:
        self.namespaces.append(namespace)
        return 1


def _point() -> VectorPoint:
    return VectorPoint.build(
        point_id="point-1",
        source_ref="aiat://project/document/1",
        checksum=CHECKSUM,
        vector=[0.1, 0.2, 0.3],
    )


@pytest.mark.asyncio
async def test_qdrant_adapter_uses_opaque_project_namespace_and_scalar_results() -> None:
    backend = FakeQdrantBackend()
    adapter = QdrantVectorAdapter(backend=backend, version="1.14.0")

    result = await adapter.upsert(project_id="project-alpha", points=[_point()])
    hits = await adapter.search(project_id="project-alpha", query_vector=[0.1, 0.2, 0.3])
    deleted = await adapter.delete_project(project_id="project-alpha")

    assert result.namespace.startswith("aiat-qdrant-")
    assert "project-alpha" not in result.namespace
    assert result.accepted_count == 1
    assert hits[0].source_ref == "aiat://project/document/1"
    assert deleted.deleted_count == 1
    assert all("project-alpha" not in namespace for namespace in backend.namespaces)
    assert "vector" not in asdict(result)


@pytest.mark.asyncio
async def test_qdrant_adapter_rejects_nonfinite_vectors_and_bounds_search() -> None:
    adapter = QdrantVectorAdapter(backend=FakeQdrantBackend(), version="1.14.0")

    with pytest.raises(ValueError, match="finite"):
        VectorPoint.build(
            point_id="point-1",
            source_ref="aiat://project/document/1",
            checksum=CHECKSUM,
            vector=[float("nan")],
        )
    with pytest.raises(ValueError, match="between 1 and 100"):
        await adapter.search(project_id="project-alpha", query_vector=[0.1], limit=101)


@pytest.mark.asyncio
async def test_qdrant_adapter_normalizes_backend_failures() -> None:
    adapter = QdrantVectorAdapter(backend=FailingQdrantBackend(), version="1.14.0")

    health = await adapter.health()
    assert health.status == "unavailable"
    assert health.retryable is True
    with pytest.raises(OptionalServiceUnavailable) as error:
        await adapter.upsert(project_id="project-alpha", points=[_point()])
    assert error.value.code == "optional_service_unavailable"
    assert "provider response" not in str(error.value)


@pytest.mark.asyncio
async def test_temporal_adapter_scopes_workflow_lifecycle_without_raw_inputs() -> None:
    backend = FakeTemporalBackend()
    adapter = TemporalWorkflowAdapter(backend=backend, version="1.25.0")
    command = WorkflowCommand.build(
        workflow_id="workflow-1",
        workflow_type="aiat.resume",
        version="1.2.3",
        input_checksum=CHECKSUM,
        idempotency_key="project-alpha-workflow-1",
    )

    started = await adapter.start(project_id="project-alpha", command=command)
    described = await adapter.describe(project_id="project-alpha", workflow_id="workflow-1")
    signalled = await adapter.signal(
        project_id="project-alpha",
        workflow_id="workflow-1",
        signal_name="resume",
        payload_checksum=CHECKSUM,
    )
    cancelled = await adapter.cancel(project_id="project-alpha", workflow_id="workflow-1")
    deleted = await adapter.delete_project(project_id="project-alpha")

    assert started.status == "running"
    assert described.workflow_id == signalled.workflow_id == cancelled.workflow_id == "workflow-1"
    assert cancelled.status == "cancelled"
    assert deleted == 1
    assert backend.commands[0].input_checksum == CHECKSUM
    assert all("project-alpha" not in namespace for namespace in backend.namespaces)
    assert "input" not in asdict(started)


@pytest.mark.asyncio
async def test_temporal_adapter_rejects_version_drift() -> None:
    class DriftBackend(FakeTemporalBackend):
        async def start(self, *, namespace: str, command: WorkflowCommand) -> WorkflowRunReference:
            return WorkflowRunReference.build(
                workflow_id=command.workflow_id,
                run_id="run-1",
                version="9.9.9",
                status="running",
            )

    adapter = TemporalWorkflowAdapter(backend=DriftBackend(), version="1.25.0")
    command = WorkflowCommand.build(
        workflow_id="workflow-1",
        workflow_type="aiat.resume",
        version="1.2.3",
        input_checksum=CHECKSUM,
        idempotency_key="workflow-1-key",
    )

    with pytest.raises(ValueError, match="workflow version"):
        await adapter.start(project_id="project-alpha", command=command)
