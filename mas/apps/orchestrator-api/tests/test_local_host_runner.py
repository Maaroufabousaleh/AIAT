from __future__ import annotations

import pytest
from orchestrator_api import local_host_runner


@pytest.mark.asyncio
async def test_runner_fences_host_if_health_listener_fails_after_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class Storage:
        async def connect(self) -> None:
            events.append("storage_connect")

        async def list_workers(self) -> list[dict[str, str]]:
            events.append("storage_list_workers")
            return []

        async def close(self) -> None:
            events.append("storage_close")

    class Runtime:
        active = True

        async def start(self) -> None:
            events.append("runtime_start_registered_ready")

        async def stop(self) -> None:
            events.append("runtime_stop_fenced_offline")

    async def fail_start_server(*_args, **_kwargs):
        raise OSError("health listener unavailable")

    monkeypatch.setenv("MAS_ENVIRONMENT", "test")
    monkeypatch.setenv("AIAT_LOCAL_HOST_QUEUE_ENABLED", "true")
    monkeypatch.setenv("AIAT_LOCAL_WORKER_HOST_ID", "local-test-host")
    monkeypatch.setenv("AIAT_LOCAL_WORKER_HOST_TOKEN", "test-only-registration-token")
    monkeypatch.setenv("AIAT_LOCAL_WORKER_HOST_SANDBOX_PROFILE", "sandboxed")
    monkeypatch.setenv("AIAT_LOCAL_WORKER_HOST_SANDBOX_RUNTIME", "runsc")
    monkeypatch.setattr(local_host_runner.asyncio, "start_server", fail_start_server)

    with pytest.raises(OSError):
        await local_host_runner.run_host_runner(
            storage_factory=lambda **_kwargs: Storage(),
            runtime_factory=lambda *_args, **_kwargs: Runtime(),
        )

    assert events == [
        "storage_connect",
        "runtime_start_registered_ready",
        "runtime_stop_fenced_offline",
        "storage_list_workers",
        "storage_close",
    ]
