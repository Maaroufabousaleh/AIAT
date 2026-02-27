# MAS Architecture — Manual Actions Per Phase

**TL;DR**: This companion document lists every action **you** (the human developer/operator) must perform manually for each implementation phase. Actions are things that cannot be automated by an AI coding agent — decisions requiring your judgment, credentials/secrets you must create, external service configuration, hardware/network setup, and testing that requires real infrastructure. Everything else (writing code, creating files, running scripts) is handled by the AI agent.

> **Companion documents**:
> - **`plan-masArchitectureUpgrade.prompt.md`** — Infrastructure plan (Router, Redis, Tool Service, Storage, Compose, Observability, Shutdown/Resume)
> - **`plan-orgArchitecture.prompt.md`** — Organizational architecture (Agent hierarchy, Workflow, Protocols, Policy, Teams)

---

## How to Read This Document

Each phase has three sections:

- **Decisions You Must Make** — Open questions where your preference determines the implementation direction. Provide your answer before or during that phase.
- **Manual Actions** — Steps you must perform yourself (credentials, infrastructure, external services, etc.).
- **Verification You Must Run** — Tests or checks that require real infrastructure, Docker, or your LLM provider.

Items marked with ⚠️ are **blocking** — the phase cannot proceed without them.

---

## Phase 0 + 1 — Repo Scaffold & Protocols

### Decisions You Must Make

1. ⚠️ **Package manager**: `uv` (recommended — fast, supports workspaces natively) or `hatch`? This determines `pyproject.toml` workspace format.
2. ⚠️ **Python version**: Target `3.11` or `3.12`? Affects base Docker image and type hint syntax.
3. **Project ID format**: UUIDs are specified in the plan. If you have an existing ID scheme, say so now.

### Manual Actions

1. ⚠️ **Create the virtual environment** — Run `uv venv` or `python -m venv .venv` and activate it. The AI agent cannot persist environment state across sessions.
2. ⚠️ **Install the workspace** — After the agent creates `pyproject.toml` files, run `uv pip install -e "packages/mas-core[dev]"` (or equivalent) to verify local package resolution works.
3. **Review generated models** — The agent will create Pydantic models (`MessageEnvelope`, `BlobRef`, `AgentRole`, `TaskBudget`, etc.). Read them and confirm the field names/types match your mental model. This is the canonical schema everything else builds on — changes later are expensive.

### Verification You Must Run

- `cd mas && uv pip install -e "packages/mas-core[dev]"` — must resolve without errors.
- `pytest packages/mas-core/tests/` — scaffold tests should pass.

---

## Phase 2 — Policy Engine

### Decisions You Must Make

1. **Policy config format**: Policy rules defined as Python dicts (in-code) or loaded from YAML? Python is simpler for v1; YAML allows runtime changes without redeploy.
2. **Escalation skip**: The plan says `ESCALATION` messages can skip one hierarchy level. Confirm you're OK with exactly one level (worker → PM or PM → COO), not arbitrary skipping.

### Manual Actions

None — this is pure Python logic, fully automatable.

### Verification You Must Run

- `pytest packages/mas-core/tests/test_policy.py` — Run and review the output. Ensure the 6-role matrix matches your expectations. Check edge cases: worker→CEO (rejected), CSO veto (allowed), CTO→dept_devops PM (allowed).

---

## Phase 5 — LLM Gateway

### Decisions You Must Make

1. ⚠️ **LLM provider URL**: What is the URL of your OpenAI-compatible proxy? (e.g., `http://localhost:4000/v1`, `https://your-proxy.example.com/v1`).
2. ⚠️ **API key / auth mechanism**: Does your proxy require an API key? Bearer token? No auth?
3. **Default model name**: What model string to use? (e.g., `gpt-4o`, `gpt-4o-mini`, or a custom model name your proxy exposes).
4. **Streaming preference**: Should agents use streaming responses (lower time-to-first-token) or non-streaming (simpler parsing)? Recommended: non-streaming for v1.

### Manual Actions

