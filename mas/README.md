# AIAT MAS

AIAT MAS is a self-hosted multi-agent system for software-project orchestration.
The active workspace is this `mas/` directory; run service, test, migration, and
dashboard commands from here.

The core MAS stack, configurable flows, project context layer, worker registry,
credentials manager, privileged-operation policy, dashboard, and compose/systemd
deployment files are implemented in code. Current implementation truth and
remaining validation work are tracked by the phased plans under
`../.github/prompts/` and by `../Docs/AIAT_LIVE_TEST_LEDGER.md`.

## What Is Included

- FastAPI services: orchestrator API, message router, tool service, and team runner.
- Provider-neutral PM/SCM adapter package plus the internet-facing `pm-gateway`.
- Shared Python packages: `mas-core` and `mas-tools-sdk`.
- Next.js dashboard at `apps/mas-dashboard`, exposed at `http://localhost:4000`.
- 11 configured departments, 39 worker manifests, and 11 role prompts.
- Versioned company manifests, durable worker-run queues/leases, idempotent usage
  accounting, and atomic company budget reservations.
- Postgres-first workflow and knowledge model with MinIO blob storage and
  optional pgvector semantic retrieval.
- Configurable orchestration flows with API and dashboard support.
- Worker registry, upstream repository metadata, evaluation reports, and worker
  lifecycle endpoints.
- Centralized tool-service layer with grants, rate limiting, caching, audit, and
  circuit breakers.
- Credentials manager and CEO privileged-operation audit/policy layer.
- LiteLLM and OmniRoute analytics shortcuts, optional Prometheus platform
  metrics, DLQ inspection, and system visualization pages.

## Repository Layout

```text
  apps/
    orchestrator-api/      FastAPI project/workflow/control API
    pm-gateway/            Raw provider webhook ingress and bounded outbox trigger
  message-router/        Redis Streams broker and WebSocket subscriptions
  tool-service/          Central tool execution service
  team-runner/           Per-team agent process
  mas-dashboard/         Next.js operator dashboard
packages/
  mas-core/              Protocols, policy, workflow, storage, agents, LLM gateway
  mas-tools-sdk/         Tool manifest and client SDK
migrations/              Alembic schema migrations
infra/
  compose/               Docker Compose files and env example
  docker/                Dockerfiles
  systemd/               masctl and systemd service units
  sandbox/               Sandbox profile notes/templates
companies/               Versioned company/org/budget manifests
teams/                   11 department YAML configs
workers/                 39 worker manifests
prompts/                 11 role system prompts
docs/                    Architecture notes
```

## Prerequisites

- Docker Desktop or Docker Engine with Compose v2.
- Python 3.11+.
- `uv`.
- Node.js 20+ for dashboard development and password-hash generation.
- Enough local Docker resources for the full stack; use at least 8 GB RAM and 4 CPUs.

## First Run

From the repository root, copy `.env.example` to `.env` and set real values for at least:

- `POSTGRES_PASSWORD`
- `MINIO_ROOT_PASSWORD`
- `ROUTER_PASSWORD`
- `TOOLCACHE_PASSWORD`
- `ROUTER_SECRET`
- `TOOL_SECRET`
- `LLM_GATEWAY_URL`
- `LLM_API_KEY` or provider-specific API keys
- `DASHBOARD_USERNAME`
- `DASHBOARD_PASSWORD_HASH`
- `JWT_SECRET`
- `MAS_API_KEY`

Generate a dashboard password hash from the dashboard package:

```bash
cd apps/mas-dashboard
npm install
node -e "const b=require('bcryptjs'); console.log(b.hashSync('replace-me', 12))"
cd ../..
```

Paste the hash into `.env` as `DASHBOARD_PASSWORD_HASH`.

Start the base stack:

```bash
infra/compose/mas.sh up --build
```

Run migrations:

```bash
uv run alembic upgrade head
```

Check health:

```bash
curl http://localhost:8000/health
curl http://localhost:8001/health
curl http://localhost:8002/health
curl http://localhost:4000/api/health
```

Open the dashboard at `http://localhost:4000`.

## Development Mode

The dev overlay exposes Redis, Postgres, MinIO, message-router, tool-service,
LiteLLM, OmniRoute, optional Prometheus platform metrics, pgAdmin, and
RedisInsight ports:

```bash
docker compose \
  -f infra/compose/docker-compose.yml \
  -f infra/compose/docker-compose.dev.yml \
  --env-file ../.env \
  up -d --build
```

