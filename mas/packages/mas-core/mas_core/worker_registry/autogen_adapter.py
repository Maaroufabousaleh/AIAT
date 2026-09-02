"""AutoGen adapter — wraps an AutoGen group/agent chat as an AIAT worker."""

from __future__ import annotations

import logging
from typing import Any

from mas_core.protocols.worker_manifest import WorkerManifest

logger = logging.getLogger(__name__)


class AutoGenCapabilities:
    """Capabilities schema for AutoGen runtime configuration."""

    def __init__(
        self,
        group_chat_config: dict[str, Any] | None = None,
        termination_strategy: dict[str, Any] | None = None,
        max_round: int = 20,
        allowed_speakers: list[str] | None = None,
    ):
        self.group_chat_config = group_chat_config or {}
        self.termination_strategy = termination_strategy or {}
        self.max_round = max_round
        self.allowed_speakers = allowed_speakers or []

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "AutoGenCapabilities":
        return cls(
            group_chat_config=config.get("group_chat_config"),
            termination_strategy=config.get("termination_strategy", {}),
            max_round=config.get("max_round", 20),
            allowed_speakers=config.get("allowed_speakers", []),
        )


class AutoGenAdapter:
    """Wraps an AutoGen group/agent chat as an AIAT worker.

    AutoGen is a guardrailed specialist runtime — it is NOT an inner runtime
    and cannot spawn subgraphs. It runs behind AIAT's approval gates and
    tool-service boundaries.
    """

    def __init__(
        self,
        manifest: WorkerManifest,
        capabilities: AutoGenCapabilities | None = None,
    ) -> None:
        self.manifest = manifest
        self.capabilities = capabilities or AutoGenCapabilities.from_config(
            manifest.runtime_config
        )
        self._runtime = None
        self._initialized = False
        self._availability_reason: str | None = None

    async def initialize(self) -> None:
        """Set up the AutoGen runtime from manifest.runtime_config."""
        if self._initialized:
            return

        try:
            import importlib
            importlib.import_module("autogen_agentchat")
            importlib.import_module("autogen_core")
        except ImportError:
            self._availability_reason = "autogen-agentchat/autogen-core packages are not installed"
            logger.warning("AutoGenAdapter %s unavailable: %s", self.manifest.metadata.id, self._availability_reason)
            return

        cfg = self.capabilities.group_chat_config or {}
        if not cfg:
            logger.warning(
                "AutoGenAdapter %s unavailable: no group_chat_config in runtime_config",
                self.manifest.metadata.id,
            )
            self._availability_reason = "group_chat_config is required for an executable AutoGen worker"
            return

        try:
            # AutoGen's rapidly changing group-chat API is intentionally not
            # guessed here.  Until a certified adapter implementation is
            # configured, report unavailable instead of returning a fabricated
            # successful/configured result.
            self._availability_reason = "no certified AutoGen group-chat executor is configured"
        except Exception as exc:
            self._availability_reason = f"AutoGen initialization failed: {exc}"
            logger.exception("Failed to initialize AutoGen runtime for %s", self.manifest.metadata.id)

    async def send_task(self, envelope: Any) -> dict[str, Any]:
        """Execute a task through the AutoGen group chat."""
        if not self._initialized:
            await self.initialize()

        task_input = self._translate_input(envelope)

        if self._runtime is None:
            return {
                "status": "unavailable",
                "input": task_input,
                "output": None,
                "runtime": "autogen",
                "worker_id": self.manifest.metadata.id,
                "reason": self._availability_reason or "runtime is not initialized",
            }

        try:
            # AutoGen group chat execution would be implemented here
            # For Epsilon, the adapter is registered and the config is validated;
            # live execution requires autogen-agentchat runtime to be available
            return {
                "status": "unavailable",
                "input": task_input,
                "output": None,
                "runtime": "autogen",
                "worker_id": self.manifest.metadata.id,
                "max_round": self._runtime["max_round"],
                "note": "AutoGen package detected but no certified executor is configured",
                "reason": self._availability_reason or "no certified executor",
            }
        except Exception as exc:
            logger.error("AutoGen execution failed for %s: %s", self.manifest.metadata.id, exc)
            return {
                "status": "error",
                "input": task_input,
                "error": str(exc),
                "runtime": "autogen",
                "worker_id": self.manifest.metadata.id,
            }

    async def health_check(self) -> bool:
        return self._initialized

    async def shutdown(self) -> None:
        self._runtime = None
        self._initialized = False
        self._availability_reason = None
        logger.info("AutoGenAdapter %s shut down", self.manifest.metadata.id)

    def _translate_input(self, envelope: Any) -> dict[str, Any]:
        payload = getattr(envelope, "payload", {}) or {}
        return {
            "task": payload.get("task", ""),
            "context": payload.get("context", ""),
        }
