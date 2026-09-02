"""Microsoft Agent Framework adapter.

The adapter keeps the framework behind the AIAT worker contract.  The
framework remains optional at import time, but a worker is never reported as
ready unless the package, a configured agent, and an executable ``run`` or
``invoke`` method are all present.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .maf_compatibility import evaluate_microsoft_agent_framework_compatibility

if TYPE_CHECKING:
    from mas_core.protocols.worker_manifest import WorkerManifest

logger = logging.getLogger(__name__)


class MicrosoftAgentFrameworkAdapter:
    """Configuration-driven adapter for Microsoft's Agent Framework."""

    def __init__(
        self,
        manifest: WorkerManifest,
        capabilities: dict[str, Any] | None = None,
        *,
        client: Any | None = None,
    ) -> None:
        """Create an adapter around an optional MAF agent.

        ``client`` is an explicit boundary injection for a configured AIAT
        model gateway or a deterministic certification fixture.  It is not
        accepted from a worker manifest because manifests are untrusted data
        and must never carry live client objects or credentials.
        """
        self.manifest = manifest
        self.capabilities = capabilities or dict(manifest.runtime_config or {})
        self._client = client
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
        if self._client is not None:
            construction_attempts: list[tuple[tuple[Any, ...], dict[str, Any]]] = [
                ((self._client,), {"name": name, "instructions": instructions})
            ]
        else:
            # Keep compatibility with early/fake releases whose constructor
            # did not require a chat client.  The real MAF 1.13.0 constructor
            # does require one, so a missing client fails closed instead of
            # contacting a provider implicitly.
            construction_attempts = [
                ((), {"name": name, "instructions": instructions}),
                ((), {"name": name, "system_message": instructions}),
            ]
        construction_error: Exception | None = None
        for args, kwargs in construction_attempts:
            try:
                self._agent = agent_cls(*args, **kwargs)
                break
            except TypeError as exc:
                construction_error = exc
                continue
            except Exception as exc:
                construction_error = exc
                break
        if self._agent is None:
            if self._client is None:
                self._availability_reason = (
                    "runtime_config.client boundary is required by the installed "
                    "Agent implementation; no provider client was injected"
                )
            else:
                self._availability_reason = f"Agent construction failed: {construction_error}"
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
            method = getattr(self._agent, "run", None)
            if not callable(method):
                method = self._agent.invoke if hasattr(self._agent, "invoke") else None
            if not callable(method):
                raise RuntimeError("configured Agent has no run/invoke execution method")
            result = method(task)
            if hasattr(result, "__await__"):
                result = await result
            return {
                "status": "completed",
                "runtime": "microsoft_agent_framework",
                "worker_id": self.manifest.metadata.id,
                "input": task,
                "output": _normalise_result(result),
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


def _normalise_result(result: Any) -> Any:
    """Keep adapter output JSON-safe and avoid leaking framework objects."""

    if result is None or isinstance(result, (str, int, float, bool)):
        return result
    text = getattr(result, "text", None)
    if isinstance(text, str):
        return text
    if isinstance(result, Mapping):
        return {str(key): _normalise_result(value) for key, value in result.items()}
    model_dump = getattr(result, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(mode="json")
        except TypeError:
            dumped = model_dump()
        if isinstance(dumped, (dict, list, str, int, float, bool)) or dumped is None:
            return _normalise_result(dumped)
    return str(result)
