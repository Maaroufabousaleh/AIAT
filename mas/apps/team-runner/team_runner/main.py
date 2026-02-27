"""
team-runner — Docker entrypoint per team.

At startup
----------
1. Read TEAM_CONFIG env var → absolute path to a teams/*.yaml file.
2. Parse TeamConfig (Pydantic): team_id, admin agent spec, worker specs.
3. For each agent spec, instantiate the correct AgentBase subclass
   (AdminAgent / WorkerAgent / ExecutiveAgent / CSuiteAgent).
4. After connecting each agent to the message-router WebSocket:
   a. Query Postgres agent_checkpoints for pending tasks.
   b. If checkpoint found: emit DIRECTIVE(action=RESUME) internally so
      the agent's think() loop picks up from the saved iteration.
5. Run all agents as asyncio tasks until SIGTERM / SIGINT.
6. On SIGTERM (or SHUTDOWN broadcast from orchestrator-api):
   a. Drain the current LLM call / tool call to a natural breakpoint.
   b. Save agent_checkpoint rows to Postgres.
   c. Send SHUTDOWN_ACK to orchestrator-api.
   d. Clean exit.

Health endpoint
---------------
GET /health  → { "team_id": "...", "agents": [...], "status": "running" }
Exposed on HEALTH_PORT (default 8080) for Docker healthcheck.
"""

from __future__ import annotations

import asyncio
import os
import signal

import structlog
import yaml

log = structlog.get_logger(__name__)


async def main() -> None:
    config_path = os.environ["TEAM_CONFIG"]
    with open(config_path) as f:
        raw = yaml.safe_load(f)

    log.info("team_runner.starting", team_id=raw.get("team_id"), config=config_path)

    # TODO (Phase 9): parse TeamConfig, instantiate agents, run checkpoint-aware resume

    stop_event = asyncio.Event()

    def _handle_signal() -> None:
        log.info("team_runner.shutdown_signal_received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    await stop_event.wait()
    log.info("team_runner.stopped", team_id=raw.get("team_id"))


if __name__ == "__main__":
    asyncio.run(main())
