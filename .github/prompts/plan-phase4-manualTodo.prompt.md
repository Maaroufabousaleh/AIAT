# MAS Architecture — Remaining Manual TODO

**TL;DR**: This document was generated after a comprehensive audit of the MAS codebase against all three plan documents. All automated fixes have been applied (Phases 0-13). What remains below are items that **require human judgment, real infrastructure, credentials, or external service access** — things an AI coding agent cannot do.

> **Generated**: 2026-03-31
>
> **Companion documents**:
> - `plan-orgArchitecture.prompt.md` — Org architecture (2,468 lines)
> - `plan-masArchitectureUpgrade.prompt.md` — Infrastructure upgrade plan (812 lines)
> - `plan-manualActions.prompt.md` — Full manual actions reference (757 lines)

---

## Audit Summary — What Was Fixed Automatically

These gaps were found during the audit and have already been patched in the codebase:

| Phase | Issue | Fix Applied |
|-------|-------|-------------|
| **3** | `ACL SAVE` missing from `redis-acl-init.sh` | Added `ACL SAVE` after user configuration |
| **7** | `boot_at`/`shutdown_at` bootstrap rows used SQL `NULL` for `NOT NULL` column | Changed to empty string `''` sentinel |
| **11** | `message-router` and `tool-service` on `public` network | Moved both to `internal` only; ports exposed via `docker-compose.dev.yml` |
| **11** | `pgbouncer` had no healthcheck | Added `pg_isready` healthcheck; team-defaults now use `condition: service_healthy` |
| **11** | No sandbox tier enforcement on team containers | Added `cap_drop: [ALL]`, `read_only: true`, `tmpfs: /tmp:size=128m` to `x-team-defaults` |
| **12** | No Prometheus/Grafana containers | Added both to `docker-compose.dev.yml` with `prometheus.yml` scrape config and Grafana datasource provisioning |
| **12** | `message-router` used raw `structlog.configure()` | Replaced with shared `configure_logging("message-router")` from `mas_core.observability` |
| **12** | Route handlers in `message-router` used stdlib `logging.getLogger()` | Migrated to `structlog.stdlib.get_logger()` for proper context propagation |
| **12** | `message-router` had no `/metrics` endpoint | Added Prometheus `/metrics` endpoint via `prometheus_client.make_asgi_app()` |

---

## What Was Already Complete (No Action Needed)

| Phase | Component | Status |
|-------|-----------|--------|
| 0+1 | Repo scaffold, protocols, enums, envelope model | Complete |
| 2 | Communication policy engine (6-role matrix) | Complete |
| 3 | Message router (Redis Streams, XAUTOCLAIM, DLQ, WS subscribe) | Complete |
| 4b | Deterministic workflow controller (18 states, watchdog) | Complete |
| 5 | LLM gateway (9 providers: 8 API + 1 CLI) | Complete |
| 6 | Tool service (circuit breaker, rate limiter, cache) | Complete |
| 7 | Storage layer (13 Postgres tables, MinIO blob client, checkpoints) | Complete |
| 4+8 | Agent runtime (6 agent types: admin, lead, worker, human, review, stub) | Complete |
| 9 | Team runner (11 team YAMLs, 26 worker manifests) | Complete |
| 10 | Orchestrator API (30+ endpoints, 1,066 lines) | Complete |
| 11 | Docker Compose (18 containers, network segmentation) | Complete |
| 12 | Observability (10 Prometheus metrics, structlog, trace-ID propagation) | Complete |
| 13 | Shutdown/resume protocol (graceful shutdown, resume sequence, working-hours schedule) | Complete |

---

## Remaining Manual TODO

### Priority Legend
- **P0 — Blocking**: Must be done before the system can run at all
- **P1 — Required**: Must be done before first real project execution
- **P2 — Recommended**: Should be done for production readiness
- **P3 — Optional**: Nice-to-have, can defer indefinitely

---

### P0 — Blocking (Do First)

#### 1. Generate All Credentials and Create `.env`

The system cannot start without a `.env` file at `mas/.env`. Generate all secrets:

```python
import secrets
for name in [
    "POSTGRES_PASSWORD", "ROUTER_PASSWORD", "TOOLCACHE_PASSWORD",
    "MINIO_ROOT_PASSWORD", "ROUTER_SECRET", "TOOL_SECRET",
    "GRAFANA_ADMIN_PASSWORD", "MAS_API_KEY",
]:
    print(f"{name}={secrets.token_urlsafe(32)}")
```

