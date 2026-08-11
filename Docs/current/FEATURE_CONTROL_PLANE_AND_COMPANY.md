# Control Plane and Company Feature Specification

**Baseline:** 2026-08-10
**Status:** implemented foundation; company-timezone runner propagation committed as `c955ac8`, prompt/tool reconciliation contract committed as `20f0499`, with bounded review/scanner/Git workspace adapter implementation committed as `5b830e9`; modularisation and release hardening remain
**Authority:** [AIAT Target Programme](../../AIAT_TARGET_PROGRAMME.md)

## Purpose

The control plane turns AIAT from a collection of agents into one governed company. It owns canonical state, compiles the company definition, controls authority and budgets, coordinates shutdown/recovery, and exposes the operator API. External runtimes and providers submit commands and evidence; they do not write canonical state directly.

## Implemented now

- FastAPI control-plane routes for projects, documents, reviews, decisions, context, tasks, DLQ, system state, companies, runtimes, workers, stewards, model profiles, worker runs, flows, credentials, integrations, PM/SCM operations, and CEO actions.
- Versioned company manifests with validation, stable digest, apply, history, and rollback.
- Explicit manifest policy fields for timezone, retention, privacy classes,
  evidence requirements, model constraints, and deployment/sandbox defaults;
  older manifests remain valid without the optional policy blocks.
- Durable company, department, assignment, budget, reservation, and usage-ledger tables.
- A default software-company manifest with 11 authority/manager departments, USD 100 starter budget, 20 concurrent-run limit, human approval for external workers, and gVisor default.
- Authenticated message routing and team runners for all 11 departments.
- Deployed team runners use an authenticated, operation-allowlisted control-
  plane storage API for checkpoints, usage, documents, and review durability;
  database/object-storage credentials remain inside the control plane.
- Explicit CSO veto, CEO override, privileged-action approvals, and shutdown ACK/NACK paths.
- Role-scoped CFO/CTO/CEO action routes use the existing canonical write services
  behind the secret-safe `aiat.executive-action.v1` envelope: CFO model-override
  requests are durable pending records, CTO worker dispatch uses the governed
  run controller, and CEO privileged actions use the audited approval gate.
- The pure `aiat.executive-reconciliation.v1` and `aiat.executive-views.v1`
  read models are committed in `be030ac`; they aggregate bounded durable
  project, usage, worker-run, budget, reservation, and model evidence without
  becoming a second authority. API/dashboard route wiring remains separate.
- Canonical `REVIEW_RESPONSE` publication for `review.submit` and
  `review.submit_veto`, including domain findings and CSO veto evidence; the
  CEO-only `privileged_ops.request` tool routes to the audited
  `/ceo/privileged-action` gate.
- Review responses retain structured findings, recommendations, severity, and
  correlation context; sender identity comes from signed caller context rather
  than model-supplied fields. Managed project workspaces initialize Git without
  bind-mount-sensitive config-lock rewrites, using a bounded inline commit
  identity.
- Organisation and permissions data exposed to the dashboard.

## Code anchors

- Company compiler: [`mas/packages/mas-core/mas_core/company_manifest.py`](../../mas/packages/mas-core/mas_core/company_manifest.py)
- Default company: [`mas/companies/default-software-company.yaml`](../../mas/companies/default-software-company.yaml)
- Control-plane API: [`mas/apps/orchestrator-api/orchestrator_api/main.py`](../../mas/apps/orchestrator-api/orchestrator_api/main.py)
- Policy engine: [`mas/packages/mas-core/mas_core/policy/`](../../mas/packages/mas-core/mas_core/policy/)
- Company migrations: [`mas/migrations/versions/0031_company_control_plane.py`](../../mas/migrations/versions/0031_company_control_plane.py)
- Budget ledger: [`mas/migrations/versions/0033_usage_budget_ledger.py`](../../mas/migrations/versions/0033_usage_budget_ledger.py)
- API request observation ledger: [`mas/migrations/versions/0034_api_request_observations.py`](../../mas/migrations/versions/0034_api_request_observations.py)
- Compose topology: [`mas/infra/compose/docker-compose.yml`](../../mas/infra/compose/docker-compose.yml)
- Executive action routes and envelope: [`mas/apps/orchestrator-api/orchestrator_api/main.py`](../../mas/apps/orchestrator-api/orchestrator_api/main.py) (`/executive/actions/*`)

## Target contract

1. AIAT is the sole control plane for company, project, worker, policy, budget, evidence, and integration authority.
2. Each canonical state machine has one service-layer writer and compare-and-set transitions.
3. Company changes are immutable manifest versions; no environment variable or UI edit silently changes authority.
4. Authority comes from policy, assignments, grants, and signed identity—not prompt wording.
5. Every consequential action carries actor, company, project, correlation, reason, expected revision, and evidence.
6. Budgets reserve before work and settle idempotently after usage.
7. Shutdown, drain, resume, cancellation, veto, approval, and rollback remain available to the human operator.
8. External platforms are adapters/projections and never become a second scheduler or canonical database.

## Remaining gaps

- Break the very large orchestrator module into internal domain routers/services while keeping one coherent transactional control plane.
- [x] Add deterministic generated dashboard and Python SDK contract types with
  operation metadata and compatibility tests; broader external-language SDKs
  remain optional.
- [x] Implement a distinct CEO service identity and persisted section-level dashboard ACLs; native deployment/UI evidence remains.
- [x] Remove direct team-runner PgBouncer/MinIO/shared-service credentials and
  private network membership; native denial/allow evidence remains.
- [x] Replace mutable production image defaults with fixed digest refs or required deployment-supplied immutable `*_IMAGE_REF` values; SBOM/digest reconciliation remains.
- [x] Establish a new current release ledger after the post-July network and governance changes; live release certification remains separate.
- [x] Apply the manifest timezone through runner prompt headers, the `time_now`
  tool, orchestrator scheduler defaults, dashboard display helpers, and
  Compose deployment defaults; unit and type checks cover the configurable
  path. Runner prompt rendering and invalid-zone UTC fallback are covered by
  `c955ac8`.
- [x] Reconcile all 11 authority/manager prompts with the canonical tool
  manifest, concrete registrations, role/team grants, canonical review
  payloads, and the CEO privileged-action route using
  [`check_prompt_tool_reconciliation.py`](../../mas/scripts/check_prompt_tool_reconciliation.py) (`20f0499`).
- [x] Expose bounded role-scoped CFO/CTO/CEO write actions through one
  secret-safe envelope and the existing model-override, worker-dispatch, and
  privileged-action owners; live provider/recovery and broader UI evidence
  remain separate gates.

## Acceptance criteria

- A fresh database reaches the single documented migration head and seeds the same company digest twice without duplicate identities.
- Invalid department references, duplicate assignments, invalid budgets, unknown chiefs, or policy-broadening manifests are rejected.
- Concurrent manifest or budget changes cannot overwrite a newer revision.
- CSO veto, non-CSO veto denial, CEO override, and human approval produce durable audit evidence.
- Team runners contain no provider keys and cannot reach canonical data services directly.
- Shutdown/drain/restart preserves durable work and reports incomplete acknowledgements.
- The dashboard and API render the same company, assignments, permissions, budgets, and active manifest version.

## Not in scope

- Paperclip, Zeenie, TinyHumans, OpenClaw, or another runtime becoming AIAT's authority layer.
- Direct external-provider writes to AIAT tables.
- Prompt text overriding policy or grants.
