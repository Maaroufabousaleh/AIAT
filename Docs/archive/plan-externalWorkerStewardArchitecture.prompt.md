# AIAT External Worker Steward, Universal Worker Contract, Governed Model Profiles, Flow Reliability, and Projects Enhancement

> **Personal-use policy override (2026-08-09):** The architecture remains a
> design input, but licence review is now metadata capture only. It cannot
> reject, block, or delay worker discovery, hiring, activation, rollout,
> updating, or execution. Current authority is
> [`AIAT_TARGET_PROGRAMME.md`](../../AIAT_TARGET_PROGRAMME.md) and
> [`ROADMAP.md`](../../ROADMAP.md). Security, sandbox, compatibility, source/version
> provenance, budget, and human-action gates remain operative.

## Summary

This plan defines the implementation of a universal AIAT worker contract, dedicated external-runtime Steward Agents, governed model profiles, reliable flow execution, and a complete project workspace/evidence experience.

The implementation will:

- Make every worker an AIAT Specialist Shell governed through one universal worker contract.
- Add runtime-specific adapters for native workers, LangGraph, CrewAI, OpenCode, MCP, HTTP, process, and OCI runtimes.
- Create a dedicated AIAT-owned Worker Steward Agent for every externally backed worker.
- Add autonomous upstream documentation, release, security, license, and compatibility monitoring.
- Generate candidate skill bundles and adapter updates without modifying active production workers.
- Require certification, approval, canary rollout, and rollback before activating updates.
- Replace unmanaged model strings with versioned, policy-controlled Model Profiles.
- Make model resolution hierarchical, reproducible, capability-aware, budget-aware, and auditable.
- Repair flow execution, node configuration, model selection, failure handling, cancellation, retry, checkpointing, and runtime dispatch.
- Complete the projects page so documents, repositories, contexts, flows, artifacts, sprints, workers, and execution evidence are visible and actionable.
- Migrate existing native, framework-backed, and external workers without immediately breaking existing records.

The plan is intentionally architectural rather than a minimal patch. AIAT remains the sovereign control plane; external applications remain replaceable, pinned runtime implementations behind certified adapters.

## Implementation-Readiness Decisions

The following decisions are normative for implementation and resolve the
existing-system, state-machine, protocol, model-governance, OpenCode, and
delivery ambiguities in the rest of this plan. If an earlier section uses
less-specific wording, this section takes precedence.

### Existing-system reconciliation: extend, replace, and deprecate

AIAT must have one execution authority. New contract records are not a second
flow engine or a second tool system.

| Existing concept | Authority after migration | Change | Compatibility/deprecation rule |
|---|---|---|---|
| `MessageEnvelope` | transport and routing envelope | Extend payload typing and contract metadata | Retain as the only router envelope; it carries worker payloads and is not a worker-run record |
| `ToolRequest` / `ToolResponse` | tool-service mediation contract | Extend with worker-run, permission, budget, and audit references | Do not create a parallel worker-owned tool protocol; deterministic tools remain tools |
| `WorkerManifest` | Specialist Shell declaration and compatibility input | Extend with identity, model mode, adapter, provenance, and contract versions | Existing YAML remains readable during the migration window; new external hires cannot use wrapper-only activation |
| `task_log` | generic agent/task history | Retain for legacy agent messages and add worker-run linkage where applicable | It is not authoritative for external runtime lifecycle |
| `flow_instances` | authoritative flow-instance state | Extend with execution snapshot and evidence references | The flow controller remains the sole writer of flow state |
| `flow_node_executions` | authoritative node execution state | Extend with worker-run ID and normalized result references | A node advances only after the flow runtime validates a worker result |
| new `worker_runs` | authoritative runtime execution state | Add one durable record per dispatch | `WorkerRunController` is the sole writer of lifecycle state; adapters emit observations only |
| `agent_checkpoints` | legacy native-agent checkpoint store | Retain for native-agent compatibility only; `worker_checkpoints` is the formal WorkerRun replacement | New worker runs never write this table; native rows remain readable and are not silently reinterpreted as external-runtime checkpoints |
| `artifacts` | existing artifact ledger | Extend with project, worker-run, provenance, and hash references | Worker artifacts use this ledger; a worker adapter may not invent a competing artifact store |
| `worker_registry` | worker identity and shell registry | Extend with shell, adapter, steward, rollout, and certification references | Existing records are migrated to explicit compatibility status when incomplete |
| raw model strings | legacy input during migration only | Replace production selection with `ModelProfile` references and snapshots | Reject unmanaged IDs on new flow/run paths; retain read-only legacy display and a time-bounded migration shim |
| project `config` | legacy unstructured project options | Retain for unrelated legacy fields; add typed policy, workspace, and evidence projections | Typed fields win for governed behavior; raw model fields are never authoritative |

#### Existing SQL tables and record ownership

The current `mas/packages/mas-core/mas_core/memory/models.py` defines the
following tables. They are all part of the reconciliation boundary; adding a
new table with overlapping authority is prohibited. The in-progress
`0015_worker_steward_model_governance.py` migration is the current migration
boundary in this checkout. It must be extended or superseded deliberately,
not accompanied by duplicate `*_v2` tables.

| Existing table(s) | Decision | Authoritative responsibility after migration |
|---|---|---|
| `projects`, `project_state_history` | Extend, retain history | Project identity, lifecycle, terminality, typed policy/workspace references, and append-only project state transitions |
| `documents`, `review_sessions`, `review_comments` | Extend, retain | Document/review lifecycle, provenance, retrieval, supersession, and review evidence; never duplicate worker artifacts |
| `approval_gates` | Extend, retain | Project and flow approvals; add typed links for model overrides, permission expansion, certification, and rollout approvals |
| `sprints`, `issues`, `kpi_snapshots` | Retain, link | Planning and KPI read models; canary comparison may consume KPI data but does not create a second KPI authority |
| `agent_profiles`, `memory` | Retain, link | Native agent profile/semantic memory; optionally reference a worker shell/run, but not worker lifecycle state |
| `dead_letters` | Retain, narrow | Transport delivery failures and replay; worker runtime failures remain normalized worker events/results |
| `system_config`, `role_capability_map`, `capabilities` | Extend, retain | Global configuration, permission/capability policy, and capability catalog; snapshots are immutable at dispatch |
| `project_context_items`, `project_context_chunks`, `project_context_tags`, `project_context_relations` | Extend, retain | Context projection and lineage from documents/artifacts/runs; no separate external-runtime context store becomes authoritative |
| `agent_checkpoints` | Retain as legacy, formally replace for WorkerRun | Native checkpoints remain readable; new worker runs write only `worker_checkpoints`; a one-time compatibility reader may import a native checkpoint into a worker run with an explicit provenance link |
| `task_log`, `infra_events` | Retain, link | Legacy task/service observability and audit; add worker-run references where applicable, but neither table advances run state |
| `artifacts` | Extend, retain | Canonical project artifact ledger, hashes, storage references, and retention; `worker_artifacts` is a typed link/read model, not a competing blob registry |
| `worker_registry`, `evaluation_reports`, `project_usage_events` | Extend, retain | Worker identity and legacy evaluation, plus project cost/usage ledger; link to shell versions, certification, runs, and model-resolution snapshots |
| `flows`, `flow_instances`, `flow_node_executions` | Extend, retain | Flow definition, authoritative flow-instance state, and authoritative node state; only the flow controller may advance a node after a validated worker result |
| `worker_shell_versions`, `runtime_adapters`, `external_runtime_provenance`, `steward_agents` | New governed records in the current migration boundary | Immutable worker shell, adapter, provenance, and dedicated steward versions; no active bundle is mutated in place |
| `documentation_sources`, `documentation_snapshots`, `capability_snapshots`, `compatibility_matrices`, `certification_runs` | New governed records in the current migration boundary | Evidence of what was discovered, verified, and certified for a pinned runtime/adapter |
| `skill_bundles`, `skill_bundle_candidates`, `rollout_records`, `rollback_records`, `update_monitoring_jobs` | New governed records in the current migration boundary | Immutable active/candidate bundles, rollout state, rollback history, and idempotent monitoring jobs |
| `model_profiles`, `model_profile_versions`, `model_resolution_snapshots`, `model_override_requests` | New governed records in the current migration boundary | Approved model policy/catalog, immutable versions, per-run resolution, and approval workflow |
| `worker_runs`, `worker_events`, `worker_checkpoints`, `worker_artifacts`, `worker_usage_records` | New governed records in the current migration boundary | Runtime execution, append-only normalized observations, checkpoint references, artifact links, and usage; `WorkerRunController` is the sole lifecycle writer |
| `hiring_pipeline_stages`, `approval_records`, `project_repository_records`, `evidence_policies`, `project_evidence_packages` | New governed records in the current migration boundary | Hiring/approval history, repository projection, versioned evidence policies, and project completion evidence |