Then create `mas/.env` with all required variables. See `plan-manualActions.prompt.md` Phase 11 for the complete template.

#### 2. Ensure Docker is Installed (24.x+, Compose 2.20+)

```bash
docker --version
docker compose version
```

Allocate at least **8 GB memory, 4 CPUs** in Docker Desktop settings.

#### 3. Ensure Your LLM Proxy is Accessible from Docker

Your LLM proxy must be reachable from inside containers:
- If running on host: use `host.docker.internal:<port>` in `.env`
- Test: `docker run --rm curlimages/curl curl http://host.docker.internal:4000/v1/models`

Set in `.env`:
```env
LLM_GATEWAY_URL=http://host.docker.internal:4000/v1
LLM_API_KEY=<your-key>
LLM_DEFAULT_MODEL=gpt-4o
```

#### 4. First `docker compose up`

```bash
cd mas/infra/compose
docker compose --env-file ../../.env up --build
```

All 18 containers should be healthy within 90 seconds. With the dev overlay:
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml --env-file ../../.env up --build
```

#### 5. Run Alembic Migration

After Postgres is up:
```bash
cd mas
alembic upgrade head
```

Verify reversibility: `alembic downgrade -1 && alembic upgrade head`

---

### P1 — Required (Before First Real Project)

#### 6. Review and Refine All 11 System Prompts

**Location**: `mas/prompts/`

These prompts define each agent's behavior. The AI agent drafted them, but you must verify:
- Each agent understands its role in the corporate hierarchy
- Tool usage instructions are accurate
- Reporting chain is correctly described
- Edge cases are addressed (budget exhaustion, tool failure, confusion)

**Files to review** (estimated 2-4 hours total):
- `ceo.md`, `coo.md`, `cfo.md`, `cio.md`, `chrm.md`, `cso.md`, `cto.md`
- `production_pm.md`, `system_pm.md`, `qa_lead.md`, `devops_pm.md`

#### 7. Review All 11 Team YAML Configurations

**Location**: `mas/teams/`

For each team YAML, confirm:
- Agent IDs are unique across all teams
- Model assignments match your LLM proxy's available models
- `budget_defaults` are appropriate for your LLM pricing
- Tool allowlists are correct per agent role

#### 8. Review All 26 Worker Manifests

**Location**: `mas/workers/`

For each worker manifest, verify:
- `sandbox.profile` matches the worker's risk level
- `transport` mode is correct (default `process`)
- `capabilities` list is accurate (drives CTO issue assignment)
- `checkpoint.strategy` is set

#### 9. Verify Docker Network Segmentation

After compose is up:
```bash
# Team containers should NOT reach Redis directly:
docker compose exec team-exec-ceo ping redis   # Should fail/timeout

# Orchestrator-api should reach internal services:
docker compose exec orchestrator-api curl http://message-router:8001/health
```

#### 10. Verify Redis ACL Configuration

```bash
# Connect as router_user — should work:
docker compose exec redis redis-cli -u redis://router_user:<ROUTER_PASSWORD>@redis:6379 PING

# Connect as default — should be rejected:
docker compose exec redis redis-cli PING
```

#### 11. End-to-End Smoke Test

Create a project and verify it flows through the workflow:
```bash
# Create project
curl -X POST http://localhost:8000/projects \
  -H "Content-Type: application/json" \
  -d '{"name": "test-project", "description": "First smoke test"}'

