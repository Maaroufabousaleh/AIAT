"""Governed pause, checkpoint, and resume API coverage."""

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

WORKER_ID = UUID("00000000-0000-4000-a000-000000000601")
RUN_ID = UUID("00000000-0000-4000-a000-000000000602")
CHECKPOINT_ID = UUID("00000000-0000-4000-a000-000000000603")
ADAPTER_ID = UUID("00000000-0000-4000-a000-000000000604")


class _RunControlStorage:
    def __init__(self, *, state: str = "RUNNING") -> None:
        self.run = {
            "id": RUN_ID,
            "worker_id": WORKER_ID,
            "adapter_id": ADAPTER_ID,
            "state": state,
        }
        self.worker = {"id": WORKER_ID, "status": "ACTIVE"}
        self.transitions: list[dict[str, object]] = []
        self.checkpoints = [
            {
                "id": CHECKPOINT_ID,
                "run_id": RUN_ID,
                "sequence": 7,
                "resumable": True,
                "state_json": {"step": "safe"},
            }
        ]

    async def get_worker_run(self, run_id: UUID):
        return dict(self.run) if run_id == RUN_ID else None

    async def get_worker(self, worker_id: UUID):
        return dict(self.worker) if worker_id == WORKER_ID else None

    async def transition_worker_run(
        self,
        run_id: UUID,
        *,
        new_state: str,
        expected_state: str | None = None,
        **kwargs: object,
    ):
        if run_id != RUN_ID or (
            expected_state is not None and self.run["state"] != expected_state
        ):
            return None
        self.run["state"] = new_state
        self.transitions.append({"state": new_state, **kwargs})
        return dict(self.run)

    async def list_worker_checkpoints(self, run_id: UUID, *, limit: int = 100):
        if run_id != RUN_ID:
            return []
        return self.checkpoints[:limit]


class _ControllableAdapter:
    def __init__(self, checkpoint_mode: str) -> None:
        from mas_core.worker_contract import WorkerCapabilities

        self.capabilities = WorkerCapabilities(checkpoint_mode=checkpoint_mode)
        self.pause_calls: list[object] = []
        self.resume_calls: list[object] = []

    async def pause(self, request):
        self.pause_calls.append(request)

    async def resume(self, request):
        self.resume_calls.append(request)


def _patch_storage(storage: _RunControlStorage) -> None:
    from orchestrator_api.main import app

    app.state.storage = storage


@pytest.mark.anyio
async def test_worker_run_pause_resume_and_checkpoint_routes(client, monkeypatch) -> None:
    import orchestrator_api.main as main

    storage = _RunControlStorage()
    adapter = _ControllableAdapter("native")
    _patch_storage(storage)
    monkeypatch.setattr(
        main,
        "_certified_worker_adapter",
        AsyncMock(return_value=adapter),
    )

    checkpoints = await client.get(f"/workers/runs/{RUN_ID}/checkpoints")
    assert checkpoints.status_code == 200
    assert checkpoints.json()[0]["id"] == str(CHECKPOINT_ID)

    paused = await client.post(
        f"/workers/runs/{RUN_ID}/pause",
        json={"reason": "operator review", "requested_by": "reviewer"},
    )
    assert paused.status_code == 200
    assert paused.json()["state"] == "PAUSED"
    assert adapter.pause_calls[0].reason == "operator review"
    assert storage.transitions[0]["actor"] == "reviewer"

    resumed = await client.post(
        f"/workers/runs/{RUN_ID}/resume",
        json={"requested_by": "reviewer", "checkpoint_id": str(CHECKPOINT_ID)},
    )
    assert resumed.status_code == 200
    assert resumed.json()["state"] == "RUNNING"
    assert adapter.resume_calls[0].checkpoint_id == CHECKPOINT_ID
    assert storage.transitions[-1]["transition_metadata"] == {
        "checkpoint_id": str(CHECKPOINT_ID)
    }


