"""Microsoft Agent Framework adapter.

The adapter keeps the framework behind the AIAT worker contract.  The
framework remains optional at import time, but a worker is never reported as
ready unless the package, a configured agent, and an executable ``run`` or
``invoke`` method are all present.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any

from mas_core.protocols.worker_manifest import WorkerManifest

from .maf_compatibility import evaluate_microsoft_agent_framework_compatibility

logger = logging.getLogger(__name__)


class MicrosoftAgentFrameworkAdapter:
    """Configuration-driven adapter for Microsoft's Agent Framework."""

    def __init__(self, manifest: WorkerManifest, capabilities: dict[str, Any] | None = None) -> None:
        self.manifest = manifest
        self.capabilities = capabilities or dict(manifest.runtime_config or {})
        self._agent: Any | None = None
        self._initialized = False
        self._availability_reason: str | None = None

    async def initialize(self) -> None:
        if self._initialized or self._availability_reason:
            return
        compatibility = evaluate_microsoft_agent_framework_compatibility()
        if not compatibility.ready:
            self._availability_reason = "MAF compatibility preflight blocked: " + "; ".join(
                compatibility.blockers
            )
            return
        try:
            module = importlib.import_module("agent_framework")
        except ImportError:
            self._availability_reason = "agent-framework package is not installed"
            return

        agent_cls = getattr(module, "Agent", None) or getattr(module, "ChatAgent", None)
        if agent_cls is None:
            self._availability_reason = "agent_framework.Agent/ChatAgent is unavailable in the installed package"
            return
        name = str(self.capabilities.get("agent_name") or self.manifest.metadata.id)
        instructions = str(self.capabilities.get("instructions") or "")
        if not instructions:
            self._availability_reason = "runtime_config.instructions is required"
            return
        try:
            self._agent = agent_cls(name=name, instructions=instructions)
        except TypeError:
            # Early releases used ``system_message`` instead of
            # ``instructions``; keep the compatibility branch explicit.
            try:
                self._agent = agent_cls(name=name, system_message=instructions)
            except Exception as exc:  # pragma: no cover - version-specific
                self._availability_reason = f"Agent construction failed: {exc}"
                return
        except Exception as exc:
            self._availability_reason = f"Agent construction failed: {exc}"
            return
        if not callable(getattr(self._agent, "run", None)) and not callable(getattr(self._agent, "invoke", None)):
            self._agent = None
            self._availability_reason = "configured Agent has no run/invoke execution method"
            return
        self._initialized = True

    async def send_task(self, envelope: Any) -> dict[str, Any]:
        if not self._initialized:
            await self.initialize()
        payload = getattr(envelope, "payload", {}) or {}
        task = payload.get("task") or payload.get("input") or payload.get("messages") or payload
        if self._agent is None:
            return {
                "status": "unavailable",
                "runtime": "microsoft_agent_framework",
                "worker_id": self.manifest.metadata.id,
                "input": task,
                "output": None,
                "reason": self._availability_reason or "runtime is not initialized",
            }
        try:
            method = getattr(self._agent, "run", None) or getattr(self._agent, "invoke")
            result = method(task)
            if hasattr(result, "__await__"):
                result = await result
            return {
                "status": "completed",
                "runtime": "microsoft_agent_framework",
                "worker_id": self.manifest.metadata.id,
                "input": task,
                "output": result,
            }
        except Exception as exc:
            logger.exception("Microsoft Agent Framework execution failed for %s", self.manifest.metadata.id)
            return {
                "status": "error",
                "runtime": "microsoft_agent_framework",
                "worker_id": self.manifest.metadata.id,
                "input": task,
                "output": None,
                "error": str(exc),
            }

    async def health_check(self) -> bool:
        return self._initialized and self._agent is not None

    async def shutdown(self) -> None:
        close = getattr(self._agent, "close", None)
        if callable(close):
            result = close()
            if hasattr(result, "__await__"):
                await result
        self._agent = None
        self._initialized = False
        self._availability_reason = None
