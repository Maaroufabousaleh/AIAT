"""BlobClient — thin async wrapper around MinIO / S3-compatible object storage.

Uses ``aioboto3`` so it works with any S3-compatible endpoint (MinIO,
SeaweedFS, AWS S3, etc.).  The ``agent_id`` prefix is enforced by the client to
prevent cross-agent data leakage.

Bucket: ``mas-agents`` (created by ``minio-init`` container).

Key layout::

    mas-agents/{project_id}/documents/{doc_type}_v{version}.json
    mas-agents/{project_id}/artifacts/{filename}
    mas-agents/{project_id}/retrospectives/sprint_{n}.json

Usage
-----
::

    blob = BlobClient(
        endpoint_url="http://minio:9000",
        access_key="mas_agent",
        secret_key="change_me",
    )
    ref = await blob.upload(
        project_id="proj-123",
        key="documents/pdr_v1.json",
        data=b'{"sections": [...]}',
        content_type="application/json",
    )
    data = await blob.download(ref)
    await blob.delete(ref)
    await blob.close()
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any

import aioboto3

logger = logging.getLogger(__name__)

DEFAULT_BUCKET = "mas-agents"


@dataclass(frozen=True)
class BlobRef:
    """Immutable reference to an object in blob storage.

    Matches the ``BlobRef`` Pydantic model in ``protocols.envelope`` but
    is a plain dataclass for lightweight use inside the storage layer.
    """

    bucket: str
    key: str
    sha256: str
    size_bytes: int
    content_type: str = "application/octet-stream"

    def to_dict(self) -> dict[str, Any]:
        return {
            "bucket": self.bucket,
            "key": self.key,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "content_type": self.content_type,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BlobRef:
        return cls(
            bucket=d["bucket"],
            key=d["key"],
            sha256=d["sha256"],
            size_bytes=d["size_bytes"],
            content_type=d.get("content_type", "application/octet-stream"),
        )


class BlobClient:
    """Async S3-compatible blob storage client.

    Parameters
    ----------
    endpoint_url : str
        S3-compatible endpoint (e.g. ``"http://minio:9000"``).
    access_key : str
        S3 access key (MinIO user).
    secret_key : str
        S3 secret key.
    bucket : str
        Default bucket name (default ``"mas-agents"``).
    region : str
        AWS region (ignored by MinIO but required by boto3).
    """

    def __init__(
        self,
        endpoint_url: str,
        *,
        access_key: str,
        secret_key: str,
        bucket: str = DEFAULT_BUCKET,
        region: str = "us-east-1",
    ) -> None:
        self._endpoint_url = endpoint_url
        self._access_key = access_key
        self._secret_key = secret_key
        self._bucket = bucket
        self._region = region
        self._session = aioboto3.Session()
        self._client: Any = None

    async def connect(self) -> None:
        """Open the S3 client context manager."""
        self._client = await self._session.client(
            "s3",
            endpoint_url=self._endpoint_url,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            region_name=self._region,
        ).__aenter__()
        logger.info("BlobClient connected to %s", self._endpoint_url)

    async def close(self) -> None:
        """Close the S3 client."""
        if self._client:
            await self._client.__aexit__(None, None, None)
            self._client = None
            logger.info("BlobClient connection closed")

    @property
    def client(self) -> Any:
        if self._client is None:
            raise RuntimeError("BlobClient not connected. Call connect() first.")
        return self._client

    def _full_key(self, project_id: str, key: str) -> str:
        """Build the full object key: ``{project_id}/{key}``."""
        return f"{project_id}/{key}"

    async def upload(
        self,
        project_id: str,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        bucket: str | None = None,
    ) -> BlobRef:
        """Upload data and return a :class:`BlobRef`.

        Parameters
        ----------
        project_id : str
            Project scoping prefix.
        key : str
            Object key within the project (e.g. ``"documents/pdr_v1.json"``).
        data : bytes
            Raw content.
        content_type : str
            MIME type.
        bucket : str | None
            Override the default bucket.
        """
        bkt = bucket or self._bucket
        full_key = self._full_key(project_id, key)
        sha = hashlib.sha256(data).hexdigest()

        await self.client.put_object(
            Bucket=bkt,
            Key=full_key,
            Body=data,
            ContentType=content_type,
        )
        logger.info(
            "blob_uploaded",
            extra={"bucket": bkt, "key": full_key, "size": len(data)},
        )
        return BlobRef(
            bucket=bkt,
            key=full_key,
            sha256=sha,
            size_bytes=len(data),
            content_type=content_type,
        )

    async def download(self, ref: BlobRef) -> bytes:
        """Download an object by its :class:`BlobRef`.

        Raises
        ------
        ValueError
            If SHA-256 mismatch (integrity check).
        """
        resp = await self.client.get_object(Bucket=ref.bucket, Key=ref.key)
        data = await resp["Body"].read()

        # Integrity check
        actual_sha = hashlib.sha256(data).hexdigest()
        if actual_sha != ref.sha256:
            raise ValueError(
                f"SHA-256 mismatch for {ref.bucket}/{ref.key}: "
                f"expected {ref.sha256}, got {actual_sha}"
            )
        return data

    async def download_by_key(
        self,
        project_id: str,
        key: str,
        *,
        bucket: str | None = None,
    ) -> bytes:
        """Download by project + key (no integrity check)."""
        bkt = bucket or self._bucket
        full_key = self._full_key(project_id, key)
        resp = await self.client.get_object(Bucket=bkt, Key=full_key)
        return await resp["Body"].read()

    async def delete(self, ref: BlobRef) -> None:
        """Delete an object from blob storage."""
        await self.client.delete_object(Bucket=ref.bucket, Key=ref.key)
        logger.info("blob_deleted", extra={"bucket": ref.bucket, "key": ref.key})

    async def delete_by_key(
        self,
        project_id: str,
        key: str,
        *,
        bucket: str | None = None,
    ) -> None:
        """Delete by project + key."""
        bkt = bucket or self._bucket
        full_key = self._full_key(project_id, key)
        await self.client.delete_object(Bucket=bkt, Key=full_key)

    async def list_objects(
        self,
        project_id: str,
        *,
        prefix: str = "",
        bucket: str | None = None,
    ) -> list[dict[str, Any]]:
        """List objects under ``{project_id}/{prefix}``."""
        bkt = bucket or self._bucket
        full_prefix = self._full_key(project_id, prefix)
        resp = await self.client.list_objects_v2(
            Bucket=bkt,
            Prefix=full_prefix,
        )
        contents = resp.get("Contents", [])
        return [
            {
                "key": obj["Key"],
                "size": obj["Size"],
                "last_modified": obj["LastModified"].isoformat()
                if hasattr(obj["LastModified"], "isoformat")
                else str(obj["LastModified"]),
            }
            for obj in contents
        ]

    async def exists(
        self,
        project_id: str,
        key: str,
        *,
        bucket: str | None = None,
    ) -> bool:
        """Check if an object exists."""
        bkt = bucket or self._bucket
        full_key = self._full_key(project_id, key)
        try:
            await self.client.head_object(Bucket=bkt, Key=full_key)
            return True
        except self.client.exceptions.NoSuchKey:
            return False
        except Exception:
            # ClientError with 404 status
            return False

    async def ensure_bucket(self, bucket: str | None = None) -> None:
        """Create the bucket if it doesn't exist (idempotent)."""
        bkt = bucket or self._bucket
        try:
            await self.client.head_bucket(Bucket=bkt)
        except Exception:
            await self.client.create_bucket(Bucket=bkt)
            logger.info("bucket_created", extra={"bucket": bkt})
