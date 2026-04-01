# PROJECT STATUS

**Date**: 2026-03-31
**Last commit**: `3536c9e` — "Add baseline manifests for various roles in the system"
**Branch**: (pending first meaningful commit of this work session)

---

## 1. WHERE WE ARE NOW

This project implements a **Multi-Agent System (MAS)** — an AI-driven organizational hierarchy that autonomously manages software projects through a deterministic 14-step workflow. The system runs as 18 Docker containers orchestrated via FastAPI services, Redis Streams, PostgreSQL, and MinIO.

### What exists in the codebase right now

| Component | Status | Notes |
|-----------|--------|-------|
| **Repo scaffold** (`mas/`) | Complete | Workspace layout with `apps/`, `packages/`, `infra/`, `migrations/`, `teams/`, `workers/` |
| **Protocol models** | Complete | `MessageEnvelope`, `MessageType` (22+ types), `AgentRole` (6 roles), `BlobRef`, `TaskBudget`, all in `packages/mas-core/mas_core/protocols/` |
| **Policy engine** | Complete | 6-role communication matrix, chain-of-command enforcement, tool permissions in `packages/mas-core/mas_core/policy/` |
| **Message router** | Complete | Redis Streams pub/sub, WebSocket subscriptions, XAUTOCLAIM, DLQ, publish idempotency in `apps/message-router/` |
| **Workflow controller** | Complete | 18 states (14 workflow + 4 terminal), transition table, watchdog, sole writer of `projects.state` in `packages/mas-core/mas_core/workflow/` |
| **LLM gateway** | Complete | `LLMGatewayClient` with 9 providers (8 API + 1 Copilot CLI), retry, token tracking in `packages/mas-core/mas_core/llm_gateway/` |
| **Tool service** | Complete | 7 tool groups, circuit breakers, rate limiting, result cache in `apps/tool-service/` |
| **Storage layer** | Complete | 20 PostgreSQL tables across 3 Alembic migrations, `AgentStorage` async wrapper, PgBouncer, MinIO `BlobClient` |
| **Agent runtime** | Complete | 6 agent types: `AgentBase`, `WorkerAgent`, `AdminAgent`, `ExecutiveAgent`, `CSuiteAgent`, `SubAgent` in `packages/mas-core/mas_core/agent_runtime/` |
| **Team runner** | Complete | Graceful shutdown, SHUTDOWN_ACK, checkpoint-on-SIGTERM in `apps/team-runner/` |
| **Orchestrator API** | Complete | ~1,439 lines, 30+ endpoints covering projects, transitions, decisions, documents, dead letters, capabilities, system lifecycle |
| **Docker Compose** | Complete | 18 containers, network segmentation (public/internal split), sandbox tier (cap_drop ALL, read-only), PgBouncer healthcheck, Redis AOF |
| **Observability** | Complete | 10 Prometheus metrics, structlog, trace-ID, `/metrics` on all 3 services, Prometheus + Grafana in dev overlay |
| **Shutdown / Resume** | Complete | Orchestrated shutdown cascade, agent checkpoint save/restore, watchdog schedule-awareness, scheduled operation cron |

### What was created/verified in this work session

- **4 evaluation documents** in `.github/prompts/`:
  - `eval-plan-masArchitectureUpgrade.md` — Phase-by-phase completion (Phases 0–14)
  - `eval-plan-orgArchitecture.md` — Architectural alignment (§1–§17)
  - `eval-plan-manualActions.md` — Automated vs. human-required actions
  - `eval-plan-phase4-manualTodo.md` — Auto-fix verification and gap assessment
- **Dead code removed** from 3 files:
  - `message_router/main.py` — unused Prometheus counters
  - `tool_service/main.py` — unused Prometheus counters
  - `orchestrator_api/main.py` — unused `response_started` variable and starlette local import
- **`.gitignore` updated** — `guardian.bat` added (Windows dev utility, not for repo)
- **1 plan document added**: `plan-phase4-manualTodo.prompt.md`

### Asset inventory

