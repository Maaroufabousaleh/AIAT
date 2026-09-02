"""Letta adapter — wraps a Letta agent as an AIAT worker."""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mas_core.protocols.worker_manifest import WorkerManifest

logger = logging.getLogger(__name__)


class LettaCapabilities:
    """Capabilities schema for Letta runtime configuration."""

    def __init__(
        self,
        persona: str = "",
        embedding_model: str = "text-embedding-ada-002",
        persistence_store: str = "postgres",
        memory_block_types: list[str] | None = None,
    ):
        self.persona = persona
        self.embedding_model = embedding_model
        self.persistence_store = persistence_store
        self.memory_block_types = memory_block_types or ["human", "persona", "archival"]

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> LettaCapabilities:
        return cls(
            persona=config.get("persona", ""),
            embedding_model=config.get("embedding_model", "text-embedding-ada-002"),
            persistence_store=config.get("persistence_store", "postgres"),
            memory_block_types=config.get("memory_block_types", ["human", "persona", "archival"]),
        )


class LettaAdapter:
    """Wraps a Letta agent as an AIAT worker.

    Letta is a memory-heavy research specialist runtime. It is read-only by
    default and requires a memory audit before activation. It cannot make
    network calls or access the filesystem directly.
    """

    def __init__(
        self,
        manifest: WorkerManifest,
        capabilities: LettaCapabilities | None = None,
    ) -> None:
        self.manifest = manifest
        self.capabilities = capabilities or LettaCapabilities.from_config(
            manifest.runtime_config
        )
        self._client = None
        self._agent_id = None
        self._initialized = False
        self._availability_reason: str | None = None

    async def initialize(self) -> None:
        """Connect to the Letta server or embed the Letta runtime."""
        if self._initialized:
            return

        try:
            importlib.import_module("letta")
        except ImportError:
            self._availability_reason = "letta package is not installed"
            logger.warning("LettaAdapter %s unavailable: %s", self.manifest.metadata.id, self._availability_reason)
            return
        except Exception:
            self._availability_reason = "letta_import_failed"
            logger.exception("Letta import failed for %s", self.manifest.metadata.id)
            return

        if not self.capabilities.persona:
            logger.warning(
                "LettaAdapter %s unavailable: no persona defined in runtime_config",
                self.manifest.metadata.id,
            )
            self._availability_reason = "persona is required for an executable Letta worker"
            return

        try:
            # A client must be explicitly configured with an approved endpoint
            # and credential.  Do not turn package presence into a fake agent.
            self._availability_reason = "no certified Letta client endpoint is configured"
        except Exception:
            self._availability_reason = "letta_initialization_failed"
            logger.exception("Failed to initialize Letta agent for %s", self.manifest.metadata.id)

    async def send_task(self, envelope: Any) -> dict[str, Any]:
        """Send a task to the Letta agent."""
        if not self._initialized:
            await self.initialize()

        input_summary = self._summarize_input(envelope)

        if self._client is None:
            return {
                "status": "unavailable",
                "runtime": "letta",
                "worker_id": self.manifest.metadata.id,
                "input_summary": input_summary,
                "reason": self._availability_reason or "runtime is not initialized",
            }

        try:
            # Letta agent execution would send to the Letta server here
            return {
                "status": "unavailable",
                "runtime": "letta",
                "worker_id": self.manifest.metadata.id,
                "input_summary": input_summary,
                "note": "Letta package detected but no certified client endpoint is configured",
                "reason": self._availability_reason or "no certified client",
            }
        except Exception as exc:
            logger.error("Letta execution failed for %s: %s", self.manifest.metadata.id, exc)
            return {
                "status": "error",
                "runtime": "letta",
                "worker_id": self.manifest.metadata.id,
                "input_summary": input_summary,
                "reason": "letta_execution_failed",
            }

    async def health_check(self) -> bool:
        return self._initialized

    async def shutdown(self) -> None:
        self._client = None
        self._agent_id = None
        self._initialized = False
        self._availability_reason = None
        logger.info("LettaAdapter %s shut down", self.manifest.metadata.id)

    def _summarize_input(self, envelope: Any) -> dict[str, Any]:
        payload = getattr(envelope, "payload", {}) or {}
        return {
            "task_present": bool(payload.get("task")),
            "context_present": bool(payload.get("context")),
            "task_chars": _bounded_length(payload.get("task")),
            "context_chars": _bounded_length(payload.get("context")),
        }


def _bounded_length(value: Any, *, maximum: int = 100_000) -> int:
    """Return only bounded scalar input metadata; never retain the value."""
    if value is None:
        return 0
    if isinstance(value, str):
        return min(len(value), maximum)
    return 0
