# MAS Architecture Upgrade — Router, Durability, Services

**TL;DR**: Full rewrite into a clean `mas/` monorepo implementing the **execution plane** of a two-plane architecture (see *Architecture Context* below). Eight upgrades turn this prototype into a **shutdown-safe, restart-proof**, container-isolated system: (1) a Message Router service (HTTP+WS) replacing direct Redis access, (2) Redis Streams with consumer groups, XAUTOCLAIM, DLQ→Postgres, Redis ACL, and publish/consume idempotency for **at-least-once delivery** and **effectively-once processing**, (3) a role-gated Tool Service with **7 tool groups**, circuit breakers, and support for **MCP tool endpoints**, (4) shared Postgres tables keyed by `agent_id` with PgBouncer plus a **capability registry**, (5) MinIO for blob storage, (6) per-task budgets and backpressure, (7) an **orchestrated shutdown/resume protocol** with structured agent checkpoints and optional scheduled working hours, (8) a **worker manifest abstraction** (`worker.yaml`) with **sandbox tiers** for safe onboarding of external/GitHub-sourced workers. A **deterministic workflow controller** in orchestrator-api owns all project state transitions — agents emit events, the controller validates and persists atomically. Messages use a single **unified `MessageEnvelope`** schema with idempotency keys, TTL, and BlobRef for large payloads. On shutdown, agents checkpoint mid-task progress to Postgres; on startup, the controller re-publishes work messages and agents resume from checkpoints — **no work is lost across reboots**. The LLM gateway targets an OpenAI-compatible API (your custom multi-provider proxy). **11 teams** (including DevOps as v1 critical path) remain config-only YAML → **18 containers** total. All infra is $0 self-hosted OSS; only LLM API costs apply.

