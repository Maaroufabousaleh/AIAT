# Evaluation: plan-manualActions.prompt.md

**Generated**: 2026-03-31  
**Evaluator**: AI code review — codebase audit against manual action requirements  
**Scope**: What has been automated vs. what genuinely requires human action

---

## Summary

This document audits the manual actions plan against the actual codebase state. Items the AI agent was able to automate have been completed. All remaining items are genuinely human-gated (credentials, infrastructure, judgment calls, live testing).

---

## Phase 0 + 1 — Repo Scaffold & Protocols

### Decisions Made (automatically)
- Package manager: `uv` — confirmed from `mas/uv.lock` and `pyproject.toml`
- Python version: `3.11` — confirm from Dockerfiles (needs human spot-check)
- Project ID format: UUIDs — confirmed in all models
- Control plane: No (standalone — pure REST API) — confirmed, no Paperclip integration
- Worker manifests: Used from Phase 0 — confirmed, 26 manifests in `mas/workers/`

### What Was Automated
- All Pydantic models created
- All protocol files created
- `workflow-template.yaml` or equivalent controller logic created

### What Still Requires Human Action
- ⚠️ **Create virtual environment**: `uv venv && source .venv/bin/activate`
- ⚠️ **Install workspace**: `uv pip install -e "packages/mas-core[dev]"` — cannot be persisted by AI
- **Review Pydantic models in `protocols/`** — confirm field names, types, and the `MessageEnvelope` 64KB validator are correct before building downstream code against them
- **Review `workflow/controller.py`** — confirm 14 workflow states + 4 terminal states match your mental model

### Verification Status
- `pytest packages/mas-core/tests/test_phase0_scaffold.py` — **NOT RUN** (requires installed environment)
- `pytest packages/mas-core/tests/test_protocols.py` — **NOT RUN**

---

## Phase 2 — Policy Engine

### What Was Automated
- `CommunicationPolicy` implemented as Python in-code dict (simpler v1 path)
- 6-role matrix with chain-of-command and tool-permission gating

### What Still Requires Human Action
- **Run** `pytest packages/mas-core/tests/test_policy.py` and review output
- **Spot-check**: worker→CEO (rejected), CSO veto (allowed), DevOps PM→CTO (allowed)

### No Blocking Decisions Remaining

---

## Phase 5 — LLM Gateway

### What Was Automated
- `LLMGatewayClient` with 9 providers written
- Retry logic, token tracking, streaming/non-streaming support

### What Still Requires Human Action (BLOCKING)
- ⚠️ **Ensure your LLM proxy is running and reachable from Docker**
- ⚠️ **Set in `mas/.env`**:
  ```env
  LLM_GATEWAY_URL=http://host.docker.internal:<port>/v1
  LLM_API_KEY=<your-key>
  LLM_DEFAULT_MODEL=<model-name>
  ```
- **Verify**: `docker run --rm curlimages/curl curl http://host.docker.internal:<port>/v1/models`

---

## Phase 4b — Deterministic Workflow Controller

### What Was Automated
- Full workflow controller implementation

### What Still Requires Human Action
- **Decide watchdog timeout** (default 1 hour — appropriate for your LLM speed?)
- **Decide review timeout** (default 5 min per reviewer — increase if LLM is slow)
- **Run**: `pytest packages/mas-core/tests/` to verify all 14 happy-path transitions

---

## Phase 3 — Message Router

### What Was Automated
- Full message router implementation
- `redis-acl-init.sh` with `ACL SAVE` fix
- `redis.conf` with AOF enabled

### What Still Requires Human Action (BLOCKING)
- ⚠️ **Generate Redis passwords** and add to `.env`:
  ```env
  ROUTER_REDIS_PASS=<generated>
  TOOL_REDIS_PASS=<generated>
  ```
- **Review `infra/compose/redis-acl-init.sh`** — confirm dangerous commands (`KEYS`, `FLUSHALL`, `DEBUG`, `CONFIG`) are disabled for non-admin users
- **Decide XAUTOCLAIM timeout** (default 120s — increase if LLM calls > 2 min)
- **Decide DLQ max retries** (default 3)