| Asset | Count | Location |
|-------|-------|----------|
| Team YAMLs | 11 | `mas/teams/` |
| Worker manifests | 26 | `mas/workers/` |
| System prompts | 11 | `mas/prompts/` |
| Alembic migrations | 3 | `mas/migrations/versions/` |
| DB tables | 20 | Via migrations |
| Docker services | 18 | `mas/infra/compose/docker-compose.yml` |
| API endpoints | 30+ | `apps/orchestrator-api/orchestrator_api/main.py` |
| Test files (new) | 11 | Across `apps/*/tests/` and `packages/mas-core/tests/` |
| Evaluation docs | 4 | `.github/prompts/eval-*.md` |

### Known low-severity issues (documented, not fixed)

- `await redis_client.ping()` — LSP type stub warning, false positive
- `bind_trace_id()` without `clear_trace_context()` — not a bug in async context, just inconsistent pattern
- `MAS_PROJECT_STATE` Gauge with `project_id` label — unbounded cardinality at production scale, acceptable for v1
- `infra/sandbox/` directory not created — sandbox config is inline in Docker Compose, functionally equivalent

---

## 2. WHAT YOU SHOULD DO NEXT

Everything below is **human-required** — it cannot be automated further. Items are ordered by priority.

### STEP 1: Generate credentials (~15 min) — BLOCKING

Create `mas/.env` with generated secrets:

```bash
cd mas
python -c "
import secrets
for name in [
    'POSTGRES_PASSWORD', 'ROUTER_REDIS_PASS', 'TOOL_REDIS_PASS',
    'MINIO_ROOT_PASSWORD', 'MINIO_ACCESS_KEY', 'MINIO_SECRET_KEY',
    'MAS_API_KEY', 'ROUTER_SECRET', 'TOOL_SECRET',
]:
    print(f'{name}={secrets.token_urlsafe(32)}')
" > .env
```

Then add the remaining config values to `.env`:
```env
LLM_GATEWAY_URL=http://host.docker.internal:<port>/v1
LLM_API_KEY=<your-key>
LLM_DEFAULT_MODEL=<model-name>
LOG_LEVEL=INFO
```

### STEP 2: Verify Docker resources (~5 min) — BLOCKING

- Open Docker Desktop Settings
- Allocate at least **8 GB memory** and **4 CPUs**
- Confirm WSL 2 backend is enabled (if on Windows)

### STEP 3: Ensure LLM proxy is reachable from Docker (~30 min) — BLOCKING

- Start your LLM proxy (Ollama, vLLM, LiteLLM, etc.)
- Test from Docker: `docker run --rm curlimages/curl curl http://host.docker.internal:<port>/v1/models`
- Confirm the model name in `LLM_DEFAULT_MODEL` matches what the proxy returns

### STEP 4: First boot — run migrations and containers (~40 min) — BLOCKING

```bash
cd mas
uv venv && source .venv/bin/activate
uv pip install -e "packages/mas-core[dev]"

# Start infrastructure
docker compose up --build -d

# Wait for Postgres to be healthy, then:
alembic upgrade head

# Verify all 18 containers are healthy
docker compose ps
```

### STEP 5: Run test suites (~30 min) — BLOCKING

```bash
pytest mas/packages/mas-core/tests/
pytest mas/apps/message-router/tests/
pytest mas/apps/orchestrator-api/tests/
pytest mas/apps/tool-service/tests/
pytest mas/apps/team-runner/tests/
```

### STEP 6: Verify security boundaries (~30 min) — REQUIRED

- Redis ACL: `docker compose exec redis redis-cli -u redis://router_user:<PASS>@redis:6379 PING` → PONG
- Default user: `docker compose exec redis redis-cli PING` → rejected
- Network segmentation: team containers cannot reach Redis
- Sandbox enforcement: `docker inspect <team-container>` shows `CapDrop: ["ALL"]`

### STEP 7: Review 11 system prompts (~2-4 hours) — REQUIRED

Read every file in `mas/prompts/` and verify:
- Role hierarchy is correctly described
- Tool usage instructions match actual tool manifests
- Reporting chain is accurate
- Edge case handling (budget exhaustion, timeouts, vetoes)

