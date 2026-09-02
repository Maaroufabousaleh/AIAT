# AIAT MAS Plan

> **Obsolete policy notice (2026-08-09):** This plan is historical. Licence
> classification is metadata only in the current personal internal-use
> programme and does not block any resource. Use `AIAT_TARGET_PROGRAMME.md`
> and `ROADMAP.md` from the repository root.

Last updated: 2026-05-31

This document is retained only as historical context. The active implementation
truth is now split across `PLAN_alpha_beta.md`, `PLAN_gamma.md`,
`PLAN_delta.md`, and `PLAN_epsilon.md`.

## Current Baseline

The active implementation lives under `mas/`. The repository root still contains
some stale generated dashboard artifacts, but the code, migrations, compose stack,
workers, teams, and prompts are under `mas/`.

Implemented surfaces:

- FastAPI services: `orchestrator-api`, `message-router`, `tool-service`,
  `team-runner`.
- Shared packages: `mas-core` and `mas-tools-sdk`.
- Next.js dashboard: `mas/apps/mas-dashboard`, exposed by compose on
  `http://localhost:4000`.
- Agent organization: 11 team YAMLs, 26 worker manifests, and 11 role prompts.
- Workflow controller: deterministic project states, retries, archive/retry
  recovery, scheduled shutdown/resume, and watchdog support.
- Configurable orchestration flows: flow definitions, flow instances, node
  execution audit records, runtime actions, switch/override/retry/escalation,
  dashboard flow builder, and project flow visibility.
- Project context: project-scoped context items, chunks, tags, relations, keyword
  search, hybrid search, and pgvector support.
- Worker integration: registry tables, config-driven manifests, upstream source
  fields, evaluation reports, lifecycle/status endpoints, import, evaluation, and
  upgrade hooks.
- Central tool layer: grouped tool-service registry, tool grants, rate limiting,
  caching, circuit breakers, audit tracking, and browser/web/flow/project tools.
- Credentials and privileged operations: credential tables, credential audit,
  manager APIs, CEO privileged-op policy separation, and privileged audit table.
- Observability: structured logs, Prometheus metrics, Grafana/Prometheus dev
  overlay, dashboard metrics view, DLQ inspection, stream monitor, tools view,
  system status, and system visualization pages.
- Deployment: Docker Compose, dev overlay, systemd unit templates, `mas.sh`,
  `mas.bat`, and `masctl` wrappers.

## Architecture Direction

Keep the system Postgres-first:

- Postgres is the canonical source of truth for structured state, workflow,
  project records, approvals, audits, workers, credentials, and lifecycle data.
- Object storage holds large bodies and generated artifacts.
- Project context retrieval sits above the schema. It filters by `project_id`
  first, then combines metadata, keyword search, and optional semantic vector
  search.
- The orchestrator/controller remains the sole writer of project lifecycle state.
- Agents and workers consume platform services through controlled APIs rather
  than bypassing policy with their own unmanaged integrations.

## Phase Map

| Phase | Scope | Current status |
|---|---|---|
| 0 | Repo scaffold | Implemented |
| 1 | Protocols and core models | Implemented |
| 2 | Communication policy engine | Implemented |
| 3 | Redis Streams message router | Implemented |
| 4 | Agent runtime | Implemented |
| 4b | Deterministic workflow controller | Implemented |
| 5 | LLM gateway | Implemented and expanded |
| 6 | Tool service | Implemented and expanded |
| 7 | Storage layer | Implemented and expanded |
| 8 | Agent types | Implemented |
| 9 | Team runner | Implemented |
| 10 | Orchestrator API | Implemented and expanded |
| 11 | Compose/system deployment | Implemented |
| 12 | Observability | Implemented; needs production hardening |
| 13 | Shutdown/resume/scheduling | Implemented; needs live recovery validation |
| 14 | External control-plane integration | Deferred |

## Priority Roadmap

### P0 - Keep The Current System Runnable

- Keep the repository-root `.env` aligned with `.env.example`. `mas.sh` loads
  this root file as the single source of truth.
- Verify dashboard login uses both `DASHBOARD_USERNAME` and
  `DASHBOARD_PASSWORD_HASH`; login fails if the hash is empty.
- Use `mas/infra/compose/mas.sh` from the repository root, or `cd mas` before
  direct `uv` commands.
- Run migrations after schema changes:

```bash
cd mas
uv run alembic upgrade head
```

- Keep root-level generated dashboard artifacts out of planning decisions. The
  canonical dashboard source is `mas/apps/mas-dashboard`.

### P1 - Validate Operator-Critical Flows

- Run full dashboard smoke coverage for project creation, project detail,
  decisions, DLQ, tools, workers, metrics, system controls, system visualization,
  flow builder, and flow runtime.
- Validate project creation with and without a selected flow.
- Validate flow assignment, start, pause/resume, node completion, switch,
  override, retry, and escalation from both API and dashboard.
- Validate worker registration, YAML import, source repository fields, evaluation,
  activation/deactivation/draining, health, upgrade, and reclassification.
- Validate project context upload/create/list/delete/search/hybrid-search from API
  and dashboard.
- Validate credential create/update/delete/resolve/audit behavior and confirm
  secrets never serialize into dashboard JSON responses.
- Validate CEO privileged operations: executive actions should stay normal;
  privileged operations should go through policy, audit, and approval rules.

### P2 - Harden Security And Operations

- Verify Redis ACLs: `router_user` and `toolcache_user` work; default Redis user
  is rejected.
- Verify network segmentation: team containers should not access Redis directly;
  only intended services should reach internal dependencies.
- Verify sandbox behavior: capability drops, read-only filesystem where expected,
  tmpfs writable only where required, and per-worker sandbox profiles.
