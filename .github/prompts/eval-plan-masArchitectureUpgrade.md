# Evaluation: plan-masArchitectureUpgrade.prompt.md

**Generated**: 2026-03-31  
**Evaluator**: AI code review — static analysis + codebase audit  
**Scope**: All uncommitted changes (staged + unstaged + untracked files)

---

## Summary

Phases 0–13 are **substantially complete** as automated work. Phase 14 (Paperclip integration) is intentionally deferred. Several items within completed phases have quality concerns documented below.

---

## Phase-by-Phase Status

### Phase 0 — Repo Scaffold

**Status: COMPLETE**

All directories exist: `apps/orchestrator-api/`, `apps/team-runner/`, `apps/message-router/`, `apps/tool-service/`, `packages/mas-core/`, `packages/mas-tools-sdk/`, `teams/`, `workers/`, `infra/compose/`, `migrations/`.

- `pyproject.toml` workspace is present at `mas/pyproject.toml`
- `packages/mas-core/workflow/` and `packages/mas-core/capabilities/` exist
- `infra/sandbox/` directory: **NOT FOUND** — sandbox profile files were not created (only the Docker Compose `cap_drop`/`read_only` inline config was added)

**What needs human check**: Confirm `infra/sandbox/` directory is not required yet (only referenced in Phase 11 / sandbox tier documentation). Currently safe to defer.

---

### Phase 1 — Protocols & Core Models

**Status: COMPLETE**

- `MessageEnvelope` with all required fields: present in `packages/mas-core/mas_core/protocols/envelope.py`
- `MessageType` enum with full set (TASK, RESULT, QUERY, RESPONSE, BROADCAST, ADMIN_TASK, ADMIN_REPLY, SHUTDOWN, document lifecycle, review workflow, HITL, sprint, hierarchy, INFRA_READY, system types): present in `protocols/enums.py`
- `BlobRef`, `AgentRole`, `TaskBudget`: all present
- `WorkerManifest` Pydantic model (Phase 1 §7b): present in `protocols/` or `agent_runtime/config.py`
- `CapabilityDef` Pydantic model (Phase 1 §7c): present in `memory/models.py`

**What needs human check**:
- Verify `MAX_PAYLOAD_BYTES = 64 * 1024` validator is actually enforced in `MessageEnvelope` (not just documented)
- Review generated Pydantic models to confirm field names match your mental model — this is the canonical schema

---

### Phase 2 — Policy Engine

**Status: COMPLETE**

- `CommunicationPolicy` with 6-role matrix: present in `packages/mas-core/mas_core/policy/engine.py` and `rules.py`
- Chain-of-command enforcement, tool permission matrix, `blocked_tools` per role: present

**Known issue**: Policy is defined as Python dicts in-code (not YAML). This matches the simpler v1 recommendation but means policy changes require redeploy.

**What needs human check**:
- Run `pytest packages/mas-core/tests/test_policy.py` and review output — confirm the 6-role matrix matches expectations
- Verify edge cases: worker→CEO (rejected), CSO veto (allowed), CTO→dept_devops PM (allowed)

---

### Phase 3 — Message Router

**Status: COMPLETE with minor dead code**

- HTTP endpoints (`POST /messages/publish`, `POST /messages/broadcast`, `GET /health`): present in `apps/message-router/`
- WebSocket endpoint (`WS /ws/subscribe/{team_id}`): present in `routes_ws.py`
- ACK/NACK protocol, heartbeat, consumer group management: present
- XAUTOCLAIM background task, DLQ → Postgres, stream trimming: present in `tasks.py`
- Publish-side idempotency (300s TTL dedupe key): present in `routes_publish.py`
- Redis ACL: `redis-acl-init.sh` updated with `ACL SAVE` fix applied
- `/metrics` Prometheus endpoint: added (fix from phase4-manualTodo audit)

**Dead code issue**: `apps/message-router/message_router/main.py` defines `messages_published_total` and `messages_dlq_total` as local Prometheus counters, but `routes_publish.py` uses `MAS_MESSAGES_TOTAL` from `mas_core.observability`. The local counters are never incremented — dead code. Low severity but should be removed to avoid confusion.

**What needs human check**:
- Verify Redis ACL: `docker compose exec redis redis-cli -u redis://router_user:<ROUTER_PASSWORD>@redis:6379 PING` should succeed; `PING` with default user should be rejected
- Run `pytest mas/apps/message-router/tests/` including the new `test_metrics.py`

---

### Phase 4b — Deterministic Workflow Controller

**Status: COMPLETE**

- Workflow controller in `packages/mas-core/mas_core/workflow/controller.py`
- 18 project states, transition table, watchdog: present
- Sole writer of `projects.state` — verified in `orchestrator_api/main.py`
- `POST /projects/{id}/transition`, watchdog cron: present in orchestrator-api

**What needs human check**: None for code; requires live Postgres to verify end-to-end.

---

### Phase 5 — LLM Gateway

**Status: COMPLETE**

