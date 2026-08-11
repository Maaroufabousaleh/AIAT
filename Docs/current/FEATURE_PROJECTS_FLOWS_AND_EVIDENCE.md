# Projects, Flows, Knowledge, and Evidence Feature Specification

**Baseline:** 2026-08-10
**Status:** broad implementation present; versioned node-schema/evidence contracts, generated editable node forms, canonical flow templates, deterministic export/diff/import/publication, explicit evidence-preserving graph migration, operator-approved saved-definition worker migration, consolidated project evidence packages, and authenticated canonical self-improvement project API/storage/reference/action paths are implemented, while live worker/recovery remains. The guarded self-improvement core lifecycle/candidate contracts and fixtures are committed in `4d8dddf`; the deterministic evidence-package core and policy-scope resolver are reviewed and committed in `a44a1aa`, package-level workflow exports are isolated in `d0472af`, the isolated project evidence API router/snapshot contract is committed in `cbf00d9`, bounded dashboard evidence/proxy surfaces are committed in `82bbaeb`, flow portability is committed in `a219092`, evidence-preserving instance migration is committed in `67ed704`, and authenticated self-improvement lifecycle persistence is committed in `64218ab`; project-page composition and live worker/provider recovery remain open.
**Authority:** [AIAT Target Programme](../../AIAT_TARGET_PROGRAMME.md)

## Purpose

Projects are the primary ownership, security, cost, evidence, and lifecycle boundary. Flows coordinate governed work. Evidence makes every transition, decision, result, and recovery inspectable without treating logs or UI state as authority.

## Implemented now

