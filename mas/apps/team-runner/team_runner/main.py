"""team-runner entrypoint.

Loads one team YAML, instantiates the configured agents, subscribes once to the
team stream, routes messages to the correct local agent, exposes a small health
endpoint, and restores any saved checkpoints on startup.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from itertools import cycle
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
import structlog
import uvicorn
import yaml
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

from mas_core.agent_runtime import (
    AdminAgent,
    AgentBase,
    AgentConfig,
    BudgetExhausted,
    BudgetTracker,
    CSuiteAgent,
    ExecutiveAgent,
    RouterClient,
    SubAgent,
    WorkerAgent,
)
from mas_core.agent_runtime.tool_catalog import tool_definitions_for_agent
from mas_core.observability import configure_logging
from mas_core.protocols import AgentRole, MessageEnvelope, MessageType, TaskBudget
from mas_core.protocols.ws import WSMessageFrame
from mas_tools_sdk.client import ToolServiceClient
from mas_tools_sdk.manifest import resolve_tool_name

if TYPE_CHECKING:
    from collections.abc import Sequence

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
    worker_manifest_ref: str | None = None
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
    orchestrator_url: str = "http://orchestrator-api:8000"
    mas_api_key: str | None = None
    health_host: str = "0.0.0.0"
    health_port: int = 8080
    llm_model: str = "auto"
    tool_manifest_startup_attempts: int = 30
    tool_manifest_retry_seconds: float = 1.0

    @classmethod
    def from_env(cls) -> RunnerSettings:
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
            orchestrator_url=os.environ.get("ORCHESTRATOR_URL", "http://orchestrator-api:8000"),
            mas_api_key=os.environ.get("MAS_API_KEY"),
            health_host=os.environ.get("HEALTH_HOST", "0.0.0.0"),
            health_port=int(os.environ.get("HEALTH_PORT", "8080")),
            llm_model=os.environ.get("LLM_DEFAULT_MODEL", "auto"),
            tool_manifest_startup_attempts=int(
                os.environ.get("TOOL_MANIFEST_STARTUP_ATTEMPTS", "30")
            ),
            tool_manifest_retry_seconds=float(
                os.environ.get("TOOL_MANIFEST_RETRY_SECONDS", "1")
            ),
        )


class CheckpointAdapter:
    """Adapter matching the checkpoint API expected by AgentBase."""

    def __init__(
        self,
        store: CheckpointStore,
        agent_id: str,
        team_id: str,
        usage_storage: AgentStorage | None = None,
    ) -> None:
        self._store = store
        self._agent_id = agent_id
        self._team_id = team_id
        self._usage_storage = usage_storage

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

    async def record_project_usage(self, **kwargs: Any) -> dict[str, Any] | None:
        """Expose the shared usage ledger through AgentBase's storage adapter."""
        if self._usage_storage is None:
            return None
        return await self._usage_storage.record_project_usage(**kwargs)


