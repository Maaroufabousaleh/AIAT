# AIAT Implementation Index

**Scope:** personal/internal AIAT instance
**Implementation/content baseline:** current `main` working tree plus the
three-class sandbox policy and WSL host-bootstrap changes recorded below
**Index date:** 2026-09-19
**Release status:** `NO-RELEASE / P0 INCOMPLETE`

## Purpose

This is the maintained navigation index for AIAT development. It answers four
questions for every major capability:

1. What is implemented in the repository now?
2. What evidence proves that implementation?
3. What remains incomplete, unproven, or operator-dependent?
4. Which document, plan, code area, or test should be opened next?

The [AIAT Roadmap](../../ROADMAP.md) remains the delivery-order document. The
[AIAT Target Programme](../../AIAT_TARGET_PROGRAMME.md) remains the normative
architecture and capability-status document. This index is the compact entry
point between them and the detailed feature specifications, plans, tests, and
evidence. It is not a claim that every target is complete or that every
individual function is enumerated.

## Current development-host status

The WSL2 development host is now independently reproducible through
[`mas/scripts/bootstrap-dev-host.sh`](../../mas/scripts/bootstrap-dev-host.sh).
The latest secret-safe artifact is
[`dev_host_readiness.json`](../../mas/docs/provenance/dev_host_readiness.json).

| Status | Result |
| --- | --- |
| Development | **`DEV_READY`** — dedicated Docker Engine, Compose v2, local Compose services, migration head `0045_worker_tool_effects`, gVisor registration/smoke, and the local network-boundary check pass. |
| Release | **`RELEASE_CERTIFICATION_PENDING`** — WSL2 is not native-Linux release evidence. |
| Sandbox policy | `trusted → runc`, `sandboxed → runsc`, `vm_isolated → Kata`; old profile names remain read-compatible aliases and required classes fail closed. |
| Kata | **`OPTIONAL_UNAVAILABLE`** — `/dev/kvm` is visible, but no Kata runtime is registered or certified on this host. |

This status is deliberately separate from the release ledger's global
`NO-RELEASE` decision. Development is not blocked merely because native-host,
provider, operator, or deployment certification remains open.

## Status vocabulary

| Status | Meaning |
| --- | --- |
| **Implemented** | Present in code and supported by repository tests or durable schema evidence. |
| **Implemented; certification pending** | Implemented, but live, host, provider, security, recovery, or human evidence remains. |
| **Implemented foundation** | The core boundary exists, while the complete target or production proof remains. |
| **Partial** | A meaningful subset exists, but part of the target contract is not implemented or proven. |
| **Target** | Approved work that remains to be implemented. |
| **Optional / experimental** | Available only when deliberately configured or benchmarked; not part of the minimal default path. |

“Built” and “certified” are intentionally separate. A green static or local
fixture does not prove production-provider, native-host, disaster-recovery, or
operator-action completion.

## Completion contract

Every capability is tracked across four separate questions. A capability may
be implemented and locally tested while still being unavailable for an
operator-selected production run.

| Dimension | Meaning | Evidence that belongs here |
| --- | --- | --- |
| **Implemented** | The intended code, schema, API, manifest, or UI boundary exists | Source paths, migrations, manifests, and focused unit/contract tests |
| **Locally tested** | Deterministic repository tests exercise the supported behavior | Pytest/script/dashboard results, fixture certificates, static checkers |
| **Release-certified** | The required host/provider/sandbox/recovery/security evidence exists for the active profile | Current release ledger and retained provenance artifact |
| **Operator-complete** | A human has supplied the external state or approval that code cannot create | Native host, credentials, provider, outage, migration, or promotion evidence |

The capability table below reports the first two dimensions together and calls
out the latter two as remaining gates. When work advances, update the closest
feature document and its evidence record in the same change; then refresh this
index so the implementation and remaining-work views cannot drift apart.

## Latest local validation snapshot

This snapshot was refreshed on 2026-09-19 against the implementation/content
baseline above. The subsequent local history maintenance and this index-only
documentation refresh do not change the implementation tree or the evidence
described here.
It does not
replace the release ledger or close native-host, provider, sandbox, disaster-
recovery, or human/operator gates.

