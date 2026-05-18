# AIAT MAS

AIAT MAS is a self-hosted multi-agent system for software-project orchestration.
The active workspace is this `mas/` directory; run service, test, migration, and
dashboard commands from here.

Status on 2026-05-18: the core MAS stack, configurable flows, project context
layer, worker registry, credentials manager, privileged-operation policy,
dashboard, and compose/systemd deployment files are implemented in code. The next
work is validation and production hardening, tracked in
`../.github/prompts/AIAT_PLAN.md`.

## What Is Included

- FastAPI services: orchestrator API, message router, tool service, and team runner.
- Shared Python packages: `mas-core` and `mas-tools-sdk`.
- Next.js dashboard at `apps/mas-dashboard`, exposed at `http://localhost:4000`.
- 11 configured teams, 26 worker manifests, and 11 system prompts.
- Postgres-first workflow and knowledge model with MinIO blob storage and
  optional pgvector semantic retrieval.
- Configurable orchestration flows with API and dashboard support.
- Worker registry, upstream repository metadata, evaluation reports, and worker
  lifecycle endpoints.
- Centralized tool-service layer with grants, rate limiting, caching, audit, and
  circuit breakers.
- Credentials manager and CEO privileged-operation audit/policy layer.
- Prometheus/Grafana dev overlay, metrics endpoints, DLQ inspection, and system
  visualization pages.

## Repository Layout

```text
apps/
  orchestrator-api/      FastAPI project/workflow/control API
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
teams/                   11 team YAML configs
workers/                 26 worker manifests
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

From this directory:

```bash
cp infra/compose/.env.example infra/compose/.env
```

Edit `infra/compose/.env` and set real values for at least:

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

Paste the hash into `infra/compose/.env` as `DASHBOARD_PASSWORD_HASH`.

Start the base stack:

```bash
docker compose -f infra/compose/docker-compose.yml --env-file infra/compose/.env up -d --build
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
Prometheus, Grafana, pgAdmin, and RedisInsight ports:

```bash
docker compose \
  -f infra/compose/docker-compose.yml \
  -f infra/compose/docker-compose.dev.yml \
  --env-file infra/compose/.env \
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
| Prometheus | `http://localhost:9090` |
| Grafana | `http://localhost:3000` |

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

Base compose defines 19 long-running services plus two one-shot init jobs:

- Long-running infra/services: Redis, Postgres, PgBouncer, MinIO,
  orchestrator-api, message-router, tool-service, dashboard, and 11 team runners.
- One-shot init jobs: Redis ACL init and MinIO bucket/user init.
- Dev overlay adds pgAdmin, RedisInsight, Prometheus, and Grafana.

The dashboard is an authenticated server-side proxy. Browser code calls
Next.js API routes, and those routes hold service credentials server-side.

## Planning

The merged plan is `../.github/prompts/AIAT_PLAN.md`. It includes:

- current implementation baseline
- priority roadmap
- worker integration policy
- next features from `../next.txt`
- validation checklist
- known technical debt

Do not add new scattered plan files under `.github/prompts/`; update the merged
plan instead.

## License

Proprietary - internal use only.