1. ⚠️ **Ensure your LLM proxy is running and accessible** — The agent will write the client code, but it cannot start or configure your LLM provider. Test it yourself:
   ```bash
   curl -X POST http://YOUR_PROXY_URL/v1/chat/completions \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer YOUR_KEY" \
     -d '{"model":"gpt-4o","messages":[{"role":"user","content":"ping"}]}'
   ```
   Confirm you get a valid response before proceeding.
2. ⚠️ **Set environment variables** — Create a `.env` file (or export in your shell):
   ```env
   LLM_GATEWAY_URL=http://your-proxy:4000/v1
   LLM_API_KEY=your-api-key-here
   LLM_DEFAULT_MODEL=gpt-4o
   ```

### Verification You Must Run

- Run the LLM gateway unit test with real credentials (not mocked) at least once to confirm connectivity.
- Confirm token counting works for your model (OpenAI `tiktoken` may not match if using a non-OpenAI model).

---

## Phase 4b — Deterministic Workflow Controller

### Decisions You Must Make

1. **Watchdog timeout**: Default is 1 hour (3600 s). Is that appropriate for your use case? If LLM calls are slow (e.g., using a rate-limited free tier), you may want 2–4 hours.
2. **Grace period**: After system reboot, the watchdog ignores projects for 5 minutes (300 s). Acceptable?
3. **Review timeout**: Default 5 minutes per reviewer. If your LLM is slow, increase to 10–15 minutes.

### Manual Actions

None — pure Python logic.

### Verification You Must Run

- `pytest packages/mas-core/tests/test_workflow_scaffold.py` — Verify all 14 happy-path transitions and edge cases (FAILED, SECURITY_BLOCKED, retry, archive).

---

## Phase 3 — Message Router

### Decisions You Must Make

1. **Redis version**: The plan requires Redis 6.2+ (for `XAUTOCLAIM`). The compose file uses `redis:7.2-alpine`. Confirm this is acceptable.
2. **XAUTOCLAIM idle timeout**: Default 120 s. Increase if your LLM calls regularly take >2 minutes.
3. **DLQ max retries**: Default 3. Increase if you expect transient failures to be common.
4. **Stream trim threshold**: Default 50,000 entries per stream. On a dev machine with limited RAM, consider lowering to 10,000.

### Manual Actions

