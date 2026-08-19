"""Bounded contracts for optional Qdrant and Temporal integrations.

The adapters in this module are deliberately backend-injected.  They provide
the AIAT-owned boundary and scalar result shapes without importing an external
client, selecting a service, or enabling a default worker.  A real backend may
be supplied only by a separately certified integration.  Project identifiers
are converted to opaque namespaces before they cross the boundary; results do
not contain vectors, workflow inputs, credentials, or provider payloads.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

OPTIONAL_MEMORY_ADAPTER_SCHEMA = "aiat.optional-memory-adapters.v1"
QDRANT_ADAPTER_SCHEMA = "aiat.optional-qdrant-adapter.v1"
TEMPORAL_ADAPTER_SCHEMA = "aiat.optional-temporal-adapter.v1"

_MAX_BATCH = 256
_MAX_VECTOR_DIMENSION = 4096
_MAX_SEARCH_LIMIT = 100
_TOKEN_RE = re.compile(r"^[^\x00-\x1f\x7f\s]{1,128}$")
_CHECKSUM_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


class OptionalServiceContractError(ValueError):
    """Stable caller error; provider exception text never crosses the boundary."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class OptionalServiceUnavailable(RuntimeError):
    """Stable retryable failure for an unavailable optional service."""

    code = "optional_service_unavailable"
    retryable = True

    def __init__(self, service_id: str) -> None:
        self.service_id = service_id
        super().__init__(f"{service_id} optional service is unavailable")


@dataclass(frozen=True, slots=True)
class OptionalServiceHealth:
    service_id: str
    status: str
    version: str
    retryable: bool = False