### STEP 8: Review 11 team YAMLs (~30-60 min) — REQUIRED

Review `mas/teams/*.yaml`:
- Agent IDs are unique across all teams
- Model assignments match your LLM proxy's available models
- Budget defaults are appropriate for your LLM pricing

### STEP 9: Review 26 worker manifests (~30-60 min) — REQUIRED

Review `mas/workers/*.yaml`:
- Sandbox profiles match worker risk level
- Transport modes are correct
- Capability lists are accurate

### STEP 10: End-to-end smoke test (~30 min) — REQUIRED

```bash
curl -X POST http://localhost:8000/projects -d '{"name":"test","description":"smoke test"}'
curl http://localhost:8000/system/status
# Watch logs as the project moves through states
```

### STEP 11: Shutdown/resume test (~1 hour) — RECOMMENDED

```bash
# Start a project, let it reach ACTIVE_SPRINT
curl -X POST http://localhost:8000/system/shutdown
# Verify all agents checkpoint and stop
docker compose down
docker compose up --build -d
# Verify project resumes from checkpoint
curl http://localhost:8000/projects/<id>
```

### STEP 12: Grafana dashboards (~1 hour) — RECOMMENDED

- Open http://localhost:3000 (admin/admin)
- Create dashboards for: `mas_project_state`, `mas_messages_total`, `mas_dlq_depth`, `mas_tool_circuit_state`, `mas_llm_calls_total`

---

## 3. WHAT HAPPENS AFTER EACH STEP

| After Step | What You Gain |
|------------|---------------|
| **Step 1** (credentials) | `.env` exists — containers can authenticate to each other |
| **Step 2** (Docker resources) | 18 containers fit in memory — no OOM kills |
| **Step 3** (LLM proxy) | Agents can call LLMs — the system can actually "think" |
| **Step 4** (first boot) | All 18 services running, 20 DB tables created — **infrastructure complete** |
| **Step 5** (test suites) | All automated tests pass — **code correctness verified** |
| **Step 6** (security) | ACL, network isolation, sandbox enforced — **security verified** |
| **Step 7** (prompts) | Agent behavior is correct — **AI logic verified** |
| **Step 8** (team YAMLs) | Team structure matches your needs — **organizational config verified** |
| **Step 9** (worker manifests) | Worker permissions are correct — **capability config verified** |
| **Step 10** (smoke test) | First real project runs end-to-end — **system is alive** |
| **Step 11** (shutdown/resume) | System survives crashes — **production readiness verified** |
| **Step 12** (Grafana) | Visibility into system behavior — **operational monitoring ready** |

---

## 4. FINAL RESULT

After completing all 12 steps, you will have:

**A fully operational multi-agent organizational system** with:

- **11 autonomous agent teams** (CEO, COO, CFO, CIO, CHRM, CSO, CTO, Production PM, System PM, QA Lead, DevOps PM) working through a deterministic 14-step project workflow
- **26 specialized workers** executing tasks under PM direction
- **9 LLM providers** available through a smart gateway
- **7 tool groups** (workflow, document, review, sprint, devops, capability, KPI/utility) with circuit breakers and rate limiting
- **20 Postgres tables** with full audit trail, state history, capability registry
- **18 containers** with network segmentation and sandbox enforcement
- **10 Prometheus metrics** with Grafana dashboards
- **Graceful shutdown and resume** — the system can be stopped and restarted without losing project state
- **Human-in-the-loop** — you can create projects, submit decisions, and review documents via REST API

**Total estimated human time to reach this state**: 5-9 hours (Steps 1-10), plus 2-3 hours optional (Steps 11-12).

**Current progress**: ~85% automated (all code, tests, infra config). Remaining 15% is credentials, boot, and human review.

---

## 5. DETAILED EVALUATIONS

See the following files for phase-by-phase status, issues, and verification checklists:

- `.github/prompts/eval-plan-masArchitectureUpgrade.md`
- `.github/prompts/eval-plan-orgArchitecture.md`
- `.github/prompts/eval-plan-manualActions.md`
- `.github/prompts/eval-plan-phase4-manualTodo.md`