Foreign keys must preserve project scoping and immutable snapshot references.
Deletion is restricted when a version is referenced by a non-terminal run or
historical evidence. Database state transitions use compare-and-set checks and
append a transition/audit record in the same transaction.

The canonical execution relationship is:

```text
MessageEnvelope
  └─ worker payload: WorkerRunRequest / WorkerEvent / WorkerResult
       └─ WorkerRunController → worker_runs
            └─ flow_node_executions → flow_instances
                 └─ project evidence and audit records
```

Workers and tools are separate categories. A worker receives delegated work,
has an identity and lifecycle, may emit progress/checkpoints, and returns a
structured result. A tool performs a bounded operation through tool-service;
it does not own organizational identity or an independent task lifecycle.

### Separate state machines

Steward status, candidate intake, bundle certification, rollout, and worker-run
execution are distinct state machines. They must have separate persisted
status fields and transition histories.

#### Steward status

```text
PROVISIONING → READY → DEGRADED → SUSPENDED → RETIRED
```

#### Specialist Shell activation

```text
DRAFT → VALIDATING → INACTIVE → ACTIVE → SUSPENDED → RETIRED
                              ↘ ACTIVE
```

`ACTIVE` here means the AIAT shell is eligible for new dispatches; it does
not mean that a candidate rollout is active. Activation requires the selected
shell, adapter, bundle, capability snapshot, model policy (when applicable),
permissions, sandbox, and readiness checks to pass.

#### Candidate intake and certification

```text
DISCOVERED → SOURCE_REVIEW → SECURITY_REVIEW
→ INTERFACE_RESEARCH → GENERATED → CERTIFYING
→ APPROVED | REJECTED | BLOCKED
```

#### Skill/adapter candidate

```text
DRAFT → TESTING → CERTIFIED → APPROVED → SUPERSEDED
```

#### Rollout

```text
PENDING → SHADOW → CANARY → PROMOTING → ACTIVE
                         ↓
                    ROLLING_BACK → ROLLED_BACK
```

#### Worker run

```text
CREATED → VALIDATING → READY → DISPATCHING → RUNNING
       → PAUSING → PAUSED → RESUMING → RUNNING
       → SUCCEEDED | FAILED | CANCELLED | TIMED_OUT
```

Adapters may report observations and requested transitions, but only the
`WorkerRunController` may persist authoritative worker-run state. The flow
runtime/controller owns the same responsibility for node state.

| State machine | Persisted status field/history | Sole state writer | Terminal/immutability rule |
|---|---|---|---|
| Specialist Shell | `worker_registry.status` / shell-version status plus activation history | Worker activation service | `RETIRED` cannot accept new runs; an active run keeps its pinned versions even if the shell is suspended |
| Steward | `steward_agents.status` plus steward transition history | Steward lifecycle service | `RETIRED` cannot return to `READY`; a candidate rollout never changes steward status |
| Candidate intake | `hiring_pipeline_stages.status` plus `skill_bundle_candidates` review evidence | Steward/hiring service | `APPROVED`, `REJECTED`, and `BLOCKED` are immutable outcomes; a new discovery creates a new candidate lineage |
| Skill/adapter bundle | `skill_bundles.status` plus `certification_runs` evidence | Certification/approval service | Approved bundles are immutable; replacement creates a new version and marks the old one `SUPERSEDED` only after no new runs select it |
| Rollout | `rollout_records.status` plus rollout history | Rollout controller and authorized approval action | Only one pending rollout per worker lineage; active runs retain their pinned bundle, adapter, and runtime versions |
| Worker run | `worker_runs.state` plus CAS transition history | `WorkerRunController` | Terminal states cannot be reopened; adapters emit events but never write lifecycle state |
| Flow instance/node | `flow_instances.state` and `flow_node_executions.state` plus history | Flow runtime/controller | Terminal flow and node state is immutable except for explicitly audited recovery/evidence operations |

Each transition records actor, reason, correlation ID, expected prior state,
resulting state, and policy/certification references. APIs may request a
transition, but the owning controller validates the transition and commits it
atomically with its audit record.

### Wire protocol and versioning

Every contract envelope and persisted snapshot carries:

- `contract_version` — universal worker protocol, currently `aiat.worker.v1`;
- `schema_version` — schema revision for the individual payload;
- `adapter_api_version` — adapter SDK contract;
- `runtime_api_version` — discovered upstream/runtime interface version;
- `skill_bundle_format_version` — skill bundle format;
- `capability_snapshot_version` — immutable capability declaration version.

The current major version and the immediately previous major version are
accepted during the migration window. Unknown optional fields and namespaced
extensions are preserved and ignored by older readers. Unknown required
capabilities, unsupported major versions, malformed events, and missing
version metadata are rejected. A major-version change requires a new
conformance and certification run. Protocol compatibility is negotiated before
dispatch and the negotiation result is stored on the worker run.

#### Canonical wire shapes

The following shapes are normative. The generated JSON Schema in
`mas/packages/mas-core/schemas/protocol/` is the machine-readable source of
truth; this table prevents implementation drift between the envelope, adapter,
controller, and persistence layers.

| Payload | Required identity and control fields | Payload-specific fields | Controller invariants |
|---|---|---|---|
| `MessageEnvelope` | `protocol_version`, `message_id`, `correlation_id`, `msg_type`, `sender_id`, `sender_role`, `sender_team`, `timestamp`, `ttl_seconds`, `ack_required` | `contract_version`, `schema_version`, `payload_type`, `payload`, optional `blob_ref`, project and recipient scope | Transport only; it routes and deduplicates but never advances worker or flow state |
| `WorkerRunRequest` | `protocol`, `run_id`, `idempotency_key`, `worker_id`, `task_type`, `created_at` | task input, project/flow/node references, requested/resolved profile references, capability and permission requirements, workspace, budget, timeout, retry/checkpoint policy, extensions | Accepted once per `(worker_id, idempotency_key)`; a duplicate returns the existing `WorkerRun` |
| `WorkerRunAccepted` | `protocol`, `run_id`, `idempotency_key`, `worker_id`, `accepted_at` | runtime run ID, initial state, negotiated capabilities, metadata | Observation returned by an adapter; the controller persists `READY`/`RUNNING` transitions |
| `WorkerEvent` | `protocol`, `event_id`, `run_id`, `worker_id`, `sequence`, `event_type`, `emitted_at` | exactly the payload allowed by `event_type`: progress, tool request/response, checkpoint, pause/resume, result, error, usage, audit, or extension | Append-only; `(run_id, sequence)` is unique and the event hash makes conflicting duplicates a typed failure |
| `WorkerResult` | `protocol`, `run_id`, `worker_id`, `success`, `completed_at` | JSON-safe output, artifacts, usage, structured error, completion criteria, replay metadata | Only the controller may turn a validated result into a terminal `WorkerRun` and advance its flow node |
| `WorkerToolRequest` / `WorkerToolResponse` | `protocol`, request ID, run ID, tool name, idempotency key / success outcome | arguments, AIAT permission scope, approval requirement, result/error, usage | Routed through the existing `ToolRequest`/`ToolResponse` service contract; adapters cannot call tools around tool-service |
| `WorkerCancellation` / `WorkerPause` / `WorkerResume` | `protocol`, run ID, requester, requested-at timestamp | reason, force flag, optional checkpoint ID | Control requests are idempotent and advisory; the controller persists the authoritative transition |

The canonical v1 envelope carries a typed worker payload using
`payload_type` plus `payload`; the legacy direct-payload form remains accepted
only by the migration shim. `payload_type` must be one of the registered
worker payload names and must match `contract_version` and `schema_version`.
The existing `ProtocolEnvelope.message_type` helper is an ingress/SDK
compatibility alias for `payload_type`; it is normalized once and is not a
second wire envelope or persistence record.
Standalone health, readiness, usage, checkpoint, and audit payloads carry the
same `ProtocolVersion`; nested payloads may inherit the enclosing event's
version only after validation proves that they match.
Large inputs, outputs, logs, and artifacts use `blob_ref` or an artifact
reference with a SHA-256 digest rather than bypassing the envelope size limit.
Every adapter must validate these shapes before emitting an event. Every
consumer must reject an event scoped to a different run or worker, an event
with a sequence regression that is not an identical replay, and a terminal
result that does not satisfy the declared completion criteria.

### Model governance uses intersection semantics

Model resolution is not “most-specific value wins.” It computes:

```text
Allowed models
  = organization constraints
  ∩ project constraints
  ∩ flow constraints
  ∩ node constraints
  ∩ privacy/security/region constraints

Required capabilities
  = worker requirements
  ∪ steward requirements
  ∪ task requirements
  ∪ adapter requirements

Preferences
  = project → flow → node → worker → run request

Selection
  = best approved profile/version satisfying the intersection,
    required capabilities, budget, and rate/concurrency policy
```