1. ⚠️ **Generate Redis passwords** — You need two strong passwords:
   ```bash
   # Generate on your machine:
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
   - `ROUTER_REDIS_PASS` — for the `router_user` ACL user
   - `TOOL_REDIS_PASS` — for the `toolcache_user` ACL user

   Add these to your `.env` file:
   ```env
   ROUTER_REDIS_PASS=<generated-password-1>
   TOOL_REDIS_PASS=<generated-password-2>
   ```

2. ⚠️ **Review the Redis ACL file** — The agent will generate `infra/compose/redis.conf` and optionally a `redis-acl-init.sh`. Read it and confirm the ACL rules match the plan (§4.4.6 of the org architecture doc). Dangerous commands (`CONFIG`, `FLUSHALL`, `DEBUG`, `KEYS`) must be disabled for non-admin users.

3. **Review Redis AOF config** — The agent will set `appendonly yes` and `appendfsync everysec` in `redis.conf`. If you're on a slow disk (HDD, not SSD), `appendfsync everysec` is fine. If on NVMe, you could use `appendfsync always` for zero data loss (slight performance cost).

### Verification You Must Run

- Start Redis locally (or via Docker): `docker run --rm -p 6379:6379 redis:7.2-alpine`
- Run router integration tests against real Redis.
- **Manually verify ACL**: Connect with `redis-cli` and confirm `AUTH router_user <password>` works, and that `AUTH default` is rejected.

---

## Phase 6 — Tool Service

### Decisions You Must Make

1. **Rate limits per tool group**: The defaults are:
   - `sprint.*` → 20 calls/min
   - `infra.*` → 10 calls/min
   - `document.*` → 30 calls/min
   - Others → 50 calls/min

   Adjust if your system will process many concurrent projects.

2. **Circuit breaker thresholds**: Default: 3 failures in 60 s → OPEN for 120 s. Increase the failure count if your tools depend on flaky external services.

3. **Tool cache TTL**: Default 30 s. For `web_search`, you may want 60–300 s. For DB-backed tools (e.g., `kpi.query_history`), 5–10 s or 0 (no cache).

### Manual Actions

1. **Decide which tools are real vs. stub** — For v1, most tools (e.g., `infra.provision`, `cicd.configure`) will be **stubs** that accept input and return a success response. You need to decide which tools actually do real work now:
   - `web_search` / `web_fetch` — Do you have a search API (SerpAPI, Tavily, etc.)? If yes, provide the API key.
   - `blob.*` — These will be real (backed by MinIO).
   - `document.*`, `sprint.*`, `issue.*`, `kpi.*` — These will be real (backed by Postgres).
   - `infra.*`, `cicd.*`, `monitoring.*`, `secrets.*` — These will likely be stubs for now unless you want real infrastructure automation.

2. ⚠️ **Provide web search credentials** (if applicable):
   ```env
   WEB_SEARCH_API_KEY=<your-search-api-key>
   WEB_SEARCH_PROVIDER=tavily  # or serpapi, bing, etc.
   ```

### Verification You Must Run

- `pytest apps/tool-service/tests/` — All tool tests pass.
- Start tool-service locally, call `GET /tools` — verify the manifest returns all 6 tool groups.
- Call `POST /tools/web_search/run` with a real query — verify it returns results (if using a real search provider).

---

## Phase 7 — Storage Layer (Postgres + MinIO)

### Decisions You Must Make

1. ⚠️ **Postgres password**: Generate a strong Postgres password for the `mas` database user.
2. **Enable Row Level Security?** The plan mentions it as optional. RLS adds per-agent data isolation at the DB level but adds complexity. Recommendation: skip for v1.
3. **MinIO vs. SeaweedFS**: MinIO is AGPL-licensed. If you plan to distribute this externally, consider SeaweedFS (Apache 2.0). For internal/dev use, MinIO is fine.
4. **MinIO retention policy**: How long to keep documents? Infinite (recommended for v1) or lifecycle-expire after N days?

### Manual Actions

1. ⚠️ **Generate Postgres credentials**:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
   Add to `.env`:
   ```env
   POSTGRES_USER=mas
   POSTGRES_PASSWORD=<generated-password>
   POSTGRES_DB=mas
   PGBOUNCER_DSN=postgresql://mas:<password>@pgbouncer:6432/mas
   ```

2. ⚠️ **Generate MinIO credentials**:
   ```env
   MINIO_ROOT_USER=minioadmin
   MINIO_ROOT_PASSWORD=<generated-password>
   MINIO_ENDPOINT=http://minio:9000
   MINIO_ACCESS_KEY=<access-key>
   MINIO_SECRET_KEY=<secret-key>
   ```

3. ⚠️ **Run initial Alembic migration** — After the agent creates the migration files:
   ```bash
   cd mas
   alembic upgrade head
   ```
   You need a running Postgres instance for this. Either start compose services or run Postgres locally.

4. **Verify MinIO bucket creation** — After the agent creates the init script, start MinIO and confirm bucket `mas-agents` exists:
   ```bash
   docker run --rm -p 9000:9000 -p 9001:9001 minio/minio server /data --console-address ":9001"
   # Then open http://localhost:9001 and check buckets
   ```

### Verification You Must Run

- `alembic upgrade head` — Must succeed. Then `alembic downgrade -1` and `alembic upgrade head` again (verify migration is reversible).
- Connect to Postgres and verify all 16 tables exist (3 base + 11 org + `system_config` + `agent_checkpoints`).
- Upload and download a test file to MinIO via the `BlobClient`.

---

## Phase 4 + 8 — Agent Runtime & Agent Types

### Decisions You Must Make

1. **Checkpoint interval**: Default is every LLM call (iteration). If your LLM calls are very fast and frequent, you may want every 2–3 iterations to reduce Postgres writes. Recommendation: keep default (every iteration) — the cost is negligible.
2. **LRU dedup set size**: Default 1,000 entries. Increase if you run very high message volumes. 1,000 is fine for 20–40 agents.
3. **Max think iterations**: What's the maximum number of LLM calls per task before force-stopping? Default should be configurable per agent (in team YAML `budget_defaults.max_llm_calls`).

### Manual Actions

1. ⚠️ **Write system prompts** — The agent will create placeholder prompt files in `mas/prompts/`. **You must review and refine every system prompt** for each agent role. These prompts are critical — they define agent behavior, personality, and decision-making style. The existing prompts in `mas/prompts/` are a starting point. For each prompt, verify:
   - The agent understands its role in the corporate hierarchy
   - It knows which tools it can/cannot use
   - It knows who it reports to and who reports to it
   - It understands the document lifecycle and review process
   - It knows how to format its outputs (structured JSON for tool calls, etc.)

   **Prompts to review** (11 total):
   - `ceo.md` — Human-facing, feasibility aggregation, project lifecycle
   - `coo.md` — Document lifecycle, department tasking, review fan-out/fan-in
   - `cfo.md` — Financial analysis, budget review
   - `cio.md` — Technical feasibility, technology stack
   - `chrm.md` — Resource planning, capacity assessment
   - `cso.md` — Security review, veto protocol, compliance
   - `cto.md` — Sprint planning, KPI analysis, DevOps coordination
   - `production_pm.md` — PDR creation, worker task decomposition
   - `system_pm.md` — CDR creation, architecture design
   - `qa_lead.md` — Test planning, quality assurance
   - `devops_pm.md` — Infrastructure provisioning, CI/CD, INFRA_READY signal

2. **Decide on worker-level prompts** — The plan includes optional worker agents (e.g., `requirements_writer`, `system_architect`, `devops_eng`). You need prompts for these too if you want them active at launch. Otherwise, they'll use a generic worker prompt.

### Verification You Must Run

- Unit tests with mocked LLM: `pytest packages/mas-core/tests/` — All agent type tests pass.
- **Manual smoke test**: Run one agent (e.g., CEO) against your real LLM provider with a simple task. Verify it calls tools correctly and produces structured output.

---

## Phase 9 — Team Runner

### Decisions You Must Make

1. **Stop grace period**: Default 60 s. If your LLM calls regularly take >30 s, increase to 90–120 s to give agents time to checkpoint.
2. **Worker counts per team**: Review the team YAML defaults:
   - `office_cfo`: 0–2 financial analysts (default 0 for small projects)
   - `office_cio`: 0–2 tech analysts (default 0)
   - `office_cso`: 0–2 security analysts (default 0)
   - `dept_production`: 3 workers (requirements_writer, planner, cost_estimator)
   - `dept_system`: 3 workers (system_architect, solution_designer, tech_writer)
   - `dept_qa`: 1–3 testers (default 1)
   - `dept_devops`: 1–2 devops_eng + 0–1 sre_agent

   Adjust counts in team YAMLs based on your machine's memory (each worker ≈ 100–200 MB resident).

### Manual Actions

1. ⚠️ **Review and finalize all 11 team YAML files** — The agent will generate them matching the plan's specifications. You must:
   - Confirm agent IDs are unique across all teams
   - Confirm model assignments (gpt-4o for leads, gpt-4o-mini for workers — or your model names)
   - Adjust `budget_defaults` per agent/team based on your LLM pricing
   - Set tool lists per agent role

2. **Calculate memory requirements** — Based on your worker counts, estimate total memory:
   - 7 C-Suite single-agent containers × 384–512 MB ≈ 3 GB
   - 4 department containers × 512–768 MB ≈ 2.5 GB
   - 7 infra containers ≈ 2 GB
   - **Total ≈ 7.5–8 GB minimum**

   If your machine has <16 GB RAM, consider reducing worker counts or disabling optional C-Suite workers.

### Verification You Must Run

- Start one team-runner container manually:
  ```bash
  TEAM_CONFIG=teams/exec_ceo.yaml python -m team_runner.main
  ```
  Verify it connects to the router, subscribes, and handles a test message.

---

## Phase 10 — Orchestrator API

### Decisions You Must Make

1. **API port**: Default 8000. Change if conflicting with another service.
2. **CORS origins**: If you plan to build a web UI, set allowed origins now: `http://localhost:3000`, etc.
3. **Authentication**: The plan does not specify API authentication for v1. Do you want a simple API key or bearer token for the Human-facing endpoints? Recommendation: add a simple `X-API-Key` header check for v1.