### Verification Required
- `docker compose exec redis redis-cli -u redis://router_user:<ROUTER_REDIS_PASS>@redis:6379 PING` — must return PONG
- `docker compose exec redis redis-cli PING` — must be rejected (default user disabled)

---

## Phase 6 — Tool Service

### What Was Automated
- Full tool service with 7 tool groups, circuit breakers, rate limiting, cache

### What Still Requires Human Action
- **Decide which tools are real vs. stub** — `web_search`/`web_fetch` are stubs unless a search API key is provided
- **Optional**: Add `WEB_SEARCH_API_KEY` and `WEB_SEARCH_PROVIDER` to `.env`
- **Decide rate limits per group** — defaults are in `tool_service/config.py`, review if you expect many concurrent projects

### Verification Required
- `curl http://localhost:8002/tools` — must return 7 tool groups
- `pytest mas/apps/tool-service/tests/` — all tests pass

---

## Phase 7 — Storage Layer

### What Was Automated
- All 3 Alembic migrations (0001, 0002, 0003) creating 20 tables
- `AgentStorage` wrapper with `statement_cache_size=0`
- PgBouncer in compose
- MinIO `BlobClient`

### What Still Requires Human Action (BLOCKING)
- ⚠️ **Generate Postgres and MinIO credentials** and add to `.env`
- ⚠️ **Run**: `cd mas && alembic upgrade head` (requires running Postgres)
- ⚠️ **MinIO licensing decision**: If distributing externally, evaluate SeaweedFS vs MinIO commercial license
- **Decide**: Enable Row Level Security? (default: no — recommended for v1)
- **Decide**: Capability registry seed data? (recommended: yes, so CTO can assign from first project)

### Verification Required
- All 20 tables exist after migration
- `alembic downgrade -1 && alembic upgrade head` — migration is reversible
- MinIO `mas-agents` bucket accessible

---

## Phase 4 + 8 — Agent Runtime & Agent Types

### What Was Automated
- All 6 agent types implemented with full checkpoint/budget/idempotency support

### What Still Requires Human Action (HIGH PRIORITY)
- ⚠️ **Review all 11 system prompts in `mas/prompts/`** — this is the most critical human task (2-4 hours)
  - `ceo.md`, `coo.md`, `cfo.md`, `cio.md`, `chrm.md`, `cso.md`, `cto.md`
  - `production_pm.md`, `system_pm.md`, `qa_lead.md`, `devops_pm.md`
  - Verify: role in hierarchy, tool usage instructions, reporting chain, edge case handling
- **Decide checkpoint interval** (default: every LLM call — recommended)
- **Decide max think iterations** per agent via `budget_defaults.max_llm_calls` in team YAMLs

---

## Phase 9 — Team Runner

### What Was Automated
- Team runner implemented with graceful shutdown, SHUTDOWN_ACK, checkpoint-on-SIGTERM
- 11 team YAMLs and 26 worker manifests generated in `mas/teams/` and `mas/workers/`

### What Still Requires Human Action (HIGH PRIORITY)
- ⚠️ **Review all 11 team YAMLs** in `mas/teams/`:
  - Confirm agent IDs are unique across all teams
  - Confirm model assignments match your LLM proxy's available models
  - Adjust `budget_defaults` for your LLM pricing
- ⚠️ **Review all 26 worker manifests** in `mas/workers/`:
  - `sandbox.profile` matches worker risk level
  - `transport` mode is correct
  - `capabilities` list is accurate
  - `checkpoint.strategy` is set
- **Decide stop grace period** (default 60s — increase if LLM calls > 30s)
- **Calculate memory requirements** (~7.5–8 GB minimum for all 18 containers)

---

## Phase 10 — Orchestrator API

### What Was Automated
- Full orchestrator-api with 30+ endpoints and 9 test files

### What Still Requires Human Action
- **Optional**: Set `MAS_API_KEY` in `.env` for basic auth on human-facing endpoints
- **Decide CORS origins** if building a web UI
- **Manual API test** after first compose up:
  ```bash
  curl -X POST http://localhost:8000/projects -d '{"name":"test","description":"smoke test"}'
  curl http://localhost:8000/system/status
  ```

---

## Phase 11 — Docker Compose

