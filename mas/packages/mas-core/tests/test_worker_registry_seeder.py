"""Startup manifest reconciliation must not alter governed lifecycle state."""

from __future__ import annotations

from uuid import uuid4

import pytest

from mas_core.worker_registry.seeder import seed_workers_from_directory


class _SeedStorage:
    def __init__(self) -> None:
        self.worker_id = uuid4()
        self.register_kwargs: dict[str, object] | None = None

    async def get_worker_by_name(self, name: str):
        assert name == "governed-worker"
        return {
            "id": self.worker_id,
            "name": name,
            "status": "ACTIVE",
            "evaluation_status": "approved",
            "active_shell_version_id": uuid4(),
            "active_adapter_id": uuid4(),
            "active_skill_bundle_id": uuid4(),
        }

    async def register_worker(self, **kwargs):
        self.register_kwargs = kwargs
        return {"id": self.worker_id, "status": kwargs["status"]}


@pytest.mark.anyio
async def test_reseed_preserves_an_existing_governed_worker_lifecycle(tmp_path) -> None:
    (tmp_path / "governed-worker.yaml").write_text(
        """
metadata:
  id: governed-worker
  name: Governed Worker
  version: "1.0.0"
  source_repo: local
runtime:
  transport: process
integration:
  isolation_mode: native
model_mode: none
sandbox:
  profile: standard
""".strip()
        + "\n",
        encoding="utf-8",
    )
    storage = _SeedStorage()

    result = await seed_workers_from_directory(storage, workers_dir=tmp_path)

    assert result[0].action == "updated"
    assert storage.register_kwargs is not None
    assert storage.register_kwargs["status"] == "ACTIVE"
    assert storage.register_kwargs["evaluation_status"] == "approved"
