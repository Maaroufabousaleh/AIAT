"""team-runner entrypoint.

Loads one team YAML, instantiates the configured agents, subscribes once to the
team stream, routes messages to the correct local agent, exposes a small health
endpoint, and restores any saved checkpoints on startup.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from collections.abc import Sequence
from itertools import cycle
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import structlog
import uvicorn
import yaml
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

from mas_core.agent_runtime import (
    AdminAgent,
    AgentBase,
    AgentConfig,
    CSuiteAgent,
    ExecutiveAgent,
    RouterClient,
    SubAgent,
    WorkerAgent,
)
from mas_core.protocols import AgentRole, MessageEnvelope, MessageType, TaskBudget
from mas_core.protocols.ws import WSMessageFrame
from mas_tools_sdk.manifest import resolve_tool_name
from mas_tools_sdk.client import ToolServiceClient

if TYPE_CHECKING:
    from mas_core.memory import AgentStorage, CheckpointStore

log = structlog.get_logger(__name__)


class AgentSpec(BaseModel):
    """One agent entry inside a team YAML file."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    agent_id: str
    role: AgentRole
    class_name: str = Field(..., alias="class")
    display_name: str
    system_prompt_file: str | None = None
    budget_defaults: TaskBudget = Field(default_factory=TaskBudget)
    tools: list[str] = Field(default_factory=list)
    min_instances: int = 1
    max_instances: int = 1


class TeamConfig(BaseModel):
    """Parsed team YAML."""

    model_config = ConfigDict(extra="ignore")

    team_id: str
    admin: AgentSpec
    workers: list[AgentSpec] = Field(default_factory=list)


class RunnerSettings(BaseModel):
    """Environment-backed runtime settings."""

    team_config_path: Path
    router_url: str = "http://message-router:8001"
    router_secret: str = "changeme"
    tool_service_url: str | None = None
    tool_secret: str | None = None
    pgbouncer_dsn: str | None = None
    health_host: str = "0.0.0.0"
    health_port: int = 8080
    llm_model: str = "gpt-4o"

    @classmethod
    def from_env(cls) -> "RunnerSettings":
        team_config = os.environ["TEAM_CONFIG"]
        router_url = os.environ.get("ROUTER_URL", "http://message-router:8001")
        if router_url.startswith("ws://"):
            router_url = "http://" + router_url.removeprefix("ws://")
        elif router_url.startswith("wss://"):
            router_url = "https://" + router_url.removeprefix("wss://")

        return cls(
            team_config_path=Path(team_config),
            router_url=router_url.rstrip("/"),
            router_secret=(
                os.environ.get("AGENT_TOKEN_SECRET")
                or os.environ.get("ROUTER_SECRET")
                or "changeme"
            ),
            tool_service_url=os.environ.get("TOOL_SERVICE_URL"),
            tool_secret=os.environ.get("TOOL_SECRET"),
            pgbouncer_dsn=os.environ.get("PGBOUNCER_DSN"),
            health_host=os.environ.get("HEALTH_HOST", "0.0.0.0"),
            health_port=int(os.environ.get("HEALTH_PORT", "8080")),
            llm_model=os.environ.get("LLM_DEFAULT_MODEL", "gpt-4o"),
        )


class CheckpointAdapter:
    """Adapter matching the checkpoint API expected by AgentBase."""

    def __init__(self, store: CheckpointStore, agent_id: str, team_id: str) -> None:
        self._store = store
        self._agent_id = agent_id
        self._team_id = team_id

    @staticmethod
    def _maybe_uuid(value: str | None) -> UUID | None:
        if not value:
            return None
        try:
            return UUID(str(value))
        except ValueError:
            return None

    async def save_checkpoint(
        self,
        agent_id: str,
        project_id: str,
        data: dict[str, Any],
    ) -> None:
        await self._store.save(
            agent_id=agent_id,
            team_id=self._team_id,
            project_id=self._maybe_uuid(project_id),
            task_message_id=str(data.get("task_envelope_id", "unknown")),
            iteration=int(data.get("iteration", 0)),
            messages_json=list(data.get("messages", [])),
            tool_results_json=list(data.get("tool_results", [])),
            budget_state_json=data.get("budget_snapshot"),
            task_envelope_json={"project_id": project_id, **data},
        )

    async def load_checkpoint(
        self,
        agent_id: str,
        project_id: str,
    ) -> dict[str, Any] | None:
        row = await self._store.load(agent_id)
        if row is None:
            return None
        task_json = row.get("task_envelope_json") or {}
        if project_id and task_json.get("project_id") not in (None, project_id):
            return None
        return {
            "messages": row.get("messages_json", []),
            "iteration": row.get("iteration", 0),
            "tool_results": row.get("tool_results_json") or [],
            "budget_snapshot": row.get("budget_state_json"),
            "task_envelope_id": row.get("task_message_id"),
        }

    async def delete_checkpoint(
        self,
        agent_id: str,
        project_id: str,
    ) -> None:
        row = await self._store.load(agent_id)
        if row is None:
            return
        await self._store.delete(agent_id, row["task_message_id"])