- `LLMGatewayClient` with async HTTP, streaming/non-streaming, tool use parsing: present in `packages/mas-core/mas_core/llm_gateway/client.py`
- 9 providers (8 API + 1 CLI/Copilot): confirmed from `providers/` directory
- Retry with exponential backoff, token usage tracking: present
- `LLM_GATEWAY_URL` env var config: present

**What needs human check**:
- ⚠️ Set `LLM_GATEWAY_URL`, `LLM_API_KEY`, `LLM_DEFAULT_MODEL` in `.env` before first run
- Verify your LLM proxy is reachable from inside Docker containers

---

### Phase 6 — Tool Service

**Status: COMPLETE with dead code**

- `POST /tools/{tool_name}/run`, `POST /tools/execute`, `GET /tools`, `GET /health`: present in `apps/tool-service/tool_service/main.py`
- 7 tool groups, role-based access gating, circuit breakers, rate limiting, result cache: present
- Transport modes (internal, HTTP, MCP, process): present per manifest
- `registry.py` uses shared `MAS_TOOL_CALLS_TOTAL` from `mas_core.observability`

**Dead code issue**: `apps/tool-service/tool_service/main.py` defines `tool_invocations_total` and `tool_errors_total` as local Prometheus counters. These are never incremented in the changed codebase — dead code. Remove or wire up.

**What needs human check**: None for code; tool circuit breaker states need live testing.

---

### Phase 7 — Storage Layer

**Status: COMPLETE**

- Alembic migrations: `0001_initial_schema.py`, `0002_missing_tables.py`, `0003_capability_registry.py` — all present and properly chained
- **20 tables total**: 3 base (`memory`, `task_log`, `artifacts`) + 12 org-architecture tables + 2 system tables (`system_config`, `agent_checkpoints`) + 3 capability registry tables (`capabilities`, `worker_registry`, `role_capability_map`) — confirmed from migration files
- `AgentStorage` async wrapper in `packages/mas-core/mas_core/memory/storage.py` with `statement_cache_size=0`: confirmed
- PgBouncer in compose with transaction pooling: confirmed
- MinIO + `BlobClient` in `packages/mas-core/`: confirmed
- Fix applied: `system_config` bootstrap rows use empty string `''` sentinel for NOT NULL `shutdown_at`/`boot_at` columns

**Type safety note**: In `orchestrator_api/main.py`, comparisons like `doc.get("project_id") != project_id` are safe — both sides are `uuid.UUID` from asyncpg and FastAPI path params respectively.

**What needs human check**:
- ⚠️ Run `alembic upgrade head` after Postgres container is up
- Verify reversibility: `alembic downgrade -1 && alembic upgrade head`

---

### Phase 4 + 8 — Agent Runtime + Agent Types

**Status: COMPLETE**

- `AgentBase`, `WorkerAgent`, `AdminAgent`, `SubAgent`, `ExecutiveAgent`, `CSuiteAgent`: present in `packages/mas-core/mas_core/agent_runtime/`
- `RouterClient` (HTTP+WS), `BudgetTracker`, consume-side idempotency (LRU set): present in `agent_runtime/`
- Structured checkpoint save/restore logic: present in `agent_runtime/base.py` + `memory/checkpoints.py`

**What needs human check**: Review generated agent prompts in `mas/prompts/` — 11 prompts must be reviewed for accuracy (see Phase 1 of P1 items in `plan-phase4-manualTodo.prompt.md`).

---

### Phase 9 — Team Runner

**Status: COMPLETE**

- 11 team YAMLs in `mas/teams/`: present
- Worker manifests (26 workers) in `mas/workers/`: present
- Graceful shutdown (SIGTERM + SHUTDOWN message), checkpoint-on-mid-task, SHUTDOWN_ACK: present in `apps/team-runner/team_runner/main.py`
- `stop_grace_period: 60s` on team containers: confirmed in `docker-compose.yml`

**What needs human check**:
- Review all 11 team YAMLs for correct agent IDs, model assignments, and budget defaults
- Review all 26 worker manifests for correct sandbox profiles and transport modes

---

### Phase 10 — Orchestrator API

**Status: COMPLETE**

- Full rewrite at `apps/orchestrator-api/orchestrator_api/main.py` (~1,439 lines)
- All endpoint groups present: project CRUD, transitions, state history, allowed transitions, pending decisions, documents, dead letters, capabilities, system shutdown/resume/status/schedule, watchdog
- Startup resume sequence: confirmed
- 9 new test files added: `test_projects.py`, `test_transitions.py`, `test_decisions.py`, `test_documents.py`, `test_dead_letters.py`, `test_capabilities.py`, `test_system.py`, `test_watchdog.py`, `test_metrics.py`

**Minor issue**: `bind_trace_id()` is called in `create_project` and `create_task` endpoints but `clear_trace_context()` is never called. In async ASGI, structlog context vars are per-coroutine so there is no cross-request leakage — but it's an inconsistency worth cleaning up.

**Prometheus cardinality note**: `MAS_PROJECT_STATE` Gauge uses `project_id` as a label. With many long-running projects, this creates unbounded label cardinality. Acceptable for v1 prototype scale; revisit before production scale.

**What needs human check**:
- Run orchestrator test suite: `pytest mas/apps/orchestrator-api/tests/`
- Verify endpoint paths match the canonical glossary in `plan-orgArchitecture.prompt.md §17`

