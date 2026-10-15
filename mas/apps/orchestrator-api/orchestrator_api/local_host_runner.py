"""Dedicated process entrypoint for the disposable Level A worker host.

The orchestrator API owns project/run admission and does not receive the Docker
socket. This process owns the existing ``LocalHostQueueRuntime`` and is the
only Level A service granted access to the host Docker daemon. It reuses the
canonical adapter resolver and budget settlement callbacks from the API module
so WorkerRun authority, host reservations/fencing, tool mediation, and result
settlement remain unchanged.

This is an opt-in development/test entrypoint, not a production deployment
recipe. Its Docker socket access is equivalent to host authority and must stay
isolated to this dedicated host process; spawned workers receive no socket.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
from contextlib import suppress
from typing import TYPE_CHECKING, Any

import anyio

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger("aiat.local_host_runner")
HEALTH_HOST = "127.0.0.1"
HEALTH_PORT = 8765


async def _health_response(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    runtime: Any,
) -> None:
    """Serve a loopback-only, payload-safe runtime health response."""

    try:
        request_line = await asyncio.wait_for(reader.readline(), timeout=2.0)
        parts = request_line.decode("ascii", errors="ignore").strip().split()
        if len(parts) < 2 or parts[1] != "/health":
            status_code = 404
            body = {"status": "NOT_FOUND"}
        else:
            public = runtime.public_status()
            healthy = runtime.active and public.get("status") == "READY"
            status_code = 200 if healthy else 503
            body = {
                "status": "READY" if healthy else str(public.get("status") or "DEGRADED"),
                "lease_valid": bool(public.get("lease_valid")),
            }
        while True:
            header = await asyncio.wait_for(reader.readline(), timeout=2.0)
            if header in {b"\r\n", b"\n", b""}:
                break
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
        reason = "OK" if status_code == 200 else "Not Found" if status_code == 404 else "Service Unavailable"
        writer.write(
            f"HTTP/1.1 {status_code} {reason}\r\n".encode("ascii")
            + b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(encoded)}\r\n".encode("ascii")
            + b"Connection: close\r\n\r\n"
            + encoded
        )
        await writer.drain()
    except (TimeoutError, ConnectionError, OSError):
        # Health checks are intentionally payload-free and best effort.
        pass
    finally:
        writer.close()
        with suppress(ConnectionError, OSError):
            await writer.wait_closed()


async def _signal_waiter(stop_event: anyio.Event) -> None:
    """Turn normal container termination into the host runtime's shutdown."""

    with anyio.open_signal_receiver(signal.SIGINT, signal.SIGTERM) as signals:
        async for _ in signals:
            stop_event.set()
            return


async def run_host_runner(
    *,
    storage_factory: Callable[..., Any] | None = None,
    runtime_factory: Callable[..., Any] | None = None,
    signal_waiter: Callable[[anyio.Event], Any] = _signal_waiter,
) -> None:
    """Run the canonical local host queue consumer until process shutdown."""

    from mas_core.worker_registry.local_host_queue_runtime import (
        LocalHostQueueConfig,
        LocalHostQueueRuntime,
    )

    from . import main as api

    config = LocalHostQueueConfig.from_environment()
    if config is None:
        raise RuntimeError("dedicated worker host requires explicit local queue opt-in")

    if storage_factory is None:
        storage_factory = api.AgentStorage
    if runtime_factory is None:
        runtime_factory = LocalHostQueueRuntime
    storage = storage_factory(dsn=api.PGBOUNCER_DSN)
    runtime: Any | None = None
    runtime_started = False
    started_adapters: list[str] = []

    async def resolve_adapter(pins: Any) -> Any:
        worker = await storage.get_worker(pins.worker_id)
        if not isinstance(worker, dict):
            return None
        adapter = await api._certified_worker_adapter(
            storage,
            dict(worker),
            adapter_id=pins.adapter_id,
            allow_retired=True,
        )
        if adapter is not None:
            started_adapters.append(str(pins.worker_id))
        return adapter

    async def settle_terminal(summary: Any) -> None:
        await api._settle_host_queue_run_budgets(storage, summary)

    try:
        await storage.connect()
        runtime = runtime_factory(
            storage,
            config,
            adapter_resolver=resolve_adapter,
            terminal_run_handler=settle_terminal,
        )
        await runtime.start()
        runtime_started = True
        stop_event = anyio.Event()
        server = await asyncio.start_server(
            lambda reader, writer: _health_response(reader, writer, runtime=runtime),
            host=HEALTH_HOST,
            port=HEALTH_PORT,
            limit=4096,
        )
        try:
            async with server, anyio.create_task_group() as task_group:
                task_group.start_soon(runtime.run, stop_event)
                task_group.start_soon(signal_waiter, stop_event)
                await stop_event.wait()
                task_group.cancel_scope.cancel()
        finally:
            server.close()
            await server.wait_closed()
    finally:
        # Fence a successfully registered host even if health-server binding
        # or task-group setup failed before runtime.run() could own shutdown.
        if runtime is not None and runtime_started:
            await runtime.stop()
        # The adapter cache is process-local. Close its entries after the host
        # loop has exited and before releasing the database pool.
        if runtime is not None:
            try:
                workers = await storage.list_workers()
                for worker in workers:
                    worker_id = str(worker.get("id") or "")
                    if worker_id in started_adapters:
                        await api._invalidate_worker_adapter_runtime(worker_id)
            except Exception as exc:  # noqa: BLE001 - never log exception text or credentials
                logger.warning("local_host_adapter_cleanup_failed", extra={"error": type(exc).__name__})
        await storage.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the isolated AIAT Level A worker host")
    parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        anyio.run(run_host_runner)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001 - bounded, secret-safe top-level status
        logger.error("local_host_runner_failed", extra={"error": type(exc).__name__})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
