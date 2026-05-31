"""Adapter factory for configuration-driven worker integration.

Creates the appropriate adapter based on a worker's integration mode:
- native: Uses built-in agent classes (WorkerAgent, AdminAgent, CSuiteAgent, etc.)
- wrapper: Wraps external code in a thin adapter translating MAS protocol
- fork: Maintains a managed fork with isolated patches on top of upstream
"""

from __future__ import annotations

import importlib
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mas_core.protocols.worker_manifest import WorkerManifest

if TYPE_CHECKING:
    from mas_core.agent_runtime.base import AgentBase
    from mas_core.agent_runtime.config import AgentConfig
    from mas_core.protocols.envelope import MessageEnvelope

logger = logging.getLogger(__name__)

BUILTIN_CLASSES = {
    "WorkerAgent": "mas_core.agent_runtime.worker.WorkerAgent",
    "AdminAgent": "mas_core.agent_runtime.admin.AdminAgent",
    "CSuiteAgent": "mas_core.agent_runtime.csuite.CSuiteAgent",
    "ExecutiveAgent": "mas_core.agent_runtime.executive.ExecutiveAgent",
    "SubAgent": "mas_core.agent_runtime.sub_agent.SubAgent",
}


def create_adapter(
    manifest: WorkerManifest,
    config: AgentConfig,
    *,
    mirror_path: Path | None = None,
    **kwargs: Any,
) -> Any:
    """Create an agent adapter based on the worker's integration configuration.

    Parameters
    ----------
    manifest:
        Parsed worker manifest.
    config:
        AgentConfig instance for the agent.
    mirror_path:
        Path to the mirrored upstream repository (for wrapper/fork modes).
    **kwargs:
        Additional arguments passed to the agent constructor
        (storage, tool_client, system_prompt, etc.).

    Returns
    -------
    AgentBase or Epsilon runtime adapter
        Instantiated agent ready for use.
    """
    mode = manifest.integration.isolation_mode
    entrypoint = manifest.integration.adapter_entrypoint

    if mode == "native":
        return _create_native_adapter(entrypoint, config, **kwargs)
    elif mode == "wrapper":
        return _create_wrapper_adapter(manifest, config, mirror_path, **kwargs)
    elif mode == "fork":
        return _create_fork_adapter(manifest, config, mirror_path, **kwargs)
    elif mode == "langgraph":
        return _create_langgraph_adapter(manifest, config, **kwargs)
    elif mode == "crewai":
        return _create_crewai_adapter(manifest, config, **kwargs)
    elif mode == "autogen":
        return _create_autogen_adapter(manifest, config, **kwargs)
    elif mode == "letta":
        return _create_letta_adapter(manifest, config, **kwargs)
    else:
        raise ValueError(f"Unknown isolation mode: {mode}")


def _create_native_adapter(
    entrypoint: str,
    config: AgentConfig,
    **kwargs: Any,
) -> AgentBase:
    """Instantiate a built-in agent class."""
    class_path = BUILTIN_CLASSES.get(entrypoint)
    if class_path is None:
        raise ValueError(f"Unknown built-in agent class: {entrypoint}")

    module_path, class_name = class_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)

    if class_name == "CSuiteAgent":
        specialization = _derive_specialization(config.agent_id)
        return cls(config, specialization=specialization, **kwargs)

    return cls(config, **kwargs)


def _create_wrapper_adapter(
    manifest: WorkerManifest,
    config: AgentConfig,
    mirror_path: Path | None,
    **kwargs: Any,
) -> Any:
    """Create a wrapper adapter around external code."""
    if mirror_path is None:
        raise ValueError("mirror_path is required for wrapper mode")

    adapter_module = manifest.integration.adapter_module
    if adapter_module:
        external_cls = _load_external_class(adapter_module, mirror_path)
    else:
        entrypoint = manifest.integration.adapter_entrypoint
        external_cls = _load_external_class(entrypoint, mirror_path)

    return ExternalWorkerAdapter(
        manifest=manifest,
        config=config,
        external_class=external_cls,
        mirror_path=mirror_path,
        **kwargs,
    )


