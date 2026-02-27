"""
tool-service — FastAPI application.

Responsibilities
----------------
• Expose a single tool-execution endpoint, gated by AgentRole.
• Enforce (sender_role, tool_name) permission matrix from CommunicationPolicy.
• Per-tool-group token-bucket rate limiting (aiolimiter).
• Per-tool asyncio.Semaphore concurrency cap.
• Result cache: hash(tool_name + sorted(kwargs)) → Redis tool_cache:{hash}, 30 s TTL.
• Per-tool circuit breaker: ≥3 failures in 60 s → OPEN for 120 s → HALF_OPEN probe.

Tool groups (Phase 6, 6 groups)
--------------------------------
GROUP_WEB        web_search, fetch_url
GROUP_FILE       file_read, file_write
GROUP_MEMORY     shared_memory_read, shared_memory_write
GROUP_PROJECT    project.create, project.status, project.transition,
                 document_create, document_get, review_aggregate,
                 approval.override_cso, review.submit_veto,
                 human.notify, human.await_decision, review.start_session,
                 department_task
GROUP_SPRINT_KPI sprint.create, issue.create, issue.decompose,
                 kpi.compute, kpi.query_history, kpi.update_agent_profile,
                 velocity.report, estimation.adjust
GROUP_INFRA      infra.provision, cicd.configure, monitoring.setup,
                 secrets.manage, infra.ready_signal,
                 blob.upload, blob.download

Endpoints (Phase 6)
-------------------
POST /tools/execute     Execute a tool. Body: ToolRequest. Returns ToolResponse.
GET  /tools             List all tools with role requirements.
GET  /health            Service health + circuit-breaker states.
GET  /metrics           Prometheus metrics.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN001
    # TODO (Phase 6): connect to Redis (toolcache_user ACL), initialise circuit breakers
    yield


app = FastAPI(
    title="AIAT Tool Service",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/tools")
async def list_tools() -> dict[str, object]:
    # TODO (Phase 6): return TOOL_MANIFEST from mas_tools_sdk
    return {"tools": []}
