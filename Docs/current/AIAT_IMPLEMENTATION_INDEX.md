# AIAT Implementation Index

**Scope:** personal/internal AIAT instance
**Refresh baseline:** `32e02942`
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

This snapshot was refreshed on 2026-09-19 against the refresh baseline above.
It does not
replace the release ledger or close native-host, provider, sandbox, disaster-
recovery, or human/operator gates.

| Check | Result | Scope and limitation |
| --- | --- | --- |
| Source revision at refresh | **`32e02942`** | The refresh baseline is the current `origin/main` tip at the time of this refresh; the checks below describe that code baseline and do not close live/operator gates. |
| Documentation authority/index check | **PASS (2026-09-19)** | `../.venv/bin/python scripts/check_docs_index.py --json` reports 13 feature documents, 3 plans, 23 maintained/link-checked documents, and no link or policy errors. The standard isolated `uv` invocation could not acquire its read-only global cache in this sandbox; the repository-local checker was run from the existing environment. |
| Migration source-head check | **PASS (2026-09-18)** | `../.venv/bin/python scripts/check_database_migration_head.py --json` reports one source head: `0045_worker_tool_effects`; no live database was touched. |
| Release-environment identity probe | **PASS with Docker blocked (2026-09-19 UTC)** | `check_release_environment.py --json` sees Python, uv, Node, npm, and `runsc release-20260817.0`; the Docker executable is present but its Engine is unavailable from this WSL2 distribution. No deployment, provider, or sandbox mutation was performed. |
| Standard dependency/test runner | **BLOCKED in this sandbox** | The standard isolated `uv` command could not download missing wheels because network access is restricted and its global cache is read-only. The repository-local fallback environment does not contain the workspace packages as installed distributions, so its broad fallback run is not accepted as suite evidence. The last authoritative green run remains the 2026-09-15 run recorded below. |
| Python repository test suite | **PASS (last authoritative run: 2026-09-15)** | `uv run --isolated pytest -q`; all collected tests passed, with two non-failing `AsyncMock` resource warnings in existing system tests. |
| Script test suite | **PASS (last authoritative run: 2026-09-15)** | `uv run --isolated pytest -q scripts/tests`; all collected script tests passed; two existing Python 3.14 tar-extraction deprecation warnings remained non-failing. |
| Current focused architecture/documentation suites | **PASS (2026-09-19)** | The repository-local fallback environment passed `test_docs_index.py`, architecture invariants/adapter inventory, ProcessAdapter security, project outbox, runtime-binding/tool-effect, worker-governance, TeamRunner runtime-plane, Redis characterization, and project state-history ownership coverage. This narrower run does not replace the full standard suite. |
| Bounded Python compilation | **PASS** | Checked-in `mas_core`, orchestrator, message-router, team-runner, tool-service, and `mas/scripts` Python roots; generated dashboard dependency trees excluded. |
| Static release ledger | **PASS: 64/64 checks (2026-09-19)** | The current static invocation passes all 64 checks and confirms source migration head `0045_worker_tool_effects`; the ledger still returns **NO-RELEASE** because two pending evidence items, no live profile, and the unrelated untracked operator file keep the worktree/release decision open. |
| Database migration-head guard | **PASS static; BLOCKED live** | `check_database_migration_head.py --json` verifies source head `0045_worker_tool_effects`; the live read-only query is blocked in this WSL2/Compose profile because the migration-check DSN cannot reach the published database port. Independent read-only inspection still reports the running local database at `0042_worker_run_host_binding`; no migration or restart was performed. |
| Authenticated local-Compose live release sweep | **83/89 pass; 0 fail; 6 blocked** | `check_release_ledger.py --live --compose-local --json` at `2026-09-15T12:11:27Z` using the configured operator/tool-service authentication aliases without retaining secrets. Bounded live tool trace, catalog/metrics/runtime/trace/SLO, and worker-reconciliation checks passed; the six remaining blockers are native-host/environment, database migration-head reachability, deployment-image provenance, Firecracker pool readiness, outbound mail lifecycle, and operator-selected self-improvement scope. Four pending evidence items and the dirty worktree keep the global decision **NO-RELEASE**. |
| Source/deployment migration boundary | **SOURCE 0045; local Compose DB 0042** | Read-only inspection of the running local Postgres reports `0042_worker_run_host_binding`; migrations `0043_project_transition_outbox`, `0044_worker_run_runtime_bindings`, and `0045_worker_tool_effects` are present in source but not applied to that running database. No migration or service restart was performed in this pass. Source-level tests and static checks therefore do not constitute live evidence for those later tables. |
| Runtime-adapter inventory/consolidation | **PASS** | Machine-checked reference inventory; LangGraph/CrewAI now share the canonical `runtime_adapters.py` implementation through compatibility shims, while the legacy factory, Letta dotted configuration, and MAF certification references still prevent full-family deletion. |
| Optional memory/workflow service contract | **PASS: 3 candidates** | `check_optional_memory_services.py --json` and the focused 9-test suite validate exact Letta/Qdrant/Temporal adapter contracts, AIAT authority/data boundaries, disabled-by-default policy, measurable-value fields, outage/recovery declarations, and removal definitions without network or mutation. Live value/outage/restore/removal testing remains deliberately unstarted. |
| Worker-placement policy contract | **PASS: 3 cases** | `check_worker_placement.py --json` validates eligible-host selection, capacity ordering, duplicate-host fail-closed behavior, and worker-plane isolation without dispatch or mutation. Durable host registration, live multi-host scheduling, lease settlement, and host-loss/split-brain evidence remain separate. |
| Durable specialist-runtime binding and mediated-tool effects | **PASS** | Migrations `0044_worker_run_runtime_bindings` and `0045_worker_tool_effects`, controller/storage lookup, per-attempt binding/effect persistence, bounded runtime observation, completed-response replay, and fail-closed ambiguous-effect regression coverage; external-runtime/live recovery and provider-specific effect reconciliation remain separate gates. |
| Concrete runtime reconciliation | **PASS: bounded local coverage** | OpenCode consumes a durable session reference after adapter restart; the inactive OpenHands candidate consumes a durable conversation reference and reports conservative REST observations; controller persistence records subordinate status/timestamp without changing canonical worker-run state. Live termination/result recovery remains separate. |
| Reservation/binding settlement hardening | **PASS** | Normal commit/release now updates the reservation and run-host binding on one database connection; failed new assignments release their newly-created reservation. Host-loss reassignment and live fault evidence remain open. |
| Worker lifecycle fixture | **PASS** | Checkpoint, pause/resume, cancellation, cold-crash normalization, lease recovery, and artifact/usage ordering; not a database, canary, sandbox, or live-run certificate. |
| OpenCode candidate triage tooling | **PASS** | Focused parser/classification/image-identity regression suite; candidate remains inactive pending scanner/runtime review. |
| Dashboard lint/typecheck/build/auth | **PASS** | `npm run lint`, `npm run typecheck`, `npm run build`, and `npm run test:auth`; the Node-level harness exercises the real JWT/bcrypt module, while production UI/live-provider evidence remains separate. |
| Dashboard protocol fixture check | **PASS** | Four `aiat.v1` protocol fixtures plus TypeScript typecheck. |
| Typed team metadata/API contract | **PASS** | `GET /teams` now returns the canonical team ID, display name, and policy role; OpenAPI, generated Python/TypeScript contracts, API-contract checks, and hierarchy tests pass. |
| Project document ownership normalization | **PASS** | Document revision/status/read/preview/download and document-backed context routes accept database-string UUIDs while preserving fail-closed foreign/malformed ownership behavior; focused document/API tests pass. |
| Context chunk project ownership | **PASS** | `POST /projects/{project_id}/context/chunks` checks project existence before metadata/chunk insertion; the retrieval API suite passes the unknown-project regression. |
| Project state-history ownership | **PASS** | `GET /projects/{project_id}/state-history` verifies project existence before reading history; existing-project audit reads and unknown-project 404 coverage pass across project, flow, operator, and security suites. |
| Restricted Redis ACL/Lua smoke | **PASS** | Local Redis service recreated from the current Compose ACL migration; authenticated router scripting, atomic stream append, and denied `CONFIG` access were verified, with the probe entry removed afterward. This is not a production outage or multi-host proof. |
| Review-regression suite | **PASS for focused coverage; full-suite evidence is historical** | Post-review focused coverage passes for Redis ACL/script authorization, cancellation/recovery races, scanner failure classification and evidence hashes, migration-head compatibility, sandboxed image probing, governance model-provenance producer wiring, project ownership, and state-history ownership. The last authoritative full Python and script-suite run remains 2026-09-15; the later broad rerun was not completed in this environment and is not represented as a fresh full-suite result. Live outage, host, provider, schema-migration, and operator gates remain separate. |