def _create_fork_adapter(
    manifest: WorkerManifest,
    config: AgentConfig,
    mirror_path: Path | None,
    **kwargs: Any,
) -> Any:
    """Create a fork adapter with isolated patches."""
    if mirror_path is None:
        raise ValueError("mirror_path is required for fork mode")

    adapter_module = manifest.integration.adapter_module
    if adapter_module:
        external_cls = _load_external_class(adapter_module, mirror_path)
    else:
        entrypoint = manifest.integration.adapter_entrypoint
        external_cls = _load_external_class(entrypoint, mirror_path)

    return ForkedWorkerAdapter(
        manifest=manifest,
        config=config,
        external_class=external_cls,
        mirror_path=mirror_path,
        patch_config=manifest.integration.wrapper_config,
        **kwargs,
    )


def _load_external_class(module_path: str, mirror_path: Path) -> type:
    """Dynamically import a class from an external module in the mirror.

    Uses importlib.util.spec_from_file_location to avoid polluting sys.path.
    Supports dotted module paths like 'foo.bar.baz' → foo/bar/baz.py or
    foo/bar/baz/__init__.py.
    """
    import importlib.util

    parts = module_path.rsplit(".", 1)
    if len(parts) == 2:
        module_name, class_name = parts
    else:
        module_name = parts[0]
        class_name = parts[0]

    path_candidates = [
        mirror_path / f"{module_name.replace('.', '/')}.py",
        mirror_path / f"{module_name.replace('.', '/')}" / "__init__.py",
        mirror_path / f"{module_name}.py",
    ]

    file_path = None
    for candidate in path_candidates:
        if candidate.exists():
            file_path = candidate
            break

    if file_path is None:
        raise FileNotFoundError(
            f"Cannot find module file for '{module_path}' in {mirror_path}. "
            f"Tried: {', '.join(str(c) for c in path_candidates)}"
        )

    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec for {module_name} from {file_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, class_name)


def _derive_specialization(agent_id: str) -> str:
    mapping = {
        "ceo": "CEO",
        "coo": "COO",
        "cfo": "CFO",
        "cio": "CIO",
        "chrm": "CHRM",
        "cso": "CSO",
        "cto": "CTO",
    }
    aid = agent_id.lower()
    for key, value in mapping.items():
        if key in aid:
            return value
    return "GENERIC"


