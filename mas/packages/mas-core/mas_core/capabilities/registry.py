"""In-memory capability registry used before Phase 7 persistence wiring."""

from __future__ import annotations

from dataclasses import dataclass, field

from mas_core.protocols.capability import WorkerCapabilityRecord


@dataclass
class InMemoryCapabilityRegistry:
    _workers: dict[str, WorkerCapabilityRecord] = field(default_factory=dict)

    def register(self, worker: WorkerCapabilityRecord) -> None:
        self._workers[worker.worker_id] = worker

    def deregister(self, worker_id: str) -> bool:
        return self._workers.pop(worker_id, None) is not None

    def list_workers(self) -> list[WorkerCapabilityRecord]:
        return list(self._workers.values())

    def search(self, capability_name: str) -> list[WorkerCapabilityRecord]:
        needle = capability_name.strip().lower()
        if not needle:
            return self.list_workers()
        return [
            worker
            for worker in self._workers.values()
            if any(cap.lower() == needle for cap in worker.capabilities)
        ]