def _token(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        raise OptionalServiceContractError("invalid_scope", f"{field} must be a bounded token")
    return value


def _checksum(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not _CHECKSUM_RE.fullmatch(value):
        raise OptionalServiceContractError("invalid_checksum", f"{field} must be a SHA-256 checksum")
    return value.lower()


def _version(value: str) -> str:
    if not isinstance(value, str) or not _VERSION_RE.fullmatch(value):
        raise OptionalServiceContractError("invalid_version", "exact semantic version is required")
    return value


def _namespace(service_id: str, project_id: str) -> str:
    """Return a stable opaque namespace without exposing the project ID."""

    digest = hashlib.sha256(f"{service_id}:{project_id}".encode()).hexdigest()
    return f"aiat-{service_id}-{digest[:32]}"


def _vector(values: Sequence[float], *, field: str) -> tuple[float, ...]:
    if isinstance(values, (str, bytes)):
        raise OptionalServiceContractError("invalid_vector", f"{field} must be a numeric sequence")
    result = tuple(values)
    if not result or len(result) > _MAX_VECTOR_DIMENSION:
        raise OptionalServiceContractError("invalid_vector", f"{field} has an unsupported dimension")
    if not all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in result):
        raise OptionalServiceContractError("invalid_vector", f"{field} must contain finite numbers")
    return tuple(float(value) for value in result)


@dataclass(frozen=True, slots=True)
class VectorPoint:
    """AIAT-owned vector input; the adapter never retains or returns its body."""

    point_id: str
    source_ref: str
    checksum: str
    vector: tuple[float, ...]

    @classmethod
    def build(
        cls,
        *,
        point_id: str,
        source_ref: str,
        checksum: str,
        vector: Sequence[float],
    ) -> VectorPoint:
        return cls(
            point_id=_token(point_id, field="point_id"),
            source_ref=_token(source_ref, field="source_ref"),
            checksum=_checksum(checksum, field="checksum"),
            vector=_vector(vector, field="vector"),
        )


@dataclass(frozen=True, slots=True)
class VectorSearchHit:
    point_id: str
    source_ref: str
    checksum: str
    score: float

    @classmethod
    def build(
        cls,
        *,
        point_id: str,
        source_ref: str,
        checksum: str,
        score: float,
    ) -> VectorSearchHit:
        if not isinstance(score, (int, float)) or not math.isfinite(float(score)):
            raise OptionalServiceContractError("invalid_result", "vector score must be finite")
        return cls(
            point_id=_token(point_id, field="point_id"),
            source_ref=_token(source_ref, field="source_ref"),
            checksum=_checksum(checksum, field="checksum"),
            score=float(score),
        )


@dataclass(frozen=True, slots=True)
class VectorWriteResult:
    service_id: str
    namespace: str
    accepted_count: int
    status: str = "accepted"


@dataclass(frozen=True, slots=True)
class VectorDeleteResult:
    service_id: str
    namespace: str
    deleted_count: int
    status: str = "deleted"


class QdrantBackend(Protocol):
    """Minimal injected backend; an external client must implement this shape."""

    async def health(self) -> bool: ...

    async def upsert(self, *, namespace: str, points: Sequence[VectorPoint]) -> int: ...

    async def search(
        self, *, namespace: str, query_vector: Sequence[float], limit: int
    ) -> Sequence[VectorSearchHit]: ...

    async def delete_namespace(self, *, namespace: str) -> int: ...


class QdrantVectorAdapter:
    """Project-scoped retrieval enrichment with no canonical-state authority."""

    service_id = "qdrant"
    schema_version = QDRANT_ADAPTER_SCHEMA

    def __init__(self, *, backend: QdrantBackend, version: str) -> None:
        self._backend = backend
        self.version = _version(version)

    async def health(self) -> OptionalServiceHealth:
        try:
            healthy = bool(await self._backend.health())
        except Exception:
            return OptionalServiceHealth(self.service_id, "unavailable", self.version, retryable=True)
        return OptionalServiceHealth(
            self.service_id,
            "healthy" if healthy else "unavailable",
            self.version,
            retryable=not healthy,
        )

    async def upsert(self, *, project_id: str, points: Sequence[VectorPoint]) -> VectorWriteResult:
        project_id = _token(project_id, field="project_id")
        batch = tuple(points)
        if not batch or len(batch) > _MAX_BATCH:
            raise OptionalServiceContractError("invalid_batch", "vector batch must contain 1..256 points")
        if not all(isinstance(point, VectorPoint) for point in batch):
            raise OptionalServiceContractError("invalid_batch", "vector batch contains an invalid point")
        batch = tuple(
            VectorPoint.build(
                point_id=point.point_id,
                source_ref=point.source_ref,
                checksum=point.checksum,
                vector=point.vector,
            )
            for point in batch
        )
        dimensions = {len(point.vector) for point in batch}
        if len(dimensions) != 1:
            raise OptionalServiceContractError("invalid_vector", "all vectors must have one dimension")
        namespace = _namespace(self.service_id, project_id)
        try:
            accepted = int(await self._backend.upsert(namespace=namespace, points=batch))
        except OptionalServiceContractError:
            raise
        except Exception as exc:
            raise OptionalServiceUnavailable(self.service_id) from exc
        if accepted < 0 or accepted > len(batch):
            raise OptionalServiceContractError("invalid_result", "backend returned an invalid write count")
        return VectorWriteResult(self.service_id, namespace, accepted)

    async def search(
        self, *, project_id: str, query_vector: Sequence[float], limit: int = 10
    ) -> tuple[VectorSearchHit, ...]:
        project_id = _token(project_id, field="project_id")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= _MAX_SEARCH_LIMIT:
            raise OptionalServiceContractError("invalid_limit", "search limit must be between 1 and 100")
        vector = _vector(query_vector, field="query_vector")
        namespace = _namespace(self.service_id, project_id)
        try:
            hits = tuple(await self._backend.search(namespace=namespace, query_vector=vector, limit=limit))
        except OptionalServiceContractError:
            raise
        except Exception as exc:
            raise OptionalServiceUnavailable(self.service_id) from exc
        if len(hits) > limit:
            raise OptionalServiceContractError("invalid_result", "backend returned too many search hits")
        if not all(isinstance(hit, VectorSearchHit) for hit in hits):
            raise OptionalServiceContractError("invalid_result", "backend returned an invalid search hit")
        return tuple(
            VectorSearchHit.build(
                point_id=hit.point_id,
                source_ref=hit.source_ref,
                checksum=hit.checksum,
                score=hit.score,
            )
            for hit in hits
        )

    async def delete_project(self, *, project_id: str) -> VectorDeleteResult:
        project_id = _token(project_id, field="project_id")
        namespace = _namespace(self.service_id, project_id)
        try:
            deleted = int(await self._backend.delete_namespace(namespace=namespace))
        except Exception as exc:
            raise OptionalServiceUnavailable(self.service_id) from exc
        if deleted < 0:
            raise OptionalServiceContractError("invalid_result", "backend returned an invalid delete count")
        return VectorDeleteResult(self.service_id, namespace, deleted)


@dataclass(frozen=True, slots=True)
class WorkflowCommand:
    workflow_id: str
    workflow_type: str
    version: str
    input_checksum: str
    idempotency_key: str

    @classmethod
    def build(
        cls,
        *,
        workflow_id: str,
        workflow_type: str,
        version: str,
        input_checksum: str,
        idempotency_key: str,
    ) -> WorkflowCommand:
        return cls(
            workflow_id=_token(workflow_id, field="workflow_id"),
            workflow_type=_token(workflow_type, field="workflow_type"),
            version=_version(version),
            input_checksum=_checksum(input_checksum, field="input_checksum"),
            idempotency_key=_token(idempotency_key, field="idempotency_key"),
        )


@dataclass(frozen=True, slots=True)
class WorkflowRunReference:
    workflow_id: str
    run_id: str
    version: str
    status: str

    @classmethod
    def build(cls, *, workflow_id: str, run_id: str, version: str, status: str) -> WorkflowRunReference:
        return cls(
            workflow_id=_token(workflow_id, field="workflow_id"),
            run_id=_token(run_id, field="run_id"),
            version=_version(version),
            status=_token(status, field="status"),
        )


class TemporalBackend(Protocol):
    """Minimal injected backend for a certified Temporal integration."""

    async def health(self) -> bool: ...

    async def start(
        self,
        *,
        namespace: str,
        command: WorkflowCommand,
    ) -> WorkflowRunReference: ...

    async def describe(self, *, namespace: str, workflow_id: str) -> WorkflowRunReference: ...

    async def signal(
        self,
        *,
        namespace: str,
        workflow_id: str,
        signal_name: str,
        payload_checksum: str,
    ) -> WorkflowRunReference: ...

    async def cancel(self, *, namespace: str, workflow_id: str) -> WorkflowRunReference: ...

    async def delete_namespace(self, *, namespace: str) -> int: ...


class TemporalWorkflowAdapter:
    """AIAT-controlled workflow facade; raw workflow inputs never cross it."""

    service_id = "temporal"
    schema_version = TEMPORAL_ADAPTER_SCHEMA

    def __init__(self, *, backend: TemporalBackend, version: str) -> None:
        self._backend = backend
        self.version = _version(version)

    async def health(self) -> OptionalServiceHealth:
        try:
            healthy = bool(await self._backend.health())
        except Exception:
            return OptionalServiceHealth(self.service_id, "unavailable", self.version, retryable=True)
        return OptionalServiceHealth(
            self.service_id,
            "healthy" if healthy else "unavailable",
            self.version,
            retryable=not healthy,
        )

    async def start(self, *, project_id: str, command: WorkflowCommand) -> WorkflowRunReference:
        project_id = _token(project_id, field="project_id")
        namespace = _namespace(self.service_id, project_id)
        try:
            result = await self._backend.start(namespace=namespace, command=command)
        except Exception as exc:
            raise OptionalServiceUnavailable(self.service_id) from exc
        return self._checked_result(result, command.workflow_id, command.version)

    async def describe(self, *, project_id: str, workflow_id: str) -> WorkflowRunReference:
        project_id = _token(project_id, field="project_id")
        workflow_id = _token(workflow_id, field="workflow_id")
        namespace = _namespace(self.service_id, project_id)
        try:
            result = await self._backend.describe(namespace=namespace, workflow_id=workflow_id)
        except Exception as exc:
            raise OptionalServiceUnavailable(self.service_id) from exc
        return self._checked_result(result, workflow_id, None)

    async def signal(
        self,
        *,
        project_id: str,
        workflow_id: str,
        signal_name: str,
        payload_checksum: str,
    ) -> WorkflowRunReference:
        project_id = _token(project_id, field="project_id")
        workflow_id = _token(workflow_id, field="workflow_id")
        signal_name = _token(signal_name, field="signal_name")
        payload_checksum = _checksum(payload_checksum, field="payload_checksum")
        namespace = _namespace(self.service_id, project_id)
        try:
            result = await self._backend.signal(
                namespace=namespace,
                workflow_id=workflow_id,
                signal_name=signal_name,
                payload_checksum=payload_checksum,
            )
        except Exception as exc:
            raise OptionalServiceUnavailable(self.service_id) from exc
        return self._checked_result(result, workflow_id, None)

    async def cancel(self, *, project_id: str, workflow_id: str) -> WorkflowRunReference:
        project_id = _token(project_id, field="project_id")
        workflow_id = _token(workflow_id, field="workflow_id")
        namespace = _namespace(self.service_id, project_id)
        try:
            result = await self._backend.cancel(namespace=namespace, workflow_id=workflow_id)
        except Exception as exc:
            raise OptionalServiceUnavailable(self.service_id) from exc
        return self._checked_result(result, workflow_id, None)

    async def delete_project(self, *, project_id: str) -> int:
        project_id = _token(project_id, field="project_id")
        namespace = _namespace(self.service_id, project_id)
        try:
            deleted = int(await self._backend.delete_namespace(namespace=namespace))
        except Exception as exc:
            raise OptionalServiceUnavailable(self.service_id) from exc
        if deleted < 0:
            raise OptionalServiceContractError("invalid_result", "backend returned an invalid delete count")
        return deleted

    def _checked_result(
        self, result: WorkflowRunReference, workflow_id: str, expected_version: str | None
    ) -> WorkflowRunReference:
        if not isinstance(result, WorkflowRunReference):
            raise OptionalServiceContractError("invalid_result", "backend returned an invalid workflow reference")
        checked = WorkflowRunReference.build(
            workflow_id=result.workflow_id,
            run_id=result.run_id,
            version=result.version,
            status=result.status,
        )
        if checked.workflow_id != workflow_id:
            raise OptionalServiceContractError("invalid_result", "backend returned a mismatched workflow ID")
        if expected_version is not None and checked.version != expected_version:
            raise OptionalServiceContractError("version_mismatch", "backend returned a mismatched workflow version")
        return checked


__all__ = [
    "OPTIONAL_MEMORY_ADAPTER_SCHEMA",
    "QDRANT_ADAPTER_SCHEMA",
    "TEMPORAL_ADAPTER_SCHEMA",
    "OptionalServiceContractError",
    "OptionalServiceUnavailable",
    "OptionalServiceHealth",
    "VectorPoint",
    "VectorSearchHit",
    "VectorWriteResult",
    "VectorDeleteResult",
    "QdrantBackend",
    "QdrantVectorAdapter",
    "WorkflowCommand",
    "WorkflowRunReference",
    "TemporalBackend",
    "TemporalWorkflowAdapter",
]