- Canonical projects with state, history, revision, owner/requester, company, configuration, retry, archive, and deletion paths.
- Default software lifecycle from feasibility through PDR/CDR, human approval, requirements review, sprint execution, retrospective, KPI persistence, completion, and archive.
- Durable documents with lineage, immutable revisions, statuses, preview/download, and supersession.
- Review sessions/comments, CSO veto, approval gates, pending decisions, and audit timeline.
- Sprints, issues, parent/dependencies, comments, links, KPI snapshots, agent performance observations, and estimation adjustment.
- A first transition to `DONE`/`COMPLETED`/`CLOSED` on an assigned issue now automatically records its estimated-versus-actual hours in the durable agent profile; repeated terminal updates do not double-count the observation, and the response links the profile update to the source issue.
- The same first terminal transition for a sprint issue persists a `sprint_retrospective` KPI snapshot with aggregate completion/velocity/estimation values and raw source issue IDs, completed issue IDs, status counts, and assigned-agent profile lineage; the existing project KPI API is the read surface.
- Project context items/chunks/tags/relations with text, semantic, and hybrid search through pgvector.
- Artifact and usage APIs plus a consolidated project workspace/evidence view.
- Versioned flow definitions, React Flow editor, validation/dry run, instances, execution history, actions, node actions, switch, override, context, escalation, and retry.
- Governed task dispatch now distinguishes asynchronous Worker Run progress from terminal outcomes: queued/claimed/running runs keep the node active and are recorded in `context_json.active_worker_runs`; only an authoritative terminal run may settle the node. Safe retry re-enters this same dispatch path for restored governed tasks, while legacy task compatibility remains manual until migration. The deterministic `aiat.flow-worker-binding.v1` fixture covers state classification, copy-on-write parallel bindings, terminal settlement, and unknown-state fail-closed behavior.
- Flow retry preserves prior node executions as `SUPERSEDED` evidence instead of deleting the failed attempt; both the recorded-safe-node path and the no-safe-node storage fallback use that boundary. The newly created execution is the only traversal-authoritative attempt, while inputs, outputs, errors, and timestamps remain inspectable.
- The deterministic `aiat.workflow-watchdog-recovery.v1` fixture drives the real `WatchdogConfig`/elapsed-time helpers and pure `WorkflowController` through boot grace, downtime-aware timeout, watchdog failure, safe-state retry, and terminal-state exclusion; native watchdog/cold-crash recovery remains a live operator gate.
- Nine node types: start, end, task, approval, condition, parallel, join, switch, and escalate.
- Versioned node-schema catalogue (`aiat.flow-node-schemas` v1.0) drives backend typed validation, the `/flows/node-schemas` contract, a checked-in JSON artifact, and generated dashboard TypeScript metadata; new definitions persist the schema version. Both flow editors now render editable generated forms for typed fields, enums, CSV arrays, JSON objects, governed workers, and approved Model Profiles; deprecated `team_id`/`action` aliases are metadata-marked and kept only in collapsed compatibility controls alongside adapter extensions.
- Flow validation now reconciles declared control topology with persisted edges: parallel nodes require unique, existing branch roots and matching outgoing edges, joins require at least two incoming branch edges, and switch case targets must exist and be explicitly connected. The deterministic `aiat.flow-topology-check.v1` fixture proves valid and invalid definitions without starting workers or mutating storage. The real traversal engine also has a deterministic `aiat.flow-execution-semantics.v1` fixture covering parallel fan-out, one-branch join waiting, single join scheduling, switch case selection, and unknown-case blocking; live fan-out/join, watchdog, and recovery execution remain separate gates.
- Flow definitions expose deterministic export/hash, node/edge diff, import, publish, and deprecate endpoints; existing instances continue to pin their flow version and terminal instances remain immutable. Compatible instance migration verifies schema/version and active-node identity/type, while explicitly opted-in graph rewrites require a one-to-one active-node mapping, preserve execution history, and record the mapping in migration evidence. The dashboard proxies both the explicit saved-definition worker migration and the evidence-preserving instance migration through operator boundaries.
- `POST /flows/{flow_id}/migrate-legacy-tasks` provides a deterministic dry-run and an operator-approved immutable version migration. Every unbound task requires an explicit UUID worker binding; `action` becomes `task_type` only when needed, deprecated aliases are removed, missing model declarations become node-level `model_mode: none` so the worker policy remains authoritative, and the source flow is never mutated. The new version records `aiat.flow-legacy-task-migration.v1` evidence in metadata.
- Evidence policy catalogue (`/evidence-policies`) is selectable by operator clients. Required artifact kinds are evaluated, and evidence-policy dry runs include worker-run and repository checks instead of omitting those resources.
- Evidence policy selections persist at project default and milestone override scope through `PUT /projects/{project_id}/evidence-policy`; company defaults persist in the active company manifest through `PUT /companies/{company_id}/evidence-policy`. The canonical `resolve_evidence_policy_selection` helper makes precedence explicit: project milestone, project default, flow-definition metadata, company milestone, company default, then manual fallback. The `aiat.evidence-policy-resolution-check.v1` fixture covers every scope and reports licence/restriction metadata as non-gating.
- `GET /projects/{project_id}/evidence/package` exposes the fresh `aiat.project-evidence-package.v1` read model across repository, documents, tests, security, deployment, cost, approvals, flow, worker, artifact, and audit categories. `POST /projects/{project_id}/evidence/package` is an operator-only, idempotent snapshot projection backed by `project_evidence_packages`; it never mutates project state or creates a second completion predicate. Resource licence/restriction values remain bounded notices in package metadata and never affect status.
- Six canonical reusable templates (`software_delivery`, `research`, `hiring`, `incident_response`, `integration_rollout`, and `self_improvement`) are validated by the same flow engine and exposed through `/flow-templates` and `/flows/from-template`; the new-flow dashboard now fetches and applies that canonical catalogue, preserving node configs, evidence metadata, and reference remapping. A blank canvas remains the local fallback when the catalogue is unavailable.
- `aiat.self-improvement.v1` defines a typed improvement opportunity and
  canonical project request carrying owner, risk, budget, evidence policy, and
  source metadata. Independent coding/testing/review/security/migration/
  rollback gates feed a deterministic shadow → canary → human approval →
  promotion lifecycle; a separate rollback transition restores the exact prior
  version. Agents may propose and produce evidence but cannot approve
  promotion. `POST /projects/self-improvement` authenticates the creator,
  validates company scope, and delegates to the canonical project writer;
  project config stores a revisioned lifecycle snapshot, project-history
  entries, and typed references to canonical issue/worker-run/artifact/budget/
  branch/SBOM/deployment/evidence records. Authenticated lifecycle actions
  apply technical gates, shadow/canary observations, promotion requests,
  human approval, and rollback through the same compare-and-set writer.
  The `record_outcome` action persists bounded terminal cost, incident,
  rollback, evidence, and KPI-learning records in that same revisioned
  snapshot; stable outcome IDs are idempotent and conflicting retries fail
  closed. Licence/restriction values remain metadata only.