The resolver returns a typed `NO_COMPLIANT_MODEL` policy failure when the
intersection is empty. It may return an approval request for a governed
override, but it may never choose an arbitrary model or an unrestricted
`auto` route. Rejected candidates and reasons are part of the immutable
resolution snapshot. A worker declares `model_mode` as one of:
`none`, `aiat_gateway`, `certified_external_runtime`, or `hybrid`. Only modes
other than `none` require model-policy resolution.

### Evidence policies are versioned and project-scoped

Completion integrity is evaluated against a selected immutable
`EvidencePolicy`, not one universal checklist. Initial policy profiles are:

```text
software_delivery | research | documentation | operations
manual | legacy_import | custom
```

Each profile declares required/optional flow terminality, documents, document
retrievability, artifacts, repository state, approvals, and audit evidence.
Projects without a repository or flow are valid when their selected policy
does not require one. Legacy records with missing evidence are explicitly
marked `legacy/incomplete evidence`; they are not silently presented as fully
complete.

### OpenCode interface verification is a gate before adapter implementation

#### Phase 0B — OpenCode Interface Verification

Before implementing or activating the OpenCode adapter:

1. Pin one exact OpenCode release and commit.
2. Capture its official configuration schema.
3. Capture its OpenAPI schema, when available.
4. Record authentication behavior and credential boundaries.
5. Verify project/path discovery, session creation, and task submission.
6. Verify event ordering, reconnect behavior, and stream termination.
7. Verify cancellation semantics and in-flight behavior.
8. Determine whether checkpoint/resume is native, wrapper-provided,
   restart-only, or unsupported.
9. Verify provider-qualified model configuration.
10. Commit compatibility fixtures and an Interface Verification Report.
11. Obtain security/policy approval for the report.
12. Block adapter implementation and activation until the report is approved.

Unsupported capabilities must be declared as unsupported and cause the worker
to remain inactive when the flow requires them; they must not be simulated.

### Safe canary, locking, retention, and supply-chain rules

Canary progression is explicitly:

```text
shadow evaluation → read-only canary → limited low-risk live canary
→ controlled promotion
```

Shadow and read-only canary runs cannot perform irreversible production side
effects. A rollout records eligible task classes, sample count, duration,
comparison metrics, minimum sample size, automatic rollback thresholds,
in-flight run treatment, and promoting authority. The default is 10 shadow
runs, 5 read-only canary runs, then 3 low-risk live runs, with rollback on any
critical failure, audit/artifact loss, permission expansion, or a >10%
regression in success, latency, cost, or cancellation metrics. In-flight runs
finish on their pinned version unless a security emergency explicitly cancels
them.

The rollout policy has these concrete semantics:

| Stage | Eligible work | Side effects | Default gate |
|---|---|---|---|
| `SHADOW` | Duplicated low/medium-risk inputs with an approved baseline | No tool writes, repository writes, external messages, or production credentials; output is comparison-only | 10 samples or 24 hours, whichever is later; no critical failure and no missing event/artifact/audit evidence |
| `CANARY` read-only | Read-only and deterministic tasks only | Read-only workspace and read-only tool grants | 5 samples or 24 hours; compare quality, success, p95 latency, cost, cancellation, and event completeness against the pinned baseline |
| `CANARY` low-risk live | Explicitly allowlisted low-risk task classes | Only reversible or idempotent side effects; no permission expansion or production credential use | 3 samples or 72 hours; a human `worker_rollout_approver` promotes or rejects |
| `PROMOTING` | No new candidate runs are admitted while the decision is committed | Existing runs remain pinned; the candidate becomes active only in one transaction with the approval record | All thresholds pass, required approvals exist, and no competing rollout lock is held |

Automatic rollback is triggered by any critical security/permission failure,
lost audit or artifact evidence, an adapter contract failure, or a configured
regression threshold. A candidate may not be promoted solely because its raw
output looks plausible. The rollout record stores baseline/candidate hashes,
the comparison dataset, sample counts, metric values, thresholds, task risk
class, side-effect policy, approver, and decision time.

Candidate discovery, generation, certification, rollout, and rollback use
idempotency keys, immutable versions, uniqueness constraints, and a
distributed/advisory lock keyed by worker and candidate lineage. A candidate
cannot be activated while another rollout is pending; versions referenced by
active runs cannot be deleted or mutated.

Concurrency rules are explicit:

| Race | Required result |
|---|---|
| Two monitors discover one upstream release | A unique `(steward_id, upstream_version, commit_sha)` key returns one candidate; the loser records a deduplicated observation |
| Simultaneous candidate generation | Lock the steward/lineage; one build is active, and the other request returns the existing build or a conflict with its status |
| Duplicate certification | Unique `(candidate_id, certification_suite_version)` key makes the operation idempotent; incompatible reruns create a new suite version |
| Rollout and rollback overlap | One worker rollout lock serializes them; rollback wins only after recording the observed active version and reason |
| Activation during approval | Activation uses a CAS on candidate approval and rollout status; stale approvals are rejected |
| Deprecation/deletion while runs are active | Mark the version deprecated for new dispatches, retain it until all referencing runs are terminal and retention policy permits cleanup, and never mutate it in place |

Retention is represented by a versioned `RetentionPolicy` selected by project
risk class, with legal hold and incident hold overriding deletion. The default
operational profile is: audit events, resolution snapshots, certification
evidence, rollout/rollback history, and terminal results retained permanently;
high-volume progress/log events kept hot for 30 days then compressed to object
storage for 365 days; high-risk project evidence retained for 7 years unless
the deployment policy requires longer; ordinary artifacts and documentation
snapshots retained for the project policy default of 365 days; superseded
candidate builds and generated bundles cleaned 30 days after supersession; and
active-run references retained until terminal plus the applicable evidence
retention period. Each profile is configurable, but a shorter profile cannot
delete evidence referenced by an active or historical run. Logs are redacted
by field/type before hot storage, and secrets/credential values are never
persisted in event payloads.

Licence and supply-chain evidence may include the root licence, dependency
licences, generated SBOM, OCI base-image licences, redistribution class,
network-service obligations, modified-file obligations, notice/attribution
bundle, and active-versus-candidate licence changes. These values are
informational provenance metadata for the personal/internal programme. Missing
or unusual licence metadata never blocks certification; security, compatibility,
source/version, sandbox, resource, and approval evidence remains authoritative.

### Repository-level implementation map

| Current path | Current responsibility | Planned change | Migration/test impact |
|---|---|---|---|
| `mas/packages/mas-core/mas_core/protocols/` | message, tool, manifest, capability schemas | Add universal worker wire schemas and version negotiation; extend manifest | Protocol fixtures, schema export, forward/backward tests |
| `mas/packages/mas-core/mas_core/worker_registry/` | registry, legacy wrappers, evaluation, compatibility | Add adapter SDK/transports, steward/candidate/certification services, OpenCode verification | Keep legacy readers; block new wrapper-only external activation |
| `mas/packages/mas-core/mas_core/llm_gateway/` | provider calls and legacy selector | Add Model Profiles, constraint resolver, resolution snapshots, governed override API | Existing client remains a gateway transport; production run paths use resolver |
| `mas/packages/mas-core/mas_core/workflow/` | flow graph validation/traversal | Add typed node policy validation, WorkerRunController integration, recovery rules, terminal guards | Existing graph tests plus worker-run integration/restart tests |
| `mas/packages/mas-core/mas_core/memory/models.py` | SQLAlchemy Core table metadata | Add tables/links for shells, adapters, stewards, candidates, profiles, runs, events, evidence | Metadata and migration checks |
| `mas/packages/mas-core/mas_core/memory/storage.py` | async persistence wrapper | Add scoped CRUD, CAS transitions, idempotency, locks, evidence/read models | Storage unit/integration tests and project-boundary tests |
| `mas/packages/mas-tools-sdk/` | tool invocation SDK | Extend existing tool request/response references and repository adapter contracts | Tool mediation and security tests; no duplicate worker tool API |
| `mas/apps/orchestrator-api/` | project, worker, flow APIs | Add versioned governed APIs and make controller the sole run-state writer | Existing API tests plus contract, rollout, and evidence tests |
| `mas/apps/message-router/` | envelope routing and WebSocket transport | Carry versioned worker payloads and normalized events without owning run state | Envelope compatibility and duplicate-event tests |
| `mas/apps/tool-service/` | bounded tool execution and sandboxing | Add worker-run/audit references, artifact hash checks, repository allowlist enforcement | Tool mediation, permission denial, isolation, and audit tests |
| `mas/apps/team-runner/` | native team/agent execution | Dispatch native workers through the universal adapter and report events | Native end-to-end exit gate; checkpoint migration tests |
| `mas/apps/mas-dashboard/` | project, flow, hiring, runtime UI | Add typed flow panels, model explanations, steward/candidate/canary views, evidence tabs | Existing named E2E specs extended; terminal controls hidden/blocked |
| `mas/workers/` and `mas/teams/` | YAML worker/team manifests | Add compatible shell/model-mode/provenance metadata and migration markers | Manifest validation; native/external migration status visible |
| `mas/prompts/` | role/system prompts | Add steward and governance prompts with untrusted-doc rules | Prompt security/provenance tests |
| `mas/migrations/` | Alembic history | Add migrations after `0014` for immutable versioned architecture records | Upgrade/downgrade and fresh-schema tests |
| `mas/infra/` | compose/images/deployment | Rebuild image correctness and expose build/schema metadata | Deployment smoke and version-mismatch tests |