### Manual Actions

1. ⚠️ **(Optional) Set API key** — If you want basic auth:
   ```env
   MAS_API_KEY=<your-secret-api-key>
   ```

2. **Test the full API manually** — After the agent builds the orchestrator:
   - `POST /projects` — Create a test project
   - `GET /projects/{id}` — Check status
   - `GET /system/status` — Verify it returns `RUNNING`
   - `POST /system/shutdown` — Test graceful shutdown (with at least one running team)

### Verification You Must Run

- `pytest apps/orchestrator-api/tests/` — All tests pass.
- **End-to-end manual test**: Create a project via API → verify it flows through CEO → feasibility → etc. This is the first time you see the full workflow in action.

---

## Phase 11 — Docker Compose

### Decisions You Must Make

1. ⚠️ **Resource limits**: The plan suggests 8 GB / 4 cores minimum. What are your machine's actual specs? Adjust `mem_limit` and `cpus` on team containers accordingly.
2. **Volume locations**: Docker volumes store data on your system disk by default. If you have limited space on your C: drive, configure named volumes to a different drive.
3. **Dev tools**: Do you want `redis_ui`, `pgadmin`, `minio-console` in the dev compose? (Recommended: yes for development.)

### Manual Actions

1. ⚠️ **Create the master `.env` file** — Consolidate all credentials into one file at `mas/.env`:
   ```env
   # Postgres
   POSTGRES_USER=mas
   POSTGRES_PASSWORD=<your-postgres-password>
   POSTGRES_DB=mas

   # PgBouncer
   PGBOUNCER_DSN=postgresql://mas:<password>@pgbouncer:6432/mas

   # Redis
   ROUTER_REDIS_PASS=<your-router-redis-password>
   TOOL_REDIS_PASS=<your-tool-redis-password>

   # MinIO
   MINIO_ROOT_USER=minioadmin
   MINIO_ROOT_PASSWORD=<your-minio-password>
   MINIO_ENDPOINT=http://minio:9000
   MINIO_ACCESS_KEY=<access-key>
   MINIO_SECRET_KEY=<secret-key>

   # LLM
   LLM_GATEWAY_URL=http://your-proxy:4000/v1
   LLM_API_KEY=<your-llm-key>
   LLM_DEFAULT_MODEL=gpt-4o

   # Router
   ROUTER_URL=http://message-router:8001

   # Tool Service
   TOOL_SERVICE_URL=http://tool-service:8002

   # Web Search (optional)
   WEB_SEARCH_API_KEY=<your-search-key>
   WEB_SEARCH_PROVIDER=tavily

   # API Auth (optional)
   MAS_API_KEY=<your-api-key>
   ```