class TeamRuntime:
    """Owns one team's agents, subscription loop, and health state."""

    def __init__(self, settings: RunnerSettings, team_config: TeamConfig) -> None:
        self.settings = settings
        self.team_config = team_config
        self.tool_client: ToolServiceClient | None = None
        self.storage: AgentStorage | None = None
        self.checkpoint_store: CheckpointStore | None = None
        self.router = RouterClient(
            router_url=settings.router_url,
            agent_id=f"team_runner:{team_config.team_id}",
            agent_secret=settings.router_secret,
        )
        self.admin_agent: AgentBase | None = None
        self.worker_agents: list[AgentBase] = []
        self.agents_by_id: dict[str, AgentBase] = {}
        self._worker_cycle: cycle[AgentBase] | None = None
        self._stop_event = asyncio.Event()
        self._health_status = "starting"
        self._resume_tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        if self.settings.tool_service_url:
            self.tool_client = ToolServiceClient(
                self.settings.tool_service_url,
                secret=self.settings.tool_secret,
            )

        if self.settings.pgbouncer_dsn:
            from mas_core.memory import AgentStorage, CheckpointStore

            self.storage = AgentStorage(self.settings.pgbouncer_dsn)
            await self.storage.connect()
            self.checkpoint_store = CheckpointStore(self.storage.engine)

        await self.router.start()
        self._instantiate_agents()
        for agent in self.agents_by_id.values():
            await agent.start()
        await self._schedule_checkpoint_resumes()
        self._health_status = "running"

    async def stop(self) -> None:
        self._stop_event.set()
        self._health_status = "stopping"

        for agent in self.agents_by_id.values():
            await agent.stop()

        for task in self._resume_tasks:
            task.cancel()
        if self._resume_tasks:
            await asyncio.gather(*self._resume_tasks, return_exceptions=True)
            self._resume_tasks.clear()

        await self.router.stop()
        if self.tool_client is not None:
            await self.tool_client.close()
        if self.storage is not None:
            await self.storage.close()
        self._health_status = "stopped"

    async def run(self) -> None:
        await self.router.subscribe(
            team_id=self.team_config.team_id,
            handler=self._handle_frame,
            stop_event=self._stop_event,
        )

    def health_payload(self) -> dict[str, Any]:
        return {
            "team_id": self.team_config.team_id,
            "status": self._health_status,
            "agents": sorted(self.agents_by_id.keys()),
            "worker_count": len(self.worker_agents),
            "has_storage": self.storage is not None,
            "has_tool_client": self.tool_client is not None,
        }

    def _instantiate_agents(self) -> None:
        agent_specs = [self.team_config.admin, *self._expanded_workers(self.team_config.workers)]
        for spec in agent_specs:
            agent = self._build_agent(spec)
            self.agents_by_id[spec.agent_id] = agent
            if spec.agent_id == self.team_config.admin.agent_id:
                self.admin_agent = agent
            else:
                self.worker_agents.append(agent)

        if self.worker_agents:
            self._worker_cycle = cycle(self.worker_agents)

    def _build_agent(self, spec: AgentSpec) -> AgentBase:
        storage_adapter: Any | None = None
        if self.checkpoint_store is not None:
            storage_adapter = CheckpointAdapter(
                self.checkpoint_store,
                agent_id=spec.agent_id,
                team_id=self.team_config.team_id,
            )

        system_prompt = self._load_prompt_text(spec.system_prompt_file)
        config = AgentConfig.model_construct(
            agent_id=spec.agent_id,
            team_id=self.team_config.team_id,
            agent_role=spec.role,
            agent_secret=self.settings.router_secret,
            router_url=self.settings.router_url,
            budget_defaults=spec.budget_defaults,
            llm_model=self.settings.llm_model,
        )

        kwargs: dict[str, Any] = {
            "storage": storage_adapter,
            "tool_client": self.tool_client,
            "system_prompt": system_prompt,
        }
        class_name = spec.class_name
        if class_name == "WorkerAgent":
            return WorkerAgent(config, **kwargs)
        if class_name == "AdminAgent":
            return AdminAgent(config, **kwargs)
        if class_name == "SubAgent":
            return SubAgent(config, **kwargs)
        if class_name == "ExecutiveAgent":
            return ExecutiveAgent(config, **kwargs)
        if class_name == "CSuiteAgent":
            return CSuiteAgent(
                config,
                specialization=self._derive_specialization(spec),
                **kwargs,
            )
        raise ValueError(f"Unsupported agent class {class_name!r}")

    def _load_prompt_text(self, prompt_file: str | None) -> str | None:
        if not prompt_file:
            return None
        path = Path(prompt_file)
        if not path.is_absolute():
            repo_root = self.settings.team_config_path.parent.parent
            path = repo_root / path
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")

    def _derive_specialization(self, spec: AgentSpec) -> str:
        mapping = {
            "ceo": "CEO",
            "coo": "COO",
            "cfo": "CFO",
            "cio": "CIO",
            "chrm": "CHRM",
            "cso": "CSO",
            "cto": "CTO",
        }
        agent_id = spec.agent_id.lower()
        for key, value in mapping.items():
            if key in agent_id:
                return value
        display = spec.display_name.upper()
        for value in mapping.values():
            if value in display:
                return value
        return "GENERIC"

    def _expanded_workers(self, workers: Sequence[AgentSpec]) -> list[AgentSpec]:
        expanded: list[AgentSpec] = []
        for worker in workers:
            count = max(1, worker.min_instances)
            for index in range(count):
                if count == 1:
                    expanded.append(worker)
                    continue
                expanded.append(
                    worker.model_copy(
                        update={"agent_id": f"{worker.agent_id}_{index + 1}"}
                    )
                )
        return expanded

    async def _handle_frame(self, frame: WSMessageFrame) -> None:
        agent = self._choose_agent(frame.envelope)
        if agent is None:
            raise RuntimeError(
                f"No agent available for {frame.envelope.msg_type} in team {self.team_config.team_id}"
            )
        await agent._dispatch(frame)

    def _choose_agent(self, envelope: MessageEnvelope) -> AgentBase | None:
        if envelope.recipient_id and envelope.recipient_id in self.agents_by_id:
            return self.agents_by_id[envelope.recipient_id]

        if self.admin_agent is None:
            return None

        if not self.worker_agents:
            return self.admin_agent

        same_team_sender = envelope.sender_team == self.team_config.team_id
        if same_team_sender and envelope.sender_id == self.admin_agent.agent_id:
            if envelope.msg_type in {MessageType.ADMIN_TASK, MessageType.ISSUE_ASSIGN}:
                return next(self._worker_cycle) if self._worker_cycle else self.admin_agent

        if envelope.msg_type in {
            MessageType.ADMIN_REPLY,
            MessageType.RESULT,
            MessageType.ISSUE_COMPLETE,
            MessageType.SHUTDOWN,
            MessageType.SHUTDOWN_ACK,
            MessageType.DIRECTIVE,
            MessageType.DOCUMENT_SUBMIT,
            MessageType.DOCUMENT_REVISION,
            MessageType.REVIEW_REQUEST,
            MessageType.REVIEW_RESPONSE,
            MessageType.APPROVAL_REQUEST,
            MessageType.APPROVAL_RESPONSE,
            MessageType.SPRINT_PLAN,
            MessageType.SPRINT_REPORT,
            MessageType.INFRA_READY,
            MessageType.SYSTEM_EVENT,
        }:
            return self.admin_agent

        if same_team_sender and envelope.msg_type in {MessageType.ADMIN_TASK, MessageType.ISSUE_ASSIGN}:
            return next(self._worker_cycle) if self._worker_cycle else self.admin_agent

        if envelope.msg_type in {MessageType.TASK, MessageType.QUERY}:
            return self.admin_agent

        return self.admin_agent

    async def _schedule_checkpoint_resumes(self) -> None:
        if self.checkpoint_store is None:
            return
        checkpoints = await self.checkpoint_store.load_all_for_team(self.team_config.team_id)
        for checkpoint in checkpoints:
            agent = self.agents_by_id.get(checkpoint["agent_id"])
            if agent is None:
                continue
            task = asyncio.create_task(
                self._resume_agent(agent, checkpoint),
                name=f"resume:{checkpoint['agent_id']}:{checkpoint['task_message_id']}",
            )
            self._resume_tasks.append(task)

    async def _resume_agent(self, agent: AgentBase, checkpoint: dict[str, Any]) -> None:
        task_json = checkpoint.get("task_envelope_json") or {}
        agent.restore_from_checkpoint(
            {
                "messages": checkpoint.get("messages_json", []),
                "iteration": checkpoint.get("iteration", 0),
                "tool_results": checkpoint.get("tool_results_json") or [],
                "budget_snapshot": checkpoint.get("budget_state_json"),
                "task_envelope_id": checkpoint.get("task_message_id"),
            }
        )
        envelope = MessageEnvelope(
            msg_type=MessageType.DIRECTIVE,
            sender_id="team-runner",
            sender_role=AgentRole.ADMIN,
            sender_team=self.team_config.team_id,
            recipient_id=agent.agent_id,
            project_id=str(checkpoint.get("project_id") or task_json.get("project_id") or "resume"),
            payload={
                "action": "RESUME",
                "task_message_id": checkpoint["task_message_id"],
            },
        )
        frame = WSMessageFrame(
            entry_id=f"resume-{checkpoint['task_message_id']}",
            envelope=envelope,
            stream=f"stream:{self.team_config.team_id}",
            retry_count=0,
        )
        await agent._dispatch(frame)


