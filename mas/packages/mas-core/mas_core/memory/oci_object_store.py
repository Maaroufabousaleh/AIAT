"""OCI Object Storage adapter with fail-closed SSE/KMS evidence.

The normal AIAT object-store contract is provider-neutral and S3-compatible.
OCI provider-managed encryption needs an additional, explicit boundary: the
bucket's customer-managed Vault key and each tested object's encryption
metadata must be read back from OCI.  This module keeps that proof in the
existing ``ObjectStoreAdapter``/``MultipartObjectStoreAdapter`` interfaces and
does not persist credentials or object payloads.

The OCI SDK is optional at import time.  ``OCIObjectStorageSdkTransport``
loads it only for a live run, while deterministic tests use the injected fake
transport.  This keeps WSL and ordinary fixture runs independent of OCI
credentials and SDK installation.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from .blob import BlobClient, BlobRef, verify_blob_readback
from .object_store_conformance import run_object_store_conformance
from .object_store_multipart import MultipartUploadConfig, run_object_store_multipart_probe

OCI_OBJECT_STORE_SCHEMA = "aiat.object-store-oci-sse-kms.v1"
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class OCIProviderUnavailable(RuntimeError):
    """The live OCI SDK/credential/provider boundary is unavailable."""


class OCIEncryptionEvidenceError(RuntimeError):
    """The provider did not positively prove the configured SSE/KMS key."""


class OCITransportError(RuntimeError):
    """Transport failure with an optional provider status code."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class OCIObjectStoreConfig:
    """Non-secret OCI target configuration.

    Credentials are intentionally represented only by a governed config-file
    or instance-principal reference.  The values never enter an evidence
    report.
    """

    region: str
    namespace: str
    bucket: str
    kms_key_id: str
    auth_profile: str = "DEFAULT"
    config_file: str | None = None
    auth_mode: str = "config"
    endpoint: str | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> tuple[OCIObjectStoreConfig | None, list[str]]:
        values = environ if environ is not None else os.environ
        required = {
            "OCI_REGION": values.get("OCI_REGION", "").strip(),
            "OCI_NAMESPACE": values.get("OCI_NAMESPACE", "").strip(),
            "OCI_BUCKET": values.get("OCI_BUCKET", "").strip(),
            "OCI_KMS_KEY_ID": values.get("OCI_KMS_KEY_ID", "").strip(),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            return None, missing
        mode = values.get("OCI_AUTH_MODE", "config").strip().lower() or "config"
        if mode not in {"config", "instance_principal"}:
            return None, ["OCI_AUTH_MODE must be config or instance_principal"]
        return (
            cls(
                region=required["OCI_REGION"],
                namespace=required["OCI_NAMESPACE"],
                bucket=required["OCI_BUCKET"],
                kms_key_id=required["OCI_KMS_KEY_ID"],
                auth_profile=values.get("OCI_AUTH_PROFILE", "DEFAULT").strip() or "DEFAULT",
                config_file=values.get("OCI_CONFIG_FILE", "").strip() or None,
                auth_mode=mode,
                endpoint=values.get("OCI_OBJECT_STORAGE_ENDPOINT", "").strip() or None,
            ),
            [],
        )

    @property
    def kms_key_id_sha256(self) -> str:
        return hashlib.sha256(self.kms_key_id.encode("utf-8")).hexdigest()

    def evidence_identity(self) -> dict[str, Any]:
        return {
            "provider": "oci-object-storage",
            "region": self.region,
            "namespace_configured": bool(self.namespace),
            "bucket_configured": bool(self.bucket),
            "kms_key_id_sha256": self.kms_key_id_sha256,
            "auth_mode": self.auth_mode,
            "auth_profile_configured": bool(self.auth_profile),
            "config_file_configured": bool(self.config_file),
            "endpoint_configured": bool(self.endpoint),
            "secret_values_persisted": False,
        }


class OCIObjectStorageTransport(Protocol):
    """Async provider operations used by the OCI adapter."""

    async def get_namespace(self) -> str: ...

    async def get_bucket(self, namespace: str, bucket: str) -> Mapping[str, Any]: ...

    async def put_object(
        self,
        namespace: str,
        bucket: str,
        key: str,
        body: bytes,
        *,
        content_type: str,
        kms_key_id: str,
    ) -> Mapping[str, Any]: ...

    async def head_object(self, namespace: str, bucket: str, key: str) -> Mapping[str, Any]: ...

    async def get_object(self, namespace: str, bucket: str, key: str) -> bytes: ...

    async def delete_object(self, namespace: str, bucket: str, key: str) -> None: ...

    async def list_objects(self, namespace: str, bucket: str, prefix: str) -> Sequence[Mapping[str, Any]]: ...

    async def create_multipart_upload(
        self,
        namespace: str,
        bucket: str,
        key: str,
        *,
        content_type: str,
        kms_key_id: str,
    ) -> str: ...

    async def upload_part(
        self,
        namespace: str,
        bucket: str,
        key: str,
        upload_id: str,
        part_number: int,
        body: bytes,
    ) -> str: ...

    async def complete_multipart_upload(
        self,
        namespace: str,
        bucket: str,
        key: str,
        upload_id: str,
        parts: Sequence[Mapping[str, Any]],
    ) -> None: ...

    async def abort_multipart_upload(
        self,
        namespace: str,
        bucket: str,
        key: str,
        upload_id: str,
    ) -> None: ...


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "to_dict"):
        converted = value.to_dict()
        if isinstance(converted, Mapping):
            return converted
    return {}