The implementation must prefer these existing paths and interfaces. A new
subsystem requires a documented reason when an existing responsibility above
already covers it.

#### Concrete extension points, retained endpoints, and removals

The directory map above is complemented by the following file-level map. The
named files are the first extension points in the current checkout; a coding
agent must inspect their current signatures before adding a sibling subsystem.

| Current file(s) | Planned change | Preserve/remove decision |
|---|---|---|
| `mas/packages/mas-core/mas_core/protocols/envelope.py`, `tool.py`, `worker_manifest.py`, `schema_export.py`, `schemas/protocol/aiat.v1.schema.json` | Add the canonical worker payload discriminator, version metadata, schema exports, and ToolRequest/ToolResponse linkage | Extend the existing envelope/tool contract; retain legacy direct payload reads only in the migration shim |
| `mas/packages/mas-core/mas_core/worker_contract/models.py`, `protocol.py`, `adapters.py`, `controller.py`, `conformance.py` | Make these the universal wire/adapter/controller implementation and add generated fixtures | Extend the existing in-progress contract scaffold; no parallel `runtime_*` protocol package |
| `mas/packages/mas-core/mas_core/worker_registry/adapter_factory.py`, `runtime_adapters.py`, `ingestion.py`, `evaluator.py`, `compat_tests.py`, `steward.py` | Move wrapper/runtime discovery behind certified adapters, candidate certification, provenance, and steward services | Retain registry APIs/readers; deprecate wrapper-only activation after migration telemetry proves no active dependency |
| `mas/packages/mas-core/mas_core/llm_gateway/model_selector.py`, `model_profiles.py`, `model_resolver.py`, `persistence.py` | Route all governed selection through the intersection resolver and persist resolution snapshots | Keep gateway transport/provider clients; remove direct production use of unrestricted raw IDs and `auto` selection |
| `mas/packages/mas-core/mas_core/memory/models.py`, `storage.py`, `checkpoints.py` | Reconcile existing tables with worker runs/events/checkpoints/artifacts and add CAS/idempotency/lock operations | Existing tables remain authoritative per the reconciliation matrix; do not add duplicate checkpoint or artifact stores |
| `mas/packages/mas-core/mas_core/workflow/controller.py`, `flow_engine.py`, `events.py`, `evidence.py`, `worker_policy.py`, `transitions.py` | Validate typed node policies, dispatch `WorkerRunController`, normalize terminal results, and enforce evidence/terminal guards | Flow/controller remains the sole node/instance state writer; manual actions become audited overrides, not fake worker success |
| `mas/apps/orchestrator-api/orchestrator_api/main.py` | Extend current project, worker, model-profile, worker-run, and flow handlers with versioned contracts, typed errors, scopes, and idempotency | Retain current unversioned routes as thin compatibility aliases during the migration window; one handler implementation owns state changes |
| `mas/apps/message-router/message_router/routes_publish.py`, `routes_ws.py`, `tasks.py`, `redis_client.py` | Route typed worker envelopes, deduplicate by message/event identity, and preserve DLQ behavior | Router transports only; it cannot write `worker_runs`, flow nodes, approvals, or model state |
| `mas/apps/tool-service/tool_service/routes.py`, `registry.py`, `sandbox_runner.py`, `mcp_client.py`, `main.py` | Mediate worker tool calls, enforce permission/budget/workspace scopes, and attach run/audit/artifact references | Extend the existing tool service; deterministic scanners, formatters, queries, and file utilities remain tools |
| `mas/apps/team-runner/team_runner/main.py` and `mas/packages/mas-core/mas_core/agent_runtime/` | Adapt native team/agent execution to the universal request/event/result contract | Native execution remains supported, but lifecycle authority moves to `WorkerRunController` |
| `mas/apps/mas-dashboard/app/(dashboard)/projects/[id]/page.tsx`, `flows/[id]/page.tsx`, `workers/page.tsx`, `lib/flow-types.ts`, `lib/flow-store.ts`, `lib/protocol-fixtures.ts` | Add model-policy explanation, worker-run/steward/canary views, evidence tabs, and terminal-state guards | Keep dashboard as an API client; do not duplicate policy or lifecycle decisions in client state |
| `mas/apps/mas-dashboard/app/api/projects/`, `app/api/flows/`, `app/api/workers/` | Add proxies for versioned APIs and preserve auth/project scoping | Existing proxy paths remain compatible aliases; no direct database access from the dashboard |
| `mas/workers/*.yaml`, `mas/teams/*.yaml`, `mas/prompts/*.md` | Add model mode, adapter/provenance, permission, evidence, and steward metadata; add governance prompts | Existing manifests stay readable and receive migration markers; invalid external wrapper-only manifests cannot activate |
| `mas/migrations/versions/0004_orchestration_flows.py`, `0005_flow_execution_config.py`, `0007_worker_integration.py`, `0011_guarded_worker_evaluation.py`, `0012_document_lineage.py`, `0015_worker_steward_model_governance.py` | Use the existing Alembic chain and current 0015 groundwork for additive, immutable records and links | Never rewrite applied migrations; add a successor migration when schema correction is required; retain old columns for the compatibility window |
| `mas/infra/compose/`, `mas/infra/docker/Dockerfile.*`, deployment metadata | Pin runtime/adapter images, expose build/schema versions, and ensure API/dashboard images contain current code | Deployment fixes are a prerequisite gate; no runtime capability is considered verified from stale images |

Retained API surfaces include `/capabilities/workers` and its status/health/
evaluation/upstream operations, `/worker-contract/version`, `/workers/runs`
and its event/cancel operations, `/model-profiles`, `/flows` and flow-instance
operations, and `/projects` plus repository/document/context/artifact/evidence
operations. New canonical routes use `/api/v1`; existing unversioned routes
delegate to the same handlers until the published compatibility window ends.

The removal/deprecation checklist is explicit: remove the wrapper-only
external activation path around `_wrapper_manifest_for_hiring` in
`orchestrator_api/main.py`, remove direct production calls to unrestricted raw
model selection, retire duplicate runtime lifecycle writes, and retire direct
manual node advancement after audited-override compatibility is proven. Old
database columns and read APIs are deprecated only after migration telemetry,
replay tests, and an operator-visible deprecation report show no remaining
consumers.

#### Dependency direction

`mas-core.protocols`, `worker_contract`, `memory`, and workflow policy code are
library-layer code and must not import FastAPI route handlers, dashboard code,
or deployment clients. `mas-core` is composed by `orchestrator-api` and
`team-runner`; `message-router` depends on protocol schemas only; the
`mas-tools-sdk` exposes tool contracts without owning worker lifecycle;
`tool-service` implements tool mediation and sandbox policy; adapters depend on
the worker contract and SDK, never on dashboard code or raw database writes.
The dashboard calls versioned API routes and cannot become a second resolver,
controller, or persistence client.

### Program structure and exit gates

The work is delivered as six programs with independently testable gates:

#### Program A — Universal execution foundation

Contracts, native adapter, `WorkerRunController`, persistence, conformance, and
API versioning. **Exit gate:** one existing native worker runs end-to-end only
through the universal contract.

#### Program B — Model governance

Model Profiles, intersection resolver, snapshots, overrides, and explanation
UI. **Exit gate:** native runs are reproducible and no new production path
accepts unmanaged raw model IDs.

#### Program C — Steward and hiring lifecycle

Steward runtime, provenance, documentation snapshots, candidate bundles and
adapters, certification, and hiring changes. **Exit gate:** a synthetic
external worker can be hired, certified, canaried, and rolled back.

#### Program D — OpenCode integration

Interface report, pinned release, certified adapter/steward, model mapping, and
fixtures. **Exit gate:** after the approved Phase 0B report, a pinned OpenCode
release completes a real coding task in the supported integration environment,
including cancellation, failure recovery, artifact capture, and audit
verification. Compatibility fixtures supplement the live gate and make CI
deterministic; they are not a substitute for the live acceptance run. If the
supported environment cannot execute the pinned release, Program D is blocked
and the adapter remains inactive rather than claiming fixture-only success.

#### Program E — Flow-runtime migration

Typed node policy, WorkerRun dispatch, retry/cancellation/checkpoint/recovery,
deterministic node advancement, and terminal immutability. **Exit gate:** one
complete flow uses only certified workers and survives service restart.