- The lifecycle also accepts a frozen `aiat.self-improvement-artifacts.v1`
  manifest with exactly one checksum-bearing `change`, `provenance`, `sbom`,
  `migration`, and `rollback` pointer. Each artifact ID is linked through the
  existing canonical reference map; incomplete, mutable, and conflicting
  manifests fail closed. `ImprovementArtifactBundle.from_worker_artifacts`
  converts normalized worker-result records (including canonical artifact-row
  IDs), while `ImprovementArtifactReadback.from_bytes` and the lifecycle
  read-back action verify SHA-256/size parity without copying bytes into the
  project snapshot. External provider and certified-worker evidence remains a
  live boundary.
- `aiat.self-improvement-candidate-detection.v1` normalizes bounded defect,
  metric, upstream-update, cost, and operator-goal signals into deterministic
  opportunities. Exact duplicate signal IDs collapse, conflicting reuse fails
  closed, risk/budget mapping is deterministic, and detection has no project,
  budget, credential, or deployment side effects; licence/restriction values
  remain provenance metadata only.
- `scripts/check_flow_instance_recovery.py --live --instance-id ...` provides a
  secret-safe flow-instance status/execution-history probe and guarded
  start/pause/resume/cancel/retry actions. State-changing actions require
  explicit `--confirm` and the helper validates the expected post-action state;
  worker canary, full-project, UI, and provider recovery remain separate.

## Code anchors