class ExternalWorkerAdapter(ABC):
    """Wraps an external worker class, translating between MAS protocol and
    the external code's native interface.

    The original upstream code remains untouched; all adaptation logic lives
    in this thin wrapper.
    """

    def __init__(
        self,
        *,
        manifest: WorkerManifest,
        config: AgentConfig,
        external_class: type,
        mirror_path: Path,
        **kwargs: Any,
    ) -> None:
        self.manifest = manifest
        self.config = config
        self.mirror_path = mirror_path
        self._external = external_class(
            **manifest.integration.wrapper_config,
            **kwargs,
        )
        self._router = kwargs.get("router")

    async def publish(self, envelope: Any) -> str:
        """Publish a message envelope back to the MAS router.

        Subclasses must implement this to route envelopes to the appropriate
        transport (e.g., message-router service, direct WS, etc.).
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement publish() to send envelopes"
        )

    async def handle_message(self, envelope: Any) -> None:
        """Handle an incoming message envelope, execute the external worker, and reply."""
        from mas_core.protocols.enums import MessageType

        task_desc = envelope.payload.get("task", "")

        external_input = self._translate_input(envelope)
        result = await self._external.execute(external_input)
        reply_content = self._translate_output(result)

        reply_type = (
            MessageType.ISSUE_COMPLETE
            if envelope.msg_type == MessageType.ISSUE_ASSIGN
            else MessageType.ADMIN_REPLY
        )

        reply = envelope.reply(
            msg_type=reply_type,
            sender_id=self.config.agent_id,
            sender_role=self.config.agent_role,
            sender_team=self.config.team_id,
            payload={"result": reply_content, "task": task_desc},
        )

        await self.publish(reply)

    def _translate_input(self, envelope: Any) -> dict[str, Any]:
        return {
            "task": envelope.payload.get("task", ""),
            "context": envelope.payload.get("context", ""),
            "project_id": str(envelope.project_id) if envelope.project_id else None,
            "correlation_id": str(envelope.correlation_id) if envelope.correlation_id else None,
        }

    def _translate_output(self, result: Any) -> str:
        if isinstance(result, str):
            return result
        import json

        return json.dumps(result, default=str)


class ForkedWorkerAdapter(ExternalWorkerAdapter):
    """Extends the wrapper adapter with patch application on top of the
    upstream fork. Patches are stored separately and applied at load time
    so the original source remains as untouched as possible.
    """

    def __init__(
        self,
        *,
        patch_config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._patch_config = patch_config or {}
        self._patches_applied: list[str] = []

    async def apply_patches(self) -> list[str]:
        """Apply isolated patches from the patch configuration."""
        applied = []
        for patch_name, patch_data in self._patch_config.items():
            try:
                await self._apply_single_patch(patch_name, patch_data)
                applied.append(patch_name)
            except Exception as exc:
                logger.error("Failed to apply patch %s: %s", patch_name, exc)
        self._patches_applied = applied
        return applied

    async def _apply_single_patch(self, name: str, data: Any) -> None:
        if isinstance(data, dict) and "file" in data and "patch" in data:
            target = self.mirror_path / data["file"]
            if target.exists():
                content = target.read_text(encoding="utf-8")
                content = content.replace(
                    data["patch"].get("search", ""), data["patch"].get("replace", "")
                )
                target.write_text(content, encoding="utf-8")
                logger.info("Applied patch %s to %s", name, data["file"])


def _create_langgraph_adapter(
    manifest: WorkerManifest,
    config: AgentConfig,
    **kwargs: Any,
) -> Any:
    """Create a LangGraph-backed worker adapter (Epsilon)."""
    from mas_core.worker_registry.langgraph_adapter import (
        LangGraphAdapter,
        LangGraphCapabilities,
    )
    capabilities = LangGraphCapabilities.from_config(manifest.runtime_config or {})
    return LangGraphAdapter(manifest=manifest, capabilities=capabilities)


def _create_crewai_adapter(
    manifest: WorkerManifest,
    config: AgentConfig,
    **kwargs: Any,
) -> Any:
    """Create a CrewAI-backed worker adapter (Epsilon)."""
    from mas_core.worker_registry.crewai_adapter import (
        CrewAIAdapter,
        CrewAICapabilities,
    )
    capabilities = CrewAICapabilities.from_config(manifest.runtime_config or {})
    return CrewAIAdapter(manifest=manifest, capabilities=capabilities)


def _create_autogen_adapter(
    manifest: WorkerManifest,
    config: AgentConfig,
    **kwargs: Any,
) -> Any:
    """Create an AutoGen guardrailed specialist worker adapter (Epsilon)."""
    from mas_core.worker_registry.autogen_adapter import (
        AutoGenAdapter,
        AutoGenCapabilities,
    )
    capabilities = AutoGenCapabilities.from_config(manifest.runtime_config or {})
    return AutoGenAdapter(manifest=manifest, capabilities=capabilities)


def _create_letta_adapter(
    manifest: WorkerManifest,
    config: AgentConfig,
    **kwargs: Any,
) -> Any:
    """Create a Letta memory-heavy research specialist worker adapter (Epsilon)."""
    from mas_core.worker_registry.letta_adapter import (
        LettaAdapter,
        LettaCapabilities,
    )
    capabilities = LettaCapabilities.from_config(manifest.runtime_config or {})
    return LettaAdapter(manifest=manifest, capabilities=capabilities)