#### Program F — Project workspace and evidence

Repository read model, documents/context/artifacts, evidence policies,
terminal-state repair, and project UI. **Exit gate:** a new software project
is traceable from project → flow → node → worker run → model → tools →
artifacts → evidence.

The numbered migration phases below are execution checkpoints, not competing
programs. Phase 0 and Phase 0B are prerequisites; Program A maps to Phase 1,
Program B to Phase 2, Program C to Phases 3 and 5, Program D to Phase 4,
Program E to Phase 6, and Program F to Phase 7. Phase 8 is the controlled
rollout portion of Programs C/D. No phase may claim completion until its
program exit gate and its migration tests pass.

## Current Problems Confirmed

### Projects

The completed projects examined in the UI expose several defects:

- Documents exist in object storage but are not shown with preview/download controls.
- Project context projection is incomplete or empty even when documents exist.
- Feasibility and review documents are not consistently represented in the project document lifecycle.
- Completed projects still expose stale “blocked worker activation” next actions.
- Completed projects expose workflow mutation controls that should be read-only.
- Repository endpoints return `404` because projects have no consistently initialized repository record.
- Project creation fields exist in source but are not reflected in the running dashboard deployment.
- The project list does not show meaningful lifecycle, document, repository, flow, worker, or health information.
- The project workspace has no clear explanation of where the project lives on disk or how Git is managed.
- Projects can be completed without a complete evidence package.
- The UI allows attaching or mutating flows on terminal projects.
- The third inspected project is a legacy/incomplete record with no documents, artifacts, flow, repository, or execution evidence but a completed state.

### Flows

The flow system currently has a serialized graph model, but:

- Node configuration is mostly untyped free-form JSON.
- The flow editor does not provide a first-class worker, runtime, steward, model-profile, permission, or workspace selector.
- Model choice is not validated against worker/runtime capabilities.
- Flow configuration can contain raw model IDs that bypass policy.
- Flow nodes do not consistently dispatch through the universal worker contract.
- Runtime metadata and actual runtime execution are separate.
- Generic external-worker wrappers assume a Python class with `execute()`, which does not represent CLI, HTTP, MCP, or OpenCode server execution.
- Retry, timeout, cancellation, escalation, and checkpoint semantics are not expressed uniformly in the worker contract.
- Runtime-specific event streams are not normalized into a stable AIAT event model.
- Flow controls expose broad wildcard actions instead of state- and capability-aware actions.
- Flow validation checks graph structure but not runtime readiness, model compatibility, permissions, budgets, or adapter certification.
- Flow execution can be manually advanced without proving that the underlying worker run completed successfully.
- The UI does not explain why a model or worker was selected.
- There is no deterministic model-resolution snapshot attached to a flow run.

### Workers and hiring

The repository has manifests, adapters, source provenance, evaluation, version pins, and update policy fields, but they are incomplete:

- OpenCode/OpenHands are represented mainly as metadata in worker YAML.
- `isolation_mode: wrapper` expects a mirrored Python module/class rather than a certified external application driver.
- There is no first-class external runtime identity, steward identity, skill bundle, documentation snapshot, or capability negotiation record.
- Hiring registers a source repository and queues evaluation, but does not create a dedicated upstream steward.
- Evaluation focuses on repository/configuration checks, not a full runtime certification lifecycle.
- A worker can be marked approved without proving real task execution, model compatibility, cancellation, checkpoint, artifact, or failure-recovery behavior.
- Existing `update_policy` values are not connected to a candidate-update pipeline.
- There is no active-versus-candidate bundle model.
- There is no canary, rollback, compatibility matrix, or update approval history.
- Native and external workers do not share the same operational contract.
- External runtimes may implicitly own their own model selection or credentials unless explicitly mediated.

### OpenCode-specific issue

OpenCode is not simply an LLM model ID. The repository must treat the
following as Phase 0B verification targets rather than guaranteed adapter
capabilities:

- provider-qualified model IDs such as `provider/model`;
- project and agent configuration;
- custom agents with prompts, permissions, and model settings;
- a headless HTTP server with an OpenAPI surface;
- event and session APIs;
- continuously changing configuration and runtime behavior.

