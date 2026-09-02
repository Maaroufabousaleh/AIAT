"""Focused tests for the generic external-worker router boundary."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mas_core.agent_runtime.config import AgentConfig
from mas_core.protocols.worker_manifest import WorkerManifest
from mas_core.worker_registry.adapter_factory import ExternalWorkerAdapter


def _manifest() -> WorkerManifest:
    return WorkerManifest.model_validate(
        {
            "metadata": {
                "id": "external-test-worker",
                "name": "External Test Worker",
                "evaluation_status": "approved",
            },
            "integration": {
                "adapter_entrypoint": "ExternalWorker",
                "isolation_mode": "wrapper",
                "wrapper_config": {},
            },
            "runtime_tier": "external",
        }
    )


def _config() -> AgentConfig:
    return AgentConfig.model_construct(
        agent_id="external-test-worker",
        team_id="team-test",
        agent_role="worker",
        agent_secret="secret",
    )


class _ExternalWorker:
    def __init__(self, **_: object) -> None:
        pass


@pytest.mark.asyncio
async def test_external_adapter_publishes_through_injected_router() -> None:
    published: list[object] = []

    class Router:
        async def publish(self, envelope: object) -> str:
            published.append(envelope)
            return "stream-entry-1"

    adapter = ExternalWorkerAdapter(
        manifest=_manifest(),
        config=_config(),
        external_class=_ExternalWorker,
        mirror_path=SimpleNamespace(),  # type: ignore[arg-type]
        router=Router(),
    )
    envelope = object()

    assert await adapter.publish(envelope) == "stream-entry-1"
    assert published == [envelope]


@pytest.mark.asyncio
async def test_external_adapter_fails_closed_without_router() -> None:
    adapter = ExternalWorkerAdapter(
        manifest=_manifest(),
        config=_config(),
        external_class=_ExternalWorker,
        mirror_path=SimpleNamespace(),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="requires a router"):
        await adapter.publish(object())


@pytest.mark.asyncio
async def test_external_adapter_rejects_non_string_router_result() -> None:
    class Router:
        async def publish(self, _: object) -> int:
            return 7

    adapter = ExternalWorkerAdapter(
        manifest=_manifest(),
        config=_config(),
        external_class=_ExternalWorker,
        mirror_path=SimpleNamespace(),  # type: ignore[arg-type]
        router=Router(),
    )

    with pytest.raises(RuntimeError, match="non-string entry id"):
        await adapter.publish(object())
