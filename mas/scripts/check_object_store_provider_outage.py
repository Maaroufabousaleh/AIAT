"""Certify a bounded real object-store process outage and recovery.

Fixture mode exercises the report contract without Docker or a network.  Live
mode requires two explicitly labelled disposable S3-compatible containers on
the existing ``mas_internal`` network.  Each provider receives the same small
checksum workload, is stopped through a strict Docker identity check, and is
started again before read-back and scoped cleanup.  The checker never controls
the Compose ``mas`` provider or an unlabeled container, and it retains only
scalar evidence.  Licence metadata is informational and never an activation
or release gate.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

from mas_core.memory import BlobClient, BlobRef, InMemoryObjectStore

SCHEMA = "aiat.object-store-provider-outage.v1"
NETWORK = "mas_internal"
DISPOSABLE_LABEL = "aiat.release-gate"
DISPOSABLE_LABEL_VALUE = "object-store-provider-outage"
PROJECT_DEFAULT = "aiat-object-store-outage-fixture-v1"
PAYLOAD_SIZES = (64 * 1024, 1024 * 1024)
OPERATION_TIMEOUT_SECONDS = 5.0
RECOVERY_TIMEOUT_SECONDS = 30.0
PAYLOAD_MARKER = "aiat object-store outage fixture payload must never enter evidence"


class HarnessError(RuntimeError):
    """A test setup or disposable-container identity failure."""


class ProviderFunctionalError(RuntimeError):
    """A provider operation failed after the harness was proven valid."""


class ContainerController(Protocol):
    """Minimal lifecycle boundary used by the live runner and tests."""

    def validate(self) -> None: ...

    def stop(self) -> None: ...

    def start(self) -> None: ...

    def remove(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    name: str
    endpoint: str
    access_key: str
    secret_key: str
    container_name: str
    network_alias: str


@dataclass(frozen=True, slots=True)
class OutagePlan:
    project_id: str
    bucket: str
    operation_timeout_seconds: float = OPERATION_TIMEOUT_SECONDS
    recovery_timeout_seconds: float = RECOVERY_TIMEOUT_SECONDS


class DockerContainerController:
    """Safely control one explicitly labelled disposable container."""

    def __init__(self, spec: ProviderSpec, *, project_id: str) -> None:
        self.spec = spec
        self.project_id = project_id
        self._docker = shutil.which("docker")
        self._volume_names: tuple[str, ...] = ()

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        if not self._docker:
            raise HarnessError("docker executable is unavailable")
        try:
            return subprocess.run(
                [self._docker, *args],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise HarnessError(f"docker command failed: {type(exc).__name__}") from exc

    def _inspect(self, *, allow_missing: bool = False) -> dict[str, Any] | None:
        result = self._run("inspect", self.spec.container_name)
        if result.returncode != 0:
            if allow_missing:
                return None
            raise HarnessError("disposable outage container is not inspectable")
        try:
            payload = json.loads(result.stdout)
            value = payload[0]
        except (IndexError, TypeError, json.JSONDecodeError) as exc:
            raise HarnessError("docker inspect returned invalid container metadata") from exc
        if not isinstance(value, dict):
            raise HarnessError("docker inspect returned invalid container metadata")
        return value

    def _validate_metadata(self, value: dict[str, Any]) -> None:
        labels = value.get("Config", {}).get("Labels") or {}
        if labels.get(DISPOSABLE_LABEL) != DISPOSABLE_LABEL_VALUE:
            raise HarnessError("container lacks the required disposable release-gate label")
        if labels.get("aiat.project") != self.project_id:
            raise HarnessError("container project namespace does not match the reserved project")
        if labels.get("aiat.provider") != self.spec.name:
            raise HarnessError("container provider label does not match the selected provider")
        compose_project = labels.get("com.docker.compose.project")
        if compose_project == "mas":
            raise HarnessError("refusing to control the persistent Compose mas project")
        networks = value.get("NetworkSettings", {}).get("Networks") or {}
        network = networks.get(NETWORK)
        if not isinstance(network, dict):
            raise HarnessError(f"container is not attached to canonical {NETWORK} network")
        aliases = {str(alias) for alias in network.get("Aliases") or []}
        if self.spec.network_alias not in aliases:
            raise HarnessError("container is missing its declared network alias")

    def validate(self) -> None:
        value = self._inspect()
        assert value is not None
        self._validate_metadata(value)
        mounts = value.get("Mounts") or []
        volume_names: list[str] = []
        for mount in mounts:
            if (
                mount.get("Type") != "volume"
                or mount.get("Destination") != "/data"
                or not re.fullmatch(r"[0-9a-f]{64}", str(mount.get("Name") or ""))
            ):
                raise HarnessError("outage fixture storage must be an anonymous disposable volume")
            volume_names.append(str(mount["Name"]))
        self._volume_names = tuple(volume_names)

    def _running(self) -> bool:
        value = self._inspect()
        assert value is not None
        self._validate_metadata(value)
        return bool((value.get("State") or {}).get("Running"))

    def stop(self) -> None:
        self.validate()
        if not self._running():
            raise HarnessError("disposable provider was not running before the outage")
        result = self._run("stop", "--time", "10", self.spec.container_name)
        if result.returncode != 0 or self._running():
            raise HarnessError("disposable provider did not stop cleanly")

    def start(self) -> None:
        self.validate()
        result = self._run("start", self.spec.container_name)
        if result.returncode != 0 or not self._running():
            raise HarnessError("disposable provider did not restart cleanly")

    def remove(self) -> None:
        value = self._inspect(allow_missing=True)
        if value is None:
            for volume_name in self._volume_names:
                if self._run("volume", "inspect", volume_name).returncode == 0:
                    self._run("volume", "rm", volume_name)
            if any(self._run("volume", "inspect", name).returncode == 0 for name in self._volume_names):
                raise HarnessError("disposable provider volume was not removed")
            return
        self._validate_metadata(value)
        self.validate()
        result = self._run("rm", "--force", "--volumes", self.spec.container_name)
        if result.returncode != 0 or self._inspect(allow_missing=True) is not None:
            raise HarnessError("disposable provider container was not removed")
        for volume_name in self._volume_names:
            volume = self._run("volume", "inspect", volume_name)
            if volume.returncode == 0:
                self._run("volume", "rm", volume_name)
            if self._run("volume", "inspect", volume_name).returncode == 0:
                raise HarnessError("disposable provider volume was not removed")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a machine-readable report")
    parser.add_argument("--live", action="store_true", help="stop/restart labelled disposable providers")
    parser.add_argument("--project-id", default=os.getenv("AIAT_OBJECT_STORE_OUTAGE_PROJECT", PROJECT_DEFAULT))
    parser.add_argument("--bucket", default=os.getenv("AIAT_OBJECT_STORE_OUTAGE_BUCKET", "mas-agents"))
    for name in ("minio", "seaweedfs"):
        upper = name.upper()
        parser.add_argument(f"--{name}-endpoint", default=os.getenv(f"AIAT_OBJECT_STORE_OUTAGE_{upper}_ENDPOINT"))
        parser.add_argument(f"--{name}-access-key", default=os.getenv(f"AIAT_OBJECT_STORE_OUTAGE_{upper}_ACCESS_KEY"))
        parser.add_argument(f"--{name}-secret-key", default=os.getenv(f"AIAT_OBJECT_STORE_OUTAGE_{upper}_SECRET_KEY"))
        parser.add_argument(f"--{name}-container", default=os.getenv(f"AIAT_OBJECT_STORE_OUTAGE_{upper}_CONTAINER"))
        default_alias = "seaweedfs-resource" if name == "seaweedfs" else "minio-outage"
        parser.add_argument(
            f"--{name}-alias",
            default=os.getenv(f"AIAT_OBJECT_STORE_OUTAGE_{upper}_ALIAS", default_alias),
        )
    return parser


def _base(*, mode: str, status: str, **details: Any) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": SCHEMA,
        "mode": mode,
        "status": status,
        "payload_free": True,
        "secret_free": True,
        "licence_metadata_is_gate": False,
        "network": NETWORK,
        "mutation_performed": mode == "live",
        "local_database_access_performed": False,
        "external_network_access_performed": mode == "live",
        "external_provider_mutation_performed": mode == "live",
    }
    report.update(details)
    return report


def _blocked(reason: str, missing: list[str]) -> dict[str, Any]:
    return _base(
        mode="live",
        status="blocked",
        reason=reason,
        missing_configuration=missing,
        failure_classification={
            "harness_or_configuration_failure": "blocked before provider execution",
            "provider_functional_failure": "not_checked",
            "provider_resource_limit_failure": "not_checked",
            "infrastructure_or_environment_failure": "not_checked",
        },
        providers=[],
        scope="real disposable provider process stop/restart and checksum recovery",
    )


def _payload(key: str, size: int) -> bytes:
    seed = hashlib.sha256(f"aiat-object-store-outage:{key}:{size}".encode()).digest()
    return (seed * ((size // len(seed)) + 1))[:size]


def _error_type(exc: BaseException) -> str:
    return type(exc).__name__


async def _timed(awaitable: Any, timeout: float) -> Any:
    return await asyncio.wait_for(awaitable, timeout=timeout)


async def _close(client: BlobClient | None) -> None:
    if client is not None:
        with suppress(Exception):
            await client.close()


async def _connect(spec: ProviderSpec, *, bucket: str, timeout: float) -> BlobClient:
    client = BlobClient(spec.endpoint, access_key=spec.access_key, secret_key=spec.secret_key, bucket=bucket)
    try:
        await _timed(client.connect(), timeout)
        await _timed(client.ensure_bucket(bucket), timeout)
        return client
    except Exception:
        await _close(client)
        raise


async def _tcp_reachable(endpoint: str, *, timeout: float) -> None:
    """Check only the provider TCP listener, without opening an S3 session."""

    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HarnessError("provider endpoint must be an http(s) URL with a host")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(parsed.hostname, port),
        timeout=timeout,
    )
    del reader
    writer.close()
    with suppress(Exception):
        await writer.wait_closed()


async def _remaining(client: BlobClient, *, project_id: str, bucket: str, timeout: float) -> int:
    return len(await _timed(client.list_objects(project_id, bucket=bucket), timeout))


async def _delete_refs(client: BlobClient, refs: list[BlobRef], *, timeout: float) -> int:
    deleted = 0
    for ref in refs:
        await _timed(client.delete(ref), timeout)
        deleted += 1
    return deleted


async def _run_live_provider(spec: ProviderSpec, plan: OutagePlan) -> dict[str, Any]:
    controller = DockerContainerController(spec, project_id=plan.project_id)
    client: BlobClient | None = None
    recovery_client: BlobClient | None = None
    refs: list[BlobRef] = []
    outage_observed = False
    recovery_observed = False
    recovery_endpoint_observed = False
    cleanup_deleted = 0
    remaining_before_cleanup: int | None = None
    remaining_after_cleanup: int | None = None
    outage_error_type: str | None = None
    started_again = False
    container_removed = False
    stage = "validate"
    report: dict[str, Any]
    failure_classification = {
        "harness_or_configuration_failure": "not_observed",
        "provider_functional_failure": "not_observed",
        "provider_resource_limit_failure": "not_checked",
        "infrastructure_or_environment_failure": "not_observed",
    }
    try:
        controller.validate()
        stage = "connect"
        client = await _connect(spec, bucket=plan.bucket, timeout=plan.operation_timeout_seconds)
        remaining_before_cleanup = await _remaining(
            client, project_id=plan.project_id, bucket=plan.bucket, timeout=plan.operation_timeout_seconds
        )
        if remaining_before_cleanup:
            raise HarnessError("reserved project prefix is not empty")
        stage = "seed"
        for index, size in enumerate(PAYLOAD_SIZES, start=1):
            key = f"outage/object-{index}-{size}.bin"
            ref = await _timed(
                client.upload(plan.project_id, key, _payload(key, size), bucket=plan.bucket),
                plan.operation_timeout_seconds,
            )
            await _timed(client.download(ref), plan.operation_timeout_seconds)
            refs.append(ref)
        await _close(client)
        client = None

        stage = "stop"
        controller.stop()
        stage = "outage-probe"
        try:
            await _tcp_reachable(spec.endpoint, timeout=plan.operation_timeout_seconds)
        except Exception as exc:
            outage_observed = True
            outage_error_type = _error_type(exc)
        if not outage_observed:
            failure_classification["harness_or_configuration_failure"] = (
                "container stop completed but the endpoint remained reachable"
            )
            raise HarnessError("provider outage was not observable after the controlled stop")

        stage = "restart"
        controller.start()
        started_again = True
        stage = "recovery"
        deadline = asyncio.get_running_loop().time() + plan.recovery_timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            try:
                await _tcp_reachable(spec.endpoint, timeout=plan.operation_timeout_seconds)
                recovery_endpoint_observed = True
                recovery_client = await _connect(
                    spec, bucket=plan.bucket, timeout=plan.operation_timeout_seconds
                )
                remaining_after_restart = await _remaining(
                    recovery_client,
                    project_id=plan.project_id,
                    bucket=plan.bucket,
                    timeout=plan.operation_timeout_seconds,
                )
                if remaining_after_restart == len(refs):
                    recovery_observed = True
                    break
                failure_classification["provider_functional_failure"] = (
                    "provider restarted but did not retain the reserved object set"
                )
                raise ProviderFunctionalError("provider restart lost the reserved object set")
            except Exception:
                await _close(recovery_client)
                recovery_client = None
            await asyncio.sleep(0.25)
        if not recovery_observed or recovery_client is None:
            failure_classification["infrastructure_or_environment_failure"] = (
                "provider endpoint did not recover within the bounded restart window"
            )
            raise RuntimeError("provider did not recover after restart")

        stage = "readback"
        for ref in refs:
            await _timed(recovery_client.download(ref), plan.operation_timeout_seconds)
        stage = "cleanup"
        cleanup_deleted = await _delete_refs(
            recovery_client, refs, timeout=plan.operation_timeout_seconds
        )
        remaining_after_cleanup = await _remaining(
            recovery_client,
            project_id=plan.project_id,
            bucket=plan.bucket,
            timeout=plan.operation_timeout_seconds,
        )
        if remaining_after_cleanup != 0:
            failure_classification["provider_functional_failure"] = (
                "provider retained one or more reserved objects after cleanup"
            )
            raise RuntimeError("provider cleanup left residue")
        report = _base(
            mode="live",
            status="pass",
            provider=spec.name,
            adapter="s3-compatible/aioboto3",
            reserved_project=plan.project_id,
            workload={
                "object_count": len(PAYLOAD_SIZES),
                "payload_sizes_bytes": list(PAYLOAD_SIZES),
                "total_payload_bytes": sum(PAYLOAD_SIZES),
                "operation_timeout_seconds": plan.operation_timeout_seconds,
                "recovery_timeout_seconds": plan.recovery_timeout_seconds,
            },
            multipart_upload="not_checked",
            checksum_readback_verified=len(refs) == len(PAYLOAD_SIZES),
            process_outage_observed=outage_observed,
            outage_error_type=outage_error_type,
            restart_verified=started_again,
            recovery_readback_verified=recovery_observed,
            recovery_endpoint_observed=recovery_endpoint_observed,
            cleanup_deleted_count=cleanup_deleted,
            remaining_fixture_count=remaining_after_cleanup,
            cleanup_verified=remaining_after_cleanup == 0,
            container_removed=False,
            disposable_storage_state="anonymous disposable volume; container and volume removal verified in finalizer",
            failure_classification=failure_classification,
            scope="real provider process stop/restart, checksum read-back, and scoped cleanup",
        )
    except HarnessError as exc:
        failure_classification["harness_or_configuration_failure"] = "controlled disposable harness rejected the run"
        report = _base(
            mode="live",
            status="fail",
            provider=spec.name,
            reserved_project=plan.project_id,
            reason="provider outage harness/configuration failure",
            error_type=_error_type(exc),
            process_outage_observed=outage_observed,
            recovery_readback_verified=recovery_observed,
            recovery_endpoint_observed=recovery_endpoint_observed,
            cleanup_deleted_count=cleanup_deleted,
            remaining_fixture_count=remaining_after_cleanup,
            cleanup_verified=False,
            container_removed=False,
            failure_classification=failure_classification,
        )
    except Exception as exc:  # pragma: no cover - external provider boundary
        if isinstance(exc, ProviderFunctionalError):
            failure_classification["provider_functional_failure"] = (
                "provider restarted but did not retain the reserved object set"
            )
        elif stage in {"connect", "outage-probe", "recovery", "restart"}:
            failure_classification["infrastructure_or_environment_failure"] = (
                "provider endpoint or Docker runtime was unavailable during the bounded workflow"
            )
        elif failure_classification["infrastructure_or_environment_failure"] == "not_observed":
            failure_classification["provider_functional_failure"] = (
                "provider operation failed during the bounded outage/recovery workflow"
            )
        report = _base(
            mode="live",
            status="fail",
            provider=spec.name,
            reserved_project=plan.project_id,
            reason="provider outage/recovery workflow failed",
            error_type=_error_type(exc),
            process_outage_observed=outage_observed,
            recovery_readback_verified=recovery_observed,
            recovery_endpoint_observed=recovery_endpoint_observed,
            cleanup_deleted_count=cleanup_deleted,
            remaining_fixture_count=remaining_after_cleanup,
            cleanup_verified=False,
            container_removed=False,
            failure_classification=failure_classification,
        )
    finally:
        await _close(client)
        await _close(recovery_client)
        if not started_again:
            with suppress(Exception):
                controller.start()
        try:
            controller.remove()
            container_removed = True
        except Exception:
            container_removed = False
    report["container_removed"] = container_removed
    report["disposable_storage_cleanup_verified"] = container_removed
    if report.get("status") == "pass" and not container_removed:
        report["status"] = "fail"
        report["cleanup_verified"] = False
        report["failure_classification"] = {
            **dict(report.get("failure_classification") or {}),
            "harness_or_configuration_failure": "disposable container removal could not be verified",
        }
    return report


async def _run_fixture_provider(name: str, plan: OutagePlan) -> dict[str, Any]:
    """Run the deterministic report contract without claiming live outage."""

    store = InMemoryObjectStore(bucket=plan.bucket)
    refs: list[BlobRef] = []
    for index, size in enumerate(PAYLOAD_SIZES, start=1):
        key = f"outage/object-{index}-{size}.bin"
        ref = await store.upload(plan.project_id, key, _payload(key, size), bucket=plan.bucket)
        await store.download(ref)
        refs.append(ref)
    deleted = 0
    for ref in refs:
        await store.delete(ref)
        deleted += 1
    remaining = len(await store.list_objects(plan.project_id, bucket=plan.bucket))
    return _base(
        mode="fixture",
        status="pass" if remaining == 0 else "fail",
        provider=name,
        reserved_project=plan.project_id,
        workload={
            "object_count": len(PAYLOAD_SIZES),
            "payload_sizes_bytes": list(PAYLOAD_SIZES),
            "total_payload_bytes": sum(PAYLOAD_SIZES),
        },
        process_outage_observed="not_checked",
        recovery_readback_verified="not_checked",
        checksum_readback_verified=True,
        cleanup_deleted_count=deleted,
        remaining_fixture_count=remaining,
        cleanup_verified=remaining == 0,
        container_removed="not_applicable",
        failure_classification={
            "harness_or_configuration_failure": "not_observed",
            "provider_functional_failure": "not_checked",
            "provider_resource_limit_failure": "not_checked",
            "infrastructure_or_environment_failure": "not_checked",
        },
        scope="fixture checksum/read-back and scoped cleanup; live process outage not asserted",
    )


def _specs(args: argparse.Namespace) -> tuple[ProviderSpec, ProviderSpec]:
    return tuple(
        ProviderSpec(
            name=name,
            endpoint=str(getattr(args, f"{name}_endpoint") or ""),
            access_key=str(getattr(args, f"{name}_access_key") or ""),
            secret_key=str(getattr(args, f"{name}_secret_key") or ""),
            container_name=str(getattr(args, f"{name}_container") or ""),
            network_alias=str(getattr(args, f"{name}_alias") or ""),
        )
        for name in ("minio", "seaweedfs")
    )


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    plan = OutagePlan(project_id=str(args.project_id), bucket=str(args.bucket))
    if not args.live:
        providers = [await _run_fixture_provider(name, plan) for name in ("minio", "seaweedfs")]
        return _base(
            mode="fixture",
            status="pass" if all(item["status"] == "pass" for item in providers) else "fail",
            providers=providers,
            reserved_project=plan.project_id,
            workload={
                "object_count": len(PAYLOAD_SIZES),
                "payload_sizes_bytes": list(PAYLOAD_SIZES),
                "total_payload_bytes": sum(PAYLOAD_SIZES),
            },
            scope="fixture contract only; real provider process outage requires --live",
        )
    specs = _specs(args)
    missing = [
        f"{spec.name}.{field}"
        for spec in specs
        for field, value in (
            ("endpoint", spec.endpoint),
            ("access_key", spec.access_key),
            ("secret_key", spec.secret_key),
            ("container", spec.container_name),
        )
        if not value
    ]
    if missing:
        return _blocked(f"missing live configuration: {', '.join(missing)}", missing)
    providers = [await _run_live_provider(spec, plan) for spec in specs]
    statuses = {str(item.get("status")) for item in providers}
    status = "fail" if "fail" in statuses else "pass"
    return _base(
        mode="live",
        status=status,
        providers=providers,
        reserved_project=plan.project_id,
        workload={
            "object_count": len(PAYLOAD_SIZES),
            "payload_sizes_bytes": list(PAYLOAD_SIZES),
            "total_payload_bytes": sum(PAYLOAD_SIZES),
            "operation_timeout_seconds": plan.operation_timeout_seconds,
            "recovery_timeout_seconds": plan.recovery_timeout_seconds,
        },
        scope="real disposable provider process stop/restart, checksum read-back, and scoped cleanup",
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = asyncio.run(_run(args))
    except (TypeError, ValueError) as exc:
        report = _base(
            mode="live" if args.live else "fixture",
            status="fail",
            reason="outage checker configuration error",
            error_type=_error_type(exc),
        )
    report["payload_free"] = PAYLOAD_MARKER not in json.dumps(report, sort_keys=True)
    if not report["payload_free"]:
        report["status"] = "fail"
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"object-store-provider-outage: {str(report.get('status')).upper()}")
        if report.get("reason"):
            print(f"  {report['reason']}")
    return {"pass": 0, "fail": 1, "blocked": 2}[str(report.get("status"))]


if __name__ == "__main__":
    raise SystemExit(main())