### What Was Automated
- `docker-compose.yml` with all 18 containers
- Network segmentation (public/internal split)
- Sandbox tier (Tier 0 + 1: `cap_drop: ALL`, `read_only`, `tmpfs`)
- PgBouncer healthcheck
- Redis AOF + `noeviction` config
- `docker-compose.dev.yml` with Prometheus + Grafana

### What Still Requires Human Action (BLOCKING)
- ⚠️ **Create `mas/.env`** with all credentials (use `plan-phase4-manualTodo.prompt.md §P0-1` script to generate)
- ⚠️ **Allocate 8 GB / 4 CPUs** in Docker Desktop settings
- ⚠️ **First `docker compose up --build`** and verify all 18 containers healthy
- **Verify network segmentation** (team containers cannot reach Redis)
- **Verify sandbox enforcement** (`docker inspect` shows `CapDrop: ["ALL"]`)
- **Note**: `infra/sandbox/` directory not created — sandbox profiles are inline in `docker-compose.yml` (not separate YAML files per the plan). Functionally equivalent for Tier 0 and Tier 1, but the directory-based profiles described in Phase 11 §45b are not implemented.

---

## Phase 12 — Observability

### What Was Automated
- `mas_core/observability/` module with 10 Prometheus metrics, structlog, trace-ID
- `/metrics` endpoints on all 3 services
- Prometheus + Grafana added to `docker-compose.dev.yml`
- `prometheus.yml` scrape config

### What Still Requires Human Action
- **Configure Grafana dashboards** — datasource is auto-provisioned; dashboards are manual:
  - `mas_project_state`, `mas_messages_total`, `mas_dlq_depth`, `mas_tool_circuit_state`, `mas_llm_calls_total`, `mas_budget_exhausted_total`
- **Set log level** in `.env` (default `INFO`; use `DEBUG` during development)

---

## Phase 13 — Shutdown, Resume & Scheduled Operation

### What Was Automated
- Full orchestrated shutdown/resume protocol
- Agent checkpoint save/restore
- Watchdog schedule-awareness
- Scheduled operation cron

### What Still Requires Human Action (RECOMMENDED)
- ⚠️ **Test full shutdown/resume cycle** (see `plan-phase4-manualTodo.prompt.md §P2-12`)
- ⚠️ **Test cold crash recovery** (see `plan-phase4-manualTodo.prompt.md §P2-13`)
- **Optional**: Configure `PUT /system/schedule` with working hours and timezone

---

## Phase 14 — Paperclip Integration

### Status: DEFERRED

No action needed unless you decide to integrate Paperclip. The execution plane operates fully standalone.

---

## Cross-Phase: Credential Generation

**NOT DONE** — The `.env` file does not exist (gitignored). Generate all credentials now:

```python
import secrets
for name in [
    "POSTGRES_PASSWORD", "ROUTER_REDIS_PASS", "TOOL_REDIS_PASS",
    "MINIO_ROOT_PASSWORD", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY",
    "MAS_API_KEY", "ROUTER_SECRET", "TOOL_SECRET",
]:
    print(f"{name}={secrets.token_urlsafe(32)}")
```

---

## Overall Remaining Manual Work

| Priority | Item | Time Estimate |
|----------|------|---------------|
| **P0 (Blocking)** | Generate `.env` credentials | 15 min |
| **P0 (Blocking)** | Verify Docker resources (8 GB/4 CPU) | 5 min |
| **P0 (Blocking)** | Ensure LLM proxy is accessible from Docker | 30 min |
| **P0 (Blocking)** | `alembic upgrade head` after Postgres up | 10 min |
| **P0 (Blocking)** | `docker compose up --build` | 30 min |
| **P1 (Required)** | Review all 11 system prompts | 2–4 hours |
| **P1 (Required)** | Review 11 team YAMLs | 30–60 min |
| **P1 (Required)** | Review 26 worker manifests | 30–60 min |
| **P1 (Required)** | Verify network segmentation, Redis ACL | 30 min |
| **P1 (Required)** | End-to-end smoke test | 30–60 min |
| **P2 (Recommended)** | Shutdown/resume cycle test | 1 hour |
| **P2 (Recommended)** | Cold crash recovery test | 30 min |
| **P2 (Recommended)** | Grafana dashboards | 1 hour |
| **Total P0+P1** | | **~5–9 hours** |