# Watch it progress
curl http://localhost:8000/projects/<id>
curl http://localhost:8000/system/status
```

---

### P2 — Recommended (Production Readiness)

#### 12. Test Full Shutdown/Resume Cycle

```bash
# 1. Create a project and let it reach mid-workflow
# 2. Graceful shutdown:
curl -X POST http://localhost:8000/system/shutdown
# 3. Verify all SHUTDOWN_ACKs received
# 4. docker compose down
# 5. docker compose up -d
# 6. Verify project continues from where it stopped
```

#### 13. Test Cold Crash Recovery

```bash
# 1. Start system with an active project
# 2. Kill without graceful shutdown:
docker compose kill
# 3. Restart:
docker compose up -d
# 4. Verify project resumes from Redis PEL redelivery
```

#### 14. Configure Grafana Dashboards

After Prometheus + Grafana are running (dev overlay):
1. Open Grafana at `http://localhost:3000` (admin / `$GRAFANA_ADMIN_PASSWORD`)
2. Prometheus datasource is auto-provisioned
3. Create dashboards for:
   - Project state distribution (`mas_project_state`)
   - Message throughput (`mas_messages_total`)
   - DLQ depth (`mas_dlq_depth`)
   - Tool circuit breaker states (`mas_tool_circuit_state`)
   - LLM call volume (`mas_llm_calls_total`)
   - Agent budget exhaustion (`mas_budget_exhausted_total`)

#### 15. Verify Sandbox Tier Enforcement

```bash
# Inspect a team container for CAP_DROP:
docker inspect mas-team-dept-devops | findstr /i "CapDrop"
# Should show: ["ALL"]

# Verify read-only filesystem:
docker compose exec team-dept-devops touch /test-file
# Should fail: "Read-only file system"

# Verify tmpfs is writable:
docker compose exec team-dept-devops touch /tmp/test-file
# Should succeed
```

#### 16. Verify Capability Registry Sync

After team-runners start:
```bash
curl http://localhost:8000/capabilities
curl http://localhost:8000/capabilities/workers
```

All workers from team YAMLs should appear in the registry with correct `adapter_type` and `capability_ids`.

#### 17. Verify Redis AOF Persistence

```bash
docker compose exec redis redis-cli INFO persistence | grep aof_enabled
# Should show: aof_enabled:1

# Restart Redis and verify data survives:
docker compose restart redis
docker compose exec redis redis-cli XLEN stream:exec_ceo
```

---

### P3 — Optional (Defer as Needed)

#### 18. Phase 14 — Paperclip Control Plane Integration

**Entirely deferred.** The execution plane works standalone. Paperclip adds:
- Human-facing org-chart UI
- Task board with approvals
- Budget dashboards
- Audit log visualization

If you want Paperclip, see `plan-manualActions.prompt.md` Phase 14 for full instructions. Key steps:
1. Add Paperclip containers to compose
2. Configure event bridge (push webhook or pull polling)
3. Register all 25 agents as Paperclip "employees"
4. Map 11 teams to Paperclip departments

#### 19. Sandbox Tiers 2-3 (gVisor / Firecracker)

Current setup uses Tier 0 (Docker) and Tier 1 (restricted: `cap_drop: ALL`, `read_only`, `tmpfs`).

- **Tier 2 (gVisor)**: Requires `runsc` installed on host and registered as Docker runtime `--runtime=runsc`
- **Tier 3 (Firecracker)**: Requires `firecracker-containerd` deployed

Only needed for workers handling untrusted code or external data.

#### 20. Log Aggregation (Loki / ELK)

Structured JSON logs go to stdout. For centralized logging:
- Add Loki + Grafana to compose, OR
- Use Docker's `json-file` log driver with `max-size` / `max-file` limits

#### 21. Alert Channels (Slack / Email)

Current: alerts only in logs and Prometheus metrics. For v2+:
- Configure Grafana alerting rules
- Add Slack webhook or email notification channels

#### 22. Web Search Provider Integration

If you want real `web_search` / `web_fetch` tools (not stubs):
```env
WEB_SEARCH_API_KEY=<your-key>
WEB_SEARCH_PROVIDER=tavily  # or serpapi, bing
```

#### 23. MinIO Retention Policy

Current: infinite retention. For production:
- Configure lifecycle rules on the `mas-agents` bucket
- Set expiry for old document versions (e.g., 90 days)

---

## Estimated Time

| Category | Time |
|----------|------|
| P0 — Credentials + Docker + LLM setup | 1-2 hours |
| P1 — Prompt review (11 prompts) | 2-4 hours |
| P1 — YAML review (11 teams + 26 workers) | 1-2 hours |
| P1 — Verification (networks, ACL, smoke test) | 1 hour |
| P2 — Shutdown/resume + crash recovery testing | 1 hour |
| P2 — Grafana dashboards | 1 hour |
| **Total P0+P1** | **~5-9 hours** |
| **Total P0+P1+P2** | **~7-11 hours** |