- Project workflow: [`mas/packages/mas-core/mas_core/workflow/`](../../mas/packages/mas-core/mas_core/workflow/)
- Flow engine: [`mas/packages/mas-core/mas_core/workflow/flow_engine.py`](../../mas/packages/mas-core/mas_core/workflow/flow_engine.py)
- Node-schema source/generator: [`mas/packages/mas-core/mas_core/workflow/node_schema.py`](../../mas/packages/mas-core/mas_core/workflow/node_schema.py), [`mas/scripts/generate_flow_node_schemas.py`](../../mas/scripts/generate_flow_node_schemas.py), [`mas/schemas/workflow/flow_nodes.v1.json`](../../mas/schemas/workflow/flow_nodes.v1.json)
- Flow portability and templates: [`definition_tools.py`](../../mas/packages/mas-core/mas_core/workflow/definition_tools.py), [`templates.py`](../../mas/packages/mas-core/mas_core/workflow/templates.py), [`test_flow_definition_lifecycle_api.py`](../../mas/apps/orchestrator-api/tests/test_flow_definition_lifecycle_api.py), [`test_flow_templates.py`](../../mas/packages/mas-core/tests/test_flow_templates.py), and the status notes [portability](FLOW_DEFINITION_PORTABILITY_STATUS.md) and [templates](FLOW_TEMPLATE_STATUS.md)
- Evidence rules: [`mas/packages/mas-core/mas_core/workflow/evidence.py`](../../mas/packages/mas-core/mas_core/workflow/evidence.py)
- Evidence-policy scope resolver and fixture: [`resolve_evidence_policy_selection`](../../mas/packages/mas-core/mas_core/workflow/evidence.py), [`check_evidence_policy_resolution.py`](../../mas/scripts/check_evidence_policy_resolution.py), [`test_evidence_policy_resolution.py`](../../mas/packages/mas-core/tests/test_evidence_policy_resolution.py)
- Evidence-package review batches: core/export commits `a44a1aa` and `d0472af`; isolated API/snapshot/policy routes and clean-checkout tests commit `cbf00d9`; bounded dashboard project-evidence/deep-link/proxy surfaces commit `82bbaeb` and pass clean-checkout `npm run typecheck`. [`test_evidence_package_runner.py`](../../mas/packages/mas-core/tests/test_evidence_package_runner.py), [`check_project_evidence_package.py`](../../mas/scripts/check_project_evidence_package.py), and [`test_project_evidence_routes.py`](../../mas/apps/orchestrator-api/tests/test_project_evidence_routes.py) cover deterministic grouping, operator-only persistence, metadata-only notices, and fail-closed live mode.
- Storage/tables: [`mas/packages/mas-core/mas_core/memory/`](../../mas/packages/mas-core/mas_core/memory/)
- Canonical self-improvement project API/writer: [`mas/apps/orchestrator-api/orchestrator_api/main.py`](../../mas/apps/orchestrator-api/orchestrator_api/main.py) (`POST /projects/self-improvement`, `GET /projects/{project_id}/self-improvement`, `POST /projects/{project_id}/self-improvement/references`, and `POST /projects/{project_id}/self-improvement/actions`) and [`mas/packages/mas-core/mas_core/memory/storage.py`](../../mas/packages/mas-core/mas_core/memory/storage.py) (`create_self_improvement_project`, `get_self_improvement_lifecycle`, `update_self_improvement_lifecycle`)
- Self-improvement artifact conversion/read-back: [`ImprovementArtifactBundle.from_worker_artifacts`](../../mas/packages/mas-core/mas_core/workflow/self_improvement.py), [`ImprovementArtifactReadback`](../../mas/packages/mas-core/mas_core/workflow/self_improvement.py), [`check_self_improvement_lifecycle.py`](../../mas/scripts/check_self_improvement_lifecycle.py), and [`test_self_improvement.py`](../../mas/packages/mas-core/tests/test_self_improvement.py)
- Flow UI: [`mas/apps/mas-dashboard/app/(dashboard)/flows/`](<../../mas/apps/mas-dashboard/app/(dashboard)/flows/>)
- Schema contract summary/form: [`mas/apps/mas-dashboard/components/flows/NodeSchemaContractSummary.tsx`](../../mas/apps/mas-dashboard/components/flows/NodeSchemaContractSummary.tsx), [`mas/apps/mas-dashboard/components/flows/NodeSchemaForm.tsx`](../../mas/apps/mas-dashboard/components/flows/NodeSchemaForm.tsx)
- Project UI: [`mas/apps/mas-dashboard/app/(dashboard)/projects/`](<../../mas/apps/mas-dashboard/app/(dashboard)/projects/>)
- Migrations: [`mas/migrations/versions/`](../../mas/migrations/versions/)
- Flow recovery probe: [`mas/scripts/check_flow_instance_recovery.py`](../../mas/scripts/check_flow_instance_recovery.py)
- Evidence-preserving retry storage: [`supersede_flow_node_executions`](../../mas/packages/mas-core/mas_core/memory/storage.py) and the guarded retry endpoint [`/flows/instances/{instance_id}/retry`](../../mas/apps/orchestrator-api/orchestrator_api/main.py)
- Evidence-preserving instance migration: [`/flows/instances/{instance_id}/migrate`](../../mas/apps/orchestrator-api/orchestrator_api/main.py), [`migrate_flow_instance`](../../mas/packages/mas-core/mas_core/memory/storage.py), [dashboard proxy](<../../mas/apps/mas-dashboard/app/api/flows/instances/[id]/migrate/route.ts>), [`test_flow_instance_migration_api.py`](../../mas/apps/orchestrator-api/tests/test_flow_instance_migration_api.py), and [migration status](FLOW_INSTANCE_MIGRATION_STATUS.md)
- Watchdog/recovery contract: [`mas/scripts/check_workflow_watchdog_recovery.py`](../../mas/scripts/check_workflow_watchdog_recovery.py), [`test_workflow_watchdog_recovery.py`](../../mas/packages/mas-core/tests/test_workflow_watchdog_recovery.py), and [`mas/packages/mas-core/mas_core/workflow/watchdog.py`](../../mas/packages/mas-core/mas_core/workflow/watchdog.py)
- Flow topology contract/fixture: [`mas/packages/mas-core/mas_core/workflow/flow_engine.py`](../../mas/packages/mas-core/mas_core/workflow/flow_engine.py), [`mas/scripts/check_flow_topology.py`](../../mas/scripts/check_flow_topology.py), [`test_flow_node_schema.py`](../../mas/packages/mas-core/tests/test_flow_node_schema.py)
- Flow traversal semantics contract/fixture: [`mas/scripts/check_flow_execution_semantics.py`](../../mas/scripts/check_flow_execution_semantics.py), [`test_flow_execution_semantics.py`](../../mas/packages/mas-core/tests/test_flow_execution_semantics.py), and [`get_next_nodes`](../../mas/packages/mas-core/mas_core/workflow/flow_engine.py)
- Governed asynchronous task binding: [`mas/packages/mas-core/mas_core/workflow/worker_binding.py`](../../mas/packages/mas-core/mas_core/workflow/worker_binding.py), [`mas/scripts/check_flow_worker_binding.py`](../../mas/scripts/check_flow_worker_binding.py), [`test_flow_worker_binding.py`](../../mas/packages/mas-core/tests/test_flow_worker_binding.py), and the `flow_node_action` Worker Run boundary in [`orchestrator_api/main.py`](../../mas/apps/orchestrator-api/orchestrator_api/main.py)
- Evidence package contract/API/dashboard/fixture: [`mas/packages/mas-core/mas_core/workflow/evidence.py`](../../mas/packages/mas-core/mas_core/workflow/evidence.py), [`GET /projects/{project_id}/evidence/package`](../../mas/apps/orchestrator-api/orchestrator_api/main.py), [`POST /projects/{project_id}/evidence/package`](../../mas/apps/orchestrator-api/orchestrator_api/main.py), [`check_project_evidence_package.py`](../../mas/scripts/check_project_evidence_package.py), [`project evidence dashboard`](<../../mas/apps/mas-dashboard/app/(dashboard)/projects/[id]/page.tsx>)
- Self-improvement lifecycle contract: [`mas/packages/mas-core/mas_core/workflow/self_improvement.py`](../../mas/packages/mas-core/mas_core/workflow/self_improvement.py), [`mas/scripts/check_self_improvement_lifecycle.py`](../../mas/scripts/check_self_improvement_lifecycle.py)
- Self-improvement outcome persistence: [`ImprovementOutcome`](../../mas/packages/mas-core/mas_core/workflow/self_improvement.py), [`test_self_improvement.py`](../../mas/packages/mas-core/tests/test_self_improvement.py), and authenticated `record_outcome` action in [`orchestrator_api/main.py`](../../mas/apps/orchestrator-api/orchestrator_api/main.py)
- Self-improvement artifact manifest: [`ImprovementArtifactBundle`](../../mas/packages/mas-core/mas_core/workflow/self_improvement.py), authenticated `record_artifacts` action in [`orchestrator_api/main.py`](../../mas/apps/orchestrator-api/orchestrator_api/main.py), and deterministic lifecycle fixture [`check_self_improvement_lifecycle.py`](../../mas/scripts/check_self_improvement_lifecycle.py)
- Self-improvement candidate detection: [`mas/packages/mas-core/mas_core/workflow/improvement_candidates.py`](../../mas/packages/mas-core/mas_core/workflow/improvement_candidates.py), [`mas/scripts/check_self_improvement_candidates.py`](../../mas/scripts/check_self_improvement_candidates.py), [`mas/packages/mas-core/tests/test_improvement_candidates.py`](../../mas/packages/mas-core/tests/test_improvement_candidates.py)