The OpenCode Steward must therefore drive OpenCode through a certified adapter and must translate AIAT model profiles into valid OpenCode provider/model configuration. OpenCode’s current model and server concepts should be treated as upstream contracts that are discovered and version-pinned, not hardcoded assumptions. See the official [OpenCode model documentation](https://opencode.ai/docs/models/), [agent documentation](https://opencode.ai/docs/agents), and [server documentation](https://opencode.ai/docs/server/).

## Target Architecture

Every worker will have three layers:

1. AIAT Specialist Shell

The authoritative AIAT-owned layer containing:

- stable worker identity;
- department and organizational role;
- worker lifecycle state;
- versioned skill bundle;
- policy and permission grants;
- model-policy requirements;
- project and memory access;
- budget limits;
- tool grants;
- telemetry and audit configuration;
- evaluation and certification status;
- update and rollout state;
- dedicated Steward Agent reference when externally backed.

2. Runtime Adapter

A certified adapter implementing the universal AIAT worker contract and translating to:

- native in-process Python;
- process/stdio;
- HTTP;
- WebSocket/event stream;
- MCP;
- OCI/container;
- LangGraph;
- CrewAI;
- OpenCode;
- future external applications.

3. Runtime Implementation

The actual runtime:

- native AIAT worker;
- framework graph;
- CLI process;
- HTTP service;
- MCP server;
- OCI image;
- external open-source application.

The control plane must only interact with the Specialist Shell and adapter contract. It must not depend on OpenCode session internals, CrewAI objects, LangGraph state, CLI output formats, or external project state.

## Universal Worker Contract

Add versioned schemas and SDK interfaces for:

- `WorkerManifest`
- `WorkerIdentity`
- `WorkerCapabilities`
- `WorkerCapabilityRequirement`
- `WorkerRunRequest`
- `WorkerRunAccepted`
- `WorkerEvent`
- `WorkerProgress`
- `WorkerToolRequest`
- `WorkerToolResponse`
- `WorkerResult`
- `WorkerArtifact`
- `WorkerError`
- `WorkerCancellation`
- `WorkerPause`
- `WorkerCheckpoint`
- `WorkerResume`
- `WorkerHealth`
- `WorkerReadiness`
- `WorkerUsage`
- `WorkerAuditEvent`

The contract must support:

- task identity and idempotency keys;
- project, flow, node, and run identifiers;
- requested and resolved model profiles;
- capability negotiation;
- tool mediation;
- permission and approval requirements;
- progress events;
- structured results;
- artifact references;
- structured errors;
- cooperative and forced cancellation;
- pause/checkpoint/resume;
- health and readiness;
- token, cost, latency, and resource usage;
- logs, metrics, traces, and audit records;
- deterministic replay metadata;
- forward/backward protocol compatibility.

Runtime-specific extensions may be stored under controlled namespaces:

- `extensions.opencode`
- `extensions.langgraph`
- `extensions.crewai`
- `extensions.mcp`
- `extensions.process`
- `extensions.oci`

Extensions must never bypass AIAT authority, permissions, credentials, budgets, model policy, tool-service, project state, or audit history.

Capability declarations must include, at minimum:

- `checkpoint_mode`: `native`, `wrapper`, `restart_only`, `unsupported`;
- `cancellation_mode`: `immediate`, `cooperative`, `after_current_step`;
- `streaming_mode`: `event_stream`, `polling`, `final_only`;
- `tool_mode`: `aiat_mediated`, `certified_native_bridge`;
- `memory_mode`: `aiat`, `runtime_native`, `hybrid`;
- `workspace_mode`: `isolated`, `shared_readonly`, `approved_write`;
- `model_mode`: `none`, `aiat_gateway`, `certified_external_runtime`, `hybrid`.

No worker becomes `ACTIVE` unless its adapter passes the conformance suite for every claimed capability.

## ExternalWorkerSteward

Add a reusable `ExternalWorkerSteward` base runtime, but instantiate a separate steward per external worker.

Each steward owns:

- upstream identity and provenance;
- official documentation sources;
- repository and release monitoring;
- interface and schema discovery;
- capability mapping;
- configuration schema;
- command/API/event templates;
- adapter knowledge;
- runtime limitations;
- permission model;
- error and recovery procedures;
- skill bundle generation;
- compatibility suite generation;
- update candidate preparation;
- certification comparison;
- rollout recommendations.

The steward may generate candidate changes but cannot modify the active bundle directly.

### Steward, candidate, certification, and rollout lifecycles

These are separate state machines. The steward itself uses:

1. `PROVISIONING`
2. `READY`
3. `DEGRADED`
4. `SUSPENDED`
5. `RETIRED`

Each steward may own many immutable candidate intake records. Candidate intake
uses `DISCOVERED`, `SOURCE_REVIEW`, optional `LICENSE_METADATA` (the historical
`LICENSE_REVIEW` label), and `SECURITY_REVIEW`,
`INTERFACE_RESEARCH`, `GENERATED`, `CERTIFYING`, and then `APPROVED`,
`REJECTED`, or `BLOCKED`. Generated skill bundles and adapters have their own
`DRAFT`, `TESTING`, `CERTIFIED`, `APPROVED`, and `SUPERSEDED` status. A rollout
record uses `PENDING`, `SHADOW`, `CANARY`, `PROMOTING`, `ACTIVE`,
`ROLLING_BACK`, and `ROLLED_BACK`. A steward must not be marked `READY` merely
because a candidate is active, and a candidate status must not be inferred from
the steward status.

Activation must be blocked when:

- provenance is uncertain;
- the source is not pinned;
- licence metadata is missing or unusual (record an operator notice only; this
  is never an activation or certification blocker);
- documentation cannot verify required interfaces;
- required capabilities cannot be proven;
- permissions expand unexpectedly;
- adapter conformance fails;
- security or supply-chain checks fail;
- cost or performance exceeds policy;
- cancellation/checkpoint behavior regresses;
- artifacts or audit events are incomplete.

## External Runtime Provenance

Add first-class persisted fields/entities for:

- external worker identity;
- canonical source repository;
- source provider;
- exact release/version;
- commit SHA;
- package version;
- OCI image digest;
- dependency lock hash;
- protocol/API version;
- adapter version;
- transport type;
- runtime image or executable fingerprint;
- license and redistribution status;
- security scan status;
- documentation snapshot version;
- last verified documentation timestamp;
- steward identity;
- active skill bundle;
- candidate skill bundle;
- active adapter;
- candidate adapter;
- capability snapshot;
- certification status;
- compatibility matrix;
- update policy;
- monitoring cadence;
- rollout state;
- canary state;
- rollback target;
- approval records.

Keep immutable history for every change.

## Skill Bundle Format

Create a versioned skill-bundle format containing:

- bundle ID and semantic version;
- upstream compatibility range;
- steward identity;
- source provenance;
- documentation references;
- verified capabilities;
- model requirements;
- command templates;
- API request templates;
- event parsers;
- configuration schema;
- permission mappings;
- sandbox requirements;
- tool mappings;
- workspace behavior;
- checkpoint/recovery procedures;
- error taxonomy;
- known limitations;
- evaluation fixtures;
- migration notes;
- generated artifact hashes.

The active bundle must be immutable. Candidate bundles are separate records with diff, evidence, test results, and approval state.

## OpenCode Steward and Adapter

Implement an OpenCode-specific steward and adapter only after the mandatory
Phase 0B Interface Verification Report is approved.

The adapter may use OpenCode’s headless HTTP server only when Phase 0B proves
that the pinned release exposes the required stable interface. A process/CLI
fallback is allowed only when separately certified; otherwise the capability
is marked unsupported and the worker remains inactive for tasks that require
it.

It may declare and implement only the capabilities verified for the pinned
release. The adapter capability set must explicitly record `native`,
`wrapper`, `restart_only`, or `unsupported` for checkpoint/resume and the
corresponding verified mode for cancellation, streaming, artifact capture,
and provider/model configuration. It must not claim a capability because the
universal contract has a field for it.

For the verified capability subset, support:

- server health/version discovery;
- OpenAPI document retrieval;
- project/path discovery;
- session creation;
- agent selection;
- provider-qualified model selection;
- prompt/task submission;
- event streaming;
- progress normalization;
- tool and permission mediation;
- artifact/workspace collection;
- cancellation;
- checkpoint/recovery behavior;
- structured error handling;
- version and schema compatibility checks.

The OpenCode Steward must maintain:

- supported OpenCode versions;
- provider/model ID mapping;
- agent configuration format;
- permission configuration;
- project-local skill and agent files;
- API endpoint schemas;
- event schemas;
- CLI command changes;
- release notes and migration notes;
- compatibility fixtures;
- known model/runtime incompatibilities.

OpenCode must not receive unmanaged provider credentials. Model calls must go through AIAT’s LLM gateway unless a separately approved certified exception exists.

## Model Profiles

Replace raw model strings in project, flow, node, worker, and adapter
configuration with versioned `ModelProfile` entities for workers whose
`model_mode` is `aiat_gateway`, `certified_external_runtime`, or `hybrid`.
Workers with `model_mode: none` remain governed by capability, permission,
budget, and audit policy but do not require an LLM Model Profile.

A Model Profile contains:

- logical profile ID;
- purpose;
- approved provider IDs;
- exact provider model IDs;
- model/API versions where available;
- required capabilities;
- context-window requirements;
- tool-calling support;
- structured-output support;
- vision support;
- reasoning support;
- streaming support;
- embedding support;
- cost limits;
- token limits;
- latency targets;
- concurrency/rate-limit constraints;
- privacy and data-retention policy;
- local/cloud/region requirements;
- provider-specific adapter settings;
- bounded fallback order;
- evaluation status;
- certification status;
- effective dates;
- deprecation state;
- compatibility history.

### Deterministic resolution hierarchy

Resolve effective model policy in this order:

1. organization/company;
2. project;
3. flow;
4. flow node;
5. worker;
6. external steward;
7. task type;
8. individual run override;
9. approved bounded fallback chain.

More-specific configuration may narrow or select within inherited policy, but cannot bypass:

- privacy;
- security;
- capability;
- budget;
- provider;
- region;
- licensing;
- approval restrictions.

Every run must persist:

- requested profile;
- resolved profile version;
- provider;
- exact model;
- model/API version;
- effective configuration;
- capability checks;
- fallback decisions;
- cost estimate;
- actual usage;
- override reason;
- approval record;
- selection reason.

A running or replayed execution must retain its original resolution snapshot.

### Model selector fixes

Refactor the existing selector so it:

- never falls back to an unrestricted `"auto"` route;
- only considers approved profiles;
- does not assume all tasks should prefer free models;
- validates provider-qualified IDs;
- checks actual runtime compatibility;
- accounts for expected prompt and output size;
- checks privacy and region policy;
- checks project and worker budgets;
- returns an explicit policy failure when no compliant profile exists;
- records all rejected candidates and reasons;
- produces a deterministic fallback chain;
- supports dry-run compatibility checks;
- distinguishes recommendation from authorized selection.

## Database and Migrations

Add migrations after the current worker/document/project migrations for:

- worker shell metadata;
- runtime adapters;
- external runtime provenance;
- steward agents;
- skill bundles;
- skill-bundle candidates;
- documentation sources and snapshots;
- capability snapshots;
- compatibility matrices;
- certification runs;
- model profiles;
- model profile versions;
- model resolution snapshots;
- model override requests;
- rollout and canary records;
- rollback records;
- worker run records;
- normalized worker events;
- worker checkpoints;
- worker artifacts;
- worker usage records;
- hiring pipeline stages;
- approval records;
- update monitoring jobs;
- project repository records;
- project evidence-package records.

Use immutable version records for active and candidate configurations.

Add foreign keys linking:

- project → flow instance;
- flow instance → flow node execution;
- flow node execution → worker run;
- worker run → worker shell version;
- worker run → adapter version;
- worker run → steward version;
- worker run → model-resolution snapshot;
- worker run → artifacts/events/checkpoints;
- worker → certification and rollout state.

## Hiring Process

Hiring any external worker must execute this pipeline:

1. Source/provenance intake.
2. Repository and licence/notice metadata capture.
3. Optional redistribution-context metadata capture (never an AIAT gate).
4. Security and supply-chain scan.
5. Documentation and interface discovery.
6. Exact upstream version selection.
7. Runtime transport selection.
8. Specialist Shell creation.
9. Dedicated Steward Agent creation.
10. Initial skill bundle generation.
11. Initial adapter generation.
12. Capability negotiation.
13. AIAT contract mapping.
14. Sandbox certification.
15. Compatibility tests.
16. Regression tests.
17. Performance tests.
18. Cost tests.
19. Cancellation tests.
20. Checkpoint/resume tests.
21. Crash/failure-recovery tests.
22. Artifact and audit completeness tests.
23. Department-chief review.
24. Security/policy review.
25. Human approval where required.
26. Controlled activation.
27. Canary monitoring.
28. Continuous upstream monitoring.

Existing `/capabilities/workers` APIs should be extended rather than replaced. Add endpoints for:

- steward creation;
- steward status;
- source provenance;
- documentation snapshots;
- capability negotiation;
- skill bundle listing;
- candidate generation;
- certification;
- compatibility matrix;
- update check;
- candidate approval/rejection;
- canary start;
- rollout;
- rollback;
- worker run history;
- worker health/readiness;
- worker event stream;
- model compatibility preview.

Worker activation must require (when the worker's `model_mode` is not `none`):

- approved evaluation;
- certified adapter;
- valid active skill bundle;
- valid model profile;
- valid sandbox;
- valid permissions;
- readiness check;
- no unresolved security blocker (licence metadata is informational only).

For `model_mode: none`, activation instead requires a deterministic runtime
contract, certified adapter, valid capability and permission declarations,
readiness, sandbox validation where applicable, and no unresolved security
blocker. Pure tools such as scanners, formatters, repository
queries, and file utilities remain registered with tool-service and do not
enter the worker hiring lifecycle.

## Flow Changes

### Flow definition model

Extend nodes with typed configuration:

- assigned worker or team;
- required capabilities;
- model profile when the assigned worker's `model_mode` is not `none`;
- task type;
- permission requirements;
- project workspace mode;
- tool grants;
- budget;
- timeout;
- retry policy;
- cancellation policy;
- checkpoint policy;
- escalation policy;
- artifact expectations;
- completion criteria;
- runtime extension configuration.

Separate:

- graph topology;
- node execution policy;
- runtime configuration;
- inherited model policy;
- user-provided task input.

Validate every flow before saving and before execution.

### Flow execution

A task node must:

1. Resolve worker/team assignment.
2. Resolve Specialist Shell version.
3. Resolve adapter version.
4. Resolve steward and skill bundle when external.
5. Resolve a Model Profile hierarchically when `model_mode` is not `none`;
   otherwise persist the deterministic capability/permission policy snapshot
   and prove that no model resolution is required.
6. Negotiate capabilities.
7. Validate permissions, budget, workspace, and secrets.
8. Create a durable worker run.
9. Dispatch through the adapter contract.
10. Persist accepted/progress/result/error events.
11. Register artifacts and usage.
12. Advance the flow only after a valid normalized result.
13. Persist the exact execution snapshot.

Manual node completion must be restricted to authorized operator actions and recorded as an override, never treated as normal worker success.

### Retry and recovery

Implement explicit retry policies:

- retry same worker/version/model;
- retry with approved fallback model;
- retry with alternate certified adapter;
- retry from checkpoint;
- restart from last safe node;
- escalate;
- fail terminally.

Retries must not silently duplicate side effects. Use idempotency keys and durable run state.

### Timeout and cancellation

Implement:

- worker-level timeout;
- node-level timeout;
- flow-level timeout;
- cooperative cancellation;
- forced termination for supported transports;
- cancellation acknowledgment;
- orphan-run detection;
- restart/reconciliation after service failure.

### Approval and escalation

Approval nodes must include:

- approver role/user;
- scope;
- expiry;
- decision options;
- required evidence;
- audit record;
- rejection path;
- revision path;
- cancellation path.

Escalation must be capability-aware and must not send a task to an uncertified or inactive worker.

## Flow UI

Update the flow builder to include:

- typed node configuration panels;
- worker/team selector;
- runtime and adapter readiness;
- Steward Agent display;
- skill bundle version;
- model profile selector when applicable, with an explicit model-less mode;
- inherited model-policy explanation;
- capability requirements;
- budget preview;
- permission preview;
- sandbox preview;
- timeout/retry/cancellation/checkpoint configuration;
- artifact expectations;
- validation results;
- compatibility test action;
- dry-run execution;
- graph validation errors;
- runtime readiness errors.

The flow detail page must show:

- current instance state;
- active nodes;
- worker runs;
- resolved model profile;
- fallback events;
- approvals;
- checkpoints;
- artifacts;
- errors;
- retries;
- escalations;
- audit timeline;
- effective policy;
- immutable terminal evidence.

Terminal instances must be read-only except for explicitly permitted evidence export or rollback/recovery operations.

## Projects Page and Project Lifecycle

### Project creation

Create a guided project wizard with:

- name and description;
- owner/department;
- initial context;
- repository mode;
- repository URL;
- branch;
- workspace mode;
- project model policy;
- default flow;
- initial workers;
- permissions;
- sandbox profile;
- budget;
- document generation policy;
- approval policy.

Creation must initialize:

- project record;
- project workspace;
- repository record;
- Git state;
- context projection;
- initial document/evidence collection;
- policy snapshot;
- audit record.

### Project workspace and Git

Make the project location explicit:

- logical project ID;
- workspace path;
- repository mode;
- remote URL;
- branch;
- commit;
- dirty state;
- last sync;
- adapter health.

The current bounded repository adapter should remain the authority for project Git operations. It must:

- create `/workspace/<project_id>`;
- initialize or clone the repository;
- create `.aiat/project.json`;
- make the initial commit;
- reject credential-bearing URLs;
- enforce repository allowlists;
- provide status/sync/commit/push/remove operations;
- record every operation in the project audit history.

The UI must never imply that a repository exists when initialization failed.

### Project overview

Replace the minimal project table with columns/cards for:

- state;
- owner;
- active flow;
- current node;
- worker health;
- document count;
- artifact count;
- repository status;
- last commit;
- blocked reason;
- next valid action;
- last activity;
- evidence completeness.

### Project detail tabs

Add or repair:

- Overview;
- Documents;
- Context;
- Repository;
- Flow;
- Worker Runs;
- Artifacts;
- Sprints/Issues;
- KPIs;
- Reviews;
- Audit;
- Policies.

Documents must support:

- type;
- version;
- status;
- author/worker;
- created/updated times;
- provenance;
- object-storage reference;
- SHA-256;
- preview;
- download;
- revision;
- supersession history;
- review comments;
- context inclusion state.

The context tab must show the source document and artifact lineage for every context item.

### Completion integrity

A project cannot be marked completed unless the selected immutable
`EvidencePolicy` is satisfied. Every policy requires all active worker runs to
be terminal, no pending approval gates, a terminally empty next action, and
persisted terminal audit evidence. A policy may additionally require terminal
flow nodes, documents and retrievable bodies, registered artifacts, repository
state, or other evidence. The completion response must identify each required
check and its evidence reference. A missing optional evidence item must not be
reported as a blocker, while a missing required item must produce a typed
`INCOMPLETE_EVIDENCE` failure.

Completed projects must show a completion summary and evidence completeness indicator.

## API and Contract Changes

Add or extend APIs for:

- project overview/read model;
- project evidence completeness;
- project document preview/download;
- context lineage;
- repository status/init/sync;
- project policy;
- project worker runs;
- flow effective configuration;
- flow dry-run validation;
- model profile catalog;
- model resolution preview;
- model override request;
- worker conformance;
- steward lifecycle;
- documentation snapshots;
- candidate skill bundles;
- candidate adapters;
- certification;
- rollout/canary/rollback;
- worker health/readiness;
- worker run events;
- checkpoint/resume.

All APIs must:

- enforce project scoping;
- enforce role and permission checks;
- return typed errors;
- preserve audit records;
- reject terminal mutations;
- use idempotency keys for creation, execution, retry, rollout, and rollback.

API compatibility is explicit: new canonical routes are under `/api/v1`,
worker payloads declare `contract_version` and `schema_version`, and adapters
declare `adapter_api_version`, `runtime_api_version`,
`skill_bundle_format_version`, and `capability_snapshot_version`. The current
v1 schema and the immediately previous compatible schema revision are accepted
for the migration window. Unknown optional fields are ignored/preserved;
unknown required fields, capabilities, or major versions produce typed
`UNSUPPORTED_CONTRACT_VERSION` or `UNSUPPORTED_CAPABILITY` errors. Major
contract changes require a new adapter conformance and worker certification.
The current unversioned routes are compatibility aliases to the v1 handlers,
not a second implementation, and their removal requires an operator-visible
deprecation window.

## Security Controls

Implement:

- no credentials in repository URLs;
- secret references rather than secret values;
- least-privilege tool grants;
- runtime-specific permission translation;
- sandbox enforcement;
- egress allowlists;
- workspace isolation;
- read-only candidate evaluation;
- immutable active bundles;
- signature/hash verification for source and artifacts;
- security and policy gates; licence/notice fields remain provenance metadata;
- prompt/document provenance;
- protection against upstream instruction injection;
- external documentation treated as untrusted input;
- bounded command execution;
- event schema validation;
- resource quotas;
- approval for permission expansion;
- audit records for every override and rollout;
- rollback on health or security regression.

The Steward Agent may read external documentation and propose changes, but may not independently activate code, alter permissions, access production credentials, or mutate authoritative project state.

## Migration Strategy

### Phase 0 - Deployment correctness

- Rebuild Python images so current `mas-core` code is actually installed.
- Rebuild dashboard images so current project UI changes are deployed.
- Verify migrations and seeded workers.
- Remove stale image/source mismatches.
- Add deployment version/build metadata to the dashboard and API.

### Phase 0B - OpenCode interface verification

- Pin one exact OpenCode release and commit in a reviewable fixture/config.
- Capture official configuration and available OpenAPI schemas.
- Verify authentication, project/path discovery, session/task lifecycle,
  event ordering/reconnect, cancellation, artifact capture, and provider/model
  configuration against that exact release.
- Classify checkpoint/resume as native, wrapper-provided, restart-only, or
  unsupported and record the result in the capability snapshot.
- Commit compatibility fixtures and the Interface Verification Report.
- Obtain security/policy approval before any OpenCode adapter implementation
  or activation; a failed or unavailable verification blocks Phase 4.

### Phase 1 — Contract foundation

- Add universal worker schemas.
- Add adapter SDK and conformance runner.
- Add normalized event/result/error/artifact contracts.
- Add native adapter.
- Add process/HTTP/MCP/OCI transport abstractions.
- Add worker-run persistence.

### Phase 2 — Model governance

- Add Model Profiles and versions.
- Add policy hierarchy.
- Add capability-aware resolver.
- Add model-resolution snapshots.
- Replace direct model strings in worker and flow execution.
- Add compatibility preview and governed overrides.

### Phase 3 — Steward foundation

- Add steward entities and lifecycle.
- Add documentation/provenance snapshots.
- Add skill bundle format.
- Add candidate bundles and adapters.
- Add update monitor and certification pipeline.

### Phase 4 - OpenCode implementation

- Start only after Phase 0B is approved; do not implement against an assumed
  upstream interface.
- Implement OpenCode server adapter.
- Implement process fallback adapter.
- Implement OpenCode Steward.
- Add provider-qualified model mapping.
- Add OpenCode agent/permission/skill generation.
- Add OpenCode compatibility and event fixtures.
- Certify one pinned OpenCode release before activation, including the live
  Program D exit gate; fixtures alone cannot activate the worker.

### Phase 5 — Hiring migration

- Update hiring APIs and board.
- Make steward creation mandatory for external workers.
- Add certification gates.
- Migrate coding worker and tester first.
- Then migrate security, browser/research, planning, DevOps, and documentation workers.
- Keep compatibility shims for existing manifests during a defined migration window.
- Prevent new external workers from using the legacy wrapper-only path.

### Phase 6 — Flow execution

- Dispatch task nodes through Worker Runs.
- Add model and capability resolution.
- Add normalized lifecycle events.
- Add cancellation, checkpoint, retry, fallback, and recovery semantics.
- Add flow dry-run and certification validation.
- Make terminal flow states immutable.

### Phase 7 — Projects and evidence

- Add repository read model.
- Add document preview/download/context lineage.
- Add project evidence completeness.
- Add project policy and worker-run views.
- Repair terminal-state controls.
- Add project creation wizard.
- Migrate legacy completed projects into explicit `legacy/incomplete evidence` status where evidence is missing rather than silently presenting them as fully complete.

### Phase 8 — Canary and rollout

- Activate one certified OpenCode worker in canary mode.
- Compare active and candidate adapter/skill/model behavior.
- Verify rollback.
- Expand to additional external workers.
- Remove compatibility shims after all workers pass conformance.

## Tests

### Protocol and SDK

- schema validation;
- unknown extension handling;
- version negotiation;
- malformed event rejection;
- forward/backward compatibility;
- idempotency;
- duplicate event handling;
- artifact hash verification;
- error normalization.

### Adapter conformance

For every adapter:

- health/readiness;
- task acceptance;
- progress;
- success;
- structured failure;
- timeout;
- cancellation;
- pause/resume;
- checkpoint;
- crash recovery;
- duplicate request;
- tool mediation;
- permission denial;
- budget reporting;
- audit completeness;
- workspace isolation.

### Model policy

- organization/project/flow/node/worker precedence;
- policy narrowing;
- forbidden provider rejection;
- privacy/local-only enforcement;
- context-size filtering;
- tool-calling filtering;
- OpenCode provider/model ID validation;
- budget rejection;
- rate-limit fallback;
- bounded fallback chain;
- no-compliant-model failure;
- override approval;
- reproducible resolution snapshot;
- replay preserves original model resolution.

### OpenCode

- server health/version discovery;
- OpenAPI retrieval;
- project/path discovery;
- session creation;
- agent configuration;
- permission mapping;
- provider/model configuration;
- prompt submission;
- event stream parsing;
- cancellation;
- artifact collection;
- runtime error mapping;
- upstream schema incompatibility detection;
- model incompatibility handling;
- pinned-version reproducibility.

### Steward

- documentation ingestion;
- provenance tracking;
- release detection;
- license change detection;
- security advisory detection;
- capability snapshot;
- candidate skill generation;
- candidate adapter generation;
- active bundle immutability;
- candidate certification;
- permission expansion blocking;
- regression detection;
- canary;
- automatic rollback;
- approval enforcement.

### Hiring board

- external hire creates Specialist Shell;
- external hire creates Steward Agent;
- source provenance required;
- exact version required;
- security/policy review required; licence metadata is collected but is not a gate;
- failed evaluation blocks activation;
- missing adapter certification blocks activation;
- missing model profile blocks activation only for `model_mode` other than
  `none`;
- human approval required for configured risk classes;
- native worker can use the same schema without steward requirement;
- deterministic `model_mode: none` worker can activate without an LLM profile;
- legacy worker migration status is visible.

### Flow API/runtime

- typed node configuration;
- invalid worker rejection;
- inactive worker rejection;
- missing capability rejection;
- model-policy rejection;
- budget rejection;
- task dispatch;
- normalized progress;
- successful completion;
- timeout;
- cancellation;
- retry;
- fallback;
- escalation;
- checkpoint/resume;
- crash recovery;
- duplicate-effect prevention;
- terminal immutability;
- evidence completeness;
- flow restart without duplicate execution.

### Project API/UI

- create project with repository;
- repository initialization failure;
- clone existing repository;
- status/sync/commit/push authorization;
- document list;
- document preview/download;
- document version/supersession;
- context lineage;
- artifact download/hash;
- evidence completeness;
- completed project read-only controls;
- stale next-action cleanup;
- no flow mutation on terminal project;
- legacy incomplete-project display;
- project list summary;
- project creation wizard;
- worker-run and model-resolution display.

### Browser E2E

Extend:

- `flow-builder.spec.ts`;
- `flow-runtime-test2.spec.ts`;
- `hiring-board.spec.ts`;
- project page E2E coverage;
- runtime status coverage.

Add E2E scenarios for:

- creating a project with repository and policy;
- inspecting project workspace/Git status;
- viewing PDR/CDR/RR bodies;
- viewing context lineage;
- creating a flow with worker/model profile;
- validating a flow before save;
- starting a worker run;
- observing resolved model;
- approving a model override;
- observing cancellation/retry/checkpoint;
- hiring an external worker and seeing its Steward;
- viewing candidate update and certification status;
- rolling back a canary;
- terminal project read-only behavior.

## Acceptance Criteria

The implementation is complete when:

- every active worker conforms to the universal worker contract;
- every external worker has a dedicated Steward Agent;
- no active external worker depends only on the legacy generic wrapper;
- every active worker has pinned provenance and certified adapter/runtime state;
- every run has a durable worker-run record;
- every run with `model_mode` other than `none` has an immutable
  model-resolution snapshot; `model_mode: none` runs instead persist the
  capability/permission/execution-policy snapshot that proves no model was
  required;
- model selection never bypasses approved profiles or inherited policy;
- the approved pinned OpenCode release completes the real Program D live gate
  through a certified adapter with provider-qualified models;
- OpenCode failures, events, cancellation, artifacts, and usage normalize into AIAT contracts;
- candidate upstream updates never modify active production bundles;
- candidate updates are tested, approved, canaried, and rollback-capable;
- flow task nodes dispatch through the worker contract;
- terminal flows and projects cannot expose invalid mutation controls;
- completed projects expose documents, context lineage, repository state, artifacts, worker runs, and completion evidence;
- project Git ownership and workspace location are visible in the UI;
- legacy workers are either migrated, explicitly marked compatibility-mode, or blocked from activation;
- all required unit, integration, migration, and browser tests pass.

## Assumptions and Defaults

- The requested filename was the repository-root file
  `plan-externalWorkerStewardArchitecture.prompt.md`; it is retained here as a
  historical design input.
- The control plane remains AIAT-owned.
- External applications never own authoritative project, approval, credential, permission, budget, hiring, or audit state.
- Continuous updating means continuous monitoring and candidate preparation, never silently following upstream HEAD.
- Active runtimes, adapters, skill bundles, and model resolutions remain pinned and reproducible.
- Pinned-and-gated updates are the default policy.
- Model Profiles are the only supported production model-selection interface.
- The universal worker contract is mandatory for native and external workers.
- Runtime-specific extensions are allowed only behind the universal contract.
- OpenCode is integrated through its certified server/API adapter first, with process fallback only when certified.
- Existing uncommitted project-related changes are preserved and reviewed during implementation rather than overwritten.
