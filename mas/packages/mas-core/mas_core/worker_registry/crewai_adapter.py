"""CrewAI adapter — wraps a CrewAI crew as an AIAT worker."""

from __future__ import annotations

import logging
from typing import Any

from mas_core.protocols.worker_manifest import WorkerManifest

logger = logging.getLogger(__name__)


class CrewAICapabilities:
    """Capabilities schema for CrewAI runtime configuration."""

    def __init__(
        self,
        crew_config: dict[str, Any] | None = None,
        agents: list[str] | None = None,
        tasks: list[str] | None = None,
        process: str = "sequential",
        memory_enabled: bool = False,
        shared_memory: bool = False,
    ):
        self.crew_config = crew_config or {}
        self.agents = agents or []
        self.tasks = tasks or []
        self.process = process
        self.memory_enabled = memory_enabled
        self.shared_memory = shared_memory

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "CrewAICapabilities":
        return cls(
            crew_config=config.get("crew_config", {}),
            agents=config.get("agents", []),
            tasks=config.get("tasks", []),
            process=config.get("process", "sequential"),
            memory_enabled=config.get("memory_enabled", False),
            shared_memory=config.get("shared_memory", False),
        )


class CrewAIAdapter:
    """Wraps a CrewAI crew as an AIAT worker.

    Translates between AIAT's MessageEnvelope protocol and CrewAI's
    crew/agent/task interface, preserving AIAT's control-plane authority.
    """

    def __init__(
        self,
        manifest: WorkerManifest,
        capabilities: CrewAICapabilities | None = None,
    ) -> None:
        self.manifest = manifest
        self.capabilities = capabilities or CrewAICapabilities.from_config(
            manifest.runtime_config
        )
        self._crew = None
        self._initialized = False

    async def initialize(self) -> None:
        """Instantiate the CrewAI crew from manifest.runtime_config."""
        if self._initialized:
            return

        try:
            import importlib

            # Verify crewai is installed
            importlib.import_module("crewai")
        except ImportError:
            logger.warning(
                "CrewAIAdapter %s: crewai package not installed; running in stub mode",
                self.manifest.metadata.id,
            )
            self._initialized = True
            return

        crew_cfg = self.capabilities.crew_config
        if not crew_cfg:
            logger.warning(
                "CrewAIAdapter %s: no crew_config in runtime_config; running in stub mode",
                self.manifest.metadata.id,
            )
            self._initialized = True
            return

        try:
            # crew_config may contain raw agent/task dicts or import paths
            agents_cfg = crew_cfg.get("agents", [])
            tasks_cfg = crew_cfg.get("tasks", [])

            # Import CrewAI core classes if available
            from crewai import Agent, Task, Crew  # type: ignore

            agents = []
            for agent_cfg in agents_cfg:
                if isinstance(agent_cfg, dict):
                    role = str(agent_cfg.get("role") or "worker")
                    goal = str(agent_cfg.get("goal") or "")
                    backstory = str(agent_cfg.get("backstory") or "")
                    agents.append(Agent(role=role, goal=goal, backstory=backstory))

            # Map agents by index; tasks reference agents by explicit index from config
            agent_count = len(agents)
            tasks = []
            for task_cfg in tasks_cfg:
                if isinstance(task_cfg, dict):
                    desc = str(task_cfg.get("description") or "")
                    expected = str(task_cfg.get("expected_output") or "")
                    agent_idx = task_cfg.get("agent_index")
                    task_agent = agents[agent_idx] if agent_idx is not None and 0 <= agent_idx < agent_count else None
                    tasks.append(Task(description=desc, expected_output=expected, agent=task_agent))

            crew_kwargs = {
                "agents": agents,
                "tasks": tasks,
                "process": self.capabilities.process,
            }
            if self.capabilities.memory_enabled:
                crew_kwargs["memory"] = True

            self._crew = Crew(**crew_kwargs)
            self._initialized = True
            logger.info(
                "CrewAIAdapter %s initialized: %d agents, %d tasks, process=%s",
                self.manifest.metadata.id,
                len(agents),
                len(tasks),
                self.capabilities.process,
            )
        except Exception as exc:
            logger.error("Failed to initialize CrewAI crew for %s: %s", self.manifest.metadata.id, exc)
            self._initialized = True

    async def send_task(self, envelope: Any) -> dict[str, Any]:
        """Execute a task through the CrewAI crew."""
        if not self._initialized:
            await self.initialize()

        task_input = self._translate_input(envelope)

        if self._crew is None:
            return {
                "status": "stub",
                "input": task_input,
                "output": None,
                "runtime": "crewai",
                "worker_id": self.manifest.metadata.id,
            }

        try:
            result = self._crew.kickoff(inputs=task_input)
            return {
                "status": "completed",
                "input": task_input,
                "output": str(result),
                "runtime": "crewai",
                "worker_id": self.manifest.metadata.id,
            }
        except Exception as exc:
            logger.error("CrewAI execution failed for %s: %s", self.manifest.metadata.id, exc)
            return {
                "status": "error",
                "input": task_input,
                "error": str(exc),
                "runtime": "crewai",
                "worker_id": self.manifest.metadata.id,
            }

    async def health_check(self) -> bool:
        return self._initialized

    async def shutdown(self) -> None:
        self._crew = None
        self._initialized = False
        logger.info("CrewAIAdapter %s shut down", self.manifest.metadata.id)

    def _translate_input(self, envelope: Any) -> dict[str, Any]:
        payload = getattr(envelope, "payload", {}) or {}
        return {
            "task": payload.get("task", ""),
            "context": payload.get("context", ""),
        }