Current environment boundary: this WSL2 distribution has `runsc
release-20260817.0`, but Docker Engine is currently unavailable to the Docker
CLI, so Compose and gVisor registration/smoke cannot be refreshed in this
session. The latest authenticated `check_release_ledger.py --live
--compose-local --json` run at `2026-09-15T12:11:27Z` reports 83/89 pass, 0
fail, and 6 externally blocked.
The remaining blockers are native-host/environment, database migration-head
reachability, deployment-image provenance, Firecracker worker-pool readiness,
outbound mail lifecycle, and operator-selected self-improvement scope. Four
pending evidence items and the dirty worktree keep the global decision
`NO-RELEASE`. This does not claim a
fresh native Linux, Firecracker, independent-host, provider-backed worker,
outbound-mail, or operator-selected self-improvement certification run.
The source migration head is `0045_worker_tool_effects`, while the currently
running local Compose database remains at `0042_worker_run_host_binding`; no
deployment migration or service restart was performed during this validation.

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
| Projects, documents, reviews, approvals, issues, context, and evidence | **Implemented** | Canonical project API/storage, explicit project-scoped state-history reads (`f49f7501`), transition history, project-transition outbox and bounded retry dispatcher (migration `0043_project_transition_outbox`), database-shaped document ownership normalization for revision/status/read/preview/download/context routes (`66f8f80d`), project-existence enforcement before context chunk insertion (`90ac61ff`), evidence package/detail views, artifacts, audit timeline, context and repository workspace | Full live worker/provider generation and release-grade recovery proof; project-outbox live outage evidence | [Projects, Flows, Knowledge, and Evidence](FEATURE_PROJECTS_FLOWS_AND_EVIDENCE.md) · [P0 plan](plans/P0_RELEASE_INTEGRITY_PLAN.md) |
| Visual flows and governed execution | **Implemented foundation** | React Flow authoring, nine node types, versioning, templates, validation, traversal, fan-out/join/switch, retry, watchdog, migration, worker binding | Live worker canary/recovery and complete native/operator evidence | [Projects, Flows, Knowledge, and Evidence](FEATURE_PROJECTS_FLOWS_AND_EVIDENCE.md#flow-authoring-target) · [P1 plan](plans/P1_DEFAULT_PRODUCT_COMPLETION_PLAN.md#workstream-3--flow-and-evidence-completion) |
| Identity, credentials, external accounts, mail, and browser sessions | **Implemented; default path certified** | Dedicated identity service, credential manager, approval/lease/revocation controls, provider-neutral mail, default Cloudflare/Resend path | Optional profiles, broader outage/restore, key/domain migration, and unrelated provider evidence | [Identity, Mail, Credentials, and External Accounts](FEATURE_IDENTITY_MAIL_AND_CREDENTIALS.md) · [Mail-Edge Observability](FEATURE_MAIL_EDGE_OBSERVABILITY.md) |
| PM and source-control integrations | **PM implemented; active/GitHub certification pending** | Provider-neutral ports, YouTrack projections/inbox/outbox/reconciliation, governed GitHub contract and fixtures | YouTrack ACTIVE command certification and complete GitHub App live matrix | [PM and Source-Control Integrations](FEATURE_INTEGRATIONS_PM_AND_SCM.md) · [P1 identity/collaboration workstream](plans/P1_DEFAULT_PRODUCT_COMPLETION_PLAN.md#workstream-4--identity-and-external-collaboration) |
| Data, storage, memory, retention, backup, and migration | **Implemented foundation / partial** | Postgres/pgvector, Redis, MinIO, object conformance/copy/backup/restore/encryption/multipart/lifecycle/migration fixtures | Provider-managed KMS/key custody, production cutover, clean-host, disaster-recovery, optional service value | [Data, Storage, Memory, and Retention](FEATURE_DATA_STORAGE_AND_MEMORY.md) · [Object-Store Migration Status](FEATURE_OBJECT_STORE_MIGRATION_STATUS.md) |
| Trace evidence, retention, SLOs, capacity, and analytics | **Implemented foundation; live evidence pending** | Payload-safe request/message/tool/worker spans, evidence joins, incident/read-only projections, retention planner, SLO/capacity read models | Live model/tool/provider coverage, authoritative hold/erasure/restore, load/soak/chaos evidence | [Trace Evidence and Retention](FEATURE_TRACE_EVIDENCE_AND_RETENTION.md) · [SLO, Capacity, and Operational Forecast](FEATURE_SLO_CAPACITY_AND_OPERATIONS.md) |
| Security, sandboxing, network, supply chain, and operations | **Partial** | Policy-backed network matrix, Compose restrictions, scanner aliases, gVisor/Firecracker contracts, diagnostics, control CLI, image/provenance checks, and OpenCode candidate triage tooling | Native host proof, image/SBOM/scan disposition, high-risk runtime evidence, clean-host and disaster recovery | [Security, Observability, and Operations](FEATURE_SECURITY_OBSERVABILITY_AND_OPERATIONS.md) · [P0 plan](plans/P0_RELEASE_INTEGRITY_PLAN.md) |
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
| External provider-backed dispatch across independent/multi-host Firecracker/gVisor boundaries | **Local provider-shaped retry only** | The local durable provider-shaped retry certificate passes; real provider execution, independent host/process loss, sandbox, callback/delivery, and recovery evidence remain required | [Provider-shaped recovery evidence](../../mas/docs/provenance/gateway_worker_mail_edge_provider_recovery_postgres_evidence.json) · [P2 Workstream 3](plans/P2_SCALE_STORAGE_AND_AUTONOMY_PLAN.md#workstream-3--multi-host-and-high-risk-execution) |
| Independent deployed-host loss, split-brain avoidance, queue recovery, duplicate-effect protection, and complete version pinning | **Local control-plane prerequisites pass; deployed proof absent** | Local multi-host, independent-process, lease/recovery, duplicate-effect, and version-pinning certificates are retained; two or more independently controlled hosts/processes, fault injection, durable replay/reconciliation, and zero duplicate protected effects remain required | [Multi-host evidence](../../mas/docs/provenance/worker_multi_host_execution_postgres_evidence.json) · [Independent-process evidence](../../mas/docs/provenance/worker_independent_process_execution_postgres_evidence.json) · [P2 Workstream 3](plans/P2_SCALE_STORAGE_AND_AUTONOMY_PLAN.md#workstream-3--multi-host-and-high-risk-execution) |

### Feature-spec-only release gate

The following gate is intentionally listed separately because it is an open
checkbox in a maintained feature specification, not one of the three unchecked
P2 plan checkpoints:

| Gate | Current state | Required evidence | Authority |
| --- | --- | --- | --- |
| gVisor smoke/network behavior and optional Firecracker host proof | **Static contract PASS; runtime proof blocked** | A certified native host must run the gVisor deny/allow matrix and the optional Firecracker launcher/readiness, network, cleanup, and recovery checks. `runsc release-20260817.0` is installed in the current WSL2 environment, but Docker Engine registration/smoke is unavailable and the Firecracker launcher/binary is absent; no weaker runtime fallback is permitted. | [Workers, Stewards, Tools, and Models](FEATURE_WORKERS_STEWARDS_AND_MODELS.md) · [P2 Workstream 3](plans/P2_SCALE_STORAGE_AND_AUTONOMY_PLAN.md#workstream-3--multi-host-and-high-risk-execution) · [Native-Linux exit runbook](../../mas/docs/P0_NATIVE_LINUX_EXIT_RUNBOOK.md) |

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