Useful local URLs:

| Service | URL |
|---|---|
| Dashboard | `http://localhost:4000` |
| Orchestrator API | `http://localhost:8000` |
| Message router | `http://localhost:8001` |
| Tool service | `http://localhost:8002` |
| RedisInsight | `http://localhost:8003` |
| pgAdmin | `http://localhost:5050` |
| MinIO Console | `http://localhost:9001` |
| LiteLLM analytics | `http://localhost:4001/ui/` |
| OmniRoute analytics | `http://localhost:20128/dashboard/analytics` |
| Prometheus platform metrics (optional) | `http://localhost:9090` |

The AIAT sidebar links to both analytics pages. For remote or reverse-proxied
deployments, set `LITELLM_DASHBOARD_URL` and `OMNIROUTE_DASHBOARD_URL` to the
browser-reachable URLs.

Prometheus is optional observability. The dashboard bounds each Prometheus
request to `PROMETHEUS_TIMEOUT_MS` (default `750` ms), so a stopped metrics
container cannot delay the overview page; the UI shows a degraded metrics
state instead. The Compose development overlay starts Prometheus alongside
the dashboard, while the OmniRoute data and log volumes are initialized for
its non-root service user on every deployment. If a host security product or
enterprise proxy re-signs HTTPS traffic, set `AIAT_EXTRA_CA_CERT` to a
read-only PEM bundle containing that host root CA; Compose passes it through
`NODE_EXTRA_CA_CERTS` without disabling TLS verification.

The gateway path is AIAT -> LiteLLM -> OmniRoute -> provider. On `mas.sh up`,
AIAT idempotently imports the configured legacy provider keys into OmniRoute,
enables model/pricing synchronization, and starts 9Router and CLIProxyAPI.
See [the OmniRoute gateway guide](docs/OMNIROUTE.md) for aliases, embedded
service behavior, and the authentication handoff.

## Tests

Python workspace:

```bash
uv sync
uv run pytest
uv run ruff check .
uv run mypy .
```

Dashboard:

```bash
cd apps/mas-dashboard
npm install
npm run build
npm run test:e2e
```

Live/provider tests remain opt-in:

```bash
MAS_RUN_LIVE_TESTS=1 uv run pytest -m live packages/mas-core/tests/test_llm_live.py
```

## Runtime Shape

Base compose defines the core infrastructure, control-plane services, analytics,
and 11 team runners plus one-shot init jobs. The signed identity service and
private identity database/migration are enabled by the `mail-local` profile in
`infra/compose/docker-compose.stalwart-local.yml`.

- Long-running infra/services: Redis, Postgres, PgBouncer, MinIO,
  orchestrator-api, message-router, tool-service, dashboard, LiteLLM,
  OmniRoute, and 11 team runners.
- One-shot init jobs: Redis ACL init and MinIO bucket/user init.
- Dev overlay adds pgAdmin, RedisInsight, LiteLLM and OmniRoute host access,
  plus optional Prometheus platform metrics. Grafana is not bundled.

The dashboard is an authenticated server-side proxy. Browser code calls
Next.js API routes, and those routes hold service credentials server-side.

## Planning

The reconciled implementation plan is [`../Docs/AIAT_Deep_Research_Implementation_Plan.md`](../Docs/AIAT_Deep_Research_Implementation_Plan.md).
Historical phased plans remain useful for context, but the reconciled plan and
the license/provenance inventory are authoritative for current work:

- `../.github/prompts/PLAN_alpha_beta.md`
- `../.github/prompts/PLAN_gamma.md`
- `../.github/prompts/PLAN_delta.md`
- `../.github/prompts/PLAN_epsilon.md`

Use `../Docs/AIAT_LIVE_TEST_LEDGER.md` for current live-test evidence, defects,
fixes, enhancement opportunities, and remaining work. The old merged plan under
`../.github/prompts/obsolete/AIAT_PLAN.md` is historical context only.

Provider-neutral PM/SCM integration architecture and operator setup are documented in
[`../Docs/PM_Platform_Integration_Plan.md`](../Docs/PM_Platform_Integration_Plan.md) and
[`../Docs/PM_Platform_Integration_Runbook.md`](../Docs/PM_Platform_Integration_Runbook.md).
See the linked ADR, adapter-authoring, provider setup, deployment, dashboard,
and certification references in `../Docs/PM_Platform_*`.

## License

Proprietary - internal use only.