| Check | Result | Scope and limitation |
| --- | --- | --- |
| Source revision at refresh | **`617b62fc`** | The validation run was executed from its parent implementation tree `f68ce9cc`; the resulting architecture/host-readiness change is committed as `617b62fc`. These checks do not close live/operator gates. |
| Documentation authority/index check | **PASS (2026-09-19)** | `uv run --isolated python scripts/check_docs_index.py --json` reports 13 feature documents, 3 plans, 23 maintained/link-checked documents, and no link or policy errors. |
| Migration source-head check | **PASS (2026-09-19)** | `uv run --isolated python scripts/check_database_migration_head.py --json` reports one source head: `0045_worker_tool_effects`; the live development database is also at that head. |
| Release-environment identity probe | **PASS (2026-09-19 UTC)** | The host bootstrap verifies Python, uv, Node, npm, Docker Engine `29.8.1`, Compose `v5.0.0-desktop.1`, and pinned `runsc 20260914.0` through the dedicated `aiat-wsl` context. |
| Native-host, gVisor/Kata, compatibility, and live-network probes | **DEV PASS / RELEASE PENDING** | WSL2 Docker/gVisor registration, digest-pinned smoke, Kata optional probe, local Compose, migration, and live network checks pass. `--require-native-linux` remains a separate release gate; no Kata runtime is registered and direct Firecracker remains superseded compatibility evidence. |
| Standard dependency/test runner | **PASS (2026-09-19)** | The isolated `uv` environment completed the configured repository suite. Two existing non-failing `AsyncMock` resource warnings remain in system tests; live/provider/operator gates remain separate. |
| Broad repository Python suite | **PASS (2026-09-19)** | `uv run --isolated pytest -q` completed the configured `mas/pyproject.toml` test paths at 100%. |
| Python repository test suite | **PASS (2026-09-19)** | `uv run --isolated pytest -q`; all collected tests passed, with two non-failing `AsyncMock` resource warnings in existing system tests. |
| Current repository-local Python component suites | **PASS (2026-09-19)** | Fresh fallback-environment runs pass `packages/mas-core/tests`, `packages/mas-api-sdk/tests`, `apps/orchestrator-api/tests`, `apps/tool-service/tests`, `apps/team-runner/tests`, `apps/message-router/tests`, `apps/identity-service/tests`, `apps/pm-gateway/tests`, `infra/mail-edge/tests`, `infra/smtp-gateway/tests`, and `scripts/tests`; identity/tool suites retain their explicit skips and the orchestrator suite retains two existing non-failing `AsyncMock` warnings. The three live model-smoke CLI scripts under `packages/mas-core/scripts` are not pytest suites and require provider credentials. |
| Script test suite | **PASS (2026-09-19)** | `uv run --isolated pytest -q scripts/tests` and the repository-local fallback both pass; two existing Python 3.14 tar-extraction deprecation warnings remain non-failing. |
| Current focused architecture/documentation suites | **PASS (2026-09-19)** | The repository-local fallback environment passed `test_docs_index.py`, architecture invariants/adapter inventory, ProcessAdapter security, project outbox, runtime-binding/tool-effect, worker-governance, TeamRunner runtime-plane, Redis characterization, and project state-history ownership coverage. This narrower run does not replace the full standard suite. |
| Bounded Python compilation | **PASS** | Checked-in `mas_core`, orchestrator, message-router, team-runner, tool-service, and `mas/scripts` Python roots; generated dashboard dependency trees excluded. |
| Static release ledger | **PASS: 61 active + 3 not-in-scope = 64 checks (2026-09-19)** | The current static invocation has 0 failures/blocked checks and confirms source migration head `0045_worker_tool_effects`; the ledger still returns **NO-RELEASE** because two pending evidence items, no live profile, and the dirty worktree keep the release decision open. |
| Clean-clone static release certificate | **PASS (historical candidate)** | The retained certificate covers the exact pre-repartition candidate `76272905db777829ccf21b61748eb467cafaf645`, with zero changed paths and 64/64 static checks. It is not a certificate for the current working tree; regenerate and deliberately freeze a new exact candidate before using it as release evidence. The scalar certificate is [`release_ledger_clean_candidate_static.json`](../../mas/docs/provenance/release_ledger_clean_candidate_static.json). |
| Database migration-head guard | **PASS static and live** | The normal local Alembic path applied migrations through `0045_worker_tool_effects`; the host-side read-only checker reports the same source and live head. |
| Authenticated local-Compose live release sweep | **74 pass; 0 fail; 10 blocked; 5 not-in-scope** | The host bootstrap ran `check_release_ledger.py --live --compose-local --json` at `2026-09-19T20:21:23Z` through the dedicated `aiat-wsl` Docker daemon. The source/live database head is `0045_worker_tool_effects`; project/network/sandbox/local service checks pass. Remaining blockers are native-host/environment, trace/observability credentials or endpoint availability, deployment-image provenance, SLO/worker-reconciliation/runtime-catalog reachability, and pending operator evidence. Firecracker is superseded, self-improvement is deferred, and the maintained default mail path is `PASS_FOR_DEFAULT_SCOPE`; those are not active blockers. Four pending evidence items and the dirty worktree keep the global decision **NO-RELEASE**. |
| Source/deployment migration boundary | **SOURCE 0045; local Compose DB 0045** | The local development database is current at `0045_worker_tool_effects`; no `alembic_version` shortcut or manual state edit was used. |
| Runtime-adapter inventory/consolidation | **PASS** | Machine-checked reference inventory; LangGraph/CrewAI now share the canonical `runtime_adapters.py` implementation through compatibility shims, while the legacy factory, Letta dotted configuration, and MAF certification references still prevent full-family deletion. |
| OpenCode/OpenHands benchmark plan | **PASS: ready, not run** | The fixed 40-task corpus and 160-run plan validate with no network, provider, credential, or payload activity; the checker deliberately emits `NOT_RUN` with no winner. Live candidate certification and benchmark execution remain operator/runtime gates. | [`check_coding_runtime_benchmark.py`](../../mas/scripts/check_coding_runtime_benchmark.py) · [`corpus.json`](../../mas/scripts/fixtures/coding-runtime-benchmark/corpus.json) · [benchmark specification](../../mas/docs/AIAT_OSS_ARCHITECTURE_AND_IMPLEMENTATION_PLAN.md#opencode-versus-openhands) |
| Pydantic AI vs AgentBase benchmark plan | **PASS: ready, not run** | The fixed 48-case corpus and 288-run plan validate with no Pydantic AI dependency, activation, provider, credential, or payload activity; the checker deliberately emits `NOT_RUN` with no adoption decision. Provider execution and qualitative/code-deletion gates remain open. | [`check_pydantic_agentbase_benchmark.py`](../../mas/scripts/check_pydantic_agentbase_benchmark.py) · [`corpus.json`](../../mas/scripts/fixtures/pydantic-agentbase-benchmark/corpus.json) · [benchmark specification](../../mas/docs/AIAT_OSS_ARCHITECTURE_AND_IMPLEMENTATION_PLAN.md#pydantic-ai-versus-agentbase) |
| Playwright/browser-agent benchmark plan | **PASS: ready, not run** | The fixed 30-workflow/180-run plan validates deterministic-first Playwright, conditional fallback, disposable/no-credential execution, no duplicate consequential effects, and no Stagehand activation. The checker emits `NOT_RUN` with no browser-runtime decision. | [`check_browser_runtime_benchmark.py`](../../mas/scripts/check_browser_runtime_benchmark.py) · [`corpus.json`](../../mas/scripts/fixtures/browser-runtime-benchmark/corpus.json) · [benchmark specification](../../mas/docs/AIAT_OSS_ARCHITECTURE_AND_IMPLEMENTATION_PLAN.md#deterministic-playwright-versus-agent-assisted-browser) |
| Optional memory/workflow service contract | **PASS: 3 candidates** | `check_optional_memory_services.py --json` and the focused 9-test suite validate exact Letta/Qdrant/Temporal adapter contracts, AIAT authority/data boundaries, disabled-by-default policy, measurable-value fields, outage/recovery declarations, and removal definitions without network or mutation. Live value/outage/restore/removal testing remains deliberately unstarted. |
| Worker-placement policy contract | **PASS: 3 cases** | `check_worker_placement.py --json` validates eligible-host selection, capacity ordering, duplicate-host fail-closed behavior, and worker-plane isolation without dispatch or mutation. Durable host registration, live multi-host scheduling, lease settlement, and host-loss/split-brain evidence remain separate. |
| Durable specialist-runtime binding and mediated-tool effects | **PASS** | Migrations `0044_worker_run_runtime_bindings` and `0045_worker_tool_effects`, controller/storage lookup, per-attempt binding/effect persistence, bounded runtime observation, completed-response replay, and fail-closed ambiguous-effect regression coverage; external-runtime/live recovery and provider-specific effect reconciliation remain separate gates. |
| Concrete runtime reconciliation | **PASS: bounded local coverage** | OpenCode consumes a durable session reference after adapter restart; the inactive OpenHands candidate consumes a durable conversation reference and reports conservative REST observations; controller persistence records subordinate status/timestamp without changing canonical worker-run state. Live termination/result recovery remains separate. |
| Reservation/binding settlement hardening | **PASS** | Normal commit/release now updates the reservation and run-host binding on one database connection; failed new assignments release their newly-created reservation. Host-loss reassignment and live fault evidence remain open. |
| Worker lifecycle fixture | **PASS** | Checkpoint, pause/resume, cancellation, cold-crash normalization, lease recovery, and artifact/usage ordering; not a database, canary, sandbox, or live-run certificate. |
| OpenCode candidate triage tooling | **PASS** | Focused parser/classification/image-identity regression suite; candidate remains inactive pending scanner/runtime review. |
| Dashboard lint/typecheck/build/auth | **PASS (2026-09-19)** | `npm run lint`, `npm run typecheck`, `npm run build`, and `npm run test:auth`; the Node-level harness exercises the real JWT/bcrypt module, while production UI/live-provider evidence remains separate. |
| Generated API contract | **PASS (2026-09-19)** | `check_api_contract.py --json`, `generate_python_api.py --check`, and `generate_typescript_api.py --check` agree on 238 OpenAPI paths, 137 schemas/models, and 271 operations; the Python SDK contract tests pass. |
| Dashboard protocol fixture check | **PASS** | Four `aiat.v1` protocol fixtures plus TypeScript typecheck. |
| Typed team metadata/API contract | **PASS** | `GET /teams` now returns the canonical team ID, display name, and policy role; OpenAPI, generated Python/TypeScript contracts, API-contract checks, and hierarchy tests pass. |
| Project document ownership normalization | **PASS** | Document revision/status/read/preview/download and document-backed context routes accept database-string UUIDs while preserving fail-closed foreign/malformed ownership behavior; focused document/API tests pass. |
| Context chunk project ownership | **PASS** | `POST /projects/{project_id}/context/chunks` checks project existence before metadata/chunk insertion; the retrieval API suite passes the unknown-project regression. |
| Project state-history ownership | **PASS** | `GET /projects/{project_id}/state-history` verifies project existence before reading history; existing-project audit reads and unknown-project 404 coverage pass across project, flow, operator, and security suites. |
| Restricted Redis ACL/Lua smoke | **PASS** | Local Redis service recreated from the current Compose ACL migration; authenticated router scripting, atomic stream append, and denied `CONFIG` access were verified, with the probe entry removed afterward. This is not a production outage or multi-host proof. |
| Review-regression suite | **PASS for focused and broad repository-local coverage** | Post-review focused coverage passes for Redis ACL/script authorization, cancellation/recovery races, scanner failure classification and evidence hashes, migration-head compatibility, sandboxed image probing, governance model-provenance producer wiring, project ownership, and state-history ownership. The current configured repository-local Python suite also passes at 100%; one existing non-failing `AsyncMock` warning remains. Live outage, host, provider, schema-migration, and operator gates remain separate. |

Current environment boundary: the WSL2 host-bootstrap result at
`2026-09-19T20:21:24Z` reports `DEV_READY`. Docker Engine/Compose are
reachable through the dedicated `aiat-wsl` context; `runsc` is registered and
the digest-pinned smoke passes; local Compose, migration head `0045`, and the
live network-boundary check pass. `/dev/kvm` is visible, but no Kata runtime is
registered or certified, so `vm_isolated` is `OPTIONAL_UNAVAILABLE`.
The release result remains `RELEASE_CERTIFICATION_PENDING`: this does not claim
native Linux, provider-backed worker, independent-host, deployment, outbound-
mail, operator-selected self-improvement, or Kata certification evidence. The
historical direct Firecracker readiness record remains compatibility evidence
only and is not an active development blocker.

The current working tree is intentionally dirty with pre-existing/user changes;
the validation rows above describe the checked files and do not imply a clean
release candidate or a commit.

## Current capability index

The table is the first stop for future development. Each row links to the
authoritative feature specification, then identifies the principal code and
evidence surfaces. Detailed acceptance criteria and remaining gaps live in the
linked feature document rather than being copied here.

| Capability | Current state | Implemented surface / current evidence | Remaining work or gate | Open next |
| --- | --- | --- | --- | --- |
| Control plane, company, authority, policy, approvals, and budgets | **Implemented** | Orchestrator API, versioned company manifests, org graph, role-scoped actions, policy and budget persistence, and the typed `/teams` ID/name/role registry | Modularisation and broader live/release evidence remain | [Control Plane and Company](FEATURE_CONTROL_PLANE_AND_COMPANY.md) · [Programme A](plans/P1_DEFAULT_PRODUCT_COMPLETION_PLAN.md#workstream-1--modular-control-plane-and-contracts) |
| Governance/company-agent runtime | **Implemented foundation; provider evidence pending** | `mas/apps/team-runner/`, `AgentBase`, executive/C-suite/admin agents, router communication, governance prompts, optional envelope snapshot carrier, scoped TeamRunner snapshot lookup, context-local `GovernanceModelBinding`, exact bound calls including the direct C-suite human-directive path, scoped outbound snapshot propagation and conflict rejection, orchestrator producer-boundary snapshots for fresh `TASK`/`ADMIN_TASK`/`DIRECTIVE`/`QUERY` messages, checkpoint/direct-dispatch snapshot restoration, snapshot-bearing opt-in legacy CEO-fallback binding, and TeamRunner fail-closed rejection of model-bearing messages without snapshots when control-plane storage is active | Provider-backed evidence remains; direct AgentBase compatibility fixtures may still exercise explicitly unbound calls, but deployed TeamRunner no longer falls back to its configured/automatic model for a model-bearing message missing an AIAT snapshot | [Workers, Stewards, Tools, and Models](FEATURE_WORKERS_STEWARDS_AND_MODELS.md#purpose) · [Target Programme §5](../../AIAT_TARGET_PROGRAMME.md#5-company-operating-model) |
| Message routing, Redis Streams, retries, DLQ, and recovery | **Implemented; live hardening evidence pending** | Authenticated publish/subscribe, consumer groups, ACK/NACK, reclaim, retry, TTL, DLQ, replay, trace propagation, Redis-atomic publication/requeue, and restricted-router Lua ACL permissions | Release-level outage, duplicate-effect, and native recovery evidence; preserve at-least-once semantics | [Security, Observability, and Operations](FEATURE_SECURITY_OBSERVABILITY_AND_OPERATIONS.md) · [P0 plan](plans/P0_RELEASE_INTEGRITY_PLAN.md) |
| Specialist worker contract, runs, stewards, manifests, and certification | **Implemented foundation; external-runtime certification pending** | `aiat.worker.v1`, worker-run controller, manifests, runtime catalogue, steward/candidate/certification/rollout/rollback models, compatible runtime reconciliation/cancellation receipts, durable per-attempt runtime bindings (`0044_worker_run_runtime_bindings`), durable mediated-tool effects (`0045_worker_tool_effects`), and bounded OpenCode/OpenHands runtime lookups | Adapter-specific live termination/result recovery, provider-specific external-effect reconciliation, default-worker live certification, sandbox proof, canary/rollback, provider-backed execution | [Workers, Stewards, Tools, and Models](FEATURE_WORKERS_STEWARDS_AND_MODELS.md) · [Worker-Plane Host Execution](FEATURE_WORKER_HOST_EXECUTION.md) |
| Host placement, reservations, bindings, leases, fencing, and recovery | **Implemented foundation** | `HostScheduler`, reservations, `HostExecutor`, generation fencing, host recovery, durable local certificates | Native independent-host, split-brain, chaos, and disaster-recovery evidence | [Worker-Plane Host Execution](FEATURE_WORKER_HOST_EXECUTION.md) · [P2 scale plan](plans/P2_SCALE_STORAGE_AND_AUTONOMY_PLAN.md#workstream-3--multi-host-and-high-risk-execution) |
| Tools, MCP, privileged operations, and browser identity | **Implemented foundation** | Tool registry/service, grants, policy, rate/concurrency controls, audit, MCP bridges, identity-mediated browser leases | Broader live/provider/browser certification and hardened untrusted-runtime execution | [Workers, Stewards, Tools, and Models](FEATURE_WORKERS_STEWARDS_AND_MODELS.md#implemented-now) · [Identity, Mail, Credentials, and External Accounts](FEATURE_IDENTITY_MAIL_AND_CREDENTIALS.md) |
| Model governance, gateway, routing, usage, and executive views | **Implemented foundation; provider evidence pending** | Model profiles/resolution snapshots, LiteLLM/OmniRoute routes, usage/budget records, cooldown/fallback state, executive views/actions, optional envelope snapshot carrier, scoped per-dispatch `GovernanceModelBinding` for TeamRunner/AgentBase, exact bound C-suite one-turn handling, outbound governance-message propagation and mismatch rejection, orchestrator producer-boundary snapshots for fresh governance messages, snapshot-bearing opt-in CEO-fallback binding, and TeamRunner fail-closed handling for missing snapshots | Add uniform provider evidence, provider-backed failover/recovery, immutable deployment pin evidence, and complete the direct AgentBase compatibility/producer inventory; deployed TeamRunner no longer permits unbound automatic fallback for model-bearing messages | [Workers, Stewards, Tools, and Models](FEATURE_WORKERS_STEWARDS_AND_MODELS.md#implemented-now) · [SLO and Capacity](FEATURE_SLO_CAPACITY_AND_OPERATIONS.md) |
| Projects, documents, reviews, approvals, issues, context, and evidence | **Implemented** | Canonical project API/storage, explicit project-scoped state-history reads (`73e16c33`), transition history, project-transition outbox and bounded retry dispatcher (migration `0043_project_transition_outbox`), database-shaped document ownership normalization for revision/status/read/preview/download/context routes (`165db488`), project-existence enforcement before context chunk insertion (`e0a50323`), evidence package/detail views, artifacts, audit timeline, context and repository workspace | Full live worker/provider generation and release-grade recovery proof; project-outbox live outage evidence | [Projects, Flows, Knowledge, and Evidence](FEATURE_PROJECTS_FLOWS_AND_EVIDENCE.md) · [P0 plan](plans/P0_RELEASE_INTEGRITY_PLAN.md) |
| Visual flows and governed execution | **Implemented foundation** | React Flow authoring, nine node types, versioning, templates, validation, traversal, fan-out/join/switch, retry, watchdog, migration, worker binding | Live worker canary/recovery and complete native/operator evidence | [Projects, Flows, Knowledge, and Evidence](FEATURE_PROJECTS_FLOWS_AND_EVIDENCE.md#flow-authoring-target) · [P1 plan](plans/P1_DEFAULT_PRODUCT_COMPLETION_PLAN.md#workstream-3--flow-and-evidence-completion) |
| Identity, credentials, external accounts, mail, and browser sessions | **Implemented; default path certified** | Dedicated identity service, credential manager, approval/lease/revocation controls, provider-neutral mail, default Cloudflare/Resend path | Optional profiles, broader outage/restore, key/domain migration, and unrelated provider evidence | [Identity, Mail, Credentials, and External Accounts](FEATURE_IDENTITY_MAIL_AND_CREDENTIALS.md) · [Mail-Edge Observability](FEATURE_MAIL_EDGE_OBSERVABILITY.md) |
| PM and source-control integrations | **PM implemented; active/GitHub certification pending** | Provider-neutral ports, YouTrack projections/inbox/outbox/reconciliation, governed GitHub contract and fixtures | YouTrack ACTIVE command certification and complete GitHub App live matrix | [PM and Source-Control Integrations](FEATURE_INTEGRATIONS_PM_AND_SCM.md) · [P1 identity/collaboration workstream](plans/P1_DEFAULT_PRODUCT_COMPLETION_PLAN.md#workstream-4--identity-and-external-collaboration) |
| Data, storage, memory, retention, backup, and migration | **Implemented foundation / partial** | Postgres/pgvector, Redis, MinIO, object conformance/copy/backup/restore/encryption/multipart/lifecycle/migration fixtures | Provider-managed KMS/key custody, production cutover, clean-host, disaster-recovery, optional service value | [Data, Storage, Memory, and Retention](FEATURE_DATA_STORAGE_AND_MEMORY.md) · [Object-Store Migration Status](FEATURE_OBJECT_STORE_MIGRATION_STATUS.md) |
| Trace evidence, retention, SLOs, capacity, and analytics | **Implemented foundation; live evidence pending** | Payload-safe request/message/tool/worker spans, evidence joins, incident/read-only projections, retention planner, SLO/capacity read models | Live model/tool/provider coverage, authoritative hold/erasure/restore, load/soak/chaos evidence | [Trace Evidence and Retention](FEATURE_TRACE_EVIDENCE_AND_RETENTION.md) · [SLO, Capacity, and Operational Forecast](FEATURE_SLO_CAPACITY_AND_OPERATIONS.md) |
| Security, sandboxing, network, supply chain, and operations | **Partial** | Policy-backed network matrix, Compose restrictions, scanner aliases, canonical `trusted`/`sandboxed`/`vm_isolated` classes with legacy aliases, gVisor/runsc and Kata runtime selection contracts, direct Firecracker compatibility checks, diagnostics, control CLI, image/provenance checks, and OpenCode candidate triage tooling | Native host proof, image/SBOM/scan disposition, certified Kata `vm_isolated` host evidence, clean-host and disaster recovery | [Security, Observability, and Operations](FEATURE_SECURITY_OBSERVABILITY_AND_OPERATIONS.md) · [P0 plan](plans/P0_RELEASE_INTEGRITY_PLAN.md) · [sandbox policy](../../mas/docs/AIAT_OSS_ARCHITECTURE_AND_IMPLEMENTATION_PLAN.md#isolation-policy-migration-runc--gvisor--kata) |
| Dashboard and operator experience | **Implemented foundation; production UX evidence pending** | Projects, flows, workers, governance, tools, integrations, credentials, identity, CEO, analytics, logs, streams, DLQ, system controls and visualization | Native-Linux/browser matrix, page-by-page visual parity, full live provider/worker golden paths | [Dashboard and Operator UX](FEATURE_DASHBOARD_AND_OPERATOR_UX.md) · [P1 operator UX](plans/P1_DEFAULT_PRODUCT_COMPLETION_PLAN.md#workstream-6--complete-operator-ux) |
| Self-development and guarded autonomy | **Implemented contract; live lifecycle pending** | Candidate detection, canonical self-improvement projects, evidence/artifact manifests, gates, approvals, rollout, rollback, bounded outcomes | Live worker/provider/deployment lifecycle and human promotion evidence | [P2 scale plan](plans/P2_SCALE_STORAGE_AND_AUTONOMY_PLAN.md#workstream-4--guarded-self-improvement) · [Target Programme §16](../../AIAT_TARGET_PROGRAMME.md#16-safe-self-development-and-autonomy) |

## Authoritative document map

### Programme and delivery order

| Need | Document |
| --- | --- |
| Normative architecture, target capabilities, status definitions, and completion gates | [AIAT Target Programme](../../AIAT_TARGET_PROGRAMME.md) |
| Ordered roadmap, implementation baseline, evidence register, milestones, backlog, and Now/Next/Later | [AIAT Roadmap](../../ROADMAP.md) |
| OSS architecture, simplification, benchmark decisions, and implementation sequencing | [AIAT OSS Architecture and Implementation Plan](../../mas/docs/AIAT_OSS_ARCHITECTURE_AND_IMPLEMENTATION_PLAN.md) |
| Documentation authority, maintained set, and link/policy validation | [Documentation Authority Status](DOCUMENTATION_AUTHORITY_STATUS.md) |
| Current release evidence, blocked gates, and release decision | [AIAT Current Release Ledger](../../mas/docs/AIAT_CURRENT_RELEASE_LEDGER.md) |

### Current feature specifications

| Feature area | Specification |
| --- | --- |
| Control plane and company | [FEATURE_CONTROL_PLANE_AND_COMPANY.md](FEATURE_CONTROL_PLANE_AND_COMPANY.md) |
| Dashboard and operator UX | [FEATURE_DASHBOARD_AND_OPERATOR_UX.md](FEATURE_DASHBOARD_AND_OPERATOR_UX.md) |
| Data, storage, memory, and retention | [FEATURE_DATA_STORAGE_AND_MEMORY.md](FEATURE_DATA_STORAGE_AND_MEMORY.md) |
| Identity, mail, credentials, and external accounts | [FEATURE_IDENTITY_MAIL_AND_CREDENTIALS.md](FEATURE_IDENTITY_MAIL_AND_CREDENTIALS.md) |
| PM and source-control integrations | [FEATURE_INTEGRATIONS_PM_AND_SCM.md](FEATURE_INTEGRATIONS_PM_AND_SCM.md) |
| Mail-edge and provider observations | [FEATURE_MAIL_EDGE_OBSERVABILITY.md](FEATURE_MAIL_EDGE_OBSERVABILITY.md) |
| Object-store migration review | [FEATURE_OBJECT_STORE_MIGRATION_STATUS.md](FEATURE_OBJECT_STORE_MIGRATION_STATUS.md) |
| Projects, flows, knowledge, and evidence | [FEATURE_PROJECTS_FLOWS_AND_EVIDENCE.md](FEATURE_PROJECTS_FLOWS_AND_EVIDENCE.md) |
| Security, observability, and operations | [FEATURE_SECURITY_OBSERVABILITY_AND_OPERATIONS.md](FEATURE_SECURITY_OBSERVABILITY_AND_OPERATIONS.md) |
| SLO, capacity, and operational forecast | [FEATURE_SLO_CAPACITY_AND_OPERATIONS.md](FEATURE_SLO_CAPACITY_AND_OPERATIONS.md) |
| Trace evidence and retention | [FEATURE_TRACE_EVIDENCE_AND_RETENTION.md](FEATURE_TRACE_EVIDENCE_AND_RETENTION.md) |
| Workers, stewards, tools, and models | [FEATURE_WORKERS_STEWARDS_AND_MODELS.md](FEATURE_WORKERS_STEWARDS_AND_MODELS.md) |
| Worker-plane host execution | [FEATURE_WORKER_HOST_EXECUTION.md](FEATURE_WORKER_HOST_EXECUTION.md) |

### Ordered implementation plans

| Phase | Plan |
| --- | --- |
| P0 release integrity | [P0 Release Integrity Plan](plans/P0_RELEASE_INTEGRITY_PLAN.md) · [P0 Status](P0_RELEASE_INTEGRITY_STATUS.md) |
| P1 default programme completion | [P1 Default Programme Completion Plan](plans/P1_DEFAULT_PRODUCT_COMPLETION_PLAN.md) |
| P2 scale and guarded autonomy | [P2 Scale, Storage, and Guarded Autonomy Plan](plans/P2_SCALE_STORAGE_AND_AUTONOMY_PLAN.md) |

### Current status and evidence notes

These documents are maintained implementation/status records rather than
additional architectural authorities. Open the row matching the subsystem
before changing code or interpreting a green fixture as a release result.

| Area | Status/evidence document |
| --- | --- |
| Release scope and operator decision | [P0 Release Scope Matrix](P0_RELEASE_SCOPE_MATRIX.md) |
| P0 implementation status | [P0 Release Integrity Status](P0_RELEASE_INTEGRITY_STATUS.md) |
| Flow definition portability | [Flow Definition Portability Status](FLOW_DEFINITION_PORTABILITY_STATUS.md) |
| Flow execution semantics | [Flow Execution Semantics Status](FLOW_EXECUTION_SEMANTICS_STATUS.md) |
| Flow instance migration | [Flow Instance Migration Status](FLOW_INSTANCE_MIGRATION_STATUS.md) |
| Flow instance recovery | [Flow Instance Recovery Status](FLOW_INSTANCE_RECOVERY_STATUS.md) |
| Legacy task migration | [Flow Legacy Task Migration Status](FLOW_LEGACY_TASK_MIGRATION_STATUS.md) |
| Flow node-schema generation | [Flow Node Schema Generation Status](FLOW_NODE_SCHEMA_GENERATION_STATUS.md) |
| Flow node-schema topology | [Flow Node Schema Topology Status](FLOW_NODE_SCHEMA_TOPOLOGY_STATUS.md) |
| Flow templates | [Flow Template Status](FLOW_TEMPLATE_STATUS.md) |
| Flow worker binding | [Flow Worker Binding Status](FLOW_WORKER_BINDING_STATUS.md) |
| Workflow watchdog/recovery | [Workflow Watchdog Recovery Status](WORKFLOW_WATCHDOG_RECOVERY_STATUS.md) |
| PM inbound canary | [PM Inbound Canary Status](PM_INBOUND_CANARY_STATUS.md) |
| Trace retention review | [Trace Retention Review Status](TRACE_RETENTION_REVIEW_STATUS.md) |
| Documentation authority | [Documentation Authority Status](DOCUMENTATION_AUTHORITY_STATUS.md) |

### Service, deployment, and integration runbooks

| Need | Operational document |
| --- | --- |
| AIAT service architecture | [AIAT service architecture](../../mas/docs/ARCHITECTURE.md) |
| Model gateway and routing | [OmniRoute and model gateway](../../mas/docs/OMNIROUTE.md) |
| PM integration design | [PM Integration Plan](../../mas/docs/PM_INTEGRATION_PLAN.md) |
| PM integration operations | [PM Integration Runbook](../../mas/docs/PM_INTEGRATION_RUNBOOK.md) |
| PM readiness | [PM Active Readiness](../../mas/docs/PM_ACTIVE_READINESS.md) |
| PM certification evidence | [PM Active Certification Ledger](../../mas/docs/PM_ACTIVE_CERTIFICATION_LEDGER.md) |
| PM active dashboard | [PM Active Dashboard](../../mas/docs/PM_ACTIVE_DASHBOARD.md) |
| PM active deployment | [PM Active Deployment](../../mas/docs/PM_ACTIVE_DEPLOYMENT.md) |
| Native release-host procedure | [P0 Native-Linux Exit Runbook](../../mas/docs/P0_NATIVE_LINUX_EXIT_RUNBOOK.md) |
| Local sandbox boundary | [Sandbox README](../../mas/infra/sandbox/README.md) |
| Compose deployment | [Compose README](../../mas/infra/compose/README.stalwart-local.md) |
| Systemd deployment | [Systemd README](../../mas/infra/systemd/README.md) |
| Mail edge | [Mail-edge README](../../mas/infra/mail-edge/README.md) |
| SMTP gateway | [SMTP gateway README](../../mas/infra/smtp-gateway/README.md) |
| Cloudflare mail worker | [Cloudflare email-worker README](../../mas/infra/cloudflare/email-worker/README.md) |
| MAF runtime profile | [Microsoft Agent Framework runtime README](../../mas/infra/runtime/maf/README.md) |
| OpenCode fixture contract | [OpenCode event fixtures](../../mas/docs/opencode/phase0b/1.17.13/event-fixtures/README.md) · [request/response fixtures](../../mas/docs/opencode/phase0b/1.17.13/request-response-fixtures/README.md) |

### Supporting plans, setup guides, and historical references

These links are useful when implementing a specific integration, but they do
not override the target programme, roadmap, feature specifications, or plans
above. Historical research should be treated as context; current code and
current evidence win when they disagree.

| Area | References |
| --- | --- |
| Email identity implementation | [Provider architecture](../../Docs/AIAT_Email_Identity_Provider_Architecture.md) · [Implementation map](../../Docs/AIAT_Email_Identity_Implementation_Map.md) · [Changed files](../../Docs/AIAT_Email_Identity_Changed_Files.md) · [Domain migration](../../Docs/AIAT_Email_Identity_Domain_Migration.md) · [Live certification](../../Docs/AIAT_Email_Identity_Live_Certification.md) |
| PM provider setup | [PM platform ADR](../../Docs/PM_Platform_Integration_ADR.md) · [PM integration plan](../../Docs/PM_Platform_Integration_Plan.md) · [PM runbook](../../Docs/PM_Platform_Integration_Runbook.md) · [YouTrack setup](../../Docs/PM_Platform_YouTrack_Setup.md) · [GitHub setup](../../Docs/PM_Platform_GitHub_Setup.md) |
| PM operator guides | [Adapter authoring](../../Docs/PM_Platform_Adapter_Authoring.md) · [Deployment](../../Docs/PM_Platform_Deployment.md) · [Dashboard guide](../../Docs/PM_Platform_Dashboard_Guide.md) · [Certification ledger](../../Docs/PM_Platform_Certification_Ledger.md) |
| Architecture background | [Modular AI company OS](<../../Docs/AIAT as a Modular AI Company Operating System.md>) · [Deep research implementation plan](../../Docs/AIAT_Deep_Research_Implementation_Plan.md) |
| Repository/service entry points | [Root README](../../README.md) · [MAS README](../../mas/README.md) · [Tools](../../tools.md) · [Third-party notices](../../THIRD_PARTY_NOTICES.md) |

Archived drafts and superseded research remain in [Docs/archive](../archive/README.md).
They are retained for provenance and are not implementation authorities.

### Supporting evidence and policy

| Need | Document or source |
| --- | --- |
| Third-party version/source/licence metadata | [third-party component catalogue](../../mas/docs/provenance/third_party_components.yaml) · [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md) |
| Native-Linux release exit procedure | [P0 Native-Linux Exit Runbook](../../mas/docs/P0_NATIVE_LINUX_EXIT_RUNBOOK.md) |
| Mail-edge and default identity certification | [AIAT Email Identity Live Certification](../../Docs/AIAT_Email_Identity_Live_Certification.md) |
| Current machine-checked documentation set | `uv run --isolated python scripts/check_docs_index.py --json` from `mas/` |

## Development queue

The roadmap's milestone sections are authoritative; this queue is only a
compact pointer to the next work.

### Now — release integrity and truth gaps

- Close remaining P0 native-host, immutable-image, sandbox, security-review,
  clean-worktree, recovery, and provider evidence gates.
- Keep the global release decision explicit until every required gate has
  current durable evidence.
- Reconcile any code, schema, manifest, evidence, and documentation drift.

See [Roadmap §4 R1](../../ROADMAP.md#r1--release-integrity) and the [P0
Release Integrity Plan](plans/P0_RELEASE_INTEGRITY_PLAN.md).

### Next — default programme completion

- Certify the default specialist-worker bindings and live execution path.
- Complete flow/worker recovery, PM/SCM, model governance, and operator UX
  evidence.
- Keep optional runtimes disabled unless their value and removal path are
  demonstrated.

See [Roadmap §4 R2–R5](../../ROADMAP.md#r2--certified-default-workforce) and
the [P1 plan](plans/P1_DEFAULT_PRODUCT_COMPLETION_PLAN.md).

### Later — scale and guarded autonomy

- Complete storage/provider/retention/restore decisions.
- Evaluate optional memory/workflow services, high-risk execution, and
  multi-host evidence.
- Advance self-improvement only through independent technical gates and human
  promotion.

See [Roadmap §4 R6–R7](../../ROADMAP.md#r6--storage-and-multi-host-scale) and
the [P2 plan](plans/P2_SCALE_STORAGE_AND_AUTONOMY_PLAN.md).

### Explicitly unfinished plan checkpoints

These are the remaining unchecked P2 plan items visible in the maintained plan
files. They require operator-selected external state or a certified release
host; they are not silently treated as implemented because their local
contracts and fixtures pass.

### Plan checklist audit

The checked-item counts below describe plan bookkeeping, not a release claim;
the capability table and evidence documents remain authoritative for whether a
checked item is implemented, certified, or still bounded.

| Plan | Checked items | Unchecked items | Interpretation |
| --- | ---: | ---: | --- |
| [P0 Release Integrity](plans/P0_RELEASE_INTEGRITY_PLAN.md) | 43 | 0 | Repository-local P0 deliverables are recorded complete; live/operator exit gates remain open. |
| [P1 Default Programme Completion](plans/P1_DEFAULT_PRODUCT_COMPLETION_PLAN.md) | 162 | 0 | Repository-local P1 deliverables are recorded complete; default-worker, provider, sandbox, recovery, and UX certification remain bounded where stated. |
| [P2 Scale, Storage, and Guarded Autonomy](plans/P2_SCALE_STORAGE_AND_AUTONOMY_PLAN.md) | 94 | 3 | Three live/operator-dependent checkpoints remain explicitly unchecked and are listed below. |

| Checkpoint | Current state | Required evidence | Authority |
| --- | --- | --- | --- |
| Optional memory/workflow service live value, outage, backup/restore, and removal tests | **Contract PASS; live not started; disabled by default** | The optional-service catalogue/checker and injected-backend Qdrant/Temporal tests pass locally; operator-selected exact endpoints, versions, budgets, certified sandboxes, value comparison, outage/restore, and clean removal remain required | [Optional-service contract](../../mas/docs/provenance/optional_memory_services_contract.json) · [P2 Workstream 2](plans/P2_SCALE_STORAGE_AND_AUTONOMY_PLAN.md#workstream-2--optional-memory-and-workflow-services) |
| External provider-backed dispatch across independent/multi-host Kata/gVisor boundaries | **Local provider-shaped retry only** | The local durable provider-shaped retry certificate passes; real provider execution, independent host/process loss, gVisor/Kata sandbox, callback/delivery, and recovery evidence remain required | [Provider-shaped recovery evidence](../../mas/docs/provenance/gateway_worker_mail_edge_provider_recovery_postgres_evidence.json) · [P2 Workstream 3](plans/P2_SCALE_STORAGE_AND_AUTONOMY_PLAN.md#workstream-3--multi-host-and-high-risk-execution) |
| Independent deployed-host loss, split-brain avoidance, queue recovery, duplicate-effect protection, and complete version pinning | **Local control-plane prerequisites pass; deployed proof absent** | Local multi-host, independent-process, lease/recovery, duplicate-effect, and version-pinning certificates are retained; two or more independently controlled hosts/processes, fault injection, durable replay/reconciliation, and zero duplicate protected effects remain required | [Multi-host evidence](../../mas/docs/provenance/worker_multi_host_execution_postgres_evidence.json) · [Independent-process evidence](../../mas/docs/provenance/worker_independent_process_execution_postgres_evidence.json) · [P2 Workstream 3](plans/P2_SCALE_STORAGE_AND_AUTONOMY_PLAN.md#workstream-3--multi-host-and-high-risk-execution) |

### Roadmap-level release and evidence gates

The roadmap's Now/Later narrative contains several multi-clause unchecked
evidence gates that are not plan-file checklist rows. They are listed here so
the index counts all remaining work without marking local contracts as live
certification.

| Roadmap gate | Current state | Required evidence | Authority / next document |
| --- | --- | --- | --- |
| Native-Linux dashboard/UI matrix and broader WCAG/mobile/visual evidence | **Local WSL2 matrix and focused page baselines pass; native run open** | Repeat the 58/59 dashboard/UI matrix and the page-level accessibility/visual checks on the certified native-Linux host | [Dashboard and Operator UX](FEATURE_DASHBOARD_AND_OPERATOR_UX.md) · [Roadmap §6 Now](../../ROADMAP.md#now--r1p0) |
| Native-Linux metrics and clean release-host resource evidence | **Local bounded scrape passes; native release-host run open** | Repeat the many-project bounded scrape and clean-host resource/archive evidence on the release host | [SLO, Capacity, and Operational Forecast](FEATURE_SLO_CAPACITY_AND_OPERATIONS.md) · [Roadmap §6 Now](../../ROADMAP.md#now--r1p0) |
| Native-Linux tool image, SBOM, scanner, network, sandbox, and recovery evidence | **Static/local contracts pass; native deployment proof open** | Run the split-image archive/SBOM/scan checks, network deny/allow matrix, gVisor smoke, and recovery checks on the certified host | [Security, Observability, and Operations](FEATURE_SECURITY_OBSERVABILITY_AND_OPERATIONS.md) · [P0 Native-Linux Exit Runbook](../../mas/docs/P0_NATIVE_LINUX_EXIT_RUNBOOK.md) |
| Frozen release-ledger refresh | **Deferred until native/live evidence is current** | Re-run the release ledger against an exact clean candidate after the preceding gates are complete and retain the new immutable result | [AIAT Current Release Ledger](../../mas/docs/AIAT_CURRENT_RELEASE_LEDGER.md) · [Roadmap §6 Now](../../ROADMAP.md#now--r1p0) |
| Live specialist-worker dispatch, provider/sandbox recovery, and Kata `vm_isolated` pools | **Local provider-shaped/control-plane certificates pass; live boundary open** | Prove real worker dispatch, provider outage/recovery, gVisor/Kata sandbox operation, and Kata-capable `vm_isolated` pool behavior across the certified execution boundary; direct Firecracker remains compatibility evidence | [Workers, Stewards, Tools, and Models](FEATURE_WORKERS_STEWARDS_AND_MODELS.md) · [P2 Workstream 3](plans/P2_SCALE_STORAGE_AND_AUTONOMY_PLAN.md#workstream-3--multi-host-and-high-risk-execution) |
| Guarded self-improvement live lifecycle and independent recovery | **Local lifecycle/rollback contract passes; live lifecycle open** | Exercise issue creation, worker execution, provider/deployment rollout, rollback, and independent recovery with human promotion evidence | [P2 Workstream 4](plans/P2_SCALE_STORAGE_AND_AUTONOMY_PLAN.md#workstream-4--guarded-self-improvement) · [Target Programme §16](../../AIAT_TARGET_PROGRAMME.md#16-safe-self-development-and-autonomy) |
| Production SLO, capacity, soak, chaos, and disaster-recovery cadence | **Targets and deterministic forecasts exist; production evidence open** | Establish the native operational cadence and retain load/soak/chaos/backup/restore results for the active profile | [SLO, Capacity, and Operational Forecast](FEATURE_SLO_CAPACITY_AND_OPERATIONS.md) · [Roadmap §7 R6–R7](../../ROADMAP.md#r6--storage-and-multi-host-scale) |
| Deployed external callback/delivery, provider outage, hold, audit, erasure, and restore rollback | **Local trace/provider-shaped evidence passes; deployed boundary open** | Prove external callback/delivery and provider outage behavior, production hold authority, durable audit/erasure, and restore rollback on the selected deployment | [Trace Evidence and Retention](FEATURE_TRACE_EVIDENCE_AND_RETENTION.md) · [Roadmap §7 R6–R7](../../ROADMAP.md#r6--storage-and-multi-host-scale) |

### Feature-spec-only release gate

The following gate is intentionally listed separately because it is an open
checkbox in a maintained feature specification, not one of the three unchecked
P2 plan checkpoints:

| Gate | Current state | Required evidence | Authority |
| --- | --- | --- | --- |
| gVisor smoke/network and Kata `vm_isolated` host proof | **WSL development PASS; native release proof pending** | The dedicated WSL2 `aiat-wsl` Docker daemon registers pinned `runsc 20260914.0` and the digest-pinned smoke passes. The local network-boundary check also passes. A certified native host must still run the release gVisor deny/allow matrix and, for high-risk work, Kata `vm_isolated` runtime/readiness, network, cleanup, and recovery checks. Direct Firecracker remains compatibility/benchmark evidence; no weaker runtime fallback is permitted. | [Workers, Stewards, Tools, and Models](FEATURE_WORKERS_STEWARDS_AND_MODELS.md) · [P2 Workstream 3](plans/P2_SCALE_STORAGE_AND_AUTONOMY_PLAN.md#workstream-3--multi-host-and-high-risk-execution) · [Native-Linux exit runbook](../../mas/docs/P0_NATIVE_LINUX_EXIT_RUNBOOK.md) · [WSL readiness artifact](../../mas/docs/provenance/dev_host_readiness.json) |

## Completed implementation slices tracked here

These entries record repository work already present in the current working
tree. They do not convert pending live, provider, host, security, recovery, or
human-evidence gates into completion claims.

| Slice | Current result | Verification pointer |
| --- | --- | --- |
| PR A — architecture invariants and characterization | Ownership tests cover the project lifecycle, split specialist worker-run ownership, separate TeamRunner/AgentBase governance runtime, legacy adapter references, failure boundaries, model/tool/evidence distinctions, and current restart/cancellation behavior | `mas/packages/mas-core/tests/test_architecture_*.py`, `mas/apps/team-runner/tests/test_architecture_runtime_planes.py`, `mas/apps/message-router/tests/test_architecture_failure_characterization.py` |
| PR B — ProcessAdapter secret/environment isolation | Explicit child environment default, validation, secret-safe output/stderr redaction, and regression coverage are present; this is still subject to the repository's live-host certification gates | `mas/packages/mas-core/mas_core/worker_registry/runtime_adapters.py`, `mas/packages/mas-core/tests/test_process_adapter_security.py` |
| PR C — project-transition transactional outbox | Project state/history and notification intent are committed together; the orchestrator drains due rows using stable message IDs and leaves failed rows retryable | `mas/migrations/versions/0043_project_transition_outbox.py`, `mas/packages/mas-core/tests/test_project_transition_outbox.py`, `mas/apps/orchestrator-api/tests/test_project_transition_outbox.py` |
| PR D — Redis publication/requeue atomicity | Point-to-point and broadcast publication, plus production reclaim/requeue, now use Redis-atomic scripts with stable duplicate results; Redis remains at-least-once transport | `mas/apps/message-router/message_router/redis_client.py`, `mas/apps/message-router/message_router/routes_publish.py`, `mas/apps/message-router/message_router/tasks.py`, `mas/apps/message-router/tests/test_architecture_failure_characterization.py` |
| PR F — worker restart/reconciliation/cancellation and mediated-effect hardening | Compatible runtime-status observation and cancellation receipts are present; durable per-attempt runtime bindings provide canonical external-reference lookup, OpenCode and the inactive OpenHands candidate consume those references for bounded status observation, the controller records subordinate observations without changing canonical run state, and mediated tool outcomes are durably replayed or fail closed when ambiguous. In-process maps remain process-local; live/durable external-runtime termination/result recovery and provider-specific external-effect reconciliation are still open | `mas/migrations/versions/0044_worker_run_runtime_bindings.py`, `mas/migrations/versions/0045_worker_tool_effects.py`, `mas/packages/mas-core/mas_core/memory/models.py`, `mas/packages/mas-core/mas_core/memory/storage.py`, `mas/packages/mas-core/mas_core/worker_contract/controller.py`, `mas/packages/mas-core/mas_core/worker_registry/runtime_adapters.py`, `mas/packages/mas-core/mas_core/worker_registry/openhands_agent_server_adapter.py`, `mas/packages/mas-core/tests/test_worker_runtime_bindings.py`, `mas/packages/mas-core/tests/test_worker_tool_effects.py`, `mas/packages/mas-core/tests/test_worker_governance.py`, `mas/packages/mas-core/tests/test_openhands_agent_server_adapter.py`, `mas/packages/mas-core/tests/test_architecture_invariants.py`, `mas/scripts/check_worker_run_lifecycle.py` |
| PR E — governance AgentBase model-provenance parity (bounded runtime complete; provider evidence pending) | Snapshot-bearing TeamRunner dispatches use a scoped immutable AIAT decision through `GovernanceModelBinding`; bound AgentBase calls, including the direct C-suite human-directive one-turn path, use the exact model and record safe usage provenance; bound AgentBase outbound project messages propagate the same snapshot and reject conflicting bindings; the orchestrator attaches persisted snapshots at the fresh governance-message producer boundary; checkpoint resumes, direct snapshot-bearing AgentBase dispatch, and the snapshot-bearing opt-in legacy CEO fallback restore or resolve the same scoped decision; deployed TeamRunner rejects model-bearing messages without a snapshot and enables AgentBase binding-required enforcement for every model call, while explicit direct AgentBase compatibility fixtures remain unbound by design | `mas/apps/orchestrator-api/orchestrator_api/main.py`, `mas/apps/orchestrator-api/tests/test_governance_model_provenance.py`, `mas/apps/orchestrator-api/tests/test_ceo_chat.py`, `mas/packages/mas-core/mas_core/agent_runtime/config.py`, `mas/packages/mas-core/mas_core/agent_runtime/base.py`, `mas/packages/mas-core/mas_core/agent_runtime/csuite.py`, `mas/packages/mas-core/mas_core/protocols/envelope.py`, `mas/apps/team-runner/team_runner/main.py`, `mas/apps/team-runner/team_runner/storage_client.py`, `mas/packages/mas-core/tests/test_architecture_invariants.py`, `mas/apps/team-runner/tests/test_architecture_runtime_planes.py`, `mas/apps/team-runner/tests/test_shutdown.py` |
| PR G — runtime-adapter consolidation (partial) | LangGraph/CrewAI framework behavior now lives in the canonical `runtime_adapters.py` family; the old module paths remain thin compatibility shims. The machine-readable inventory still tracks the legacy factory, Letta dotted configuration, and MAF certification references before any deletion | `mas/packages/mas-core/mas_core/worker_registry/runtime_adapters.py`, `mas/packages/mas-core/mas_core/worker_registry/langgraph_adapter.py`, `mas/packages/mas-core/mas_core/worker_registry/crewai_adapter.py`, `mas/scripts/check_runtime_adapter_inventory.py`, `mas/packages/mas-core/tests/test_architecture_adapter_inventory.py`, [canonical adapter inventory](../../mas/docs/AIAT_OSS_ARCHITECTURE_AND_IMPLEMENTATION_PLAN.md#legacyparallel-adapter-plane) |
| PR H — OpenCode/OpenHands benchmark plan | The fixed 40-task corpus, category counts, governed execution policy, required observation set, and 160-run plan are now validated locally without starting either runtime or claiming a winner. | `mas/scripts/fixtures/coding-runtime-benchmark/corpus.json`, `mas/scripts/check_coding_runtime_benchmark.py`, `mas/scripts/tests/test_check_coding_runtime_benchmark.py` |
| PR I — Pydantic AI vs AgentBase benchmark plan | The fixed 48-case corpus, category counts, governed comparison policy, required metrics, and 288-run plan are validated locally without adding Pydantic AI, changing AgentBase, or claiming an adoption decision. | `mas/scripts/fixtures/pydantic-agentbase-benchmark/corpus.json`, `mas/scripts/check_pydantic_agentbase_benchmark.py`, `mas/scripts/tests/test_check_pydantic_agentbase_benchmark.py` |
| PR J — Playwright/browser-agent benchmark plan | The fixed 30-workflow corpus, category counts, deterministic-first/fallback policy, no-credential/no-external-effect rules, required metrics, and 180-run plan are validated locally without launching a browser, adding Stagehand, or changing the Playwright default. | `mas/scripts/fixtures/browser-runtime-benchmark/corpus.json`, `mas/scripts/check_browser_runtime_benchmark.py`, `mas/scripts/tests/test_check_browser_runtime_benchmark.py` |

### Latest host-settlement increment

The current working tree narrows the host-assignment consistency window without
changing ownership: normal reservation/binding commit and release now share a
database transaction, failed new assignments compensate newly-created
reservations, and host-loss reassignment replaces the binding and commits the
replacement reservation in one transaction after scheduling. Replacement
reservation creation, claim, evidence/usage settlement, and live recovery
still require explicit compensation/reconciliation and fault evidence.

## Maintenance rule

When a capability changes, update this index and the authoritative detailed
document in the same change. Include:

- exact code/schema/manifest paths;
- tests and durable/live evidence appropriate to the risk;
- the status change and remaining limitations;
- the next document or acceptance gate;
- any obsolete implementation or evidence that must be retired.

Do not mark a row **Implemented** solely because code exists. Do not mark a
milestone complete while its required live, security, recovery, migration, or
human evidence remains open. The index should be refreshed whenever the
repository revision or maintained authority set changes.
