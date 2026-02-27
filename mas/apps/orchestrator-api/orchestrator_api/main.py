"""orchestrator-api FastAPI app scaffold."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from mas_core.workflow import WatchdogConfig, WorkflowController


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN001
    # Phase 0/4b scaffold: keep deterministic workflow objects in app state.
    app.state.workflow_controller = WorkflowController()
    app.state.watchdog_config = WatchdogConfig()
    yield


app = FastAPI(
    title="AIAT Orchestrator API",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