@pytest.mark.anyio
async def test_restart_only_worker_cannot_be_resumed_in_place(client, monkeypatch) -> None:
    import orchestrator_api.main as main

    storage = _RunControlStorage(state="PAUSED")
    adapter = _ControllableAdapter("restart_only")
    _patch_storage(storage)
    monkeypatch.setattr(
        main,
        "_certified_worker_adapter",
        AsyncMock(return_value=adapter),
    )

    response = await client.post(
        f"/workers/runs/{RUN_ID}/resume",
        json={"requested_by": "reviewer", "checkpoint_id": str(CHECKPOINT_ID)},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CHECKPOINT_RESTART_ONLY"
    assert adapter.resume_calls == []


@pytest.mark.anyio
async def test_restart_only_worker_cannot_be_paused_in_place(client, monkeypatch) -> None:
    import orchestrator_api.main as main

    storage = _RunControlStorage()
    adapter = _ControllableAdapter("restart_only")
    _patch_storage(storage)
    monkeypatch.setattr(
        main,
        "_certified_worker_adapter",
        AsyncMock(return_value=adapter),
    )

    response = await client.post(
        f"/workers/runs/{RUN_ID}/pause",
        json={"requested_by": "reviewer"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CHECKPOINT_RESTART_ONLY"
    assert adapter.pause_calls == []


@pytest.mark.anyio
async def test_run_controls_resolve_the_adapter_pinned_on_the_run(client, monkeypatch) -> None:
    import orchestrator_api.main as main

    storage = _RunControlStorage()
    adapter = _ControllableAdapter("native")
    _patch_storage(storage)
    resolver = AsyncMock(return_value=adapter)
    monkeypatch.setattr(main, "_certified_worker_adapter", resolver)

    response = await client.post(
        f"/workers/runs/{RUN_ID}/pause",
        json={"requested_by": "reviewer"},
    )

    assert response.status_code == 200
    assert resolver.await_args.kwargs == {
        "adapter_id": ADAPTER_ID,
        "allow_retired": True,
    }


@pytest.mark.anyio
async def test_certification_materializes_an_immutable_worker_shell() -> None:
    import orchestrator_api.main as main

    shell_id = uuid4()
    candidate_id = uuid4()
    bundle_id = uuid4()

    class ShellStorage:
        async def get_runtime_adapter(self, adapter_id):
            assert adapter_id == ADAPTER_ID
            return {
                "id": adapter_id,
                "worker_id": WORKER_ID,
                "capabilities_json": {"capability_names": ["test.run"]},
            }

        async def get_skill_bundle(self, requested_bundle_id):
            assert requested_bundle_id == bundle_id
            return {"id": bundle_id, "worker_id": WORKER_ID}

        async def get_worker_shell_version_by_version(self, worker_id, version):
            assert worker_id == WORKER_ID
            assert version == f"governed-{candidate_id}"
            return None

        async def get_external_provenance_by_worker(self, worker_id):
            assert worker_id == WORKER_ID
            return {"canonical_source_repository": "https://github.com/example/worker"}

        async def create_worker_shell_version(self, **kwargs):
            self.kwargs = kwargs
            return {"id": shell_id, **kwargs}

    storage = ShellStorage()
    shell = await main._materialize_candidate_worker_shell(
        storage,
        worker={
            "id": WORKER_ID,
            "name": "Worker",
            "team_id": "dept_qa",
            "sandbox_profile": "gvisor",
            "model_mode": "none",
            "adapter_config": {},
        },
        candidate={
            "id": candidate_id,
            "adapter_id": ADAPTER_ID,
            "skill_bundle_id": bundle_id,
        },
    )

    assert shell["id"] == shell_id
    assert storage.kwargs["worker_id"] == WORKER_ID
    assert storage.kwargs["version"] == f"governed-{candidate_id}"
    assert storage.kwargs["provenance"]["candidate_id"] == str(candidate_id)