def load_team_config(path: Path) -> TeamConfig:
    """Parse the configured YAML file into TeamConfig."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    config = TeamConfig.model_validate(raw)
    _normalize_and_validate_team_tools(config)
    return config


def _normalize_and_validate_team_tools(config: TeamConfig) -> None:
    """Validate team tools against canonical + alias-aware manifest names.

    Unknown tools fail fast at startup. Legacy alias names are normalized to
    canonical names for runtime execution.
    """

    def normalize(agent: AgentSpec) -> None:
        normalized: list[str] = []
        seen: set[str] = set()
        for tool in agent.tools:
            canonical = resolve_tool_name(tool)
            if canonical is None:
                raise ValueError(
                    f"Unknown tool '{tool}' in team '{config.team_id}' for agent '{agent.agent_id}'"
                )
            if canonical not in seen:
                normalized.append(canonical)
                seen.add(canonical)
        agent.tools = normalized

    normalize(config.admin)
    for worker in config.workers:
        normalize(worker)


def build_health_app(runtime: TeamRuntime) -> FastAPI:
    """Create the small health server used by Docker."""
    app = FastAPI(title="AIAT Team Runner", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return runtime.health_payload()

    return app


async def _serve_health(app: FastAPI, settings: RunnerSettings, stop_event: asyncio.Event) -> None:
    config = uvicorn.Config(
        app,
        host=settings.health_host,
        port=settings.health_port,
        log_level="warning",
        lifespan="off",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve(), name="team-runner-health")
    try:
        await stop_event.wait()
    finally:
        server.should_exit = True
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _wait_for_signal(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: stop_event.set())
    await stop_event.wait()


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    settings = RunnerSettings.from_env()
    team_config = load_team_config(settings.team_config_path)
    runtime = TeamRuntime(settings, team_config)
    health_app = build_health_app(runtime)
    stop_event = asyncio.Event()

    log.info("team_runner.starting", team_id=team_config.team_id, config=str(settings.team_config_path))

    await runtime.start()
    health_task = asyncio.create_task(
        _serve_health(health_app, settings, stop_event),
        name=f"health:{team_config.team_id}",
    )
    run_task = asyncio.create_task(runtime.run(), name=f"team:{team_config.team_id}")

    try:
        await _wait_for_signal(stop_event)
    finally:
        stop_event.set()
        await runtime.stop()
        run_task.cancel()
        health_task.cancel()
        await asyncio.gather(run_task, health_task, return_exceptions=True)
        log.info("team_runner.stopped", team_id=team_config.team_id)


if __name__ == "__main__":
    asyncio.run(main())