> **Paperclip integration**: This plan is designed to operate standalone **or** as the execution plane alongside [Paperclip](https://github.com/paperclipai/paperclip) as the control plane (org chart, tickets, approvals, budgets, audit, UI). See *Architecture Context* below and optional Phase 14 for integration details.

> **Companion document**: See **`plan-orgArchitecture.prompt.md`** for the corporate-hierarchy agent topology (CEO → COO → C-Suite → Departments), the **14-step** project workflow with deterministic controller (§2), **unified MessageEnvelope** (§3), Redis Streams hardening (§4.4), tool service manifest (§11.2.1), **CSO veto** + review circuit breakers (§7), **DevOps department** (§6.4) with INFRA_READY gate, agent profile learning (§8.3), **20 Postgres tables** (17 new: 12 org + 2 system + 3 capability; 3 base unchanged — §10), Human-in-the-Loop + controller + **system lifecycle endpoints** (§9), **v0 vertical slice scope** (§11b), and how all organisational elements map onto the infrastructure phases below. See **§17 of that document** for the **canonical glossary** of all stream names, table names, tool groups, role values, and endpoint signatures — that section is the single source of truth for naming across all three plan documents.

> **Delivery semantics note**: Redis Streams with consumer groups provides **at-least-once delivery** (messages stay in the Pending Entries List until XACKed). We achieve **effectively-once processing** by adding idempotency keys at the router (publish dedupe with 300 s TTL) and handler (LRU processed-marker) layers. This is not "exactly-once" — it is the correct, honest claim for this architecture.

---

## Architecture Context — Two-Plane Design

This plan implements the **execution plane** of a two-plane architecture informed by analysis of the [Paperclip](https://github.com/paperclipai/paperclip) orchestration framework (see `Docs/deep-research-report.md`).

| Plane | Source of Truth | Components |
|-------|----------------|------------|
| **Control Plane** (Paperclip — optional) | Companies, agents (org chart), issues/tasks, approvals, budgets, activity log, secrets | Paperclip server + UI + DB + adapters |
| **Execution Plane** (this plan) | Workflow templates + deterministic transitions + project state history + checkpoint/resume + tool gateway policies + durable task distribution | MAS services: orchestrator-api, message-router, tool-service, team-runners |

**Key integration decisions** (see Phase 14 and `plan-manualActions.prompt.md`):
- **Standalone mode** (default v1): MAS runs independently with its own Postgres schema, API, and workflow controller. Human interacts via orchestrator-api REST endpoints.
- **Integrated mode** (optional v1.1+): MAS maps workflow events to Paperclip issues + comments for human-auditable traceability. Paperclip provides the UI, org chart, budget enforcement, and audit log. MAS handles durable execution mechanics.

**Conflict resolutions** (from research report):
1. "Tasks as communication" (Paperclip) vs "messages + router" (MAS) → Keep Paperclip issues as the *human-auditable* contract; router messages as *internal execution mechanics*.
2. Full visibility (Paperclip) vs chain-of-command (MAS) → Enforce access control in the execution plane (router/tool-service); Paperclip UI remains board-centric.
3. Manual recovery (Paperclip) vs automatic resume (MAS) → Auto-resume for idempotent/checkpointed tasks; manual escalation for ambiguous failures (DLQ, safety vetoes).

---

## Steps

### Phase 0 — Repo scaffold

1. Create the `mas/` monorepo structure:
   - `apps/orchestrator-api/` — FastAPI app (current orchestrator.py logic, expanded + **deterministic workflow controller**)
   - `apps/team-runner/` — Docker entrypoint per team (loads config, starts agents)
   - `apps/message-router/` — New service: HTTP+WS, policy enforcement, Redis Streams backend
   - `apps/tool-service/` — New service: HTTP tool gateway with role-gated access, **7 tool groups**, circuit breakers
   - `packages/mas-core/` — Shared library (agent runtime, protocols, policy, LLM gateway, memory, util)
   - `packages/mas-tools-sdk/` — Tool interface + HTTP client for tool-service
   - `teams/` — Config-only YAML per team (**11 teams**: exec_ceo, exec_coo, office_cfo, office_cio, office_chrm, office_cso, office_cto, dept_production, dept_system, dept_qa, **dept_devops**)
   - `workers/` — **Worker manifests** (`worker.yaml` per worker type) defining runtime adapter, capabilities, sandbox profiles — makes onboarding new workers a config change, not code change (see Phase 9 for schema)
   - `packages/mas-core/workflow/` — Deterministic workflow controller (transition table, watchdog — see org-architecture plan §11.2)
   - `packages/mas-core/capabilities/` — **Capability registry** — structured catalog of worker capabilities with input/output schemas, risk levels, and cost models (see Phase 7 for DB tables)
   - `infra/docker/` — Dockerfiles per service
   - `infra/compose/` — `docker-compose.yml` + `docker-compose.dev.yml`
   - `infra/sandbox/` — **Sandbox profiles** (seccomp, gVisor, and network egress configs for worker isolation tiers — see Phase 11)
   - `migrations/` — Alembic migration scripts
2. Create `pyproject.toml` with workspace packages (e.g., `hatch` or `uv` workspaces) so `mas-core` and `mas-tools-sdk` are installable as local deps.

---

### Phase 1 — Protocols & Core Models (`packages/mas-core/protocols/`)

3. Define **unified `MessageEnvelope`** Pydantic model — the single canonical message format (replaces the previous dual `Message` + `RouterEnvelope` design). Required fields: `message_id` (UUID, idempotency key), `correlation_id`, `parent_id`, `msg_type` (MessageType enum), `sender_id`, `sender_role` (AgentRole enum: orchestrator/executive/c_suite/admin/worker/sub_agent), `recipient_id`/`recipient_team`, `project_id` (mandatory after INIT), `timestamp`, `ttl_seconds` (default 3600), `retry_count`, `ack_required`, `payload` (dict, ≤64 KB), `blob_ref` (optional BlobRef for large payloads), `budget` (optional TaskBudget). See org-architecture plan §3.1 for full schema.
   - Extend `MessageType` enum with the full set:
     - **Core**: `TASK`, `RESULT`, `QUERY`, `RESPONSE`, `BROADCAST`, `ADMIN_TASK`, `ADMIN_REPLY`, `SHUTDOWN`
     - **Document lifecycle**: `DOCUMENT_SUBMIT`, `DOCUMENT_REVISION`
     - **Review workflow**: `REVIEW_REQUEST`, `REVIEW_RESPONSE`
     - **Human-in-the-loop**: `APPROVAL_REQUEST`, `APPROVAL_RESPONSE`
     - **Sprint management**: `SPRINT_PLAN`, `SPRINT_REPORT`, `ISSUE_ASSIGN`, `ISSUE_COMPLETE`
     - **Hierarchy**: `ESCALATION`, `DIRECTIVE`
     - **Infrastructure**: `INFRA_READY`
     - **System**: `HEARTBEAT`, `ACK`, `SYSTEM_EVENT`
     - See `plan-orgArchitecture.prompt.md` §3.2 for full definitions.
   - Define `BlobRef` model: `bucket`, `key`, `sha256`, `size_bytes` — used when payload exceeds 64 KB.
   - Define `AgentRole` enum: `orchestrator`, `executive`, `c_suite`, `admin`, `worker`, `sub_agent`.
4. Define `TaskBudget` Pydantic model: `max_llm_calls: int | None`, `max_tool_calls: int | None`, `max_subtasks: int | None`, `deadline: datetime | None`, `max_cost_usd: float | None`. This gets embedded in `MessageEnvelope.budget` for `TASK` / `ADMIN_TASK` messages.
5. Define `ToolRequest` / `ToolResponse` models for agent ↔ tool-service communication.
6. Define additional domain models: `AgentProfile` (correction_factor, estimation_bias, confidence), `KPISnapshot` (with RESOURCE_UTILIZATION, INFRA_LEAD_TIME metrics), and all review/document/sprint models. See org-architecture plan §3.4 for Pydantic definitions.
7. **Message size rule**: Redis Stream payloads must stay small (task metadata + pointers only). Large blobs and results go to MinIO; messages carry `BlobRef` references instead of inline data. Enforce max payload size (`MAX_PAYLOAD_BYTES = 64 * 1024`) in the `MessageEnvelope` validator. Payloads exceeding this must use `blob_ref`.
7b. **Worker manifest models**: Define `WorkerManifest` Pydantic model for the `worker.yaml` schema — enables onboarding new workers (including GitHub-sourced agents) as a config change rather than code change:
    - `metadata`: name, version, description, source (repo URL + revision), tags
    - `runtime`: transport mode (`process` | `http` | `oci` | `mcp` | `human`), adapter-specific config (command/args/cwd for process, URL/method/headers for http, image/tag for oci, endpoint for mcp), timeout, grace period
    - `capabilities`: list of `CapabilityDef` (name, input_schema_ref, output_schema_ref, limits: max_concurrent, max_cost_usd, max_tool_calls)
    - `sandbox`: profile tier (`standard` | `restricted` | `gvisor` | `firecracker`), filesystem rules (read-only root, writable dirs), network mode (`egress-allowlist` | `egress-deny-all` | `unrestricted`), allowed egress domains, Linux security (drop capabilities flag, seccomp profile)
    - `checkpointing`: supported flag, strategy (`on-step` | `periodic` | `on-signal`), store config (kind + table), resume includes list
    - `observability`: logs format, metrics enabled flag, traces enabled flag
    - See `Docs/deep-research-report.md` for the full annotated `worker.yaml` example.
7c. **Capability registry models**: Define `CapabilityDef` Pydantic model:
    - `name`, `version`, `description`, `input_schema` (JSON Schema ref), `output_schema` (JSON Schema ref)
    - `risk_level` (low | medium | high | critical), `cost_model` (per-invocation estimate)
    - `required_tools` (list of tool names this capability needs), `required_role` (minimum AgentRole)
    - This enables the CTO/CHRM to resolve "I need X capability" → "which workers can do it?" without code changes.

---

### Phase 2 — Policy Engine (`packages/mas-core/policy/`)

8. Implement `CommunicationPolicy` — a stateless rules engine that answers `can(sender_role, sender_team, recipient_id, recipient_team, msg_type) → bool | deny_reason`. **Six roles** (extended from 4 to support corporate hierarchy — see org-architecture plan §4):
   - `orchestrator` (CEO) → anywhere, all message types, only Human interface, all tools
   - `executive` (COO) → all C-Suite + all department PMs, document/review/directive types
   - `c_suite` (CFO/CIO/CHRM/CSO/CTO) → CEO + COO + peer C-Suite (review types only cross-team) + own team workers. CTO extra: `sprint.*`, `issue.*`, `kpi.*` tools
   - `admin` (Dept PM) → COO (report up) + CTO (sprint reports for admin:devops_pm and others) + own team workers. DevOps PM extra: `infra.*`, `cicd.*`, `monitoring.*`, `secrets.*` tools
   - `worker` → same team only, message types `TASK | QUERY | RESULT | RESPONSE | ISSUE_COMPLETE | ESCALATION`. **Blocked tools**: `project.*`, `approval.*`, `review.start_session`, `sprint.create`, `sprint.activate`
   - `sub_agent` → parent agent only
   - **Chain of command enforcement**: messages that skip hierarchy levels are rejected (worker cannot reach CEO directly — must escalate through PM → COO → CEO). Exception: `ESCALATION` messages can skip one level.
   - **Tool permission enforcement**: policy also validates `(sender_role, tool_name)` on every tool-service call. See org-architecture plan §4.2 for full `POLICY_RULES` and §11.2.1 for tool manifest.
9. Policy is loaded from a YAML or Python config, used by the router at enforcement time and by tool-service for tool-access gating.

---

### Phase 3 — Message Router (`apps/message-router/`)

10. **HTTP endpoints** (FastAPI):
    - `POST /messages/publish` — accepts `MessageEnvelope`, validates via `CommunicationPolicy`, checks **publish-side idempotency** (`dedupe:{message_id}` in Redis with 300 s TTL). If key exists, returns the original `stream_entry_id` without re-enqueuing. Otherwise, XADDs to `stream:{recipient_team}` and stores the dedupe key. Returns `{entry_id, deduplicated: bool}`.
    - `POST /messages/broadcast` — same policy validation, fan-out to all 11 team streams (used for `SHUTDOWN`, `DIRECTIVE` broadcasts).
    - `GET /health` — Redis ping + internal state.
11. **WebSocket endpoint**:
    - `WS /ws/subscribe/{team_id}` — agent connects with `Authorization: Bearer {agent_id}:{secret}` header. Router authenticates, then first replays **pending PEL entries** (`XREADGROUP … 0`) for any in-flight messages from before the previous disconnect, then enters the live **new-message loop** (`XREADGROUP … >`). Sends messages as `WSMessageFrame` JSON text frames. Each delivered frame includes `retry_count` (incremented on XAUTOCLAIM reclaim).
    - **ACK/NACK protocol**: agent sends `WSAckFrame {type: "ACK", message_id, stream_entry_id}` on success → router calls `XACK`. Agent sends `WSNackFrame {type: "NACK", message_id, stream_entry_id}` on failure → message stays in PEL for XAUTOCLAIM. This prevents silent message loss.
    - **Heartbeat**: router sends periodic WS `PING` frames (every 15s). If no `PONG` within 10s, connection is considered dead → cleanup + pending entries remain for reclaim. This detects silent disconnects faster than TCP keepalive alone.
    - **Scaling caveat**: sticky sessions are required for >1 router instance. **NGINX Plus** supports sticky cookies natively; **NGINX OSS** only has `ip_hash` (imperfect behind NAT). Default: **run 1 router instance** ($0, sufficient for 20–40 agents). Add affinity when scaling.
12. **Consumer group management** (per-team streams — see org-architecture plan §4.4):
    - Stream per team: `stream:{team_id}` (e.g., `stream:exec_ceo`). Consumer group: `group:{team_id}`.
    - On agent connect: `XGROUP CREATE stream:{team_id} group:{team_id} $ MKSTREAM` (idempotent with try/except).
    - Read loop: `XREADGROUP GROUP group:{team_id} {consumer_id} BLOCK 5000 COUNT 10 STREAMS stream:{team_id} >`
    - On ACK from agent: `XACK stream:{team_id} group:{team_id} <entry_id>`
    - Pending recovery: on reconnect, first read pending entries (`XREADGROUP ... 0`), then switch to `>`
    - **XAUTOCLAIM** (Redis 6.2+): background task reclaims messages idle > **120 s**, increments `retry_count` in envelope: `XAUTOCLAIM stream:{team_id} group:{team_id} {consumer_id} 120000 0-0`
    - **Dead-Letter Queue (DLQ → Postgres)**: after `max_attempts = 3` failed deliveries (tracked via PEL delivery count) **or** TTL expiry (`now() - timestamp > ttl_seconds`), the router writes the message to Postgres `dead_letters` table, calls `XACK` + `XDEL` on the stream entry, and publishes a `SYSTEM_EVENT { event: "DLQ_ENTRY" }` to `stream:exec_ceo` so the CEO is notified. **No separate Redis DLQ stream** — dead letters go directly to Postgres for forensic review.
    - **Consume-side idempotency**: each consumer (team-runner) maintains a local LRU set (size 1 000) of recently processed `message_id` values. On delivery: if in LRU → XACK immediately, skip processing; else → process → add to LRU → XACK.
    - **Stream trimming**: periodic job (every 60 s) runs `XTRIM stream:{team_id} MAXLEN ~ 50000` on each stream to prevent unbounded Redis memory growth.
13. **Redis credentials + ACL** — only the router and tool-service containers get Redis connection strings. Agent containers do **not** — they only know `ROUTER_URL`.
    - **Redis ACL users** (not just DB separation — DB indexes are not a security boundary):
      - `router_user`: allowed commands `XADD XREADGROUP XACK XAUTOCLAIM XDEL XTRIM XLEN XINFO XGROUP SET GET DEL EXPIRE PING` on key patterns `stream:* dedupe:* heartbeat:*`
      - `toolcache_user`: allowed commands `GET SET DEL EXPIRE PING` on key pattern `tool_cache:*` only
      - `default` user: disabled
      - Dangerous commands (`CONFIG`, `FLUSHALL`, `FLUSHDB`, `DEBUG`, `KEYS`) disabled for all non-admin users
    - Provide a `redis.acl` or `redis.conf` file mounted into the Redis container with these user definitions.
    - See org-architecture plan §4.4.6 for exact ACL SETUSER commands.

---

### Phase 4 — Agent Runtime Rewrite (`packages/mas-core/agent_runtime/`)

14. Port `AgentBase` from current agent_base.py. Key changes:
    - Replace `MessageBus` dependency with a `RouterClient` (HTTP+WS client).
    - `RouterClient.publish(envelope: MessageEnvelope)` → HTTP POST to router.
    - `RouterClient.subscribe(agent_id, team_id, handler)` → WS connection to router. Automatically sends ACK after successful handler execution.
    - Add `BudgetTracker` — tracks `llm_calls_remaining`, `tool_calls_remaining`, `subtasks_remaining`, `cost_so_far`. Decrements on each call. Raises `BudgetExhausted` when a limit is hit. Inherited from parent task's `MessageEnvelope.budget`.
    - **Consume-side idempotency**: maintain a local LRU set (size 1 000) of recently processed `message_id` values. Before processing, check if `message_id` in LRU → XACK immediately, skip. Else: process → add to LRU → XACK. This shields against XAUTOCLAIM re-delivery races and completes the effectively-once guarantee (router dedupe handles publish side; LRU handles consume side).
    - **Structured checkpoint contract**: After each LLM call, agent writes a structured checkpoint to Postgres `agent_checkpoints` table containing:
      - `inputs_ref` — BlobRef or inline pointer to the original task inputs
      - `current_step` — deterministic step key (e.g., `think_iteration_3`)
      - `resume_token` — tool-specific opaque state (e.g., last tool call ID)
      - `conversation_history` — LLM messages array (for continuing multi-turn reasoning)
      - `tool_results` — accumulated tool call results so far
      - `budget_snapshot` — remaining budget counters at checkpoint time
      - `last_successful_action` — idempotency key for the last completed action (prevents re-execution on resume)
      - `repo_state` — (for repo-based workers only) branch + commit SHA, so code-editing workers resume from the correct git state
      This structured format (vs. a flat JSON dump) enables the team-runner to validate checkpoint integrity on resume and skip already-completed actions.
15. Port `AgentConfig` — add `budget_defaults: TaskBudget` field for per-agent default caps.
16. Rewrite `think()` method:
    - Replace `AsyncAnthropic` with `LLMGatewayClient` (see Phase 5).
    - Each LLM call: `budget.consume_llm_call()`.
    - Each tool call: `budget.consume_tool_call()` + calls tool-service instead of local registry.
    - Check `budget.deadline` before each iteration.
17. Add structured logging: every log line includes `trace_id`, `span_id`, `agent_id`, `team_id`. Use `structlog` or stdlib with JSON formatter.

---

### Phase 5 — LLM Gateway (`packages/mas-core/llm_gateway/`)

18. Implement `LLMGatewayClient` — async HTTP client targeting your custom OpenAI-compatible provider.
    - `POST /v1/chat/completions` with `model`, `messages`, `tools`, `tool_choice`, `max_tokens`, `temperature`.
    - Parse streaming / non-streaming responses.
    - Handle tool use blocks in the response (OpenAI format: `tool_calls` array with `function.name`, `function.arguments`).
    - Config: `LLM_GATEWAY_URL` env var pointing to your provider.
19. Add retry logic with exponential backoff for 429/5xx. Track token usage per call for cost estimation in `BudgetTracker`.

---

### Phase 6 — Tool Service (`apps/tool-service/`)

20. **FastAPI service** with endpoints:
    - `POST /tools/{tool_name}/run` — path-driven; body: `{agent_id, sender_role, sender_team, kwargs}`. Validates `(sender_role, tool_name)` via policy, runs through registry pipeline (policy → breaker → rate limit → cache → execute), returns `ToolResponse`.
    - `POST /tools/execute` — flat SDK-friendly endpoint; body is a full `ToolRequest` (includes `tool_name` in the JSON body). Runs the same registry pipeline as above. Useful for clients that resolve the tool name before calling.
    - `GET /tools` — returns tool manifest (for LLM system prompts). **7 tool groups**: Workflow, Document, Review, Sprint/Issue, DevOps, Capability, KPI/Utility. See org-architecture plan §17.3 for the full canonical manifest.
    - `GET /health` — service health including cache status and per-tool circuit-breaker states.
20b. **Transport modes for tool execution**: The tool-service supports multiple backend transport modes for invoking tools, aligned with the Paperclip adapter contract (`invoke/status/cancel`):
    - **Internal** (default): Tool logic runs in-process within the tool-service Python runtime (e.g., Postgres queries, MinIO operations, web search).
    - **HTTP webhook**: Tool delegates to an external HTTP endpoint (e.g., a separate microservice or serverless function). Useful for heavy/isolated tools.
    - **MCP endpoint**: Tool delegates to a [Model Context Protocol](https://modelcontextprotocol.io/) server. Enables standardized tool discovery and invocation for third-party tools. The tool-service acts as an MCP client, translating `ToolRequest` → MCP call → `ToolResponse`.
    - **Process adapter**: Tool spawns a local subprocess (sandboxed). Useful for CLI-based tools or GitHub-sourced tool repos.
    - Transport mode is configured per-tool in the tool manifest. The `(sender_role, tool_name)` policy check applies regardless of transport mode.
21. **Role-based tool access**: tool-service enforces `(sender_role, tool_name)` permission matrix from CommunicationPolicy. Workers cannot call `project.transition`, `approval.*`, `review.start_session`, `sprint.create`, `sprint.activate`. See org-architecture plan §4.2 for `blocked_tools` per role.
22. **Global rate limiting**: per-tool-group token bucket (e.g., `aiolimiter` or custom). Config per group: e.g., `sprint.*` → 20 calls/min, `infra.*` → 10 calls/min. Returns 429 when exceeded.
23. **Concurrency cap**: `asyncio.Semaphore` per tool (same as current registry.py but now global across all containers since there's one tool-service).
24. **Result cache / dedupe**: `hash(tool_name + sorted(kwargs))` → Redis key (`tool_cache:{hash}`) with TTL (configurable per tool, default 30 s). If cache hit, return immediately. This prevents 8 agents from simultaneously web-searching the same query.
    - **Redis isolation**: the tool-service connects as `toolcache_user` (Redis ACL — see Phase 3 step 13) restricted to `GET/SET/DEL/EXPIRE` on `tool_cache:*` key pattern only.
25. **Circuit breaker per tool**: if a tool fails ≥ 3 times in 60 s, circuit opens for 120 s (returns error immediately). After cooldown, one probe call allowed (`HALF_OPEN`). On success → `CLOSED`. Log + metric.
26. Port built-in tools from current codebase (`WebSearchTool`, `FileReadTool`, `FileWriteTool`, `SharedMemoryReadTool`, `SharedMemoryWriteTool`) into tool-service, adapted for request/response over HTTP. Add new project/sprint/KPI/DevOps tools per the org-architecture tool manifest.

---

### Phase 7 — Storage Layer

#### 7a — Postgres (`apps/storage-service/` or direct from agents via pgbouncer)

26. Design shared schema (single `public` schema or `mas` schema):
    - `memory(id BIGSERIAL, agent_id TEXT, key TEXT, value JSONB, updated_at TIMESTAMPTZ, UNIQUE(agent_id, key))`
    - `task_log(task_id UUID PK, agent_id TEXT, parent_task_id UUID NULL, team_id TEXT, status TEXT, input JSONB, output JSONB, budget_snapshot JSONB, trace_id TEXT, created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ)`
    - `artifacts(id BIGSERIAL, agent_id TEXT, path TEXT, metadata JSONB, sha256 TEXT, size_bytes BIGINT, created_at TIMESTAMPTZ)`
    - **12 org-architecture tables** (see org-architecture plan §10 for full DDL): `projects` (with `failure_reason` for FAILED state), `documents`, `review_sessions`, `review_comments`, `approval_gates`, `sprints`, `issues`, `kpi_snapshots`, `agent_profiles`, `dead_letters`, `project_state_history`, `infra_events`
    - **2 system tables**: `system_config` (shutdown/resume state, schedule, watchdog config), `agent_checkpoints` (mid-task progress for shutdown/resume — see Phase 13)
    - **3 capability registry tables** (enables "hire later" as config change — see `Docs/deep-research-report.md` §Capability Registry):
      - `capabilities(id UUID PK, name TEXT UNIQUE, version TEXT, description TEXT, input_schema JSONB, output_schema JSONB, risk_level TEXT, cost_model JSONB, required_tools TEXT[], required_role TEXT, created_at TIMESTAMPTZ)`
      - `worker_registry(id UUID PK, name TEXT UNIQUE, adapter_type TEXT, adapter_config JSONB, sandbox_profile TEXT, capability_ids UUID[], team_id TEXT, status TEXT DEFAULT 'ACTIVE', created_at TIMESTAMPTZ)`
      - `role_capability_map(id BIGSERIAL PK, role TEXT, capability_id UUID REFERENCES capabilities(id), priority INT, constraints JSONB, UNIQUE(role, capability_id))`
    - Indexes: see org-architecture plan §10 for complete index list.
    - Optional: enable Row Level Security with `agent_id = current_setting('app.agent_id')` for hard per-agent isolation.
27. Write Alembic migrations in `migrations/`.
28. Add **pgbouncer** container in compose — transaction pooling mode, `max_client_conn=200`, `default_pool_size=20`. Agents connect to pgbouncer, not directly to Postgres.
29. Implement `AgentStorage` class in `packages/mas-core/` — async wrapper (via `asyncpg` or `sqlalchemy[asyncio]`) for the tables above. Uses `agent_id` filter on every query. Connection string points to pgbouncer.
    - **PgBouncer + asyncpg gotcha (non-optional fix)**: `asyncpg` uses prepared statements by default, which break under pgbouncer's **transaction pooling** mode (connections are shared, so prepared statements from one session leak into another). **Fix**: `AgentStorage.__init__()` must **always** set `statement_cache_size=0` when creating the asyncpg pool — this is not optional, make it the hardcoded default so nobody forgets (e.g., `asyncpg.create_pool(..., statement_cache_size=0)`). If using SQLAlchemy async, pass it via `connect_args={"statement_cache_size": 0}`. PgBouncer 1.21+ has experimental prepared-statement support, but `statement_cache_size=0` is the battle-tested fix.

#### 7b — MinIO

30. Add MinIO container to compose: `minio/minio:latest`, ports 9000 (API) + 9001 (console), volume `minio_data`.
    - **License caveat**: MinIO is **dual-licensed (AGPLv3 + commercial)**. Free to self-host and run internally, but AGPL obligations apply if you distribute or expose it as a service to third parties. For purely internal use this is fine; if shipping externally, evaluate the commercial license.
    - **$0 alternative if AGPL is a concern**: **SeaweedFS** (Apache 2.0 license) provides an S3-compatible gateway and is commonly used as a business-friendly drop-in. The `BlobClient` wrapper in `mas-core` abstracts the S3 API, so swapping MinIO for SeaweedFS (or any S3-compatible store) requires only a config change, no code changes. Document this as a decision gate: evaluate before external distribution.
31. Create initialization script (or use `mc` client in an init container): create bucket `mas-agents` with lifecycle policies.
32. Implement `BlobClient` in `packages/mas-core/` — thin async wrapper around MinIO S3 API (`aioboto3` or `miniopy-async`):
    - `put(agent_id, filename, data) → url` — stores at `s3://mas-agents/{agent_id}/{filename}`, validates path (no traversal).
    - `get(agent_id, filename) → bytes`
    - `list(agent_id, prefix) → list[str]`
    - `delete(agent_id, filename)`
33. Agents use `BlobClient` instead of raw filesystem. MinIO credentials only in env vars, agent_id prefix enforced by the client.

---

### Phase 8 — Agent Types Rewrite (`packages/mas-core/agent_runtime/`)

34. Port `WorkerAgent` from agent_types.py. Changes:
    - Uses `RouterClient` instead of `MessageBus`.
    - Uses `ToolServiceClient` (from `mas-tools-sdk`) instead of local `ToolRegistry`.
    - Passes `BudgetTracker` through to `think()`.
    - Fan-out: checks `budget.subtasks_remaining` before spawning sub-agents.
35. Port `AdminAgent`. Changes:
    - Cross-team comms go through `RouterClient` (router enforces policy).
    - Drop `read_admin_channel()` — router pushes admin messages via the same WS subscription (router knows this agent's role, feeds from `admin:cross_team` stream to its WS).
36. Port `SubAgent` — lightweight, same changes as `WorkerAgent`.
36b. **New: `ExecutiveAgent`** (for COO). Extends `AdminAgent` with:
    - Document lifecycle management (drives PDR → CDR → RR transitions via **deterministic workflow controller** — emits events to `POST /projects/{id}/transition`, does NOT write `projects.state` directly)
    - Review fan-out/fan-in orchestration (parallel REVIEW_REQUEST to N reviewers, aggregate with timeout, **circuit breaker** on ≥2 timeouts → FAILED)
    - **CSO veto handling**: if CSO submits BLOCKER with `veto: true`, halt aggregation → emit `cso_veto` event to controller → SECURITY_BLOCKED
    - Department tasking (sends structured TASK to dept PMs with full context from prior steps)
    - Revision loop handling (re-tasks System dept on Human edit requests)
36c. **New: `CSuiteAgent`** (for CFO, CIO, CHRM, CSO, CTO). Extends `AdminAgent` with:
    - Review capability (receives REVIEW_REQUEST, produces structured ReviewComment)
    - **CSO specialization**: veto power — can submit `severity: BLOCKER, veto: true` to trigger SECURITY_BLOCKED
    - Advisory analysis (domain-specific system prompt per C-Suite role)
    - **CTO specialization**: sprint planning, issue decomposition (including INFRA-type issues → DevOps PM), KPI computation, agent profile updates, **DevOps coordination** (sends INFRA_PROVISIONING signal, waits for INFRA_READY gate before activating dev sprints), historical query with correction factors
    - See `plan-orgArchitecture.prompt.md` §5–6 for full per-agent specifications.

---

### Phase 9 — Team Runner (`apps/team-runner/`)

37. Entrypoint script: reads `TEAM_CONFIG` env var → loads YAML → instantiates `AdminAgent` + `WorkerAgent`s + `SubAgent`s as async tasks → runs until shutdown signal.
37b. **Worker manifest loading**: For each worker defined in the team YAML, the team-runner also loads the corresponding `worker.yaml` from the `workers/` directory (if it exists). The manifest provides:
    - **Sandbox profile**: Determines the container security tier for that worker (standard/restricted/gvisor — see Phase 11).
    - **Capability declarations**: Registered into the capability registry (Postgres) on startup so CTO/CHRM can query available capabilities.
    - **Checkpoint strategy**: Overrides the default checkpoint interval (e.g., `on-step` vs `periodic`) per worker type.
    - **Transport mode**: For non-standard workers (e.g., OCI containers, HTTP webhooks, MCP endpoints), the team-runner delegates invocation to the appropriate adapter instead of running the worker in-process.
    - Workers without a `worker.yaml` use the default in-process Python agent runtime with standard sandbox profile.
38. **Graceful shutdown** (two triggers, same behavior):
    - **SIGTERM** (from `docker compose stop`) or **`SHUTDOWN` message** (from orchestrated shutdown via orchestrator-api):
      1. Set `shutting_down = True` flag — stops XREADGROUP loop from pulling new messages
      2. If agent is mid-task (`think()` loop active):
         - Let the current LLM call finish (do NOT cancel mid-request — wasted tokens)
         - Save checkpoint to Postgres `agent_checkpoints` table (conversation history, iteration, tool results, budget snapshot)
         - NACK the original task message (stays in Redis PEL for redelivery on resume)
      3. If agent is idle: ACK the SHUTDOWN message
      4. Flush all completed ACKs to router
      5. Call `POST /system/shutdown-ack` on orchestrator-api with `{team_id, agent_id}` (orchestrator-api tracks shutdown completion; no stream routing needed)
      6. Exit cleanly
    - **Compose config**: all team-runner services use `stop_grace_period: 60s` to give agents time to checkpoint before SIGKILL
    - See Phase 13 for the full shutdown/resume protocol
39. **Startup resume**: on boot, after connecting to router, team-runner:
    1. First reads pending PEL entries (`XREADGROUP ... 0`) — these are messages that were in-flight before shutdown
    2. For `RESUME` directives (sent by orchestrator on startup): check `agent_checkpoints` table for saved progress → restore and continue `think()` from last checkpoint
    3. If no checkpoint exists: process the re-delivered message normally (start fresh)
    4. After successful task completion: delete the checkpoint row
40. Health endpoint: small HTTP server (or just TCP liveness probe) for Docker healthcheck.

---

### Phase 10 — Orchestrator API (`apps/orchestrator-api/`)

40. Port from current orchestrator.py. FastAPI app with:
    - `POST /tasks` → publishes `ADMIN_TASK` to correct team admin **via the router**.
    - `POST /tasks/cross-team` → same, with cross-team routing.
    - `GET /tasks/{task_id}` → query `task_log` in Postgres.
    - `GET /health` — checks router, tool-service, Postgres, MinIO.
    - `GET /teams` — reads team configs (mounted volume or DB).
    - **Deterministic Workflow Controller** (lives inside this service — see org-architecture plan §11.2):
      - `POST /projects/{id}/transition` — accepts `{event, actor_id, context}`, validates `(current_state, event)` against transition table, persists new state atomically to Postgres `projects` table + `project_state_history`, publishes `SYSTEM_EVENT` to relevant stream. This is the **sole writer** of `projects.state`.
      - `GET /projects/{id}/allowed-transitions` — returns valid events for current state.
      - `GET /projects/{id}/state-history` — audit log of all transitions.
      - **Watchdog cron job**: runs every 60 s, checks for projects stuck in same state > 1 hour → fires `watchdog_timeout` event → FAILED.
    - **Human-in-the-Loop endpoints** (see org-architecture plan §9):
      - `POST /projects` — Human creates a project request (triggers CEO)
      - `GET /projects/{id}` — Project status + current state
      - `GET /projects/{id}/pending-decisions` — What decisions need Human input
      - `POST /projects/{id}/decisions` — Human submits approval/reject/edit
      - `GET /projects/{id}/documents` — List project documents (PDR, CDR, RR)
      - `GET /projects/{id}/documents/{doc_id}` — Document details + MinIO download link
      - `GET /projects/{id}/feasibility` — Feasibility report from C-Suite
      - `GET /projects/{id}/sprints` — Sprint status and progress
    - **FAILED state management**:
      - `POST /projects/{id}/retry` — Reset FAILED project to last safe state
      - `POST /projects/{id}/archive` — Permanently archive a FAILED project
    - **Dead-letter inspection**:
      - `GET /dead-letters` — List DLQ entries (paginated)
      - `GET /dead-letters/{id}` — Inspect specific dead letter
      - `POST /dead-letters/{id}/replay` — Re-inject into target stream
    - **System lifecycle endpoints** (see Phase 13 for full protocol):
      - `POST /system/shutdown` — Orchestrated shutdown: sets `system_state = SHUTTING_DOWN`, publishes `SHUTDOWN` to all 11 streams, waits for `SHUTDOWN_ACK` from each team (45 s timeout), records `shutdown_at`, sets state to `STOPPED`
      - `POST /system/resume` — Manual resume trigger (also runs automatically on startup): loads active projects, re-publishes `DIRECTIVE(action=RESUME)` to responsible teams, starts watchdog with grace period
      - `GET /system/status` — Returns `{ state: RUNNING | SHUTTING_DOWN | STARTING | STOPPED, active_projects, uptime, schedule }`
      - `PUT /system/schedule` — Configure scheduled operation (active hours, timezone, days, auto-shutdown/resume)
41. The orchestrator no longer instantiates agents or holds a `ToolRegistry`. It's a thin API + deterministic controller that talks to the router.
42. **On startup**, the orchestrator-api runs the resume sequence (Phase 13.3) before accepting requests: load active projects → re-publish work messages → start watchdog with 5-minute grace period (prevents false FAILED states after downtime).

---

### Phase 11 — Docker Compose (`infra/compose/`)

42. `docker-compose.yml` services:

    | Service | Image/Build | Ports | Notes |
    |---|---|---|---|
    | `redis` | `redis:7.2-alpine` | 6379 | ACL config + **AOF persistence** (`appendonly yes`, `appendfsync everysec`). Custom `redis.conf` mounted. Critical for stream/PEL durability across restarts. |
    | `postgres` | `postgres:16-alpine` | 5432 | — |
    | `pgbouncer` | `bitnami/pgbouncer` | 6432 | Transaction pooling |
    | `minio` | `minio/minio` | 9000, 9001 | Bucket: `mas-agents` |
    | `message-router` | `Dockerfile.router` | 8001 | Only service with `REDIS_URL` |
    | `tool-service` | `Dockerfile.tools` | 8002 | Global rate limits |
    | `orchestrator-api` | `Dockerfile.orchestrator` | 8000 | Public-facing |
    | `team_ceo` | `Dockerfile.team-runner` | — | `TEAM_ID=exec_ceo` |
    | `team_coo` | `Dockerfile.team-runner` | — | `TEAM_ID=exec_coo` |
    | `team_cfo` | `Dockerfile.team-runner` | — | `TEAM_ID=office_cfo` |
    | `team_cio` | `Dockerfile.team-runner` | — | `TEAM_ID=office_cio` |
    | `team_chrm` | `Dockerfile.team-runner` | — | `TEAM_ID=office_chrm` |
    | `team_cso` | `Dockerfile.team-runner` | — | `TEAM_ID=office_cso` |
    | `team_cto` | `Dockerfile.team-runner` | — | `TEAM_ID=office_cto` |
    | `team_production` | `Dockerfile.team-runner` | — | `TEAM_ID=dept_production` |
    | `team_system` | `Dockerfile.team-runner` | — | `TEAM_ID=dept_system` |
    | `team_qa` | `Dockerfile.team-runner` | — | `TEAM_ID=dept_qa` |
    | `team_devops` | `Dockerfile.team-runner` | — | `TEAM_ID=dept_devops` **v1 critical path** |

43. Key env var changes: team containers get `ROUTER_URL`, `TOOL_SERVICE_URL`, `PGBOUNCER_DSN`, `MINIO_ENDPOINT`/`MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY`. They do **not** get `REDIS_URL`.
44. Use `mem_limit` and `cpus` (not `deploy.resources.limits`) for Compose compatibility.
45. **Network segmentation**: define separate Docker networks to reduce lateral movement if a container is compromised:
    - `public` network: only `orchestrator-api` attached (exposed to host)
    - `internal` network: `message-router`, `tool-service`, `team_*`, `pgbouncer`, `minio`, `redis` (no host exposure)
    - `orchestrator-api` bridges both networks (public + internal) to reach the router and storage
    - Redis and Postgres ports are **not** published to the host in production compose (only in dev override)
45b. **Sandbox tiers for worker isolation** (informed by `Docs/deep-research-report.md` security analysis):
    - **Tier 0 — Standard** (default for all built-in MAS agents): Default Docker container isolation. Sufficient for trusted in-process Python agents.
    - **Tier 1 — Restricted** (for workers that execute generated code or handle untrusted input): Default seccomp profile applied, all Linux capabilities dropped (`--cap-drop=ALL`), read-only root filesystem (`--read-only`), writable tmpfs for `/tmp` only. Network egress defaults to **deny-all** with an allowlist per capability (e.g., allow `api.github.com:443` + `pypi.org:443` for build workers).
    - **Tier 2 — gVisor** (for GitHub-sourced / untrusted workers): Run under [gVisor](https://gvisor.dev/) runtime (`runsc`) for stronger kernel isolation. Protects against certain kernel exploit classes. Adds ~10% performance overhead. Use when pulling worker code from external repos.
    - **Tier 3 — Firecracker** (future, for maximum isolation): [Firecracker](https://firecracker-microvm.github.io/) microVM per worker. Highest isolation boundary. Reserve for adversarial/untrusted workloads. Requires KVM support on host.
    - Sandbox tier is specified per worker in `worker.yaml` (`sandbox.profile` field) and enforced by the team-runner when spawning worker containers.
    - **Egress allowlist**: Each `worker.yaml` declares allowed egress domains. The team-runner configures iptables/nftables rules (or Kubernetes NetworkPolicy) to enforce. Default for Tier 1+: deny all egress except explicitly allowed domains.
    - Profiles stored in `infra/sandbox/` directory for reuse across workers.
46. `docker-compose.dev.yml` adds: `redis_ui`, `pgadmin`, `minio-console` (already on :9001), and optionally publishes Redis/Postgres ports for local debugging.

---

### Phase 12 — Observability

47. Add `trace_id` (UUID) generation at task entry points (API, cross-team). Propagate through all messages. Log with every structured log line.
48. Add Prometheus metrics (via `prometheus-fastapi-instrumentator` or `aioprometheus`):
    - `mas_messages_total{direction, team, msg_type}`
    - `mas_tool_calls_total{tool_name, status}`
    - `mas_llm_calls_total{model, agent_id}`
    - `mas_budget_exhausted_total{agent_id, budget_type}`
    - `mas_dlq_depth{stream}` — dead-letter count in Postgres
    - `mas_project_state{project_id, state}` — current state per project
    - `mas_review_circuit_open{project_id}` — counter of circuit breaker activations
    - `mas_infra_lead_time{project_id}` — `infra_ready_at - infra_requested_at`
    - `mas_agent_correction_factor{agent_id}` — per-agent estimation drift
    - `mas_tool_circuit_state{tool_name}` — CLOSED/OPEN/HALF_OPEN per tool
49. Optional: add Grafana + Prometheus containers in dev compose profile.

---

## Verification

- **Unit tests**: each package (`mas-core`, `mas-tools-sdk`) gets pytest tests. Mock Redis, Postgres, MinIO, LLM provider.
- **Integration tests**: docker compose up → submit a task via API → verify it flows through router → team-runner → tool-service → storage → result returned. Check `task_log` in Postgres.
- **Policy tests**: verify `CommunicationPolicy` rejects worker→cross-team, allows admin→cross-team, allows orchestrator→anywhere. Verify tool-permission gating: worker calls `project.transition` → 403.
- **DLQ test**: kill an agent mid-task → verify message appears in PEL → after 3 XAUTOCLAIM reclaims, verify row in `dead_letters` Postgres table + `SYSTEM_EVENT` published to CEO stream.
- **Budget test**: submit task with `max_llm_calls=2` → verify agent stops after 2 LLM calls and returns partial result.
- **Restart test**: `docker compose restart team_production` → verify no messages lost (consumer group replays pending on reconnect).
- **Rate limit test**: fire 100 concurrent tool calls → verify tool-service throttles correctly and returns 429 or queued results.
- **Publish idempotency test**: publish the same `MessageEnvelope` (same `message_id`) twice → verify only one stream entry is created and the second publish returns the original entry ID.
- **Consume idempotency test**: deliver the same message to a handler twice (via XAUTOCLAIM race) → verify LRU marker prevents double processing.
- **XAUTOCLAIM reclaim test**: leave a message unacked for >120 s → verify XAUTOCLAIM picks it up and increments `retry_count`.
- **Controller transition test**: call `POST /projects/{id}/transition` with valid event → verify state change + `project_state_history` row. Call with invalid event → verify 400 error, state unchanged.
- **Review circuit breaker test**: start review session → 2 reviewers timeout → verify project → FAILED(REVIEW_CIRCUIT_OPEN).
- **CSO veto test**: CSO submits BLOCKER with `veto: true` → verify project → SECURITY_BLOCKED. Test CEO override unblocks.
- **INFRA_READY gate test**: after SPRINT_PLANNING, verify dev sprints cannot start until DevOps PM sends INFRA_READY.
- **FAILED retry/archive test**: set project to FAILED → call `/retry` → verify restored. Call `/archive` → verify ARCHIVED.
- **Tool circuit breaker test**: fail tool 3× in 60 s → assert OPEN. After 120 s → HALF_OPEN probe.
- **18-container compose test**: `docker compose up` on 8 GB / 4 core machine → verify all containers healthy within 90 s.
- **Graceful shutdown test**: start a project mid-PDR_CREATION → call `POST /system/shutdown` → verify agent saves checkpoint to `agent_checkpoints` table → verify all 11 `SHUTDOWN_ACK` received → verify `system_state = STOPPED`.
- **Resume after shutdown test**: after graceful shutdown with active project in PDR_CREATION → `docker compose up` → verify orchestrator publishes `DIRECTIVE(action=RESUME)` → agent loads checkpoint → continues `think()` from saved iteration → task completes.
- **Cold crash resume test**: kill all containers with `docker compose kill` (no graceful shutdown) → `docker compose up` → verify projects resume (messages redelivered from PEL; no checkpoint so tasks restart fresh but no data loss).
- **Watchdog grace period test**: shutdown system for 2 hours → restart → verify watchdog does NOT mark active projects as FAILED during the 5-min grace period → verify downtime is excluded from watchdog timeout calculation.
- **Scheduled operation test**: configure schedule `active_hours: 08:00-09:00` → verify auto-shutdown fires at 09:00 → verify auto-resume fires at 08:00 next day → verify project resumes correctly.
- **Redis AOF durability test**: write 100 messages to streams → `docker compose restart redis` → verify all 100 messages still in streams (AOF recovery).
- **Worker manifest test**: create a `worker.yaml` with `transport: http` → start team-runner → verify worker is registered in capability registry (Postgres) → verify tool calls are routed to the HTTP endpoint.
- **Capability registry test**: register 3 capabilities → `POST /capabilities/search` with body `{"name": "implement_feature"}` → verify correct workers returned. Verify CTO can query available capabilities per role.
- **Sandbox tier test**: start a worker with `sandbox.profile: restricted` → verify `--cap-drop=ALL` and `--read-only` are applied → verify egress to non-allowlisted domains is blocked.
- **MCP tool transport test**: configure a tool with `transport: mcp` pointing to a mock MCP server → call `POST /tools/{name}/run` → verify request is translated to MCP protocol and response is translated back.

---

## Decisions

- **Router transport**: HTTP+WS hybrid (HTTP for publish, WS for subscribe)
- **Agent hierarchy**: Corporate C-Suite + Departments (CEO → COO → C-Suite → Dept PMs → Workers) — see org-architecture plan
- **Workflow controller**: Deterministic engine in orchestrator-api; agents emit events, controller validates + persists atomically (not agent-driven state changes)
- **Workflow model**: 14-step state machine (INIT → ... → COMPLETED) with PDR/CDR/RR lifecycle, INFRA_PROVISIONING, RETROSPECTIVE, KPI_PERSISTENCE steps
- **FAILED state**: Explicit with `failure_reason` enum (WATCHDOG_TIMEOUT, REVIEW_CIRCUIT_OPEN, INFRA_FAILED, UNRECOVERABLE_ERROR); recoverable via retry or archive
- **CSO veto**: SECURITY_BLOCKED sub-state; only CEO override (with audited justification) can resume
- **DevOps department**: v1 critical path (not future); INFRA_READY gate required before dev sprints
- **Message protocol**: Single unified `MessageEnvelope` (replaces dual Message + RouterEnvelope); 64 KB payload limit + BlobRef for large payloads
- **Human-in-the-loop**: API polling (v1), webhook push (v2)
- **KPI learning**: LLM in-context from Postgres historical KPI data + per-agent `correction_factor` profiles (no separate ML model)
- **Review circuit breaker**: ≥2 reviewer timeouts in same session → FAILED (don't proceed with partial reviews)
- **Cross-team enforcement**: Message Router (option A), not Redis ACL; 6-role policy with chain-of-command + tool-permission gating
- **Tool service**: **7 tool groups** (Workflow, Document, Review, Sprint/Issue, DevOps, Capability, KPI/Utility), role-gated access, per-tool circuit breakers, per-group token bucket rate limits
- **Blob storage**: No blob-service container; use MinIO (local S3) + thin `BlobClient` library wrapper in `mas-core`
- **DB layout**: Single schema, tables keyed by `agent_id`, optional RLS — not schema-per-agent
- **LLM gateway**: OpenAI-compatible API targeting custom multi-provider proxy
- **Migration strategy**: Full rewrite into new `mas/` repo structure, **11 teams** (7 C-Suite offices + **4** departments incl. DevOps)
- **Connection pooling**: pgbouncer (not raw asyncpg pools); `statement_cache_size=0` mandatory
- **Delivery semantics**: At-least-once delivery (Redis Streams PEL + XACK) with effectively-once processing (publish dedupe 300 s TTL + consume LRU set)
- **Redis isolation**: Router and tool-service are the only containers with Redis credentials (separate ACL users with restricted commands + key patterns); agents are fully separated
- **Redis reclaim**: XAUTOCLAIM (Redis 6.2+) with 120 s idle timeout; DLQ → Postgres `dead_letters` table (not Redis DLQ stream)
- **Network segmentation**: Public/internal Docker network split; Redis/Postgres not exposed to host in production
- **Shutdown/resume protocol**: Orchestrated via `POST /system/shutdown` → `SHUTDOWN` broadcast → agent checkpoint → `SHUTDOWN_ACK` → clean exit. Resume via controller re-publishing work messages on startup. Agent checkpoints in Postgres survive any restart.
- **Agent checkpoints**: Mid-task progress (LLM conversation, tool results, iteration count, budget) saved to Postgres `agent_checkpoints` table after each LLM call. On resume, agent restores from checkpoint instead of restarting from scratch. Deleted after task completion.
- **Redis persistence**: AOF enabled (`appendonly yes`, `appendfsync everysec`) — at most 1 s data loss on hard crash, zero on graceful stop. `maxmemory-policy noeviction` prevents silent stream data loss.
- **Watchdog grace period**: After system boot, watchdog ignores projects for 5 min (configurable). Downtime between `shutdown_at` and `boot_at` is excluded from timeout calculation. Prevents false FAILED states after reboot.
- **Scheduled operation**: Optional working hours (`SYSTEM_SCHEDULE` config). Cron-driven auto-shutdown at end of active window, auto-resume at start. Watchdog is schedule-aware.

---

## Implementation order (recommended)

The phases above are numbered for reference but the optimal build order is:

1. **Phase 0 + 1** — Scaffold + protocols (unified `MessageEnvelope`, `BlobRef`, `AgentRole`, `WorkerManifest`, `CapabilityDef`, all domain models from org-architecture §3)
2. **Phase 2 + 5** — Policy engine (6-role hierarchy + tool-permission gating) + LLM gateway (no external deps, unit-testable)
3. **Phase 4b** — Deterministic Workflow Controller (transition table, watchdog, FAILED state recovery — org-architecture §11.2)
4. **Phase 3** — Message Router (Redis Streams hardening: XAUTOCLAIM, DLQ→Postgres, publish/consume idempotency, stream trimming, Redis ACL)
5. **Phase 6** — Tool Service (**7 tool groups**: Workflow, Document, Review, Sprint/Issue, DevOps, Capability, KPI/Utility; role-gated, circuit breakers, token bucket rate limits, **multi-transport**: internal + HTTP + MCP + process)
6. **Phase 7** — Storage (Postgres base tables + **12 org-architecture tables** + `system_config` + `agent_checkpoints` + **3 capability registry tables** = **17 new tables** (20 total) + PgBouncer `statement_cache_size=0`; MinIO + `/retrospectives/` path)
7. **Phase 4 + 8** — Agent runtime + all 5 agent types (WorkerAgent, AdminAgent, SubAgent, ExecutiveAgent [controller-aware, circuit breaker], CSuiteAgent [CSO veto, CTO DevOps coordination]). Agent runtime includes **structured checkpoint** save/restore logic.
8. **Phase 9 + 10** — Team runner (11 team YAMLs incl. DevOps, **worker manifest loading**, checkpoint-aware graceful shutdown) + orchestrator API (controller endpoints, DLQ mgmt, Human-in-the-Loop, FAILED retry/archive, **system shutdown/resume endpoints**)
9. **Phase 11** — Docker Compose (**18 containers** total: 7 infra + 11 teams incl. DevOps). Redis AOF config. `stop_grace_period: 60s` on team containers. **Sandbox profiles** in `infra/sandbox/`.
10. **Phase 12** — Observability + KPI dashboards + DLQ alerts + tool circuit breaker metrics
11. **Phase 13** — Shutdown/resume protocol (orchestrated shutdown cascade, resume sequence, agent checkpoint integration, scheduled operation cron)
12. **Phase 14** — *(Optional, v1.1+)* Paperclip Integration (event bridge, adapter mapping, credential delegation, UI hookup)

Each phase is independently testable. You can run the router + one team-runner against real Redis/Postgres before wiring up compose.

---

### Phase 13 — Shutdown, Resume & Scheduled Operation

> **Problem**: The system may run on a developer machine or a server that is only powered on X hours/day. A clean shutdown must preserve all in-flight progress so that `docker compose up` after reboot resumes every project from where it stopped — no lost work, no false FAILED states.

#### 13.1 Why This Is Already Mostly Safe

The architecture is built on durable storage:

| Layer | Durability | On cold reboot |
|-------|-----------|----------------|
| **Project state** | Postgres `projects` table (controller is sole writer) | Survives. State is exactly where it was. |
| **State history** | Postgres `project_state_history` | Survives. Full audit trail intact. |
| **Unprocessed messages** | Redis Streams PEL (Pending Entries List) | Survives **if Redis AOF is enabled** (see §13.5). On agent reconnect, `XREADGROUP ... 0` replays pending. |
| **Documents / artifacts** | MinIO (Docker volume) | Survives. |
| **All DB rows** | Postgres (Docker volume) | Survives. |
| **In-memory caches** | LRU dedupe set (1 000 entries), tool-cache Redis keys | Lost. Harmless — LRU is optional optimization; tool-cache has 30 s TTL anyway. |

The **one real gap** is: when an agent was mid-task (e.g., 3 LLM calls into a `think()` loop), that intermediate progress is lost. The message stays in PEL and will be redelivered, but the agent starts the task from scratch. Phase 13 adds **agent checkpoints** to fix this, plus an orchestrated shutdown/resume protocol.

#### 13.2 Orchestrated Shutdown Protocol

Add a coordinated shutdown sequence so agents can finish or checkpoint their current work before containers stop.

**Orchestrator-api endpoints:**

```
POST /system/shutdown
  → Sets `system_state = SHUTTING_DOWN` in Postgres (system_config table)
  → Stops accepting new project creation (returns 503)
  → Publishes SHUTDOWN MessageEnvelope to ALL 11 team streams
  → Returns { status: "shutdown_initiated", active_projects: N }

GET /system/status
  → Returns { state: "RUNNING" | "SHUTTING_DOWN" | "STARTING" | "STOPPED" }
```

**Shutdown cascade:**

```
 1. Human (or cron) calls POST /system/shutdown
 2. Orchestrator-api → sets system_state = SHUTTING_DOWN in Postgres
 3. Orchestrator-api → publishes SHUTDOWN to all 11 team streams
 4. Each team-runner receives SHUTDOWN:
    a. Stop accepting new messages from stream (exit XREADGROUP loop)
    b. If mid-task:
       - Complete current LLM call (don't cancel mid-request)
       - Save checkpoint to Postgres (agent_checkpoints table)
       - NACK the original message (stays in PEL for resume)
    c. If idle: just ACK the SHUTDOWN message
    d. Publish SHUTDOWN_ACK back to orchestrator stream
    e. Exit cleanly
 5. Orchestrator-api → after all 11 SHUTDOWN_ACKs received (or 45 s timeout):
    a. Pause watchdog cron (so it doesn't fire during downtime)
    b. Set system_state = STOPPED, record shutdown_at timestamp
 6. docker compose stop (SIGTERM) → containers exit (already drained)
```

**`docker compose stop` fallback**: If shutdown is not called via API (e.g., user runs `docker compose down` directly), SIGTERM still triggers the team-runner's graceful handler which saves checkpoints. Less coordinated but still safe.

**Compose config** — set `stop_grace_period` to give agents time:

```yaml
# docker-compose.yml — all team-runner containers
x-team-defaults: &team-defaults
  stop_grace_period: 60s   # 60 s for agents to checkpoint before SIGKILL
```

#### 13.3 Resume Protocol (Startup)

When the system boots after a shutdown or crash, the orchestrator-api runs a **resume sequence** before accepting new requests.

**Orchestrator-api startup sequence:**

```python
async def on_startup():
    """Runs once when orchestrator-api starts."""
    
    # 1. Mark system as starting
    await db.execute("UPDATE system_config SET state = 'STARTING', boot_at = now()")
    
    # 2. Disable watchdog grace period (don't FAIL projects that were paused)
    #    Watchdog ignores projects whose updated_at < shutdown_at
    shutdown_at = await db.fetchval("SELECT shutdown_at FROM system_config")
    
    # 3. Load all active projects (non-terminal states)
    active = await db.fetch("""
        SELECT id, state, updated_at FROM projects 
        WHERE state NOT IN ('COMPLETED', 'ARCHIVED', 'FAILED')
    """)
    
    # 4. For each active project, re-publish the work message
    #    The controller knows the current state and which agent is responsible.
    for project in active:
        responsible_team = CONTROLLER.get_responsible_team(project.state)
        await router.publish(MessageEnvelope(
            msg_type=MessageType.DIRECTIVE,
            sender_id="orchestrator",
            sender_role=AgentRole.ORCHESTRATOR,
            recipient_team=responsible_team,
            project_id=project.id,
            payload={
                "action": "RESUME",
                "state": project.state,
                "context": "System restart — resume from last committed state"
            }
        ))
    
    # 5. Clear shutdown flags, start watchdog with grace period
    await db.execute("""
        UPDATE system_config 
        SET state = 'RUNNING', boot_at = now()
    """)
    
    # 6. Watchdog starts BUT skips the first WATCHDOG_GRACE_PERIOD (default 5 min)
    #    to give agents time to reconnect, process pending PEL messages, and resume
    asyncio.create_task(watchdog_loop(grace_period_seconds=300))
```

**Agent resume behavior** (in team-runner):

```python
async def handle_message(envelope: MessageEnvelope):
    if envelope.payload.get("action") == "RESUME":
        # 1. Check for checkpoint in Postgres
        checkpoint = await db.fetchrow(
            "SELECT * FROM agent_checkpoints WHERE project_id = $1 AND agent_id = $2",
            envelope.project_id, self.agent_id
        )
        if checkpoint:
            # 2. Restore intermediate state (messages history, partial results)
            self.restore_from_checkpoint(checkpoint.data)
            # 3. Continue think() loop from where it stopped
            await self.think(resume=True)
            # 4. Delete checkpoint after successful completion
            await db.execute(
                "DELETE FROM agent_checkpoints WHERE id = $1", checkpoint.id
            )
        else:
            # No checkpoint — process normally (task starts fresh)
            await self.handle_task(envelope)
```

#### 13.4 Agent Checkpoints

To avoid losing progress when an agent is mid-task, agents periodically save checkpoints during long-running `think()` loops.

**Checkpoint strategy:**

- **When to checkpoint**: After each completed LLM call + tool result pair in the `think()` loop. Not after every micro-step (too expensive), but after each meaningful unit of work.
- **What to save**: The full `messages` list (chat history sent to LLM), accumulated tool results, current iteration count, budget state, and the original task envelope.
- **Where to save**: Postgres `agent_checkpoints` table (survives any restart).
- **When to delete**: After the task completes successfully (or after DLQ).

```python
# In AgentBase.think() loop:
async def think(self, resume: bool = False):
    if resume and self._checkpoint:
        messages = self._checkpoint["messages"]
        iteration = self._checkpoint["iteration"]
    else:
        messages = [system_prompt, user_message]
        iteration = 0
    
    while iteration < max_iterations:
        response = await self.llm.chat(messages)
        # ... handle tool calls, append results to messages ...
        iteration += 1
        
        # Checkpoint every N iterations (default: every iteration for safety)
        if iteration % CHECKPOINT_INTERVAL == 0:
            await self.save_checkpoint({
                "messages": messages,           # Full LLM conversation so far
                "iteration": iteration,
                "tool_results": accumulated_results,
                "budget_snapshot": self.budget.snapshot(),
                "task_envelope_id": self.current_envelope.message_id,
            })
    
    # Task complete — remove checkpoint
    await self.delete_checkpoint()
```

**Checkpoint cost**: One Postgres INSERT per LLM call (~1 KB–10 KB of JSON). At 10–20 LLM calls per task, this is negligible compared to LLM API latency.

#### 13.5 Redis Persistence Configuration

Redis must be configured for **AOF (Append Only File)** persistence to guarantee that stream entries and PEL state survive a restart. The default RDB-only mode can lose the last few minutes of data.

```yaml
# infra/compose/redis.conf (mounted into Redis container)
appendonly yes
appendfsync everysec          # Flush AOF to disk every second (good balance)
save 900 1                    # RDB snapshot as backup (every 15 min if ≥1 write)
save 300 10
save 60 10000
maxmemory-policy noeviction   # Never evict stream data — fail writes instead
```

**Compose mount:**
```yaml
redis:
  image: redis:7.2-alpine
  command: redis-server /usr/local/etc/redis/redis.conf
  volumes:
    - redis_data:/data
    - ./infra/compose/redis.conf:/usr/local/etc/redis/redis.conf:ro
```

With `appendonly yes` + `appendfsync everysec`, at most 1 second of data can be lost on a hard crash (power cut). For a graceful `docker compose stop`, zero data loss — Redis flushes on SIGTERM.

#### 13.6 Scheduled Operation (Working Hours)

For systems that should only run during specific hours (e.g., dev machine, cost-controlled LLM usage):

**Configuration** (Postgres `system_config` table or env var):

```python
# System schedule — when the MAS is active
SYSTEM_SCHEDULE = {
    "enabled": true,
    "active_hours": "08:00-22:00",   # Local time window
    "timezone": "Europe/Paris",       # IANA timezone
    "days": ["mon", "tue", "wed", "thu", "fri"],  # Active days
    "auto_shutdown": true,             # Auto-shutdown at end of active window
    "auto_resume": true,               # Auto-resume at start of active window
}
```

**Implementation** — a cron job in the orchestrator-api:

```python
# Runs every minute
async def schedule_check():
    config = await load_schedule_config()
    if not config["enabled"]:
        return
    
    now = datetime.now(ZoneInfo(config["timezone"]))
    is_active_time = (
        now.strftime("%a").lower() in config["days"]
        and parse_time(config["active_hours"].split("-")[0]) <= now.time()
        <= parse_time(config["active_hours"].split("-")[1])
    )
    
    system_state = await get_system_state()
    
    if not is_active_time and system_state == "RUNNING" and config["auto_shutdown"]:
        await initiate_shutdown(reason="scheduled_off_hours")
    
    elif is_active_time and system_state == "STOPPED" and config["auto_resume"]:
        await initiate_resume()
```

**Important**: The watchdog must be schedule-aware. Time spent in `STOPPED` state does **not** count toward the 1-hour watchdog timeout:

```python
# Watchdog calculation:
elapsed = now - max(project.updated_at, system_config.boot_at)
# NOT: elapsed = now - project.updated_at
# This prevents false FAILED states after overnight shutdown
```

#### 13.7 System Config Table

Add to Postgres schema (Phase 7):

```sql
CREATE TABLE system_config (
    key           TEXT PRIMARY KEY,
    value         JSONB NOT NULL,
    updated_at    TIMESTAMPTZ DEFAULT now()
);

-- Bootstrap rows:
INSERT INTO system_config VALUES 
    ('system_state', '"RUNNING"'),
    ('shutdown_at', 'null'),
    ('boot_at', 'null'),
    ('schedule', '{"enabled": false}'),
    ('watchdog_grace_seconds', '300');

CREATE TABLE agent_checkpoints (
    id            BIGSERIAL PRIMARY KEY,
    agent_id      TEXT NOT NULL,
    project_id    UUID NOT NULL,
    task_message_id UUID NOT NULL,
    checkpoint_data JSONB NOT NULL,     -- messages, iteration, tool_results, budget
    created_at    TIMESTAMPTZ DEFAULT now(),
    UNIQUE(agent_id, project_id)        -- one checkpoint per agent per project
);

CREATE INDEX idx_checkpoints_project ON agent_checkpoints(project_id);
```

---

### Phase 14 — Paperclip Integration (Optional)

> **Context**: The [Paperclip](https://github.com/paperclipai/paperclip) framework provides an open-source "zero-human company" control plane with company modeling, org charts, issue/task tracking, approval flows, budget enforcement, audit logs, and agent adapters. MAS already implements the execution plane (durable messaging, workflow controller, tool gateway, sandboxing). Integrating both yields a full-stack "company simulation" with a UI, audit trail, and human-in-the-loop governance — without duplicating functionality. See `Docs/deep-research-report.md` for the full analysis.
>
> **This phase is optional for v1 standalone operation. Plan for v1.1+ integration.**

#### 14.1 Paperclip ↔ MAS Mapping

| Paperclip Primitive | MAS Equivalent | Integration Action |
|--------------------|--------------|--------------------|
| Company + Org Chart | Team YAMLs + Agent hierarchy | Sync team structure to Paperclip on startup |
| Issues/Tasks | MessageEnvelope (TASK, ADMIN_TASK) | Map workflow state transitions → Paperclip issue create/update/comment |
| Approvals | approval_gates table + Human-in-the-Loop API | Paperclip approval flow triggers `POST /projects/{id}/decisions` |
| Budgets | TaskBudget + BudgetTracker | Sync budget state to Paperclip cost events |
| Activity Log | project_state_history table | Write MAS transitions as Paperclip activity log entries |
| Secrets | .env + Docker secrets | Read secrets from Paperclip secret provider (v1.1+) |
| Adapters (process/http) | Worker manifests (worker.yaml) | Map MAS `worker.yaml` transport modes to Paperclip adapter config |
| Agent Heartbeats | Router WS PING/PONG | Emit Paperclip heartbeat_runs from team-runner |

#### 14.2 Integration Approach

1. **Event bridge service** (new, lightweight): A small Python service (or module inside orchestrator-api) that subscribes to `SYSTEM_EVENT` messages on the orchestrator stream and translates them to Paperclip API calls:
   - Workflow state transition → Create/update Paperclip issue + comment with state context
   - Document submission → Attach document ref to Paperclip issue
   - Approval gate → Create Paperclip approval request
   - DLQ entry → Create Paperclip alert issue
   - Sprint progress → Update Paperclip issue board

2. **Paperclip adapter for MAS workers**: Implement a Paperclip `http` adapter that maps to the team-runner's worker invocation. Paperclip's `invoke/status/cancel` contract maps cleanly to MAS's task/result/shutdown message types.

3. **Credential delegation**: Use Paperclip's `company_secrets` table (with local-encrypted provider) instead of plain `.env` files. The event bridge reads secret refs from Paperclip and injects them as environment variables into team-runner containers.

#### 14.3 What This Enables

- **Paperclip UI** becomes the single pane of glass for project oversight, approval, and audit
- **"Hire later" workflow**: Create a new worker in Paperclip UI → worker.yaml manifest auto-synced → capability registered → CTO can assign work to the new capability
- **Human board override**: Paperclip's board governance can override MAS workflow decisions (e.g., force-archive, budget freeze)
- **Cost visibility**: Paperclip's budget dashboard shows per-agent, per-project, per-sprint LLM costs (fed from MAS BudgetTracker)
