"""Provider-neutral conformance checks for the S3-compatible blob contract.

The production object-store boundary is deliberately small: upload and
download bytes, preserve a checksum-bearing :class:`~mas_core.memory.blob.BlobRef`,
    list only a project prefix, verify checksum/size read-back, check existence,
    and delete by reference.  This
module turns that boundary into a deterministic report that can be run against
the existing MinIO client, a future SeaweedFS/Garage/R2 adapter, or the local
fixture below.

The fixture is not a production backend.  It exists so unit tests and the
checked-in conformance command can exercise the contract without Docker or a
network service.  Provider-specific HTTP, large-object, multipart, outage,
benchmark, backup, and restore evidence remains an external/live gate.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol

from .blob import BlobClient, BlobRef, verify_blob_readback

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

OBJECT_STORE_CONFORMANCE_SCHEMA = "aiat.object-store-conformance.v1"
ObjectStoreConformanceStatus = Literal["PASS", "FAIL"]


class ObjectStoreAdapter(Protocol):
    """Minimum async interface required by the object-store conformance suite."""

    async def upload(
        self,
        project_id: str,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        bucket: str | None = None,
    ) -> BlobRef: ...

    async def download(self, ref: BlobRef) -> bytes: ...

    async def delete(self, ref: BlobRef) -> None: ...

    async def list_objects(
        self,
        project_id: str,
        *,
        prefix: str = "",
        bucket: str | None = None,
    ) -> list[dict[str, Any]]: ...

    async def exists(
        self,
        project_id: str,
        key: str,
        *,
        bucket: str | None = None,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class ObjectStoreConformanceCase:
    """One deterministic contract assertion."""

    case_id: str
    status: ObjectStoreConformanceStatus
    detail: str


@dataclass(frozen=True, slots=True)
class ObjectStoreConformanceReport:
    """Machine-readable result of the object-store contract fixture."""

    schema_version: str
    adapter_type: str
    adapter_version: str
    cases: tuple[ObjectStoreConformanceCase, ...]

    @property
    def passed(self) -> bool:
        return not any(case.status == "FAIL" for case in self.cases)

    @property
    def counts(self) -> dict[str, int]:
        return {
            status: sum(1 for case in self.cases if case.status == status)
            for status in ("PASS", "FAIL")
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "adapter_type": self.adapter_type,
            "adapter_version": self.adapter_version,
            "passed": self.passed,
            "counts": self.counts,
            "cases": [
                {
                    "case_id": case.case_id,
                    "status": case.status,
                    "detail": case.detail,
                }
                for case in self.cases
            ],
        }


class InMemoryObjectStore:
    """Deterministic fixture adapter; never use this as a production store."""

    adapter_type = "in-memory-fixture"
    adapter_version = "fixture-v1"

    def __init__(self, *, bucket: str = "mas-agents") -> None:
        self.bucket = bucket
        self._objects: dict[tuple[str, str], tuple[bytes, str]] = {}

    @staticmethod
    def _full_key(project_id: str, key: str) -> str:
        BlobClient._validate_path_component(project_id, "project_id")
        BlobClient._validate_path_component(key, "key")
        return f"{project_id}/{key}"

    def _bucket_name(self, bucket: str | None) -> str:
        return bucket or self.bucket

    async def upload(
        self,
        project_id: str,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        bucket: str | None = None,
    ) -> BlobRef:
        # Reuse the exact path rules of the production client without opening
        # a network connection.  ``_full_key`` is pure validation/build logic.
        full_key = self._full_key(project_id, key)
        bucket_name = self._bucket_name(bucket)
        payload = bytes(data)
        self._objects[(bucket_name, full_key)] = (payload, content_type)
        return BlobRef(
            bucket=bucket_name,
            key=full_key,
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            content_type=content_type,
        )

    async def download(self, ref: BlobRef) -> bytes:
        payload, _content_type = self._objects[(ref.bucket, ref.key)]
        return verify_blob_readback(ref, payload)

    async def delete(self, ref: BlobRef) -> None:
        self._objects.pop((ref.bucket, ref.key), None)

    async def list_objects(
        self,
        project_id: str,
        *,
        prefix: str = "",
        bucket: str | None = None,
    ) -> list[dict[str, Any]]:
        full_prefix = self._full_key(project_id, prefix) if prefix else f"{project_id}/"
        bucket_name = self._bucket_name(bucket)
        rows = []
        for (stored_bucket, key), (payload, _content_type) in self._objects.items():
            if stored_bucket == bucket_name and key.startswith(full_prefix):
                rows.append(
                    {
                        "key": key,
                        "size": len(payload),
                        "last_modified": datetime(1970, 1, 1, tzinfo=UTC).isoformat(),
                    }
                )
        return sorted(rows, key=lambda row: str(row["key"]))

    async def exists(
        self,
        project_id: str,
        key: str,
        *,
        bucket: str | None = None,
    ) -> bool:
        full_key = self._full_key(project_id, key)
        return (self._bucket_name(bucket), full_key) in self._objects


async def run_object_store_conformance(
    store: ObjectStoreAdapter,
    *,
    project_id: str = "aiat-conformance-project",
    peer_project_id: str = "aiat-conformance-peer",
    bucket: str | None = None,
) -> ObjectStoreConformanceReport:
    """Run the required object-store contract against a disposable prefix.

    The fixture uses stable project and key names so its JSON output can be
    compared across runs.  Any objects created by this function are deleted in
    a final cleanup step; callers should still use a disposable project scope
    when pointing it at a real provider.
    """

    cases: list[ObjectStoreConformanceCase] = []
    primary_ref: BlobRef | None = None
    empty_ref: BlobRef | None = None
    peer_ref: BlobRef | None = None

    async def case(case_id: str, operation: Callable[[], Awaitable[str]]) -> None:
        try:
            detail = await operation()
        except Exception as exc:  # pragma: no cover - exercised by adapters
            cases.append(
                ObjectStoreConformanceCase(
                    case_id,
                    "FAIL",
                    f"{type(exc).__name__}: {exc}",
                )
            )
        else:
            cases.append(ObjectStoreConformanceCase(case_id, "PASS", detail))

    payload = b"aiat-object-store-conformance\n"
    primary_key = "conformance/fixture.bin"
    empty_key = "conformance/empty.bin"

    async def upload_reference() -> str:
        nonlocal primary_ref
        primary_ref = await store.upload(
            project_id,
            primary_key,
            payload,
            content_type="application/octet-stream",
            bucket=bucket,
        )
        expected_key = f"{project_id}/{primary_key}"
        expected_sha = hashlib.sha256(payload).hexdigest()
        if primary_ref.key != expected_key:
            raise AssertionError(f"reference key is {primary_ref.key!r}, expected {expected_key!r}")
        if primary_ref.sha256 != expected_sha or primary_ref.size_bytes != len(payload):
            raise AssertionError("reference checksum or size does not describe the uploaded bytes")
        if primary_ref.content_type != "application/octet-stream":
            raise AssertionError("reference lost the content type")
        return "uploaded a scoped checksum-bearing reference"

    async def download_round_trip() -> str:
        if primary_ref is None:
            raise AssertionError("upload_reference did not produce a reference")
        if await store.download(primary_ref) != payload:
            raise AssertionError("downloaded bytes differ from the uploaded payload")
        return "download returned the exact uploaded bytes"

    async def integrity_mismatch() -> str:
        if primary_ref is None:
            raise AssertionError("upload_reference did not produce a reference")
        tampered = replace(primary_ref, sha256="0" * 64)
        try:
            await store.download(tampered)
        except ValueError:
            return "tampered checksum was rejected"
        raise AssertionError("download accepted a tampered checksum reference")

    async def empty_round_trip() -> str:
        nonlocal empty_ref
        empty_ref = await store.upload(
            project_id,
            empty_key,
            b"",
            content_type="application/octet-stream",
            bucket=bucket,
        )
        if empty_ref.size_bytes != 0 or await store.download(empty_ref) != b"":
            raise AssertionError("empty objects did not round-trip with zero size")
        return "zero-byte object round-tripped"

    async def scoped_listing() -> str:
        nonlocal peer_ref
        peer_ref = await store.upload(
            peer_project_id,
            primary_key,
            b"peer-object",
            content_type="application/octet-stream",
            bucket=bucket,
        )
        project_objects = await store.list_objects(project_id, bucket=bucket)
        project_keys = {str(row.get("key")) for row in project_objects}
        if primary_ref is None or primary_ref.key not in project_keys:
            raise AssertionError("project listing omitted the uploaded object")
        if any(key.startswith(f"{peer_project_id}/") for key in project_keys):
            raise AssertionError("project listing leaked another project's object")
        prefix_objects = await store.list_objects(project_id, prefix="conformance", bucket=bucket)
        if {str(row.get("key")) for row in prefix_objects} != project_keys:
            raise AssertionError("prefix listing did not preserve the project scope")
        return f"listed {len(project_keys)} project-scoped objects without peer leakage"

    async def existence_and_delete() -> str:
        if primary_ref is None:
            raise AssertionError("upload_reference did not produce a reference")
        if not await store.exists(project_id, primary_key, bucket=bucket):
            raise AssertionError("exists returned false for an uploaded object")
        await store.delete(primary_ref)
        if await store.exists(project_id, primary_key, bucket=bucket):
            raise AssertionError("exists returned true after deletion")
        remaining = await store.list_objects(project_id, prefix="conformance", bucket=bucket)
        if any(str(row.get("key")) == primary_ref.key for row in remaining):
            raise AssertionError("deleted object remained in the project listing")
        return "exists/delete/list agree after deletion"

    async def path_validation() -> str:
        invalid_calls = (
            (project_id, "../escape.bin"),
            ("../escape-project", primary_key),
            (project_id, "/absolute.bin"),
        )
        for invalid_project, invalid_key in invalid_calls:
            try:
                await store.upload(
                    invalid_project,
                    invalid_key,
                    b"must-not-persist",
                    bucket=bucket,
                )
            except ValueError:
                continue
            raise AssertionError(
                f"path validation accepted project={invalid_project!r}, key={invalid_key!r}"
            )
        return "traversal and absolute-key attempts were rejected"

    await case("upload_reference", upload_reference)
    await case("download_round_trip", download_round_trip)
    await case("integrity_mismatch_rejection", integrity_mismatch)
    await case("empty_object_round_trip", empty_round_trip)
    await case("project_scope_listing", scoped_listing)
    await case("exists_delete_list", existence_and_delete)
    await case("path_validation", path_validation)

    cleanup_errors: list[str] = []
    for ref in (primary_ref, empty_ref, peer_ref):
        if ref is not None:
            try:
                await store.delete(ref)
            except Exception as exc:  # pragma: no cover - provider-specific
                cleanup_errors.append(f"{ref.key}: {type(exc).__name__}: {exc}")
    if cleanup_errors:
        cases.append(
            ObjectStoreConformanceCase(
                "cleanup",
                "FAIL",
                "; ".join(cleanup_errors),
            )
        )
    else:
        cases.append(ObjectStoreConformanceCase("cleanup", "PASS", "fixture objects removed"))

    return ObjectStoreConformanceReport(
        schema_version=OBJECT_STORE_CONFORMANCE_SCHEMA,
        adapter_type=str(getattr(store, "adapter_type", type(store).__name__)),
        adapter_version=str(getattr(store, "adapter_version", "unknown")),
        cases=tuple(cases),
    )


__all__ = [
    "OBJECT_STORE_CONFORMANCE_SCHEMA",
    "InMemoryObjectStore",
    "ObjectStoreAdapter",
    "ObjectStoreConformanceCase",
    "ObjectStoreConformanceReport",
    "run_object_store_conformance",
]