## Target invariants

- Every project-owned object carries `project_id` and is inaccessible across scope unless an explicit company/operator policy permits it.
- One workflow controller writes project state and one flow controller writes flow state.
- State changes use expected revisions and append history/audit in the same transaction.
- Published flow instances pin an immutable definition version.
- Task nodes dispatch only through the universal worker-run path.
- Milestone transitions verify an evidence policy before commitment.
- WebSocket and UI events are views of durable state, never the source of truth.
- Archive preserves history and evidence; destructive deletion is exceptional and policy-controlled.

## Flow authoring target

- Typed configuration forms generated from versioned node schemas (field types, defaults, enums, CSV/JSON widgets, governed worker/profile selectors, and minimums are rendered from the catalogue; deprecated compatibility aliases are omitted from the primary form and remain an explicit collapsed extension surface).
- Flow dry-runs now return a deterministic `compatibility_aliases` audit for
  legacy task `team_id`/`action` fields, distinguishing definitions that can
  be normalized from those that still require an explicit concrete `worker_id`;
  the audit never guesses or mutates a saved definition.
- Saved-definition migration now consumes that audit through
  `POST /flows/{flow_id}/migrate-legacy-tasks`; dry-run returns the candidate,
  missing/unknown binding errors, and before/after findings, while a successful
  request creates a new immutable version and leaves the old record untouched.
- Reusable templates for software delivery, research, incident response, worker hiring, integration rollout, and self-improvement (catalogue, create-from-template API, and dashboard catalogue consumption are implemented).
- Static validation for reachability, branch targets, joins, permissions, worker capability, model profile, tool grants, evidence, retry, timeout, and budget.
- Static control-topology validation now proves parallel branch declarations match outgoing edges, joins have fan-in, and switch cases match explicit outgoing edges. The execution-semantic fixture drives the real traversal path and proves that joins are scheduled once, branches wait for fan-in, selected switch cases alone advance, and unknown cases block; neither fixture replaces live execution/recovery proof.
- Version diff, publish, deprecate, clone, import/export, and instance migration rules (export/diff/import/publish/deprecate, evidence-preserving compatible migration, and bounded graph-rewrite mapping are implemented).
- Dry run that reports resolved workers/tools/models/policies and predicted gates without creating side effects.