2. ⚠️ **First full `docker compose up`** — This is a big moment. Run:
   ```bash
   cd mas/infra/compose
   docker compose --env-file ../../.env up --build
   ```
   Watch the logs. Expect initial failures as services wait for dependencies. All 18 containers should be healthy within 90 seconds.

3. ⚠️ **Verify Docker network segmentation** — After compose is up:
   ```bash
   # Verify team containers cannot reach Redis directly:
   docker compose exec team_ceo ping redis   # Should fail or timeout
   docker compose exec team_ceo curl http://redis:6379  # Should fail

   # Verify orchestrator-api can reach both networks:
   docker compose exec orchestrator-api curl http://message-router:8001/health  # Should succeed
   ```

4. **Check disk space** — Docker images + volumes will consume 5–10 GB. Ensure you have space.

5. **Verify Redis AOF is active** — After containers are running:
   ```bash
   docker compose exec redis redis-cli INFO persistence | grep aof_enabled
   # Should show: aof_enabled:1
   ```

### Verification You Must Run

- `docker compose ps` — All 18 containers showing "healthy" or "running".
- `docker compose logs --tail=50 message-router` — No errors.
- `curl http://localhost:8000/health` — Orchestrator API healthy.
- `curl http://localhost:8001/health` — Router healthy.
- `curl http://localhost:8002/health` — Tool service healthy.
- Open MinIO console at `http://localhost:9001` — `mas-agents` bucket exists.