def _flatten_headers(value: Mapping[str, Any]) -> dict[str, Any]:
    headers = value.get("headers")
    flattened: dict[str, Any] = {str(key).lower().replace("_", "-"): item for key, item in value.items()}
    if isinstance(headers, Mapping):
        flattened.update({str(key).lower().replace("_", "-"): item for key, item in headers.items()})
    return flattened


def _field(value: Mapping[str, Any], *names: str) -> Any:
    normalized = {str(key).lower().replace("_", "-"): item for key, item in value.items()}
    for name in names:
        candidate = normalized.get(name.lower().replace("_", "-"))
        if candidate not in (None, ""):
            return candidate
    return None


def _not_found(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status == 404:
        return True
    return "404" in str(exc)


class OCIObjectStoreAdapter:
    """OCI-native object-store adapter with mandatory SSE/KMS read-back."""

    adapter_type = "oci-object-storage"
    adapter_version = "oci-native-sse-kms-v1"

    def __init__(self, config: OCIObjectStoreConfig, transport: OCIObjectStorageTransport) -> None:
        self.config = config
        self.transport = transport
        self._preflight: dict[str, Any] | None = None

    async def preflight(self) -> dict[str, Any]:
        namespace = (await self.transport.get_namespace()).strip()
        bucket = _mapping(await self.transport.get_bucket(self.config.namespace, self.config.bucket))
        bucket_name = str(_field(bucket, "name", "bucket-name") or self.config.bucket)
        bucket_kms = str(_field(bucket, "kms-key-id", "kmsKeyId", "customer-managed-key-id") or "")
        if namespace != self.config.namespace:
            raise OCIEncryptionEvidenceError("OCI namespace identity did not match the configured namespace")
        if bucket_name != self.config.bucket:
            raise OCIEncryptionEvidenceError("OCI bucket identity did not match the configured bucket")
        if bucket_kms != self.config.kms_key_id:
            raise OCIEncryptionEvidenceError("OCI bucket is not configured with the requested customer-managed KMS key")
        self._preflight = {
            "namespace_match": True,
            "bucket_match": True,
            "kms_key_match": True,
            "kms_key_id_sha256": self.config.kms_key_id_sha256,
            "encryption_mode": "SSE_KMS",
        }
        return dict(self._preflight)

    def _require_preflight(self) -> None:
        if self._preflight is None:
            raise OCIEncryptionEvidenceError("OCI SSE/KMS preflight is required before object operations")

    @staticmethod
    def _full_key(project_id: str, key: str) -> str:
        BlobClient._validate_path_component(project_id, "project_id")
        BlobClient._validate_path_component(key, "key")
        return f"{project_id}/{key}"

    async def encryption_metadata(self, ref: BlobRef) -> dict[str, Any]:
        self._require_preflight()
        raw = _flatten_headers(_mapping(await self.transport.head_object(self.config.namespace, ref.bucket, ref.key)))
        provider_key = str(
            _field(
                raw,
                "opc-sse-kms-key-id",
                "x-oci-sse-kms-key-id",
                "kms-key-id",
                "kmsKeyId",
            )
            or ""
        )
        mode = str(_field(raw, "encryption-mode", "encryption", "opc-encryption") or "SSE_KMS")
        if provider_key != self.config.kms_key_id:
            raise OCIEncryptionEvidenceError("OCI object read-back did not prove the configured SSE/KMS key")
        return {
            "mode": "SSE_KMS",
            "provider_mode": mode,
            "kms_key_id_sha256": hashlib.sha256(provider_key.encode("utf-8")).hexdigest(),
            "metadata_read_back": True,
        }

    async def upload(
        self,
        project_id: str,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        bucket: str | None = None,
    ) -> BlobRef:
        self._require_preflight()
        bucket_name = bucket or self.config.bucket
        if bucket_name != self.config.bucket:
            raise OCIEncryptionEvidenceError("OCI adapter refuses an unverified bucket override")
        full_key = self._full_key(project_id, key)
        payload = bytes(data)
        await self.transport.put_object(
            self.config.namespace,
            bucket_name,
            full_key,
            payload,
            content_type=content_type,
            kms_key_id=self.config.kms_key_id,
        )
        ref = BlobRef(
            bucket=bucket_name,
            key=full_key,
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            content_type=content_type,
        )
        await self.encryption_metadata(ref)
        return ref

    async def download(self, ref: BlobRef) -> bytes:
        self._require_preflight()
        data = await self.transport.get_object(self.config.namespace, ref.bucket, ref.key)
        return verify_blob_readback(ref, bytes(data))

    async def delete(self, ref: BlobRef) -> None:
        self._require_preflight()
        await self.transport.delete_object(self.config.namespace, ref.bucket, ref.key)

    async def list_objects(
        self,
        project_id: str,
        *,
        prefix: str = "",
        bucket: str | None = None,
    ) -> list[dict[str, Any]]:
        self._require_preflight()
        bucket_name = bucket or self.config.bucket
        if bucket_name != self.config.bucket:
            raise OCIEncryptionEvidenceError("OCI adapter refuses an unverified bucket override")
        full_prefix = self._full_key(project_id, prefix) if prefix else f"{project_id}/"
        rows = []
        for item in await self.transport.list_objects(self.config.namespace, bucket_name, full_prefix):
            row = _mapping(item)
            name = str(_field(row, "name", "key") or "")
            if name.startswith(f"{project_id}/"):
                rows.append(
                    {
                        "key": name,
                        "size": int(_field(row, "size", "content-length") or 0),
                        "last_modified": str(_field(row, "time-modified", "last-modified") or ""),
                    }
                )
        return sorted(rows, key=lambda row: str(row["key"]))

    async def exists(self, project_id: str, key: str, *, bucket: str | None = None) -> bool:
        self._require_preflight()
        bucket_name = bucket or self.config.bucket
        if bucket_name != self.config.bucket:
            raise OCIEncryptionEvidenceError("OCI adapter refuses an unverified bucket override")
        full_key = self._full_key(project_id, key)
        try:
            await self.transport.head_object(self.config.namespace, bucket_name, full_key)
        except Exception as exc:
            if _not_found(exc):
                return False
            raise
        return True

    async def create_multipart_upload(
        self,
        project_id: str,
        key: str,
        *,
        content_type: str = "application/octet-stream",
        bucket: str | None = None,
    ) -> str:
        self._require_preflight()
        bucket_name = bucket or self.config.bucket
        if bucket_name != self.config.bucket:
            raise OCIEncryptionEvidenceError("OCI adapter refuses an unverified bucket override")
        return await self.transport.create_multipart_upload(
            self.config.namespace,
            bucket_name,
            self._full_key(project_id, key),
            content_type=content_type,
            kms_key_id=self.config.kms_key_id,
        )

    async def upload_multipart_part(
        self,
        project_id: str,
        key: str,
        upload_id: str,
        part_number: int,
        data: bytes,
        *,
        bucket: str | None = None,
    ) -> str:
        self._require_preflight()
        if part_number < 1:
            raise ValueError("part_number must be positive")
        bucket_name = bucket or self.config.bucket
        return await self.transport.upload_part(
            self.config.namespace,
            bucket_name,
            self._full_key(project_id, key),
            upload_id,
            part_number,
            bytes(data),
        )

    async def complete_multipart_upload(
        self,
        project_id: str,
        key: str,
        upload_id: str,
        parts: list[dict[str, Any]],
        *,
        bucket: str | None = None,
    ) -> None:
        self._require_preflight()
        bucket_name = bucket or self.config.bucket
        full_key = self._full_key(project_id, key)
        await self.transport.complete_multipart_upload(
            self.config.namespace,
            bucket_name,
            full_key,
            upload_id,
            parts,
        )
        # Completion must prove provider metadata, not merely the request
        # header supplied at multipart initiation.
        head = BlobRef(bucket=bucket_name, key=full_key, sha256="0" * 64, size_bytes=0)
        await self.encryption_metadata(head)

    async def abort_multipart_upload(
        self,
        project_id: str,
        key: str,
        upload_id: str,
        *,
        bucket: str | None = None,
    ) -> None:
        self._require_preflight()
        bucket_name = bucket or self.config.bucket
        await self.transport.abort_multipart_upload(
            self.config.namespace,
            bucket_name,
            self._full_key(project_id, key),
            upload_id,
        )


class OCIObjectStorageSdkTransport:
    """Thin async bridge over the optional OCI Python SDK."""

    def __init__(self, config: OCIObjectStoreConfig) -> None:
        try:
            import oci  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - depends on live environment
            raise OCIProviderUnavailable("OCI Python SDK is not installed; install the governed OCI extra") from exc
        self._oci = oci
        try:
            if config.auth_mode == "instance_principal":
                signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
                self._client = oci.object_storage.ObjectStorageClient({}, signer=signer)
            else:
                sdk_config = oci.config.from_file(
                    file_location=config.config_file,
                    profile_name=config.auth_profile,
                )
                self._client = oci.object_storage.ObjectStorageClient(sdk_config)
            if config.endpoint:
                self._client.base_client.endpoint = config.endpoint
        except Exception as exc:  # pragma: no cover - depends on live environment
            raise OCIProviderUnavailable(f"OCI authentication/client setup failed: {type(exc).__name__}") from exc

    async def _call(self, method: str, **kwargs: Any) -> Any:
        try:
            return await asyncio.to_thread(getattr(self._client, method), **kwargs)
        except Exception as exc:  # pragma: no cover - depends on live provider
            status = getattr(exc, "status", None)
            raise OCITransportError(f"OCI {method} failed: {type(exc).__name__}", status_code=status) from exc

    @staticmethod
    def _response_mapping(response: Any) -> Mapping[str, Any]:
        data = getattr(response, "data", None)
        mapped = dict(_mapping(data))
        headers = getattr(response, "headers", None)
        if isinstance(headers, Mapping):
            mapped["headers"] = dict(headers)
        return mapped

    async def get_namespace(self) -> str:
        response = await self._call("get_namespace")
        return str(getattr(response, "data", "") or "")

    async def get_bucket(self, namespace: str, bucket: str) -> Mapping[str, Any]:
        return self._response_mapping(await self._call("get_bucket", namespace_name=namespace, bucket_name=bucket))

    async def put_object(
        self,
        namespace: str,
        bucket: str,
        key: str,
        body: bytes,
        *,
        content_type: str,
        kms_key_id: str,
    ) -> Mapping[str, Any]:
        return self._response_mapping(
            await self._call(
                "put_object",
                namespace_name=namespace,
                bucket_name=bucket,
                object_name=key,
                put_object_body=body,
                content_type=content_type,
                opc_sse_kms_key_id=kms_key_id,
            )
        )

    async def head_object(self, namespace: str, bucket: str, key: str) -> Mapping[str, Any]:
        return self._response_mapping(
            await self._call("head_object", namespace_name=namespace, bucket_name=bucket, object_name=key)
        )

    async def get_object(self, namespace: str, bucket: str, key: str) -> bytes:
        response = await self._call("get_object", namespace_name=namespace, bucket_name=bucket, object_name=key)
        data = getattr(response, "data", response)
        if isinstance(data, bytes):
            return data
        content = getattr(data, "content", None)
        if isinstance(content, bytes):
            return content
        read = getattr(data, "read", None)
        if callable(read):
            value = read()
            return await value if hasattr(value, "__await__") else bytes(value)
        raise OCITransportError("OCI get_object returned no readable body")

    async def delete_object(self, namespace: str, bucket: str, key: str) -> None:
        await self._call("delete_object", namespace_name=namespace, bucket_name=bucket, object_name=key)

    async def list_objects(self, namespace: str, bucket: str, prefix: str) -> Sequence[Mapping[str, Any]]:
        response = await self._call(
            "list_objects",
            namespace_name=namespace,
            bucket_name=bucket,
            prefix=prefix,
            fields="name,size,timeCreated,timeModified",
        )
        data = getattr(response, "data", None)
        objects = getattr(data, "objects", None)
        if not isinstance(objects, list):
            return []
        return [_mapping(item) for item in objects]

    async def create_multipart_upload(
        self,
        namespace: str,
        bucket: str,
        key: str,
        *,
        content_type: str,
        kms_key_id: str,
    ) -> str:
        details = self._oci.object_storage.models.CreateMultipartUploadDetails(
            object=key,
            content_type=content_type,
        )
        response = await self._call(
            "create_multipart_upload",
            namespace_name=namespace,
            bucket_name=bucket,
            create_multipart_upload_details=details,
            opc_sse_kms_key_id=kms_key_id,
        )
        data = getattr(response, "data", None)
        upload_id = getattr(data, "upload_id", None) or _mapping(data).get("upload_id")
        if not upload_id:
            raise OCITransportError("OCI multipart initiation returned no upload ID")
        return str(upload_id)

    async def upload_part(
        self,
        namespace: str,
        bucket: str,
        key: str,
        upload_id: str,
        part_number: int,
        body: bytes,
    ) -> str:
        response = await self._call(
            "upload_part",
            namespace_name=namespace,
            bucket_name=bucket,
            object_name=key,
            upload_id=upload_id,
            part_num=part_number,
            upload_part_body=body,
        )
        etag = getattr(response, "headers", {}).get("etag") if hasattr(response, "headers") else None
        return str(etag or _mapping(response).get("etag") or "")

    async def complete_multipart_upload(
        self,
        namespace: str,
        bucket: str,
        key: str,
        upload_id: str,
        parts: Sequence[Mapping[str, Any]],
    ) -> None:
        commit_parts = [
            self._oci.object_storage.models.CommitMultipartUploadPart(
                part_num=int(part.get("PartNumber") or part.get("part_num")),
                etag=str(part.get("ETag") or part.get("etag")),
            )
            for part in parts
        ]
        details = self._oci.object_storage.models.CommitMultipartUploadDetails(parts=commit_parts)
        await self._call(
            "commit_multipart_upload",
            namespace_name=namespace,
            bucket_name=bucket,
            object_name=key,
            upload_id=upload_id,
            commit_multipart_upload_details=details,
        )

    async def abort_multipart_upload(
        self,
        namespace: str,
        bucket: str,
        key: str,
        upload_id: str,
    ) -> None:
        await self._call(
            "abort_multipart_upload",
            namespace_name=namespace,
            bucket_name=bucket,
            object_name=key,
            upload_id=upload_id,
        )


class FakeOCIObjectStorageTransport:
    """Deterministic OCI-shaped provider fixture for unit/contract tests."""

    def __init__(self, *, namespace: str, bucket: str, kms_key_id: str, expose_encryption_metadata: bool = True) -> None:
        self.namespace = namespace
        self.bucket = bucket
        self.kms_key_id = kms_key_id
        self.expose_encryption_metadata = expose_encryption_metadata
        self.objects: dict[tuple[str, str], tuple[bytes, str, str]] = {}
        self.multipart: dict[str, dict[str, Any]] = {}
        self._counter = 0

    async def get_namespace(self) -> str:
        return self.namespace

    async def get_bucket(self, namespace: str, bucket: str) -> Mapping[str, Any]:
        if namespace != self.namespace or bucket != self.bucket:
            raise OCITransportError("bucket not found", status_code=404)
        return {"name": self.bucket, "kms_key_id": self.kms_key_id}

    async def put_object(self, namespace: str, bucket: str, key: str, body: bytes, *, content_type: str, kms_key_id: str) -> Mapping[str, Any]:
        self.objects[(bucket, key)] = (bytes(body), content_type, kms_key_id)
        return {"etag": hashlib.md5(body).hexdigest()}  # noqa: S324 - provider fixture ETag

    async def head_object(self, namespace: str, bucket: str, key: str) -> Mapping[str, Any]:
        try:
            _body, _content_type, kms_key_id = self.objects[(bucket, key)]
        except KeyError as exc:
            raise OCITransportError("object not found", status_code=404) from exc
        headers: dict[str, Any] = {"content-length": len(_body)}
        if self.expose_encryption_metadata:
            headers.update({"opc-sse-kms-key-id": kms_key_id, "encryption-mode": "SSE_KMS"})
        return {"headers": headers}

    async def get_object(self, namespace: str, bucket: str, key: str) -> bytes:
        try:
            return self.objects[(bucket, key)][0]
        except KeyError as exc:
            raise OCITransportError("object not found", status_code=404) from exc

    async def delete_object(self, namespace: str, bucket: str, key: str) -> None:
        self.objects.pop((bucket, key), None)

    async def list_objects(self, namespace: str, bucket: str, prefix: str) -> Sequence[Mapping[str, Any]]:
        return [
            {"name": key, "size": len(body), "time_modified": datetime(1970, 1, 1, tzinfo=UTC).isoformat()}
            for (stored_bucket, key), (body, _content_type, _kms) in self.objects.items()
            if stored_bucket == bucket and key.startswith(prefix)
        ]

    async def create_multipart_upload(self, namespace: str, bucket: str, key: str, *, content_type: str, kms_key_id: str) -> str:
        self._counter += 1
        upload_id = f"oci-fixture-{self._counter}"
        self.multipart[upload_id] = {"bucket": bucket, "key": key, "content_type": content_type, "kms_key_id": kms_key_id, "parts": {}}
        return upload_id

    async def upload_part(self, namespace: str, bucket: str, key: str, upload_id: str, part_number: int, body: bytes) -> str:
        session = self.multipart[upload_id]
        if session["bucket"] != bucket or session["key"] != key:
            raise OCITransportError("multipart object mismatch")
        etag = hashlib.md5(body).hexdigest()  # noqa: S324 - provider fixture ETag
        session["parts"][part_number] = (etag, bytes(body))
        return f'"{etag}"'

    async def complete_multipart_upload(self, namespace: str, bucket: str, key: str, upload_id: str, parts: Sequence[Mapping[str, Any]]) -> None:
        session = self.multipart[upload_id]
        payload = bytearray()
        for part in parts:
            number = int(part.get("PartNumber") or part.get("part_num"))
            etag = str(part.get("ETag") or part.get("etag")).strip('"')
            expected, body = session["parts"][number]
            if etag != expected:
                raise OCITransportError("multipart ETag mismatch")
            payload.extend(body)
        self.objects[(bucket, key)] = (bytes(payload), session["content_type"], session["kms_key_id"])
        self.multipart.pop(upload_id, None)

    async def abort_multipart_upload(self, namespace: str, bucket: str, key: str, upload_id: str) -> None:
        self.multipart.pop(upload_id, None)


async def run_oci_sse_kms_probe(
    store: OCIObjectStoreAdapter,
    *,
    config: OCIObjectStoreConfig,
    project_id: str = "aiat-oci-sse-kms-certification",
    multipart_config: MultipartUploadConfig | None = None,
) -> dict[str, Any]:
    """Run the complete scalar OCI SSE/KMS evidence wave."""

    direct_ref: BlobRef | None = None
    errors: list[str] = []
    preflight: dict[str, Any] | None = None
    direct: dict[str, Any] = {
        "put": False,
        "read_back": False,
        "checksum_verified": False,
        "encryption_metadata_verified": False,
        "delete": False,
    }
    conformance: dict[str, Any] = {"status": "not_run"}
    multipart: dict[str, Any] = {"status": "not_run"}
    try:
        preflight = await store.preflight()
        payload = b"aiat-oci-sse-kms-certification-payload"
        expected_direct_ref = BlobRef(
            bucket=config.bucket,
            key=f"{project_id}/direct/fixture.bin",
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
        )
        try:
            direct_ref = await store.upload(project_id, "direct/fixture.bin", payload)
        except Exception:
            # A provider can persist the object before its encryption
            # read-back fails.  Retain only the deterministic reference needed
            # for cleanup; never retain the payload itself.
            direct_ref = expected_direct_ref
            raise
        direct["put"] = True
        metadata = await store.encryption_metadata(direct_ref)
        direct["encryption_metadata_verified"] = metadata.get("metadata_read_back") is True
        read_back = await store.download(direct_ref)
        direct["read_back"] = read_back == payload
        verify_blob_readback(direct_ref, read_back)
        direct["checksum_verified"] = True
        conformance_report = await run_object_store_conformance(store, project_id=f"{project_id}-conformance", bucket=config.bucket)
        conformance = conformance_report.as_dict()
        multipart_report = await run_object_store_multipart_probe(
            store,
            provider="oci",
            config=multipart_config
            or MultipartUploadConfig(project_id=f"{project_id}-multipart", bucket=config.bucket),
        )
        multipart = multipart_report.as_dict()
    except OCIProviderUnavailable as exc:
        errors.append(f"provider_unavailable:{type(exc).__name__}")
    except Exception as exc:  # pragma: no cover - provider-specific boundary
        errors.append(f"probe_error:{type(exc).__name__}")
    finally:
        if direct_ref is not None:
            try:
                await store.delete(direct_ref)
                direct["delete"] = True
            except Exception as exc:  # pragma: no cover - provider-specific cleanup
                errors.append(f"cleanup_error:{type(exc).__name__}")
        try:
            remaining = await store.list_objects(project_id, bucket=config.bucket)
            zero_residue = not remaining
        except Exception as exc:  # pragma: no cover - provider-specific cleanup
            zero_residue = False
            errors.append(f"cleanup_inventory_error:{type(exc).__name__}")
    if preflight is None and not errors:
        errors.append("preflight_missing")
    provider_unavailable = any(item.startswith("provider_unavailable:") for item in errors)
    functional_fail = bool(errors) and not provider_unavailable
    all_pass = (
        preflight is not None
        and direct == {
            "put": True,
            "read_back": True,
            "checksum_verified": True,
            "encryption_metadata_verified": True,
            "delete": True,
        }
        and conformance.get("passed") is True
        and multipart.get("status") == "pass"
        and zero_residue
        and not errors
    )
    return {
        "schema_version": OCI_OBJECT_STORE_SCHEMA,
        "status": "pass" if all_pass else "fail" if functional_fail else "blocked",
        "mode": "live" if isinstance(store.transport, OCIObjectStorageSdkTransport) else "fixture",
        "provider": "oci-object-storage",
        "adapter_type": store.adapter_type,
        "adapter_version": store.adapter_version,
        "config": config.evidence_identity(),
        "preflight": preflight or {"status": "not_verified"},
        "direct_object": direct,
        "conformance": {key: conformance[key] for key in ("passed", "counts", "adapter_type", "adapter_version") if key in conformance},
        "multipart": {
            key: multipart[key]
            for key in ("status", "abort_verified", "cleanup_verified", "error_count", "provider")
            if key in multipart
        },
        "cleanup": {"zero_residue_verified": zero_residue},
        "errors": errors,
        "required_non_secret_config": [
            "OCI_REGION",
            "OCI_NAMESPACE",
            "OCI_BUCKET",
            "OCI_KMS_KEY_ID",
            "OBJECT_STORE_ENCRYPTION_MODE=SSE_KMS",
        ],
        "required_secret_references": ["OCI_CONFIG_FILE + OCI_AUTH_PROFILE or OCI_AUTH_MODE=instance_principal"],
        "payloads_credentials_logs_retained": False,
        "licence_metadata_is_gate": False,
        "scope": "OCI bucket/key identity, provider-managed SSE/KMS metadata, checksum, multipart, abort, and zero-residue evidence",
    }


__all__ = [
    "OCI_OBJECT_STORE_SCHEMA",
    "OCIEncryptionEvidenceError",
    "OCIObjectStorageSdkTransport",
    "OCIObjectStorageTransport",
    "OCIObjectStoreAdapter",
    "OCIObjectStoreConfig",
    "OCIProviderUnavailable",
    "OCITransportError",
    "FakeOCIObjectStorageTransport",
    "run_oci_sse_kms_probe",
]
