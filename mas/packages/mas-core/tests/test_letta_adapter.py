from __future__ import annotations

from types import SimpleNamespace

import pytest

from mas_core.protocols.worker_manifest import WorkerManifest
from mas_core.worker_registry.letta_adapter import LettaAdapter


def _manifest(*, persona: str = "bounded persona") -> WorkerManifest:
    return WorkerManifest.model_validate(
        {
            "metadata": {"id": "letta-fixture", "name": "Letta fixture"},
            "runtime_tier": "letta",
            "runtime_config": {"persona": persona},
            "integration": {"isolation_mode": "letta"},
        }
    )


@pytest.mark.anyio
async def test_unavailable_result_contains_only_bounded_input_metadata(monkeypatch) -> None:
    monkeypatch.setattr(
        "mas_core.worker_registry.letta_adapter.importlib.import_module",
        lambda _name: (_ for _ in ()).throw(ImportError("fixture package missing")),
    )
    secret_task = "private-task-value"
    secret_context = "private-context-value"

    result = await LettaAdapter(_manifest()).send_task(
        SimpleNamespace(payload={"task": secret_task, "context": secret_context})
    )

    assert result["status"] == "unavailable"
    assert result["reason"] == "letta package is not installed"
    assert result["input_summary"] == {
        "task_present": True,
        "context_present": True,
        "task_chars": len(secret_task),
        "context_chars": len(secret_context),
    }
    serialized = str(result)
    assert secret_task not in serialized
    assert secret_context not in serialized
    assert "input" not in result
    assert "output" not in result


@pytest.mark.anyio
async def test_import_failure_uses_stable_reason_without_raw_exception(monkeypatch) -> None:
    def fail_import(_name: str):
        raise RuntimeError("secret import detail")

    monkeypatch.setattr(
        "mas_core.worker_registry.letta_adapter.importlib.import_module", fail_import
    )

    result = await LettaAdapter(_manifest()).send_task(SimpleNamespace(payload={"task": "fixture"}))

    assert result["status"] == "unavailable"
    assert result["reason"] == "letta_import_failed"
    assert "secret import detail" not in str(result)
