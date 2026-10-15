"""Opt-in in-process runner for one explicitly configured local worker host.

The runner is development/test infrastructure. It registers an authenticated
worker-plane host, keeps the host lease alive, places only runs matching that
host's unguessable process identity, and delegates all execution to
``HostQueueConsumer``. It does not resolve adapters itself or mutate WorkerRun
state outside the canonical executor/controller path.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import anyio

from .host_queue_consumer import HostQueueConsumer, HostQueueRunSummary
from .host_recovery import HostLeaseRecovery
from .host_registry import WorkerHostRegistry
from .placement import WorkerPlacementRequest
from .run_host_binding import (
    RunHostBindingIdentity,
    RunHostBindingRequest,
    WorkerRunHostBindingService,
)

if TYPE_CHECKING:
    from ..memory.storage import AgentStorage

logger = logging.getLogger(__name__)

TerminalRunHandler = Callable[[HostQueueRunSummary], Awaitable[None]]


def _positive_int(name: str, value: str | None, default: int, *, maximum: int) -> int:
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed < 1 or parsed > maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return parsed


def _truthy(value: str | None) -> bool:
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"", "0", "false", "no", "off"}:
        return False
    raise ValueError("AIAT_LOCAL_HOST_QUEUE_ENABLED must be a boolean")


@dataclass(frozen=True, slots=True)
class LocalHostQueueConfig:
    """Explicit local host settings; credentials are never projected publicly."""

    host_id_prefix: str
    registration_token: str
    sandbox_profile: str
    sandbox_runtime: str
    slots: int = 1
    lease_seconds: int = 60
    poll_seconds: float = 1.0
    batch_size: int = 20

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> LocalHostQueueConfig | None:
        env = os.environ if environ is None else environ
        if not _truthy(env.get("AIAT_LOCAL_HOST_QUEUE_ENABLED")):
            return None
        environment = str(env.get("MAS_ENVIRONMENT") or "development").strip().lower()
        if environment not in {"development", "dev", "test", "testing"}:
            raise ValueError("local host queue is available only in development/test")
        host_id = str(env.get("AIAT_LOCAL_WORKER_HOST_ID") or "").strip()
        token = str(env.get("AIAT_LOCAL_WORKER_HOST_TOKEN") or "")
        profile = str(env.get("AIAT_LOCAL_WORKER_HOST_SANDBOX_PROFILE") or "").strip().lower()
        runtime = str(env.get("AIAT_LOCAL_WORKER_HOST_SANDBOX_RUNTIME") or "").strip().lower()
        if not host_id or not token:
            raise ValueError("local host queue requires host ID and registration token")
        if profile != "sandboxed" or runtime != "runsc":
            raise ValueError("local host queue requires sandboxed/runsc isolation")
        try:
            poll_seconds = float(env.get("AIAT_LOCAL_HOST_QUEUE_POLL_SECONDS", "1"))
        except ValueError as exc:
            raise ValueError("AIAT_LOCAL_HOST_QUEUE_POLL_SECONDS must be numeric") from exc
        if not 0.1 <= poll_seconds <= 30:
            raise ValueError("AIAT_LOCAL_HOST_QUEUE_POLL_SECONDS must be between 0.1 and 30")
        return cls(
            host_id_prefix=host_id,
            registration_token=token,
            sandbox_profile=profile,
            sandbox_runtime=runtime,
            slots=_positive_int("AIAT_LOCAL_WORKER_HOST_SLOTS", env.get("AIAT_LOCAL_WORKER_HOST_SLOTS"), 1, maximum=128),
            lease_seconds=_positive_int("AIAT_LOCAL_WORKER_HOST_LEASE_SECONDS", env.get("AIAT_LOCAL_WORKER_HOST_LEASE_SECONDS"), 60, maximum=3600),
            poll_seconds=poll_seconds,
            batch_size=_positive_int("AIAT_LOCAL_HOST_QUEUE_BATCH_SIZE", env.get("AIAT_LOCAL_HOST_QUEUE_BATCH_SIZE"), 20, maximum=100),
        )


class LocalHostQueueRuntime:
    """Lifecycle supervisor for one local host queue consumer."""

    def __init__(
        self,
        storage: AgentStorage,
        config: LocalHostQueueConfig,
        *,
        adapter_resolver: Any,
        terminal_run_handler: TerminalRunHandler | None = None,
    ) -> None:
        self.storage = storage
        self.config = config
        # A process-scoped host identity makes restart fencing explicit. A new
        # API process never inherits the previous process's host lease.
        self.host_id = f"{config.host_id_prefix}-{uuid4().hex[:12]}"
        # Carry the same process identity into WorkerRun transition actors so
        # durable claim evidence can be tied to this exact host binding.
        self.owner = f"aiat-local-host:{self.host_id}"
        self._registry = WorkerHostRegistry(storage)
        self._bindings = WorkerRunHostBindingService(storage)
        self._adapter_resolver = adapter_resolver
        self._terminal_run_handler = terminal_run_handler
        self._host_generation: int | None = None
        self._lease_valid = False
        self._consumer: HostQueueConsumer | None = None
        self._status = "STARTING"
        self._last_reason: str | None = None
        self._last_summaries: tuple[HostQueueRunSummary, ...] = ()
        self._iterations = 0

    @property
    def active(self) -> bool:
        return self._lease_valid and self._consumer is not None

    def public_status(self) -> dict[str, Any]:
        """Return a bounded status record with no credential/config payload."""

        return {
            "enabled": True,
            "status": self._status,
            "host_id": self.host_id,
            "host_lease_generation": self._host_generation,
            "lease_valid": self._lease_valid,
            "iterations": self._iterations,
            "last_reason": self._last_reason,
            "last_runs": [
                {
                    "run_id": summary.run_id,
                    "state": summary.state,
                    "reason_code": summary.reason_code,
                }
                for summary in self._last_summaries
            ],
        }

    async def start(self) -> None:
        """Register and authenticate the local host before accepting work."""

        registered = await self._registry.register_host(
            host_id=self.host_id,
            registration_token=self.config.registration_token,
            labels={"aiat.local_executor_host": self.host_id},
            capabilities=("worker-execution",),
            host_plane="worker",
            sandbox_profile=self.config.sandbox_profile,
            sandbox_runtime=self.config.sandbox_runtime,
            capacity={"slots_total": self.config.slots},
            metadata={
                "execution_runtimes": [self.config.sandbox_runtime],
                "workload_formats": ["oci", "linux"],
                "local_executor": True,
            },
            status="REGISTERING",
        )
        try:
            self._host_generation = int(registered.get("lease_generation") or 0)
            if self._host_generation < 1:
                raise RuntimeError("local host registration returned invalid lease generation")
            renewed = await self._registry.heartbeat(
                host_id=self.host_id,
                registration_token=self.config.registration_token,
                lease_generation=self._host_generation,
                lease_seconds=self.config.lease_seconds,
            )
            if renewed.get("status") != "READY":
                raise RuntimeError("local worker host did not become READY")
        except BaseException:
            # Registration is durable. If readiness fails after registration,
            # immediately fence this process identity instead of leaving a
            # REGISTERING row until its lease expires. Only use the exact
            # generation returned by authenticated registration.
            if self._host_generation is not None and self._host_generation > 0:
                with anyio.CancelScope(shield=True):
                    try:
                        await self._registry.set_status(
                            host_id=self.host_id,
                            registration_token=self.config.registration_token,
                            lease_generation=self._host_generation,
                            status="OFFLINE",
                        )
                    except Exception as cleanup_exc:  # noqa: BLE001 - sanitized only
                        logger.warning(
                            "local_host_queue_startup_cleanup_failed",
                            extra={"host_id": self.host_id, "error": type(cleanup_exc).__name__},
                        )
            self._status = "FAILED"
            self._lease_valid = False
            raise
        self._consumer = HostQueueConsumer(
            self.storage,
            host_id=self.host_id,
            owner=self.owner,
            adapter_resolver=self._adapter_resolver,
            binding_service=self._bindings,
        )
        self._lease_valid = True
        self._status = "READY"
        self._last_reason = None

    async def run(self, stop_event: Any) -> None:
        """Run the queue loop and host heartbeat until API shutdown."""

        if self._consumer is None or self._host_generation is None:
            raise RuntimeError("local host queue runtime must be started first")
        try:
            async with anyio.create_task_group() as task_group:
                task_group.start_soon(self._heartbeat_loop, stop_event)
                task_group.start_soon(self._dispatch_loop, stop_event)
                await stop_event.wait()
                task_group.cancel_scope.cancel()
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Fence this exact host generation before its storage is closed.

        The dedicated runner also calls this when post-registration setup
        fails before ``run`` starts (for example, a health-port bind failure).
        """

        if self._host_generation is None or self._status == "STOPPED":
            self._lease_valid = False
            return
        # A bounded shield lets shutdown fence the host before storage is
        # closed, including when the owning task group was cancelled.
        with anyio.CancelScope(shield=True):
            try:
                await self._registry.set_status(
                    host_id=self.host_id,
                    registration_token=self.config.registration_token,
                    lease_generation=self._host_generation,
                    status="OFFLINE",
                )
            except Exception as exc:  # noqa: BLE001 - secret-safe status only
                logger.warning(
                    "local_host_queue_shutdown_fence_failed",
                    extra={"host_id": self.host_id, "error": type(exc).__name__},
                )
            self._lease_valid = False
            self._status = "STOPPED"

    async def _heartbeat_loop(self, stop_event: Any) -> None:
        interval = max(0.1, min(5.0, self.config.lease_seconds / 3))
        while not stop_event.is_set():
            await anyio.sleep(interval)
            if stop_event.is_set():
                return
            try:
                renewed = await self._registry.heartbeat(
                    host_id=self.host_id,
                    registration_token=self.config.registration_token,
                    lease_generation=self._host_generation,
                    lease_seconds=self.config.lease_seconds,
                )
                self._lease_valid = renewed.get("status") == "READY"
                self._status = "READY" if self._lease_valid else "DEGRADED"
                self._last_reason = None if self._lease_valid else "host_not_ready"
            except Exception as exc:  # noqa: BLE001 - no exception text may contain credentials
                self._lease_valid = False
                self._status = "DEGRADED"
                self._last_reason = "host_heartbeat_failed"
                logger.warning(
                    "local_host_queue_heartbeat_failed",
                    extra={"host_id": self.host_id, "error": type(exc).__name__},
                )

    async def _dispatch_loop(self, stop_event: Any) -> None:
        while not stop_event.is_set():
            if self._lease_valid:
                try:
                    await self.schedule_queued_runs()
                    consumer = self._consumer
                    if consumer is not None and self._lease_valid:
                        self._last_summaries = await consumer.consume_once(
                            limit=self.config.batch_size
                        )
                        self._iterations += 1
                        if self._terminal_run_handler is not None:
                            for summary in self._last_summaries:
                                if summary.state in {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}:
                                    await self._terminal_run_handler(summary)
                except Exception as exc:  # noqa: BLE001 - sanitized reason only
                    self._last_reason = "queue_iteration_failed"
                    logger.warning(
                        "local_host_queue_iteration_failed",
                        extra={"host_id": self.host_id, "error": type(exc).__name__},
                    )
            await anyio.sleep(self.config.poll_seconds)

    async def schedule_queued_runs(self, *, limit: int = 100) -> int:
        """Place queued runs on this exact process identity and commit binding."""

        if not self._lease_valid:
            return 0
        await self._recover_expired_local_host_incarnations()
        runs = await self.storage.list_worker_runs(state="QUEUED", limit=limit, offset=0)
        scheduled = 0
        for run in runs:
            try:
                if await self._schedule_run(run):
                    scheduled += 1
            except Exception as exc:  # noqa: BLE001 - leave queued for bounded retry
                logger.info(
                    "local_host_queue_run_not_scheduled",
                    extra={
                        "run_id": str(run.get("id") or ""),
                        "reason": type(exc).__name__,
                    },
                )
        return scheduled

    async def _recover_expired_local_host_incarnations(self) -> None:
        """Fence expired prior processes for this configured local host prefix."""

        list_hosts = getattr(self._registry, "list_hosts", None)
        if not callable(list_hosts):
            return
        hosts = await list_hosts()
        prefix = f"{self.config.host_id_prefix}-"
        prior_host_ids = [
            str(host.get("host_id") or "")
            for host in hosts
            if isinstance(host, Mapping)
            and str(host.get("host_id") or "").startswith(prefix)
            and str(host.get("host_id") or "") != self.host_id
        ]
        if prior_host_ids:
            await HostLeaseRecovery(self.storage).reconcile_expired_hosts(
                host_ids=prior_host_ids,
                limit=min(100, len(prior_host_ids)),
            )

    async def schedule_run(self, run_id: UUID | str) -> bool:
        """Attempt placement for one canonical queued run immediately."""

        if not self._lease_valid:
            return False
        try:
            normalized = run_id if isinstance(run_id, UUID) else UUID(str(run_id))
        except (TypeError, ValueError):
            return False
        run = await self.storage.get_worker_run(normalized)
        if not isinstance(run, Mapping):
            return False
        return await self._schedule_run(run)

    async def _schedule_run(self, run: Mapping[str, Any]) -> bool:
        try:
            run_id = UUID(str(run.get("id")))
            worker_id = UUID(str(run.get("worker_id")))
        except (TypeError, ValueError):
            return False
        if str(run.get("state") or "").upper() != "QUEUED":
            return False
        worker = await self.storage.get_worker(worker_id)
        if not isinstance(worker, Mapping) or str(worker.get("status") or "").upper() not in {"ACTIVE", "DRAINING"}:
            return False

        assignment_key = f"local-host:{self.config.host_id_prefix}:{run_id}"
        placement = self._placement(worker)
        request = RunHostBindingRequest(
            run_id=run_id,
            worker_id=worker_id,
            assignment_key=assignment_key,
            owner=self.owner,
            placement=placement,
            lease_seconds=self.config.lease_seconds,
            metadata={"dispatch_mode": "local_host_queue"},
        )
        existing = await self._bindings.get(run_id)
        if existing is not None:
            if str(existing.get("host_id") or "") == self.host_id:
                return (
                    existing.get("state") == "COMMITTED"
                    and existing.get("reservation_state") == "COMMITTED"
                    and existing.get("current_host_lease_valid") is True
                    and int(existing.get("host_lease_generation") or 0)
                    == int(existing.get("current_host_lease_generation") or 0)
                )
            if (
                existing.get("state") == "COMMITTED"
                and existing.get("reservation_state") == "EXPIRED"
                and existing.get("current_host_lease_valid") is False
            ):
                recovery_request = replace(
                    request,
                    assignment_key=f"{assignment_key}:recovery:{self.host_id}",
                )
                recovered = await self._bindings.reassign_after_host_loss(
                    recovery_request,
                    recovery_reason="local_host_process_restarted",
                )
                return str(recovered.get("host_id") or "") == self.host_id
            return False

        binding = await self._bindings.assign(request)
        if str(binding.get("host_id") or "") != self.host_id:
            try:
                await self._bindings.release(
                    run_id,
                    owner=self.owner,
                    expected_identity=RunHostBindingIdentity.from_binding(binding),
                )
            except Exception:
                logger.warning(
                    "local_host_queue_wrong_host_reservation_cleanup_failed",
                    extra={"run_id": str(run_id)},
                )
            return False
        if binding.get("state") == "ASSIGNED":
            binding = await self._bindings.commit(run_id, owner=self.owner)
        return (
            binding.get("state") == "COMMITTED"
            and binding.get("reservation_state") == "COMMITTED"
            and binding.get("current_host_lease_valid") is True
        )

    def _placement(self, worker: Mapping[str, Any]) -> WorkerPlacementRequest:
        adapter_config = worker.get("adapter_config")
        adapter_config = adapter_config if isinstance(adapter_config, Mapping) else {}
        execution_policy = adapter_config.get("execution_policy")
        if not isinstance(execution_policy, Mapping):
            execution_policy = None
        return WorkerPlacementRequest(
            worker_id=str(worker.get("id") or ""),
            required_labels=(("aiat.local_executor_host", self.host_id),),
            required_host_plane="worker",
            required_sandbox_profile=str(worker.get("sandbox_profile") or self.config.sandbox_profile),
            execution_policy=execution_policy,
            slots=1,
        )


__all__ = ["LocalHostQueueConfig", "LocalHostQueueRuntime"]