---

## Phase 12 — Observability

### Decisions You Must Make

1. **Prometheus + Grafana**: Do you want to add these to the dev compose? They add ~500 MB memory. Recommended: yes for development, no for constrained machines.
2. **Log level**: Default `INFO`. Set `DEBUG` during initial development, switch to `INFO` for normal operation.
3. **Alert channels**: Where should DLQ/circuit-breaker alerts go? Options: just logs (v1), Slack webhook (v2), email (v2).

### Manual Actions

1. **(Optional) Configure Grafana dashboards** — If you add Grafana, you'll need to:
   - Log in to Grafana at `http://localhost:3000` (default admin/admin)
   - Add Prometheus as a data source
   - Import or create dashboards for: project states, message throughput, DLQ depth, tool circuit breakers, agent budget usage

2. **(Optional) Set up log aggregation** — Structured JSON logs go to stdout. If you want centralized logging:
   - Add a Loki + Grafana stack to compose, OR
   - Use Docker's built-in `json-file` log driver with `max-size` / `max-file` limits

### Verification You Must Run

- `curl http://localhost:8000/metrics` — Prometheus metrics endpoint returns data.
- Check that `trace_id` appears in all log lines: `docker compose logs team_ceo | grep trace_id`.

---

## Phase 13 — Shutdown, Resume & Scheduled Operation

### Decisions You Must Make

1. **Scheduled operation**: Do you want working hours? If yes, specify:
   - Active hours (e.g., `08:00-22:00`)
   - Timezone (e.g., `Europe/Paris`, `America/New_York`)
   - Active days (e.g., `mon-fri`)
2. **Watchdog grace period**: Default 5 minutes (300 s). Increase if your system takes long to start (slow Docker pulls, etc.).

### Manual Actions

1. ⚠️ **Test the full shutdown/resume cycle manually**:
   ```bash
   # 1. Start the system and create a project
   curl -X POST http://localhost:8000/projects -d '{"name": "test", "description": "shutdown test"}'

   # 2. Wait for the project to reach PDR_CREATION or later

   # 3. Trigger graceful shutdown
   curl -X POST http://localhost:8000/system/shutdown

   # 4. Watch logs: verify SHUTDOWN_ACK from all 11 teams
   docker compose logs --follow orchestrator-api

   # 5. Verify system_state = STOPPED
   curl http://localhost:8000/system/status

   # 6. Stop all containers
   docker compose down

   # 7. Start again
   docker compose up -d

   # 8. Verify resume: project continues from where it stopped
   curl http://localhost:8000/projects/{id}
   ```

2. ⚠️ **Test cold crash recovery**:
   ```bash
   # 1. Start system with an active project in mid-task
   # 2. Kill everything without graceful shutdown:
   docker compose kill

   # 3. Start again:
   docker compose up -d

   # 4. Verify: messages redelivered from Redis PEL, project resumes
   #    (without checkpoint, tasks restart from scratch — but no data loss)
   ```

3. **(Optional) Configure scheduled operation**:
   ```bash
   curl -X PUT http://localhost:8000/system/schedule \
     -H "Content-Type: application/json" \
     -d '{
       "enabled": true,
       "active_hours": "08:00-22:00",
       "timezone": "Europe/Paris",
       "days": ["mon","tue","wed","thu","fri"],
       "auto_shutdown": true,
       "auto_resume": true
     }'
   ```
   Then verify it shuts down at the end of the active window and resumes at the start.

### Verification You Must Run

- Graceful shutdown: all SHUTDOWN_ACKs received, system_state = STOPPED.
- Resume: all active projects continue, no false FAILED states.
- Cold crash: projects resume from Redis PEL redelivery.
- Redis AOF durability: `docker compose restart redis` → all stream messages still present.

---

## Cross-Phase Manual Actions (Do Once)

These apply across all phases and should be done before or during Phase 0.

### 1. Generate All Credentials

Run this script once to generate all passwords/secrets:

```python
import secrets
credentials = {
    "POSTGRES_PASSWORD": secrets.token_urlsafe(32),
    "ROUTER_REDIS_PASS": secrets.token_urlsafe(32),
    "TOOL_REDIS_PASS": secrets.token_urlsafe(32),
    "MINIO_ROOT_PASSWORD": secrets.token_urlsafe(32),
    "MINIO_ACCESS_KEY": secrets.token_urlsafe(16),
    "MINIO_SECRET_KEY": secrets.token_urlsafe(32),
    "MAS_API_KEY": secrets.token_urlsafe(32),
}
for k, v in credentials.items():
    print(f"{k}={v}")
```

Save the output to `mas/.env` (gitignored).

### 2. Ensure Docker is Installed and Running

- Docker Desktop (Windows/Mac) or Docker Engine (Linux) must be installed.
- Verify: `docker --version` (need 24.x+), `docker compose version` (need 2.20+).
- Allocate sufficient resources in Docker Desktop settings: **8 GB memory, 4 CPUs minimum**.

### 3. Ensure Your LLM Proxy is Accessible

- Your custom LLM proxy must be reachable from Docker containers.
- If the proxy runs on your host machine at `localhost:4000`:
  - On **Windows/Mac**: Use `host.docker.internal:4000` as the URL in `.env`.
  - On **Linux**: Use `--add-host=host.docker.internal:host-gateway` in compose or the host's IP.
- Test from inside a container:
  ```bash
  docker run --rm curlimages/curl curl http://host.docker.internal:4000/v1/models
  ```

### 4. Review and Finalize System Prompts

All 11 agent system prompts in `mas/prompts/` must be reviewed by you. The AI agent can draft them, but you must verify:
- Each agent's personality and decision-making style matches your vision
- Tool usage instructions are accurate for your tool implementations
- The corporate hierarchy and reporting chain is correctly described
- Edge cases are addressed (what does the agent do when confused? when budget is exhausted? when a tool fails?)

### 5. Git Setup

- Add `mas/.env` to `.gitignore` (never commit credentials).
- Add Docker volume data directories to `.gitignore`.
- Consider committing `infra/compose/redis.conf` (no secrets — passwords are injected via env vars at runtime).

---

## Summary Checklist

| Phase | Blocking Decisions | Manual Actions | Key Verification |
|-------|-------------------|----------------|------------------|
| **0+1** | Package manager, Python version | Install venv, review models | `pip install -e` works |
| **2** | Policy format | None | Policy tests pass |
| **5** | LLM URL, API key, model name | Ensure LLM proxy running, set env vars | LLM connectivity test |
| **4b** | Watchdog/review timeouts | None | Workflow transition tests |
| **3** | Redis version, XAUTOCLAIM timeout | Generate Redis passwords, review ACL | Redis ACL verified |
| **6** | Rate limits, tool stubs vs. real | Search API key (optional) | Tool manifest returns |
| **7** | Postgres password, RLS, MinIO license | Generate all storage credentials, run migration | All 16 tables exist |
| **4+8** | Checkpoint interval | **Review all 11 system prompts** | Agent smoke test with real LLM |
| **9** | Grace period, worker counts | Review 11 team YAMLs, check memory | Team runner connects and processes |
| **10** | API port, CORS, auth | Set API key, manual API test | End-to-end project flow |
| **11** | Resource limits, volume locations | Create master `.env`, first `docker compose up`, verify networks | All 18 containers healthy |
| **12** | Prometheus/Grafana, log level | (Optional) Grafana dashboards | Metrics endpoint works |
| **13** | Working hours, grace period | Full shutdown/resume test, cold crash test | No data lost across reboots |

---

## Estimated Time for Manual Actions

| Category | Estimated Time |
|----------|---------------|
| Credential generation | 15 minutes |
| LLM proxy setup/verification | 30 minutes |
| System prompt review (11 prompts) | 2–4 hours |
| Team YAML review (11 files) | 30 minutes |
| Docker + compose first run | 1 hour (includes troubleshooting) |
| Shutdown/resume testing | 1 hour |
| End-to-end workflow test | 1–2 hours |
| **Total** | **~6–9 hours** |

Most of this time is front-loaded (credentials + LLM setup + prompt review). After Phase 11, subsequent phases require minimal manual work.