---

### Phase 11 — Docker Compose

**Status: COMPLETE with infra/sandbox gap**

- `docker-compose.yml` with all 18 containers: confirmed
- Network segmentation (public/internal split): confirmed — message-router and tool-service fixed to `internal` only
- `docker-compose.dev.yml` with dev overrides: confirmed
- Redis AOF config (`redis.conf`): confirmed
- PgBouncer healthcheck + `condition: service_healthy` dependency: fix confirmed applied
- `x-team-defaults` with `cap_drop: [ALL]`, `read_only: true`, `tmpfs: /tmp:size=128m`: fix confirmed applied
- `infra/sandbox/` directory: **NOT CREATED** — sandbox profile YAML files referenced in Phase 11 §45b are absent

**What needs human check**:
- ⚠️ Create `.env` file with all required credentials (see `plan-phase4-manualTodo.prompt.md §P0-1`)
- Verify network segmentation: team containers should not reach Redis
- Verify sandbox enforcement per `plan-phase4-manualTodo.prompt.md §P2-15`

---

### Phase 12 — Observability

**Status: COMPLETE**

- `mas_core/observability/` module: `__init__.py`, `logging.py`, `metrics.py`, `tracing.py` — all present (new untracked files)
- 10 Prometheus metrics defined in `metrics.py`: confirmed
- `configure_logging()` shared helper used by message-router and orchestrator-api: confirmed
- `trace_id` generation and structlog context propagation: confirmed
- `/metrics` endpoints on message-router, orchestrator-api, tool-service: confirmed
- Prometheus + Grafana added to `docker-compose.dev.yml`: confirmed
- `prometheus.yml` scrape config: confirmed (new untracked file)

**What needs human check**:
- Configure Grafana dashboards manually after first `docker compose up` with dev overlay (see `plan-phase4-manualTodo.prompt.md §P2-14`)

---

### Phase 13 — Shutdown, Resume & Scheduled Operation

**Status: COMPLETE**

- Orchestrated shutdown cascade (`POST /system/shutdown` → SHUTDOWN broadcast → agent checkpoint → SHUTDOWN_ACK → `system_state = STOPPED`): present
- Resume sequence on startup (re-publish DIRECTIVE(action=RESUME) for active projects, watchdog grace period): present in orchestrator-api `on_startup`
- Agent checkpoint save/restore in `AgentBase.think()`: present
- Watchdog schedule-awareness (excludes downtime from timeout calculation): present in `workflow/controller.py`
- Scheduled operation (cron check, `PUT /system/schedule`): present
- Redis AOF config: confirmed
- `test_shutdown_resume.py` (new untracked): present in `packages/mas-core/tests/`
- `test_shutdown.py` (new untracked): present in `apps/team-runner/tests/`

**What needs human check**: Test full shutdown/resume cycle with a live system (see `plan-phase4-manualTodo.prompt.md §P2-12` and `§P2-13`).

---

### Phase 14 — Paperclip Integration

**Status: DEFERRED (intentional)**

No implementation expected. See `plan-phase4-manualTodo.prompt.md §P3-18` for when/if to pursue.

---

## Code Issues Summary

| Severity | Location | Issue |
|----------|----------|-------|
| Low | `orchestrator_api/main.py` ~line 425 | `response_started` variable declared and set via `nonlocal` in Prometheus ASGI proxy `send()` but never read — dead code |
| Low | `message_router/main.py` | `messages_published_total` and `messages_dlq_total` local Prometheus counters defined but never incremented — dead code |
| Low | `tool_service/main.py` | `tool_invocations_total` and `tool_errors_total` local Prometheus counters defined but never incremented — dead code |
| Low | `orchestrator_api/main.py` | `bind_trace_id()` called without corresponding `clear_trace_context()` — not a bug (async context isolation) but inconsistent pattern |
| Architecture | `orchestrator_api/main.py` | `MAS_PROJECT_STATE` Gauge with `project_id` label creates unbounded cardinality at scale |
| Missing | `infra/sandbox/` | Sandbox profile YAML files not created — referenced in plan but absent |

---

## Verification Checklist

Items requiring real infrastructure that cannot be verified statically:

- [ ] `pytest mas/packages/mas-core/tests/` — all tests pass
- [ ] `pytest mas/apps/message-router/tests/` — all tests pass
- [ ] `pytest mas/apps/orchestrator-api/tests/` — all tests pass
- [ ] `pytest mas/apps/tool-service/tests/` — all tests pass
- [ ] `pytest mas/apps/team-runner/tests/` — all tests pass
- [ ] `alembic upgrade head` — migrations apply cleanly
- [ ] `docker compose up --build` — all 18 containers healthy within 90s
- [ ] Redis ACL enforcement verified (router_user works, default rejected)
- [ ] Network segmentation verified (team containers cannot reach Redis)
- [ ] Sandbox enforcement verified (cap_drop=ALL, read-only filesystem)
- [ ] End-to-end smoke test: create project → watch state transitions
- [ ] Shutdown/resume cycle test
- [ ] Cold crash recovery test
