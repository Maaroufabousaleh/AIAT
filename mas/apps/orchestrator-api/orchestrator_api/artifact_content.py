"""Bounded, project-scoped storage for specialist-worker artifact bytes.

Worker artifact rows are durable metadata.  This module stores the matching
bytes in the configured S3-compatible object store and reads them back only
after the orchestrator has verified the canonical project/run relationship.
"""

from __future__ import annotations

import hashlib
import inspect
import mimetypes
import os
import re
import stat
import uuid
from collections.abc import Awaitable, Callable, Mapping
from pathlib import PurePosixPath
from typing import Any

from mas_core.memory.blob import BlobClient, BlobRef, verify_blob_readback

MAX_ARTIFACT_BYTES = 10 * 1024 * 1024
MAX_ARTIFACT_URI_LENGTH = 1_000
_MIME_TYPE = re.compile(r"^[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ArtifactContentError(ValueError):
    """Artifact metadata or workspace path failed validation."""


class ArtifactStorageUnavailable(RuntimeError):
    """The configured object store cannot be reached or lacks credentials."""


class ArtifactIntegrityError(RuntimeError):
    """The stored artifact bytes do not match their canonical metadata."""


def _object_store_settings() -> tuple[str, str, str, str]:
    endpoint = (os.getenv("MINIO_ENDPOINT") or os.getenv("BLOB_ENDPOINT_URL") or "").strip()
    access_key = (os.getenv("MINIO_ACCESS_KEY") or os.getenv("AWS_ACCESS_KEY_ID") or "").strip()
    secret_key = (os.getenv("MINIO_SECRET_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY") or "").strip()
    bucket = (os.getenv("MINIO_BUCKET") or os.getenv("BLOB_BUCKET") or "mas-agents").strip()
    if not endpoint or not access_key or not secret_key or not bucket:
        raise ArtifactStorageUnavailable("artifact object storage is not configured")
    return endpoint, access_key, secret_key, bucket


def validate_artifact_uri(uri: str) -> tuple[str, ...]:
    """Return safe relative POSIX path segments or fail closed."""
    if not isinstance(uri, str) or not uri or len(uri) > MAX_ARTIFACT_URI_LENGTH:
        raise ArtifactContentError("artifact path is empty or exceeds its limit")
    if "\x00" in uri or "\\" in uri or uri.startswith("/"):
        raise ArtifactContentError("artifact path must be a relative POSIX path")
    segments = tuple(uri.split("/"))
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ArtifactContentError("artifact path contains an unsafe component")
    if PurePosixPath(uri).is_absolute():
        raise ArtifactContentError("artifact path must be relative")
    return segments


def read_workspace_artifact(
    workspace_root: str | os.PathLike[str],
    run_id: uuid.UUID,
    uri: str,
    *,
    max_bytes: int = MAX_ARTIFACT_BYTES,
) -> bytes:
    """Read one regular file beneath ``workspace_root/<run_id>`` safely.

    Each path component is opened relative to a directory descriptor with
    ``O_NOFOLLOW``. This prevents traversal and symlink substitution from
    escaping the run workspace, including races between validation and open.
    """
    segments = validate_artifact_uri(uri)
    if max_bytes < 0 or max_bytes > MAX_ARTIFACT_BYTES:
        raise ValueError("artifact read limit is invalid")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise ArtifactContentError("safe no-follow file access is unavailable")

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        directory_flags |= os.O_CLOEXEC
        file_flags |= os.O_CLOEXEC

    directory_fd: int | None = None
    child_fds: list[int] = []
    file_fd: int | None = None
    try:
        directory_fd = os.open(os.fspath(workspace_root), directory_flags)
        current_fd = os.open(str(run_id), directory_flags, dir_fd=directory_fd)
        child_fds.append(current_fd)
        for segment in segments[:-1]:
            current_fd = os.open(segment, directory_flags, dir_fd=current_fd)
            child_fds.append(current_fd)

        file_fd = os.open(segments[-1], file_flags, dir_fd=current_fd)
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            raise ArtifactContentError("artifact path is not a regular file")
        if before.st_size > max_bytes:
            raise ArtifactContentError("artifact exceeds the per-file size limit")

        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(file_fd, min(64 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise ArtifactContentError("artifact exceeds the per-file size limit")
        after = os.fstat(file_fd)
        if total != before.st_size or after.st_size != before.st_size:
            raise ArtifactContentError("artifact changed while it was being read")
        return b"".join(chunks)
    except ArtifactContentError:
        raise
    except OSError as exc:
        # Do not expose local paths or filenames in an API/log error.
        raise ArtifactContentError("artifact is unavailable inside its run workspace") from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)
        for descriptor in reversed(child_fds):
            os.close(descriptor)
        if directory_fd is not None:
            os.close(directory_fd)


def _artifact_mime_type(uri: str, supplied: Any = None) -> str:
    detected = mimetypes.guess_type(uri)[0]
    candidate = detected or (str(supplied).strip() if supplied is not None else "")
    if not candidate or not _MIME_TYPE.fullmatch(candidate):
        return "application/octet-stream"
    return candidate.lower()


def make_opencode_artifact_registrar(
    storage: Any,
    *,
    worker_id: uuid.UUID,
    workspace_root: str | os.PathLike[str] = "/workspace",
    blob_client_factory: Callable[..., Any] = BlobClient,
) -> Callable[[Any], Awaitable[None]]:
    """Build the orchestrator-owned registrar used by the certified adapter.

    The callback derives project ownership from the durable WorkerRun, never
    from artifact metadata or task input. It writes a deterministic object key
    before WorkerRunController persists the terminal result and artifact row.
    """

    async def register(artifact: Any) -> None:
        metadata = getattr(artifact, "metadata", None)
        if not isinstance(metadata, dict):
            raise ArtifactContentError("worker artifact metadata is invalid")
        try:
            run_id = uuid.UUID(str(metadata.get("worker_run_id") or ""))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ArtifactContentError("worker artifact has no canonical run binding") from exc

        get_run = getattr(storage, "get_worker_run", None)
        if not callable(get_run):
            raise ArtifactContentError("durable WorkerRun lookup is unavailable")
        run = await get_run(run_id)
        if (
            not isinstance(run, Mapping)
            or str(run.get("id")) != str(run_id)
            or str(run.get("worker_id")) != str(worker_id)
            or not run.get("project_id")
        ):
            raise ArtifactContentError("artifact WorkerRun ownership is unavailable")
        try:
            project_id = uuid.UUID(str(run["project_id"]))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ArtifactContentError("artifact project ownership is invalid") from exc
        if metadata.get("project_id") not in (None, str(project_id)):
            raise ArtifactContentError("artifact metadata conflicts with WorkerRun project ownership")

        uri = str(getattr(artifact, "uri", "") or "")
        segments = validate_artifact_uri(uri)
        content = read_workspace_artifact(workspace_root, run_id, uri)
        digest = hashlib.sha256(content).hexdigest()
        expected_digest = str(getattr(artifact, "sha256", "") or "").lower()
        declared_size = getattr(artifact, "size_bytes", None)
        if not _SHA256.fullmatch(expected_digest) or digest != expected_digest:
            raise ArtifactIntegrityError("workspace artifact checksum does not match its WorkerArtifact")
        if declared_size is not None and int(declared_size) != len(content):
            raise ArtifactIntegrityError("workspace artifact size does not match its WorkerArtifact")

        endpoint, access_key, secret_key, bucket = _object_store_settings()
        object_key = "/".join(("worker-runs", str(run_id), *segments))
        content_type = _artifact_mime_type(uri, getattr(artifact, "mime_type", None))
        blob = blob_client_factory(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            bucket=bucket,
        )
        try:
            await blob.connect()
            ref = await blob.upload(
                str(project_id),
                object_key,
                content,
                content_type=content_type,
                bucket=bucket,
            )
        except Exception as exc:
            raise ArtifactStorageUnavailable("worker artifact upload failed") from exc
        finally:
            await blob.close()

        if (
            ref.bucket != bucket
            or ref.key != f"{project_id}/{object_key}"
            or ref.sha256 != digest
            or ref.size_bytes != len(content)
        ):
            raise ArtifactIntegrityError("object store returned an inconsistent artifact reference")
        # WorkerRunController merges this bounded metadata into the canonical
        # artifacts row before it stores the terminal WorkerRun result.
        metadata.update(
            {
                "project_id": str(project_id),
                "blob_bucket": ref.bucket,
                "blob_key": ref.key,
                "blob_sha256": ref.sha256,
                "blob_size_bytes": ref.size_bytes,
                "blob_content_type": content_type,
            }
        )

    return register


def artifact_download_reference(
    *,
    project_id: uuid.UUID,
    artifact: Mapping[str, Any],
    run_id: uuid.UUID,
) -> BlobRef:
    """Validate canonical metadata and return a project-confined blob ref."""
    metadata = artifact.get("metadata")
    if not isinstance(metadata, Mapping):
        raise ArtifactContentError("artifact content reference is unavailable")
    if str(metadata.get("project_id") or "") != str(project_id):
        raise ArtifactContentError("artifact is not owned by this project")
    if str(metadata.get("worker_run_id") or "") != str(run_id):
        raise ArtifactContentError("artifact run binding is invalid")

    path = str(artifact.get("path") or "")
    segments = validate_artifact_uri(path)
    expected_key = "/".join((str(project_id), "worker-runs", str(run_id), *segments))
    bucket = str(metadata.get("blob_bucket") or "")
    key = str(metadata.get("blob_key") or "")
    sha256 = str(artifact.get("sha256") or "").lower()
    blob_sha256 = str(metadata.get("blob_sha256") or "").lower()
    size_bytes = artifact.get("size_bytes")
    blob_size = metadata.get("blob_size_bytes")
    content_type = _artifact_mime_type(path, metadata.get("blob_content_type"))

    try:
        configured_bucket = _object_store_settings()[3]
    except ArtifactStorageUnavailable:
        raise
    if (
        bucket != configured_bucket
        or key != expected_key
        or not _SHA256.fullmatch(sha256)
        or blob_sha256 != sha256
        or isinstance(size_bytes, bool)
        or not isinstance(size_bytes, int)
        or size_bytes < 0
        or size_bytes > MAX_ARTIFACT_BYTES
        or isinstance(blob_size, bool)
        or not isinstance(blob_size, int)
        or blob_size != size_bytes
    ):
        raise ArtifactContentError("artifact content reference failed validation")
    return BlobRef(
        bucket=bucket,
        key=key,
        sha256=sha256,
        size_bytes=size_bytes,
        content_type=content_type,
    )


async def download_artifact_bytes(
    blob: Any,
    ref: BlobRef,
    *,
    max_bytes: int = MAX_ARTIFACT_BYTES,
) -> bytes:
    """Read a bounded object and verify its declared SHA-256 and byte count."""
    if max_bytes < 0 or max_bytes > MAX_ARTIFACT_BYTES:
        raise ValueError("artifact download limit is invalid")
    response = await blob.client.get_object(Bucket=ref.bucket, Key=ref.key)
    body = response.get("Body")
    if body is None:
        raise ArtifactIntegrityError("object store returned no artifact body")
    content_length = response.get("ContentLength")
    if isinstance(content_length, int) and content_length > max_bytes:
        raise ArtifactIntegrityError("stored artifact exceeds the per-file size limit")
    chunks: list[bytes] = []
    total = 0
    read_error: BaseException | None = None
    try:
        while True:
            chunk = await body.read(min(64 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise ArtifactIntegrityError("stored artifact exceeds the per-file size limit")
    except BaseException as exc:
        read_error = exc
        raise
    finally:
        close = getattr(body, "close", None)
        if callable(close):
            try:
                close_result = close()
                if inspect.isawaitable(close_result):
                    await close_result
            except Exception:
                if read_error is None:
                    raise
    data = b"".join(chunks)
    try:
        return verify_blob_readback(ref, data)
    except ValueError as exc:
        raise ArtifactIntegrityError("stored artifact failed its integrity check") from exc


def new_artifact_blob_client(*, blob_client_factory: Callable[..., Any] | None = None) -> Any:
    """Create a client for the configured artifact bucket without exposing settings."""
    endpoint, access_key, secret_key, bucket = _object_store_settings()
    factory = blob_client_factory or BlobClient
    return factory(endpoint, access_key=access_key, secret_key=secret_key, bucket=bucket)


def artifact_download_filename(path: str, artifact_id: int) -> str:
    """Return a bounded filename safe for a Content-Disposition header."""
    basename = path.rsplit("/", 1)[-1]
    filename = re.sub(r"[^A-Za-z0-9._-]", "_", basename)[:180].strip("._")
    return filename or f"artifact-{artifact_id}.bin"


__all__ = [
    "MAX_ARTIFACT_BYTES",
    "ArtifactContentError",
    "ArtifactIntegrityError",
    "ArtifactStorageUnavailable",
    "artifact_download_filename",
    "artifact_download_reference",
    "download_artifact_bytes",
    "make_opencode_artifact_registrar",
    "new_artifact_blob_client",
    "read_workspace_artifact",
    "validate_artifact_uri",
]
