"""LangGraph adapter — wraps a LangGraph state graph as an AIAT worker."""

from __future__ import annotations

import logging
from typing import Any

from mas_core.protocols.worker_manifest import WorkerManifest

logger = logging.getLogger(__name__)


class LangGraphCapabilities:
    """Capabilities schema for LangGraph runtime configuration."""

    def __init__(
        self,
        state_schema: dict[str, Any] | None = None,
        checkpointer: str = "memory",
        threads_per_worker: int = 10,
        interrupt_before: list[str] | None = None,
        interrupt_after: list[str] | None = None,
        graph_definition: dict[str, Any] | None = None,
    ):
        self.state_schema = state_schema or {}
        self.checkpointer = checkpointer
        self.threads_per_worker = threads_per_worker
        self.interrupt_before = interrupt_before or []
        self.interrupt_after = interrupt_after or []
        self.graph_definition = graph_definition or {}

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "LangGraphCapabilities":
        return cls(
            state_schema=config.get("state_schema"),
            checkpointer=config.get("checkpointer", "memory"),
            threads_per_worker=config.get("threads_per_worker", 10),
            interrupt_before=config.get("interrupt_before", []),
            interrupt_after=config.get("interrupt_after", []),
            graph_definition=config.get("graph_definition", {}),
        )


class LangGraphAdapter:
    """Wraps a LangGraph graph as an AIAT worker.

    This adapter translates between AIAT's MessageEnvelope protocol and
    LangGraph's state-machine interface, preserving AIAT's control-plane
    authority over the execution.
    """

    def __init__(
        self,
        manifest: WorkerManifest,
        capabilities: LangGraphCapabilities | None = None,
    ) -> None:
        self.manifest = manifest
        self.capabilities = capabilities or LangGraphCapabilities.from_config(
            manifest.runtime_config
        )
        self._graph = None
        self._initialized = False

    async def initialize(self) -> None:
        """Load the LangGraph state graph from manifest.runtime_config."""
        if self._initialized:
            return

        try:
            graph_def = self.capabilities.graph_definition
            if not graph_def:
                logger.warning(
                    "LangGraphAdapter %s: no graph_definition in runtime_config; "
                    "running in passthrough mode",
                    self.manifest.metadata.id,
                )
                self._initialized = True
                return

            # Dynamically import langgraph when available
            import importlib
            importlib.import_module("langgraph")

            # Support both StateGraph and other graph types
            graph_type = graph_def.get("type", "StateGraph")
            state_schema = graph_def.get("state_schema", {"messages": list})

            if graph_type == "StateGraph":
                from langgraph.graph import StateGraph
                from langgraph.checkpoint.memory import MemorySaver

                builder = StateGraph(state_schema)
                for node_name, node_config in graph_def.get("nodes", {}).items():
                    handler = node_config.get("handler", node_name)
                    builder.add_node(node_name, handler)
                for from_node, to_node in graph_def.get("edges", []):
                    builder.add_edge(from_node, to_node)
                if "entry" in graph_def:
                    builder.set_entry_point(graph_def["entry"])
                if "finish" in graph_def:
                    builder.set_finish_point(graph_def["finish"])

                checkpointer = None
                cp = self.capabilities.checkpointer
                if cp == "memory":
                    checkpointer = MemorySaver()
                elif cp == "postgres":
                    # Postgres checkpointer requires storage config — skip for now
                    checkpointer = None

                self._graph = builder.compile(checkpointer=checkpointer)

            self._initialized = True
            logger.info(
                "LangGraphAdapter %s initialized with checkpointer=%s",
                self.manifest.metadata.id,
                self.capabilities.checkpointer,
            )
        except ImportError:
            logger.warning(
                "LangGraphAdapter %s: langgraph package not installed; "
                "running in stub mode",
                self.manifest.metadata.id,
            )
            self._initialized = True

    async def send_task(self, envelope: Any) -> dict[str, Any]:
        """Send a task through the LangGraph graph.

        Translates AIAT MessageEnvelope → LangGraph input, runs the graph,
        and returns the result as an AIAT-compatible dict.
        """
        if not self._initialized:
            await self.initialize()

        task_input = self._translate_input(envelope)

        if self._graph is None:
            # Passthrough/stub mode when langgraph is not available
            return {
                "status": "stub",
                "input": task_input,
                "output": None,
                "runtime": "langgraph",
                "worker_id": self.manifest.metadata.id,
            }

        try:
            result = await self._graph.ainvoke(task_input)
            return {
                "status": "completed",
                "input": task_input,
                "output": result,
                "runtime": "langgraph",
                "worker_id": self.manifest.metadata.id,
            }
        except Exception as exc:
            logger.error("LangGraph execution failed for %s: %s", self.manifest.metadata.id, exc)
            return {
                "status": "error",
                "input": task_input,
                "error": str(exc),
                "runtime": "langgraph",
                "worker_id": self.manifest.metadata.id,
            }

    async def health_check(self) -> bool:
        """Return True if the adapter is initialized and ready."""
        return self._initialized

    async def shutdown(self) -> None:
        """Clean up resources."""
        self._graph = None
        self._initialized = False
        logger.info("LangGraphAdapter %s shut down", self.manifest.metadata.id)

    def _translate_input(self, envelope: Any) -> dict[str, Any]:
        """Convert AIAT MessageEnvelope to LangGraph-compatible input."""
        payload = getattr(envelope, "payload", {}) or {}
        return {
            "task": payload.get("task", ""),
            "context": payload.get("context", ""),
            "project_id": str(getattr(envelope, "project_id", "")) or None,
            "messages": payload.get("messages", []),
        }