- Add production dashboard protections: HTTPS, CSP, auth rate limiting, secure
  cookies, restricted Docker socket usage, and external log aggregation.
- Harden outside-LAN access for the dashboard and CEO channel with TLS, explicit
  authentication, audit logs, and firewall/VPN/reverse-proxy rules.
- Complete live shutdown/resume and cold-crash recovery tests with active
  projects and flow instances.
- Reduce metric-cardinality risk before production scale, especially
  project-specific Prometheus labels.

### P3 - Productize The Workflow Platform

- Improve the web flow builder with templates, version history, diff/revert,
  validation explainers, richer branch conditions, approval gates, retries,
  escalations, completion rules, and import/export.
- Let the CEO recommend or invoke managed flow definitions while preserving
  explicit human assignment and override from the UI.
- Make the project detail flow view operational: show current node, active nodes,
  transition history, retry counts, context, blocked/escalated reasons, and
  manual action controls.
- Add hierarchy, communication permission, and orchestration graph overlays that
  can trace possible and actual paths for a project.
- Improve metrics UX: service health, model cost, token usage, tool latency,
  worker health, queue depth, DLQ trends, and project SLA views.
- Add dark/light mode, responsive/mobile layouts, denser operations tables, and
  clearer action states for repeated operator use.

### P4 - Expand Autonomy Safely

- Implement self-development loops only behind scoped tasks, review gates, test
  gates, and change approval.
- Enhance C-suite roles one by one, starting with clearer responsibilities,
  policy boundaries, tool access, dashboard identities, and review obligations.
- Keep the CEO as the single top-level executive copilot, but separate ordinary
  executive orchestration from privileged infrastructure operations.
- Add explicit UI access controls for the CEO identity so the operator can lock or
  expose dashboard sections.
- Add budget, model-routing, and quality controls for all autonomous work.

### P5 - Storage Evolution

- Keep MinIO as the current hot-path object store until a migration is designed
  and tested.
- Evaluate SeaweedFS as the intended MinIO replacement for hot-path object
  storage.
- Treat Garage, R2, or B2 as backup/replication targets, not the primary
  hot-path object store.
- Preserve the architecture split: Postgres metadata and authority, object store
  file bodies, Redis Streams queues, Redis cache, pgvector/hybrid retrieval for
  project context.

## Worker Integration Plan

Workers are configuration-driven integration units. A worker should be described
by YAML and registry records, not deeply embedded into core agent code.

Required worker contract fields:

- identity and display name
- source repository and source revision
- version pin and upstream commit tracking
- adapter module and adapter entrypoint
- runtime requirements
- allowed tools and capability IDs
- schemas, limits, sandbox profile, and update policy
- health, evaluation, and lifecycle status

Repository ingestion policy:

- Mirror upstream repositories into a managed private area.
- Evaluate architecture fit, maintenance, licensing, security, and compatibility.
- Integrate via wrappers/adapters and isolated patches.
- Keep upstream source as untouched as possible so updates remain mergeable.
- Re-pull, validate, and upgrade through compatibility tests.

Candidate priorities from the old worker triage:

- Useful low-risk candidates: LlamaFactory, DeepCode, OfficeCLI, Taipy, Chronos,
  Qlib, Public APIs, PreTeXt, OpenDataLoader PDF, Octokit.js, Newton, TruffleHog.
- Useful but policy-gated candidates: voice/TTS tools, Tor/Qubes/Whonix,
  financial agents, n8n templates, and lightweight browser automation.
- High-risk candidates require controlled legal labs only: offensive security,
  stealth/anti-detect browsers, jailbreak/uncensored-model tools, deepfake tools,
  and dark-web scraping guides.
- Avoid weapon or military-hardware enabling repositories.

## Verification Checklist

Run these before claiming the system is current:

```bash
cd mas
UV_PROJECT_ENVIRONMENT=/tmp/aiat-mas-uv-venv uv run pytest -q
```

```bash
cd mas/apps/mas-dashboard
npm run build
npm run lint
npm run test:protocol-fixtures
npx playwright test --workers=1
```

```bash
mas/infra/compose/mas.sh validate
mas/infra/compose/mas.sh up --build
mas/infra/compose/mas.sh migrate
mas/infra/compose/mas.sh health
```

Current verification on 2026-05-31:

- Full backend pytest: passed, exit 0, 1383 tests collected, no failures.
- Dashboard production build: passed, 47 app routes generated.
- Dashboard lint and protocol fixture/type checks: passed.
- Live dashboard Playwright suite: 23/23 passed against the Compose stack.
- Compose validation, migrations, and health checks: passed.

Manual/live checks:

- Create a project.
- Attach or select a flow.
- Watch the project and flow instance advance.
- Submit a human decision.
- Trigger a retry/archive path.
- Inspect DLQ and replay a safe item.
- Register and evaluate a worker.
- Add/search project context.
- Resolve a credential only through the credentials manager.
- Exercise shutdown/resume with active work.

## Known Technical Debt

- Root-level duplicates and generated dashboard artifacts can confuse tooling.
  Treat `mas/` as the active workspace unless the repo is cleaned.
- The old plan claims and current code status diverged; this file is no longer
  the planning source of truth.
- Some live validation remains environment-specific: Docker, WSL, browser
  runtime dependencies, external LLM providers, and secrets.
- Production observability needs metric-cardinality cleanup, alert channels, and
  log aggregation.
- Production security needs hardening around dashboard auth, external access,
  Docker socket/log access, credential policy, and privileged CEO actions.
- Storage migration from MinIO to SeaweedFS is future work and should not be
  mixed with unrelated workflow or dashboard changes.