class TeamRuntime:
    """Owns one team's agents, subscription loop, and health state."""

    def __init__(self, settings: RunnerSettings, team_config: TeamConfig) -> None:
        self.settings = settings
        self.team_config = team_config
        self.tool_client: ToolServiceClient | None = None
        self._runtime_tool_manifest: list[dict[str, Any]] | None = None
        self.storage: AgentStorage | None = None
        self.checkpoint_store: CheckpointStore | None = None
        self.router = RouterClient(
            router_url=settings.router_url,
            agent_id=f"team_runner_{team_config.team_id}",
            agent_secret=settings.router_secret,
        )
        self.admin_agent: AgentBase | None = None
        self.worker_agents: list[AgentBase] = []
        self.agents_by_id: dict[str, AgentBase] = {}
        self._worker_cycle: cycle[AgentBase] | None = None
        self._stop_event = asyncio.Event()
        self._health_status = "starting"
        self._resume_tasks: list[asyncio.Task[None]] = []
        self._in_flight_frame: WSMessageFrame | None = None

    async def start(self) -> None:
        self._verify_gvisor_available()

        if self.settings.tool_service_url:
            self.tool_client = ToolServiceClient(
                self.settings.tool_service_url,
                secret=self.settings.tool_secret,
            )
            await self._load_runtime_tool_manifest()

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

        # G6: Save final checkpoint for any agent with active state
        for agent in self.agents_by_id.values():
            try:
                envelope = getattr(agent, "_current_envelope", None)
                if envelope is not None:
                    restored = getattr(agent, "_checkpoint", {}) or {}
                    budget_snap = None
                    budget = getattr(agent, "_budget", None)
                    if budget is not None and hasattr(budget, "snapshot"):
                        budget_snap = budget.snapshot()
                    await agent.save_checkpoint(
                        {
                            "messages": restored.get("messages", []),
                            "iteration": restored.get("iteration", 0),
                            "tool_results": restored.get("tool_results", []),
                            "budget_snapshot": budget_snap,
                            "task_envelope_id": str(envelope.message_id),
                            "reason": "graceful_shutdown",
                        }
                    )
            except Exception:
                log.warning(
                    "team_runner.stop_checkpoint_failed",
                    agent_id=agent.agent_id,
                    exc_info=True,
                )

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
            "tool_manifest_loaded": self._runtime_tool_manifest is not None,
            "runtime_tool_count": len(self._runtime_tool_manifest or []),
            "runtime_available_tool_count": sum(
                entry.get("available") is not False
                for entry in (self._runtime_tool_manifest or [])
            ),
            "agent_tool_counts": {
                agent_id: len(agent.available_tool_definitions())
                for agent_id, agent in sorted(self.agents_by_id.items())
            },
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
                usage_storage=self.storage,
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
            tool_names=spec.tools,
            tool_definitions=[
                tool.model_dump(mode="json")
                for tool in tool_definitions_for_agent(
                    role=spec.role,
                    team_id=self.team_config.team_id,
                    configured_tools=spec.tools,
                    runtime_tools=self._runtime_tool_manifest,
                )
            ],
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
            # Feasibility review is a four-way C-suite fan-out. Keep the
            # reviewer set explicit at the execution boundary so a default
            # ExecutiveAgent cannot silently run with an empty fan-out list.
            if self.team_config.team_id == "exec_coo":
                kwargs["reviewer_teams"] = [
                    "office_cfo",
                    "office_cio",
                    "office_chrm",
                    "office_cso",
                ]
                # Persist review sessions/comments in the shared authority DB;
                # the normal ``storage`` kwarg remains the checkpoint adapter.
                kwargs["review_storage"] = self.storage
            return ExecutiveAgent(config, **kwargs)
        if class_name == "CSuiteAgent":
            return CSuiteAgent(
                config,
                specialization=self._derive_specialization(spec),
                **kwargs,
            )
        raise ValueError(f"Unsupported agent class {class_name!r}")

    async def _load_runtime_tool_manifest(self) -> None:
        if self.tool_client is None:
            return
        attempts = max(1, self.settings.tool_manifest_startup_attempts)
        retry_seconds = max(0.0, self.settings.tool_manifest_retry_seconds)
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                manifest = await self.tool_client.list_tools()
                if not manifest:
                    raise RuntimeError("tool-service returned an empty runtime manifest")
                self._runtime_tool_manifest = manifest
                log.info(
                    "team_runner.loaded_runtime_tool_manifest",
                    tool_count=len(manifest),
                    available_tool_count=sum(
                        entry.get("available") is not False for entry in manifest
                    ),
                    attempt=attempt,
                )
                return
            except Exception as exc:
                last_error = exc
                self._runtime_tool_manifest = None
                if attempt < attempts:
                    log.warning(
                        "team_runner.runtime_tool_manifest_retry",
                        attempt=attempt,
                        attempts=attempts,
                        retry_seconds=retry_seconds,
                        error=str(exc),
                    )
                    await asyncio.sleep(retry_seconds)

        raise RuntimeError(
            f"tool-service runtime manifest unavailable after {attempts} attempt(s)"
        ) from last_error

    def _load_prompt_text(self, prompt_file: str | None) -> str | None:
        if not prompt_file:
            return None
        path = Path(prompt_file)
        if not path.is_absolute():
            repo_root = self.settings.team_config_path.parent.parent
            path = repo_root / path
        if not path.is_file():
            return None
        return self._prepend_time_block(path.read_text(encoding="utf-8"))

    @staticmethod
    def _prepend_time_block(prompt_body: str) -> str:
        """Stamp a 'current time' header on every loaded prompt so all
        agents in a team share a common time reference.

        Mirrors ``mas/apps/mas-dashboard/lib/datetime.ts`` (and the
        ``TZ=America/New_York`` env in the runtime Dockerfiles). The
        zone auto-switches between EDT (summer) and EST (winter) with
        daylight saving — currently EDT in June. Agents can refresh
        mid-conversation by calling the ``time.now`` tool.
        """
        from datetime import datetime
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/New_York")
        now = datetime.now(tz)
        offset = now.strftime("%z")  # e.g. "-0400"
        offset_str = f"UTC{offset[:3]}:{offset[3:]}"
        header = (
            f"## Current Time (America/New_York)\n"
            f"**{now.strftime('%Y-%m-%d %H:%M:%S %Z')}** "
            f"({offset_str})\n\n"
            f"_Session-start timestamp. All agents in this team share "
            f"this time reference; coordinate using EDT/EST. Call the "
            f"`time.now` tool if you need a fresh reading mid-task._\n\n"
        )
        return header + prompt_body

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
                    worker.model_copy(update={"agent_id": f"{worker.agent_id}_{index + 1}"})
                )
        return expanded

    async def _handle_frame(self, frame: WSMessageFrame) -> None:
        # G5/G6: intercept SHUTDOWN at team-runner level for coordinated shutdown
        if frame.envelope.msg_type == MessageType.SHUTDOWN:
            await self._handle_shutdown_message(frame)
            return

        agent = self._choose_agent(frame.envelope)
        if agent is None:
            raise RuntimeError(
                f"No agent available for {frame.envelope.msg_type} in team {self.team_config.team_id}"
            )
        self._in_flight_frame = frame
        try:
            await agent._dispatch(frame)
        finally:
            self._in_flight_frame = None

    async def _handle_shutdown_message(self, frame: WSMessageFrame) -> None:
        """G5+G6: Handle SHUTDOWN — save checkpoints, stop agents, send HTTP ACK."""
        log.info(
            "team_runner.shutdown_received",
            team_id=self.team_config.team_id,
        )

        # NACK any in-flight message so it can be resumed after restart
        in_flight = self._in_flight_frame
        if in_flight is not None:
            log.info(
                "team_runner.nacking_in_flight",
                entry_id=in_flight.entry_id,
                team_id=self.team_config.team_id,
            )
            self._in_flight_frame = None

        # G6: Save a final checkpoint for each agent that has active state
        checkpoint_errors: list[str] = []
        for agent in self.agents_by_id.values():
            try:
                envelope = getattr(agent, "_current_envelope", None)
                if envelope is not None:
                    restored = getattr(agent, "_checkpoint", {}) or {}
                    budget_snap = None
                    budget = getattr(agent, "_budget", None)
                    if budget is not None and hasattr(budget, "snapshot"):
                        budget_snap = budget.snapshot()
                    await agent.save_checkpoint(
                        {
                            "messages": restored.get("messages", []),
                            "iteration": restored.get("iteration", 0),
                            "tool_results": restored.get("tool_results", []),
                            "budget_snapshot": budget_snap,
                            "task_envelope_id": str(envelope.message_id),
                            "reason": "shutdown_checkpoint",
                        }
                    )
            except Exception:
                checkpoint_errors.append(agent.agent_id)
                log.warning(
                    "team_runner.checkpoint_save_failed",
                    agent_id=agent.agent_id,
                    exc_info=True,
                )

        # Forward SHUTDOWN to admin agent for cascade to workers
        if self.admin_agent is not None:
            try:
                await self.admin_agent._dispatch(frame)
            except Exception:
                log.warning("team_runner.admin_shutdown_dispatch_failed", exc_info=True)

        # G5: HTTP-call /system/shutdown-ack or /system/shutdown-nack on orchestrator
        orchestrator_url = self.settings.orchestrator_url
        admin_id = self.admin_agent.agent_id if self.admin_agent else "unknown"
        api_key = self.settings.mas_api_key
        if not api_key:
            log.error("team_runner.shutdown_ack_not_sent", reason="MAS_API_KEY is not configured")
            self._stop_event.set()
            return
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                if checkpoint_errors:
                    resp = await client.post(
                        f"{orchestrator_url}/system/shutdown-nack",
                        json={
                            "team_id": self.team_config.team_id,
                            "agent_id": admin_id,
                            "reason": f"checkpoint_save_failed for agents: {checkpoint_errors}",
                        },
                        headers={"X-API-Key": api_key},
                    )
                    log.warning(
                        "team_runner.shutdown_nack_sent",
                        team_id=self.team_config.team_id,
                        status=resp.status_code,
                        failed_agents=checkpoint_errors,
                    )
                else:
                    resp = await client.post(
                        f"{orchestrator_url}/system/shutdown-ack",
                        json={
                            "team_id": self.team_config.team_id,
                            "agent_id": admin_id,
                        },
                        headers={"X-API-Key": api_key},
                    )
                    log.info(
                        "team_runner.shutdown_ack_sent",
                        team_id=self.team_config.team_id,
                        status=resp.status_code,
                    )
        except Exception:
            log.warning("team_runner.shutdown_ack_failed", exc_info=True)

        # Signal the stop event to terminate the subscription loop
        self._stop_event.set()

    def _verify_gvisor_available(self) -> None:
        """Verify gVisor runsc is available if any worker requires gvisor sandbox.

        Epsilon: workers with sandbox.profile == "gvisor" need runsc in PATH.
        Missing runsc is a hard error — the container cannot safely run the worker.
        """
        workers = self.team_config.workers
        if not workers:
            return
        gvisor_workers = [
            spec for spec in workers
            if getattr(spec, "sandbox_profile", None) == "gvisor"
            or getattr(spec, "sandbox", {}).get("profile") == "gvisor"
        ]
        if not gvisor_workers:
            return
        import shutil
        if shutil.which("runsc") is None:
            raise RuntimeError(
                "gVisor required for workers %s but runsc not found in PATH. "
                "Install gVisor or assign those workers a non-gvisor sandbox profile. "
                "See: https://gvisor.dev/docs/install/"
                % [w.agent_id for w in gvisor_workers]
            )

    def _choose_agent(self, envelope: MessageEnvelope) -> AgentBase | None:
        if envelope.recipient_id and envelope.recipient_id in self.agents_by_id:
            return self.agents_by_id[envelope.recipient_id]

        if self.admin_agent is None:
            return None

        if not self.worker_agents:
            return self.admin_agent

        same_team_sender = envelope.sender_team == self.team_config.team_id
        if (
            same_team_sender
            and envelope.sender_id == self.admin_agent.agent_id
            and envelope.msg_type in {MessageType.ADMIN_TASK, MessageType.ISSUE_ASSIGN}
        ):
            return next(self._worker_cycle) if self._worker_cycle else self.admin_agent

        if envelope.msg_type in {
            MessageType.ADMIN_REPLY,
            MessageType.RESULT,
            MessageType.ISSUE_COMPLETE,
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

        if same_team_sender and envelope.msg_type in {
            MessageType.ADMIN_TASK,
            MessageType.ISSUE_ASSIGN,
        }:
            return next(self._worker_cycle) if self._worker_cycle else self.admin_agent

        if envelope.msg_type == MessageType.TASK:
            payload = envelope.payload or {}
            if isinstance(payload, dict) and payload.get("action") == "CHAT":
                return self.admin_agent

        if envelope.msg_type in {MessageType.TASK, MessageType.QUERY}:
            return self.admin_agent

        return self.admin_agent

    async def _schedule_checkpoint_resumes(self) -> None:
        if self.checkpoint_store is None:
            return
        checkpoints = await self.checkpoint_store.load_latest_for_team_agents(
            self.team_config.team_id
        )
        scheduled_agents: set[str] = set()
        for checkpoint in checkpoints:
            agent = self.agents_by_id.get(checkpoint["agent_id"])
            if (
                agent is None
                or agent.agent_id in scheduled_agents
                or not self._checkpoint_can_resume(checkpoint)
            ):
                continue
            scheduled_agents.add(agent.agent_id)
            task = asyncio.create_task(
                self._resume_agent(agent, checkpoint),
                name=f"resume:{checkpoint['agent_id']}:{checkpoint['task_message_id']}",
            )
            self._resume_tasks.append(task)

    @staticmethod
    def _checkpoint_can_resume(checkpoint: dict[str, Any]) -> bool:
        snapshot = checkpoint.get("budget_state_json")
        if not isinstance(snapshot, dict):
            return True
        try:
            BudgetTracker.restore_snapshot(snapshot).check_before_llm_call()
        except (BudgetExhausted, TypeError, ValueError):
            log.warning(
                "team_runner.checkpoint_not_resumable",
                agent_id=checkpoint.get("agent_id"),
                task_message_id=checkpoint.get("task_message_id"),
                reason="budget_or_deadline_exhausted",
            )
            return False
        return True

    async def _resume_agent(self, agent: AgentBase, checkpoint: dict[str, Any]) -> None:
        task_json = checkpoint.get("task_envelope_json") or {}
        task_message_id = str(checkpoint["task_message_id"])
        try:
            resume_message_id = UUID(task_message_id)
        except ValueError:
            resume_message_id = uuid5(NAMESPACE_URL, f"aiat-checkpoint:{task_message_id}")
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
            message_id=resume_message_id,
            msg_type=MessageType.DIRECTIVE,
            sender_id="team-runner",
            sender_role=AgentRole.ADMIN,
            sender_team=self.team_config.team_id,
            recipient_id=agent.agent_id,
            project_id=str(checkpoint.get("project_id") or task_json.get("project_id") or "resume"),
            payload={
                "action": "RESUME",
                "task_message_id": task_message_id,
            },
        )
        frame = WSMessageFrame(
            entry_id=f"resume-{task_message_id}",
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
    configure_logging("team-runner", json=os.environ.get("LOG_FORMAT") != "console")

    settings = RunnerSettings.from_env()
    team_config = load_team_config(settings.team_config_path)
    runtime = TeamRuntime(settings, team_config)
    health_app = build_health_app(runtime)
    stop_event = asyncio.Event()

    log.info(
        "team_runner.starting", team_id=team_config.team_id, config=str(settings.team_config_path)
    )

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