## Evidence target

Evidence packages include required documents, approvals, test/security reports, source revisions, runtime/model resolution, tool audit, artifacts/checksums, costs, integration projections, deployment records, and recovery/rollback proof. Each item carries producer, run, timestamp, version, checksum, retention, and access policy.

Resource licence, redistribution, and restriction values may be retained as
item notices, but they are metadata only: they never block package assembly,
normal internal use, or the evidence completion predicate.

## Remaining gaps

- Run the saved-definition migration for existing legacy flows with reviewed
  worker bindings, then publish the resulting immutable versions; the API and
  metadata evidence are implemented, while live worker canary/recovery proof
  remains a separate gate.
- Validate portability and instance migration against live storage, including
  atomic writes, concurrent publication, before/after graph diff, and
  historical execution preservation; the current API/core tests are
  deterministic and do not claim a live database or browser certificate.
- [x] Add deterministic real traversal semantics for parallel fan-out/join synchronization and switch routing; duplicate join scheduling and completed-join reactivation are prevented, while live parallel/join, escalation, cancellation, watchdog, and cold-crash recovery remain open.
- [x] Add deterministic parallel/join/switch topology validation and fixture; live fan-out, join synchronization, watchdog, and crash/recovery evidence remain open.
- [x] Add a read-only/explicit-confirmation flow-instance recovery probe that
  records state and execution-history evidence; live infrastructure failure,
  worker canary, and full project golden-path proof remain open.
- [x] Keep governed task nodes active while asynchronous Worker Runs are queued or running, preserve per-node authoritative run bindings across parallel work, re-enter Worker Run dispatch on safe retry, and reject unknown run states; live canary/recovery and migrating every existing legacy flow remain separate gates.
- [x] Make evidence policies configurable by company/project/flow/milestone and surface missing evidence before action; persistence, explicit precedence resolution, and core checks are implemented by the API/core helper and deterministic fixture, while live transition/recovery evidence remains.
- [x] Extend retrospective learning to persist a `sprint_retrospective` KPI snapshot from canonical issue rows, including source issue IDs, completed issue IDs, profile lineage, status counts, and aggregate estimate/actual totals; the existing project KPI surface exposes the snapshot. Live transition/recovery evidence remains.
- [x] Add full repository, test, security, deployment, and cost evidence package views; the fresh read model and operator-generated durable snapshot are implemented, while live provider/worker artifact generation remains a separate gate.
- Complete native-Linux Playwright golden paths for mobile and desktop.
- Connect lifecycle references to live issue/worker-run, evidence, budget,
  branch/artifact, worker, and deployment services and generate those records
  from governed work; the authenticated project API, revisioned canonical
  snapshot, and reference-link writer are now present, while the fixture still
  does not mutate live workers or deployments.
- Connect candidate signals to live defect, metric, upstream, cost, and
  operator-goal sources; the detector and fixture are deterministic and
  non-authorizing, but live signal ingestion remains open.
- Generate the five immutable self-improvement artifacts from certified worker
  runs and verify provider-backed checksum read-back. The worker-record
  conversion, canonical-row pointers, and checksum/size read-back contract are
  implemented and fixture-tested; external certified-worker/provider evidence
  remains open. The manifest stores safe pointers and hashes without copying
  bytes or executing migrations.

## Acceptance criteria

- Invalid/unreachable flows cannot publish.
- Concurrent or stale project/flow actions cannot overwrite newer state.
- Duplicate commands and message redelivery do not duplicate documents, issues, runs, or transitions.
- CSO veto enters `SECURITY_BLOCKED`; non-CSO veto is denied; CEO override requires durable evidence.
- Document revision preserves lineage and marks the prior latest revision superseded.
- A failed instance restarts only from a recorded safe point and preserves the failed attempt.
- Every completed milestone links a complete evidence package.
- Project workspace state can be reconstructed from canonical APIs after process restart.
- Self-improvement promotion is never complete without independent gate
  evidence, human approval, and an exact prior-version rollback path.
