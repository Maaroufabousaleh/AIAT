# MAS — Multi-Agent System

> **Status:** Phases 0-6 are implemented and aligned to the updated plans; repository baseline is prepared for Phase 7+.

A self-hosted, fully autonomous multi-agent system built on FastAPI, Redis Streams,
PostgreSQL, and MinIO. Eleven specialised agent teams cover the full software delivery
lifecycle — from CEO vision to DevOps deployment.

---

## Architecture at a Glance

```
┌─────────────────────────────────────────────────────────────┐
│  orchestrator-api  (workflow controller + REST)  :8000      │
│  message-router    (policy + Redis Streams)      :8001      │
│  tool-service      (7 tool groups, rate-limited) :8002      │
│  team-runner ×11   (one container per team)      internal   │
├─────────────────────────────────────────────────────────────┤
│  Redis 7.2   PostgreSQL 16   PgBouncer 1.22   MinIO         │
└─────────────────────────────────────────────────────────────┘
```

18 containers total: 7 infra + 11 team-runners.

### Teams
| Container | Team ID | Leader role |
|---|---|---|
| `mas-team-exec-ceo` | exec_ceo | CEO |
| `mas-team-exec-coo` | exec_coo | COO |
| `mas-team-office-cfo` | office_cfo | CFO |
| `mas-team-office-cio` | office_cio | CIO |
| `mas-team-office-chrm` | office_chrm | CHRM |
| `mas-team-office-cso` | office_cso | CSO |
| `mas-team-office-cto` | office_cto | CTO |
| `mas-team-dept-production` | dept_production | Production PM |
| `mas-team-dept-system` | dept_system | System PM |
| `mas-team-dept-qa` | dept_qa | QA Lead |
| `mas-team-dept-devops` | dept_devops | DevOps PM |

---

## Quick Start

### Prerequisites
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) 24+
- [uv](https://docs.astral.sh/uv/) 0.4+ (Python workspace manager)

### 1 — Configure secrets

```bash
cp infra/compose/.env.example infra/compose/.env
# Edit .env and fill in all secrets (never commit the real .env)
```

### 2 — Start all services

```bash
cd mas
docker compose -f infra/compose/docker-compose.yml --env-file infra/compose/.env up -d
```

### 3 — Run database migrations

```bash
# Inside the orchestrator container or locally with uv:
cd mas
uv run alembic upgrade head
```

### 4 — Check health

```bash
curl http://localhost:8000/health    # orchestrator-api
curl http://localhost:8001/health    # message-router
curl http://localhost:8002/health    # tool-service
```

### Development mode (hot-reload + exposed ports)

```bash
docker compose \
  -f infra/compose/docker-compose.yml \
  -f infra/compose/docker-compose.dev.yml \
  --env-file infra/compose/.env \
  up -d
```

Dev additions: pgAdmin (`:5050`), RedisInsight (`:8003`), hot-reload on all FastAPI services.

---

## Repository Layout

```
mas/
├── pyproject.toml              # uv workspace root
├── alembic.ini                 # Alembic config
├── migrations/                 # DB migrations
│   └── versions/
│       └── 0001_initial_schema.py
├── packages/
│   ├── mas-core/               # Shared library (protocols, policy, LLM gateway…)
│   └── mas-tools-sdk/          # Tool interface + HTTP client
├── apps/
│   ├── orchestrator-api/       # Workflow controller + REST API
│   ├── message-router/         # Policy enforcement + Redis Streams broker
│   ├── tool-service/           # Tool gateway (7 groups, rate-limited)
│   └── team-runner/            # Per-team agent process (×11 at runtime)
├── teams/                      # YAML configs for each team (11 files)
├── prompts/                    # Agent system prompt stubs (11 Markdown files)
└── infra/
    ├── docker/                 # Dockerfiles (multi-stage uv builds)
    └── compose/                # docker-compose.yml, .dev.yml, redis.conf, .env.example
```

---

## Development

### Install all workspace packages

```bash
cd mas
uv sync
```

### Run tests

```bash
uv run pytest                    # all packages + apps
uv run pytest packages/mas-core  # single package
```

```bash
# live/provider tests are opt-in:
MAS_RUN_LIVE_TESTS=1 uv run pytest -m live packages/mas-core/tests/test_llm_live.py
```

### Lint + type-check

```bash
uv run ruff check .
uv run mypy .
```

---

## Port Map

| Service | Port | Notes |
|---|---|---|
| orchestrator-api | 8000 | HTTP only in prod; TLS terminated by reverse proxy |
| message-router | 8001 | HTTP + WebSocket |
| tool-service | 8002 | HTTP only |
| Redis | 6379 | Dev only (not exposed in prod compose) |
| PostgreSQL | 5432 | Dev only |
| MinIO API | 9000 | Dev only |
| MinIO Console | 9001 | Dev only |
| pgAdmin | 5050 | Dev only |
| RedisInsight | 8003 | Dev only |

---

## Phase Roadmap

| Phase | Description |
|---|---|
| 0 | Repo scaffold |
| 1 | Protocols and core models |
| 2 | Policy engine |
| 5 | LLM gateway |
| 4b | Deterministic workflow controller |
| 3 | Message router |
| 6 | Tool service implementation (7 groups) |
| 7 | Storage layer (Postgres + MinIO + capability registry) |
| 4 + 8 | Agent runtime + agent types |
| 9 + 10 | Team runner + orchestrator API |
| 11 | Docker Compose baseline |
| 12 | Observability |
| 13 | Shutdown and resume |
| 14 (optional) | Paperclip control-plane integration |

---

## License

Proprietary — internal use only.
