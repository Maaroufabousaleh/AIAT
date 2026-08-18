# P2 Scale, Storage, and Guarded Autonomy Plan

**Priority:** P2 after default-programme completion  
**Outcome:** AIAT scales its data and workers and can improve itself without weakening authority  
**Authority:** [AIAT Target Programme](../../../AIAT_TARGET_PROGRAMME.md)

## Workstream 1 — storage abstraction and migration

- [x] Formalise an S3-compatible object-store contract and deterministic
  provider-neutral conformance suite (`aiat.object-store-conformance.v1`).
- [x] Add an explicit `--live` runner that points the same suite at a
  configured S3-compatible endpoint and returns exit code 2 with a
  machine-readable `blocked` result when credentials or the provider are
  unavailable; external provider evidence is still evaluated separately.
- [x] Run the same suite against the deployed local MinIO adapter and retain
  secret-safe 8/8 evidence in
  [`mas/docs/provenance/object_store_live_conformance.json`](../../../mas/docs/provenance/object_store_live_conformance.json);
  the run is reproducible with
  [`mas/infra/compose/scripts/check-minio-conformance.sh`](../../../mas/infra/compose/scripts/check-minio-conformance.sh);
  provider-pair, large-object, benchmark, encryption, and disaster-recovery
  evidence remain open.
- [x] Make the aggregate live release child use the checked-in private-network
  MinIO probe via `check_object_store_conformance.py --compose-local`; the
  8/8 local result is now bounded and no longer times out on the host-only
  `minio:9000` alias. Provider-diverse evidence remains separate.
- [x] Add the bounded `aiat.object-store-benchmark.v1` fixture and
  fail-closed `scripts/check_object_store_benchmarks.py --live` runner. It
  measures disposable upload/download checksum read-back and cleanup for
  named MinIO and SeaweedFS configurations without printing credentials or
  selecting a provider; the actual provider comparison still requires live
  endpoints and reliability/resource/concurrency/large-object/multipart/
  metadata/outage/recovery evidence.
- [x] Implement the deterministic checksum-verified copy/parity helper
  (`aiat.object-store-copy.v1`) over explicit `BlobRef` inputs.
- [x] Add an explicit `--live` copy/parity runner that inventories
  checksum-bearing source refs, verifies target read-back parity, preserves
  source data, and fails closed on missing providers or an empty inventory;
  dual-write, cutover, and rollback remain separate operator workflows.
- [x] Add a deterministic `aiat.object-store-backup.v1` checksum manifest and
  `aiat.object-store-restore.v1` clean-target verifier. The fixture runner
  proves source → backup → restore parity; `--live` requires three provider
  endpoints and blocks until provider, encryption, retention, and clean
  environment evidence is available.
- [x] Run a disposable same-provider backup → restore rehearsal against local
  MinIO with two objects, exact manifest/read-back parity, and scoped cleanup;
  retain secret-safe evidence in
  [`mas/docs/provenance/object_store_backup_restore_live.json`](../../../mas/docs/provenance/object_store_backup_restore_live.json)
  using [`mas/infra/compose/scripts/check-minio-backup-restore.sh`](../../../mas/infra/compose/scripts/check-minio-backup-restore.sh).
- [x] Make restore copy fail closed before mutation when the target project
  prefix is non-empty. `require_clean_target` performs the preflight and
  `clean_target_verified` is retained in `aiat.object-store-restore.v1`
  evidence; the fixture/live backup runner and governed migration workflow use
  the guard (`93bf755`). Provider-diverse, encrypted, and clean-environment
  disaster-recovery evidence remain open.
- [x] Add the provider-neutral `aiat.object-store-migration.v1` workflow and
  deterministic fixture for checksum inventory, verified copy, optional dual
  write, and explicit human-confirmed cutover/rollback; provider-certified
  routing, retention, and live rollback evidence remain open.
- Add encrypted backup/replication to Garage, R2, B2, or another approved backend.
- Automate clean-environment restore verification beyond the bounded empty-target
  preflight above.

**Decision gate:** SeaweedFS becomes primary only if measured results, operational complexity, source/version provenance, migration safety, and restore evidence beat the current profile. Licence remains metadata and does not decide the result.

## Workstream 2 — optional memory and workflow services

- Evaluate Letta for governed long-memory use cases.
- Evaluate Qdrant where it materially improves retrieval beyond pgvector.
- Evaluate Temporal for durable execution where it complements rather than replaces AIAT controllers.
- For each, create an external worker/service steward, exact provenance, data-boundary review, conformance, cost, outage, backup, and removal plan.

**Decision gate:** optional services remain disabled unless they provide measurable value without duplicating canonical authority.

## Workstream 3 — multi-host and high-risk execution

- [x] Certify the bounded local Postgres worker-run lease boundary with
  [`check_worker_lease_recovery_postgres.py`](../../../mas/scripts/check_worker_lease_recovery_postgres.py)
  (`a413997`) and retained evidence at
  [`worker_lease_recovery_postgres_evidence.json`](../../../mas/docs/provenance/worker_lease_recovery_postgres_evidence.json).
  The certificate proves live-lease claim exclusivity, owner-bound heartbeat,
  one explicitly simulated expiry/requeue, second-owner reclaim, terminal
  claim denial, durable transition read-back, and scoped cleanup. It is a
  local queue API certificate only; it does not claim a host registry,
  placement/capacity scheduler, real host-loss or split-brain proof, or
  gVisor/Firecracker host certification.
- [x] Define the deterministic `aiat.worker-placement.v1` policy and fixture
  checker with host-plane isolation, host health/lease, labels, capabilities,
  sandbox/isolation, slot/memory/GPU capacity, priority ordering, and
  duplicate-ID rejection (`3fb15db`, building on `db22e60`; evidence at
  [`worker_placement_contract.json`](../../../mas/docs/provenance/worker_placement_contract.json)).
  This is a pure read-only contract; scheduler settlement is covered by
  `d9917f8`, and host fencing/recovery is covered by `72e59ec`.
- [x] Add the durable authenticated worker-host registry and heartbeat lease
  boundary through migration `0037_worker_host_registry` (`500fc57`), then
  persist explicit `control`, `tool`, `data`, and `worker` host planes through
  migration `0041_worker_host_planes` (`3fb15db`). The
  Postgres certificate at
  [`worker_host_registry_postgres_evidence.json`](../../../mas/docs/provenance/worker_host_registry_postgres_evidence.json)
  proves token-digest registration, wrong-token rejection, AIAT-owned lease
  renewal, redacted public projections, placement snapshot and worker-plane
  read-back after
  connection reopen, expired-lease visibility, and scoped cleanup. Durable
  capacity reservation/commit is covered separately below; host fencing and
  recovery are covered by `72e59ec`; scheduler integration is covered by
  `d9917f8`.
- [x] Add the AIAT-owned durable host capacity reservation ledger through
  migration `0038_worker_host_reservations` (`232c0bb`). The retained
  certificate at
  [`worker_host_reservations_postgres_evidence.json`](../../../mas/docs/provenance/worker_host_reservations_postgres_evidence.json)
  proves host lease/readiness enforcement, row-locked over-capacity rejection,
  idempotent replay, commit/release/expiry transitions, scalar capacity
  projection, connection-reopen read-back, and scoped cleanup.
- [x] Connect the durable host registry and reservation ledger to the
  authenticated `aiat.worker-host-scheduler.v1` layer (`d9917f8`; evidence at
  [`worker_host_scheduler_postgres_evidence.json`](../../../mas/docs/provenance/worker_host_scheduler_postgres_evidence.json)).
  It proves deterministic preferred-host selection, row-locked fallback,
  idempotent replay, draining/unleased filtering, blocked full capacity,
  connection-reopen read-back, and scoped cleanup without worker dispatch.
- [x] Bind a durable Worker Run to the AIAT-selected worker-host reservation
  through migration `0042_worker_run_host_binding` (`08f1610e`; evidence at
  [`worker_run_host_binding_postgres_evidence.json`](../../../mas/docs/provenance/worker_run_host_binding_postgres_evidence.json)).
  The binding preserves the worker-plane host lease generation, replays an
  assignment key safely, settles commit/release transitions, and survives
  connection reopen; external worker execution remains a separate gate.
- [x] Add durable host lease-generation fencing and expired-host recovery
  through migration `0039_worker_host_fencing` (`72e59ec`; evidence at
  [`worker_host_recovery_postgres_evidence.json`](../../../mas/docs/provenance/worker_host_recovery_postgres_evidence.json)).
  Re-registration fences stale heartbeats and old reservations; expired-host
  reconciliation marks the host OFFLINE, advances its generation, expires
  current reservations, and excludes it from placement after reopen.
- [x] Persist explicit `control`, `tool`, `data`, and `worker` host planes and
  make worker placement fail closed on non-worker planes through migration
  `0041_worker_host_planes` (`3fb15db`; host-plane/placement evidence is
  retained in the worker-host and placement certificates).
- [x] Add the committed-binding `aiat.worker-host-execution.v1` boundary
  (`73c0bda`). `WorkerHostExecutor` admits only a committed worker-plane
  binding whose reservation, host status, lease generation, and current lease
  are valid; it claims the queued run, delegates to `WorkerRunController`, and
  releases the binding after terminal execution. The local Postgres certificate
  at [`worker_host_execution_postgres_evidence.json`](../../../mas/docs/provenance/worker_host_execution_postgres_evidence.json)
  proves native fixture dispatch, durable usage/artifact/trace evidence,
  connection-reopen read-back, payload-free projection, release settlement, and
  scoped cleanup. This is an AIAT-owned local execution edge, not proof of a
  deployed sandbox or external provider.
- [x] Certify concurrent native execution across two distinct durable
  worker-plane host identities (`f9c717b`) with
  [`check_worker_multi_host_execution_postgres.py`](../../../mas/scripts/check_worker_multi_host_execution_postgres.py)
  and retained evidence at
  [`worker_multi_host_execution_postgres_evidence.json`](../../../mas/docs/provenance/worker_multi_host_execution_postgres_evidence.json).
  The local Compose certificate commits two reservations, claims and completes
  two runs concurrently through `WorkerHostExecutor`, verifies lease-generation
  fencing, durable usage/artifact/trace coverage after Postgres reopen, releases
  both bindings, and cleans its fixture namespace. It is not independent-host,
  sandbox, provider, or outage-recovery evidence.
- [x] Certify bounded fenced host-loss queue recovery (`893293a`) with
  [`check_worker_host_loss_queue_recovery_postgres.py`](../../../mas/scripts/check_worker_host_loss_queue_recovery_postgres.py)
  and retained evidence at
  [`worker_host_loss_queue_recovery_postgres_evidence.json`](../../../mas/docs/provenance/worker_host_loss_queue_recovery_postgres_evidence.json).
  The host-filtered recovery path expires one lost host and reservation,
  requeues an expired Worker Run claim, rejects stale execution, reassigns the
  queued binding to an alternate host, completes a native retry at attempt two,
  reopens Postgres, and cleans only its fixture namespace. Independent hosts,
  sandbox, provider, and provider-backed recovery remain open.
- [x] Certify selected Model Profile/version resolution and snapshot
  propagation through a committed worker-host run (`6cef1b8`, hardened by
  `9a7db70`, durable gateway-adapter completion `8ed53df`) with
  [`check_worker_host_model_resolution_postgres.py`](../../../mas/scripts/check_worker_host_model_resolution_postgres.py)
  and retained evidence at
  [`worker_host_model_resolution_postgres_evidence.json`](../../../mas/docs/provenance/worker_host_model_resolution_postgres_evidence.json).
  The local certificate proves deterministic approved-profile selection,
  durable snapshot/reference propagation, one production `GatewayWorkerAdapter`
  call over a bounded local gateway double, `aiat_gateway` worker attribution,
  exact provider/model usage, Postgres reopen, payload-free coverage, release,
  and scoped cleanup. It uses local fixture identifiers and does not claim an
  external provider call, provider-backed recovery, independent hosts, or a
  hardened sandbox.
- [x] Enforce pre-terminal model usage attribution (`199eb5b`): the canonical
  worker controller compares successful result provider/model identifiers with
  the immutable resolution snapshot before usage or terminal evidence is
  persisted. Missing/incomplete snapshots and mismatches fail closed, while
  legacy/native runs without a snapshot remain compatible. External provider
  identity and hardened sandbox evidence remain separate.
- [x] Add the governed `aiat_gateway` model-worker adapter (`080ee18`) and
  deterministic fixture checker
  [`check_gateway_worker_adapter.py`](../../../mas/scripts/check_gateway_worker_adapter.py).
  The adapter requires an exact resolved model, normalizes bounded prompt/
  generation inputs, routes through the AIAT-owned gateway client, and emits
  provider/model usage for attribution. The fixture completes one controller
  run without external provider, network, or sandbox calls; `f6baebc` registers
  the transport in the worker manifest contract, builtin runtime catalogue,
  and static reconciliation checker, while `cec1e4c` starts/stops the owned
  gateway client and bounds prompt/message input. Evidence is
  [`gateway_worker_adapter_fixture.json`](../../../mas/docs/provenance/gateway_worker_adapter_fixture.json).
- [x] Exercise the real AIAT gateway-client HTTP boundary in a deterministic
  loopback transport (`cbbfe56`). The certificate retries one fixture `429`,
  checks the `/v1/chat/completions` path and bearer-secret boundary, verifies
  bounded model/prompt/generation input, and reads back controller terminal
  state plus exact provider/model usage. Evidence is
  [`gateway_worker_http_fixture.json`](../../../mas/docs/provenance/gateway_worker_http_fixture.json);
  external provider, outage-recovery, and sandbox evidence remain open.
- [x] Normalize gateway-worker failure semantics (`b2ae516`). Pre-dispatch
  input validation is terminal and non-retryable; transient gateway status
  classes remain retryable provider failures; permanent gateway responses are
  terminal provider rejections. Error details are bounded to status/cause type
  metadata and focused tests cover all three paths. This hardens the local
  recovery boundary but does not claim live provider recovery.
- [x] Certify the gateway worker through the bounded host-executor boundary
  (`38c99f4`). The real `WorkerHostExecutor`, `WorkerRunController`, and
  `GatewayWorkerAdapter` admit a committed in-memory worker-plane binding,
  claim the queued run, record exact fixture model/usage attribution, settle
  terminal state, release the binding, and pass payload-free scalar trace
  coverage. The retained artifact is a synthetic report pointer, not model
  output; durable host storage, external provider dispatch, independent hosts,
  sandbox execution, and live recovery remain open.
- [x] Certify gateway failure settlement through the host boundary
  (`2abc02a`). The real host/controller/adapter fixture drives both a
  retryable `429` and permanent `401`; each run settles `FAILED`, releases the
  committed binding/reservation, retains only status/cause metadata, and keeps
  injected provider detail out of evidence. This is local failure semantics,
  not automatic live retry, provider recovery, durable host, or sandbox proof.
- [x] Refresh the local read-only model gateway/profile evidence (`68e0b03`,
  route checker `f6ed16f`)
  after the worker-host certificate. The API-owned catalogue still reports
  92/94 approved covered profile versions, and `/v1/models` exposes all five
  AIAT aliases; no completion request, provider call, routing mutation, or
  activation decision is included. Evidence is
  [`model_profile_catalogue_live.json`](../../../mas/docs/provenance/model_profile_catalogue_live.json)
  and [`model_gateway_readiness_live.json`](../../../mas/docs/provenance/model_gateway_readiness_live.json).
- [ ] Prove external provider-backed model dispatch on the worker plane,
  multi-host Firecracker/gVisor operation, and provider-backed recovery.
- Certify gVisor across supported hosts.
- Add Firecracker worker pools for high-risk tasks with image/rootfs, network, secrets, artifact, and cleanup controls.
- [x] Prove durable in-flight shell/adapter/skill-bundle/steward version
  pinning with migration `0040_worker_run_skill_bundle_pin` and
  [`check_worker_version_pinning_postgres.py`](../../../mas/scripts/check_worker_version_pinning_postgres.py)
  (`6a10b0e`) and retained evidence at
  [`worker_version_pinning_postgres_evidence.json`](../../../mas/docs/provenance/worker_version_pinning_postgres_evidence.json).
  A version-one `RUNNING` run remains pinned while the registry advances and a
  new queued run uses the complete version-two set; run creation snapshots the
  active bundle under the worker-row lock and validates ownership.
- Prove independent deployed-host loss, split-brain avoidance, queue recovery,
  duplicate-effect protection, and complete run-version pinning across all
  governed version records.

## Workstream 4 — guarded self-improvement

- Detect improvement candidates from defects, metrics, upstream updates, cost, and operator goals.
- [x] Add bounded `aiat.self-improvement-candidate-detection.v1` signal
  normalization for defect, metric, upstream-update, cost, and operator-goal
  observations. Exact duplicate IDs collapse, conflicting reuse fails closed,
  risk/budget mapping is deterministic, and the detector cannot create a
  project, reserve budget, grant credentials, or change a deployment; licence
  metadata remains provenance only.
- [x] Define `aiat.self-improvement.v1` opportunity metadata and a canonical
  project request with owner, risk, budget, evidence policy, and source; the
  authenticated `POST /projects/self-improvement` path and durable storage
  persistence now delegate through the canonical project writer; a revisioned
  lifecycle snapshot, project-history record, and authenticated canonical
  reference-link/action APIs cover the durable project boundary (`64218ab`,
  `test_self_improvement_api.py`, and storage lifecycle tests). The reserved
  local Compose Postgres certificate (`10983c8`) now proves the same writer's
  revision/CAS persistence, history read-back, and cleanup; live issue/worker,
  provider, and deployment execution remains work.
- Use isolated branches/workspaces and certified coding/test/review/security workers.
- [x] Define independent coding, testing, review, security, migration, and
  rollback gate records plus a separate human approval gate; licence metadata
  is explicitly outside the predicate.
- [x] Prove a deterministic shadow/canary → human approval → promotion fixture
  and an exact prior-version rollback exercise; live worker/deployment evidence
  remains open.
- [x] Persist lifecycle revisions and typed links to canonical issue, worker,
  artifact, budget, branch/SBOM, deployment, repository, and evidence records
  without introducing a second project store; live record generation and
  reconciliation remain open.
- [x] Persist bounded terminal outcome records with cost, incident count,
  rollback state, KPI learning, evidence references, and actor attribution in
  the revisioned lifecycle snapshot; identical outcome IDs are idempotent and
  conflicting retries fail closed. Live worker/provider reconciliation remains
  open.
- [x] Define and persist a frozen `aiat.self-improvement-artifacts.v1` manifest
  containing one checksum-bearing change, provenance, SBOM, migration, and
  rollback reference, linked through the canonical artifact map; incomplete,
  mutable, and conflicting manifests fail closed.
- [x] Convert normalized worker-result records into the five-kind manifest,
  preserve canonical artifact-row IDs, and persist SHA-256/size read-back
  evidence without copying bytes or executing migrations; deterministic
  worker-record and object-store fixtures cover parity and tamper rejection.
- [x] Certify the complete bounded lifecycle against local Compose Postgres:
  six technical gates, stale-revision rejection, human-only approval, exact
  rollback, five artifact read-backs, terminal outcome persistence, durable
  project history, and reserved-project cleanup (`10983c8`; evidence at
  [`mas/docs/provenance/self_improvement_postgres_evidence.json`](../../../mas/docs/provenance/self_improvement_postgres_evidence.json)).
- Generate those immutable artifacts from live certified workers and verify
  read-back against a configured external provider; the current contract is
  ready, but live worker/provider evidence remains open.

**Prohibited:** self-granted credentials/policy, self-approval of mandatory gates, audit deletion, mutable production deployment, budget bypass, legal acceptance, or removal of the human kill switch.

## Workstream 5 — operational scale and analytics

- [x] Add request-level trace propagation and context cleanup in the
  orchestrator API, message router, and tool service; forward the bound trace
  on router/SDK publication and carry envelope/tool correlation IDs into
  message/worker records; bind the same IDs for agent message-handler
  lifetimes. Durable cross-service spans remain a live/scale evidence gate.
- [x] Add the bounded `aiat.trace-evidence.v1` operator query over task logs,
  project-usage events, worker-run transition correlations, direct
  trace-correlated model-usage/worker-artifact/integration-evidence rows (with
  legacy run fallback), API observations, and PM inbound metadata; project
  `trace_days`/`trace_sample_rate` from the company manifest, redact raw
  payloads, and expose a deterministic fixture plus fail-closed live checker.
- [x] Define and commit the bounded native transport/model/tool/audit/worker/
  integration span and trace-evidence contracts (`77d5494`), with scalar
  allow-listed attributes and deterministic redaction fixtures. The API/storage
  writers, live deployment coverage, identity-service mail-edge spans, incident
  views, and production retention authority remain separate review/live gates.
- [x] Add the `aiat.worker-trace-coverage.v1` evaluator and fail-closed
  `scripts/check_worker_trace_coverage.py` (`24c2e35`). The fixture requires
  model-usage, worker-artifact, native-model, and native-worker source
  categories; `--require-integration` makes native integration plus durable
  integration evidence explicit. Read-only live inspection accepts a selected
  trace, while dispatch requires an explicitly selected active model-backed
  worker/project/profile and `--confirm-dispatch`; no worker is auto-selected
  or activated and licence/restriction metadata is not a gate.
- [x] Define versioned descriptive SLO targets for API, queue age, worker
  startup/run, tool latency, model routing, PM/SCM sync, mail delivery, and
  recovery; expose observed/attention/no-data statuses through the operator
  API and deterministic `scripts/check_slo_capacity.py` fixture. Native
  first-class sources remain open where the report honestly returns `no_data`;
  the refreshed local deployment now returns a bounded live report (`9` SLO
  targets, `6` observed services, capacity `clear`, SLO `attention`) retained
  at [`mas/docs/provenance/slo_capacity_live.json`](../../../mas/docs/provenance/slo_capacity_live.json).
- [x] Add bounded capacity planning and budget forecasts using durable
  `project_usage_events` aggregates, including confidence, cost/token demand,
  configured budget headroom, and explicit insufficient-data notices.
- [x] Project existing PM/SCM inbox/outbox delivery and worker-recovery
  transition records into bounded SLO observations without raw payloads.
- [x] Persist the bounded `aiat.api-observation.v1` orchestrator request
  ledger (normalized route/method/status/outcome/duration and safe trace/
  principal metadata only), feed it into the platform SLO and trace evidence,
  and verify the payload-free fixture with `scripts/check_api_observability.py`.
- [x] Project existing signed identity-service outbound delivery-attempt rows
  into bounded `mail_delivery` SLO observations, dropping mail content,
  recipients, subjects, provider IDs, correlation IDs, and relay reasons;
  persist/filter safe delivery `trace_id`/`span_id` metadata for the trace
  evidence join.
- [x] Add the non-mutating `aiat.trace-retention-plan.v1` planner and
  deterministic fixture. It classifies bounded native-span metadata using
  explicit `retention_until` or the company retention period, keeps invalid
  rows out of deletion candidates, makes archive/delete mode explicit, and
  honors an explicit boolean `legal_hold` marker with a separate count;
  applying deletion, archival, authoritative holds, project narrowing, and
  backup parity remains a separate operator/live storage action (`9a80c6c`).
- [x] Expose the bounded planner through the operator-only
  `GET /observability/retention/plan` route and generated contracts, and add
  `scripts/check_trace_retention.py --live` (`f8829d6`). The route/checker
  explicitly report `mutation_performed: false`; destructive enforcement,
  authoritative legal holds, erasure, project narrowing, audit, and restore
  parity remain separate gates.
- [x] Type the retention-plan API response and reject mutation claims at
  serialization (`b3fca97`); generated OpenAPI/SDK artifacts now name the
  policy, count, and candidate schemas.
- [x] Add the provider-neutral `aiat.trace-retention-execution.v1` contract
  and deterministic in-memory rehearsal (`01996c9`). Preview is explicitly
  non-mutating; `57e13cb` makes apply validate typed checksum/count/
  clean-target backup/read-back evidence in addition to project scope,
  and `15054ba` makes it validate a typed authoritative hold-registry
  snapshot in addition to project scope and human confirmation before one
  atomic adapter call and bounded audit record. `5d71309` validates the
  bounded audit envelope and `67f5eae` supplies the fixture’s typed
  registry-read source. `96f5fc0` adds a Postgres-backed local adapter and
  `check_trace_retention_execution.py --live` certificate: four reserved
  native spans are planned, one eligible delete is guarded by database-local
  backup/read-back parity and human confirmation, two held rows remain, and
  scoped cleanup leaves zero rows. This is local fixture evidence only;
  production hold-registry authority, durable audit, erasure, archive,
  provider-diverse recovery, and restore rollback remain open.
- [x] Connect the native transport/API observation writer to the refreshed local
  orchestrator deployment, run the operator trace query after applying the
  current migrations, and retain secret-safe evidence in
  [`mas/docs/provenance/trace_observability_live.json`](../../../mas/docs/provenance/trace_observability_live.json)
  using [`scripts/check_live_trace_observability.py`](../../../mas/scripts/check_live_trace_observability.py).
  The fresh 2026-08-11 probe observes one bounded `/health` transport span and
  its API request ledger row without creating project, worker, provider,
  credential, or deployment state.
- [x] Connect the tool-service usage writer to the same native-span contract and
  run a bounded local `time_now` call with an existing project context. The
  operator trace read-back observes one `project_usage_events` row and one
  `tool_service` native tool span; secret-safe evidence is retained in
  [`mas/docs/provenance/tool_trace_live.json`](../../../mas/docs/provenance/tool_trace_live.json)
  and reproducible with [`scripts/check_live_tool_trace.py`](../../../mas/scripts/check_live_tool_trace.py) (`eac83ae`, refreshed 2026-08-11); the host-side probe is fail-closed and creates only bounded telemetry rows.
  The probe creates only normal telemetry rows and does not claim worker/model
  execution or provider evidence.
- [x] Define `aiat.mail-edge-observation.v1` and
  `aiat.mail-edge-coverage.v1` for payload-free identity/provider delivery
  attempts, verified webhooks, bounce/failure events, trace correlation,
  deterministic event-ID conflict handling, and metadata-only provider fields;
  add the deterministic redaction fixture and fail-closed checker in
  `scripts/check_mail_edge_observations.py` (`85369fe`).
- [x] Persist normalized provider webhook observations in identity-service with
  migration `0003_mail_edge_observations` and a unique `(provider,event_id)`
  conflict boundary; expose the signed delegated route and project correlated
  provider events through the scalar `mail-relay` dashboard/SLO/trace read
  model (`cfafe38`). Provider-specific ingress verification and live deployment
  read-back remain separate evidence boundaries.
- [x] Add the fail-closed Resend/Svix raw-body signature verifier, bounded
  timestamp tolerance, and provider-facing
  `POST /v1/mail-edge/provider-webhook/resend` ingress route (`2d21a2f`). The
  route authenticates the exact body before JSON normalization and reuses the
  same payload-free persistence/idempotency boundary; the injected webhook
  secret remains runtime configuration, not stored evidence.
- [x] Teach the live mail-edge checker to classify projected
  `mail.provider_webhook.<event>` spans as verified provider observations while
  retaining ordinary delivery-attempt rows as unsigned (`29d4da5`). The parser
  remains read-only and payload-free; configured callback, worker, and bounce
  read-back evidence remain separate.
- [x] Add optional signed identity-service dashboard read-back to the checker,
  trace-filtered normalization, duplicate-event preference, and fail-closed
  partial-configuration handling (`074ef8a`). The capability is read-only and
  does not claim that a live provider callback or worker run has occurred.
- [x] Certify the real identity-service Resend/Svix ingress boundary through a
  disposable local ASGI fixture (`aab6285`): signed delivered/bounced events,
  payload-free normalization and persistence, duplicate idempotency, conflict
  rejection, raw-body tamper rejection, and dashboard read-back are retained
  at [`mas/docs/provenance/mail_edge_ingress_certification.json`](../../../mas/docs/provenance/mail_edge_ingress_certification.json).
  This closes only the local application boundary; it does not claim an
  external provider callback, Postgres durability, selected worker run, or
  live bounce evidence.
- [x] Certify the same ingress against the rebuilt local Compose
  `PostgresIdentityStore` at migration `0003_mail_edge_observations`
  (`2d04b30`): reopen the store, read two normalized rows through SQL and the
  dashboard projection, verify payload-free persistence, and clean only the
  reserved fixture namespace. Secret-safe evidence is retained at
  [`mas/docs/provenance/mail_edge_postgres_ingress_certification.json`](../../../mas/docs/provenance/mail_edge_postgres_ingress_certification.json).
  This is local database evidence only; external provider, selected worker,
  live bounce, and outage/restore evidence remain open.
- [x] Join the independent worker trace-source and payload-free mail-edge
  evaluators under `aiat.worker-mail-edge-coverage.v1` (`1d8aed5`). The
  deterministic certificate requires explicit worker/trace scope, worker
  usage/artifact/model/worker sources, optional integration sources when
  requested, a verified webhook, and a bounce/failure signal; evidence is
  retained at [`worker_mail_edge_coverage_fixture.json`](../../../mas/docs/provenance/worker_mail_edge_coverage_fixture.json).
  This closes only the local evidence-join contract and does not claim a live
  worker run, external provider callback, durable worker records, or live
  bounce/read-back evidence.
- [x] Add the bounded `aiat.trace-incident.v1` summary and
  `scripts/check_trace_incident.py` (`c357fdf`) over the existing trace-evidence
  authority. Failure findings, severity, source counts, and partial/empty
  coverage are scalar and payload-free; an `attention` result is descriptive
  and never a release, activation, dispatch, or retention gate.
- [x] Expose the bounded incident projection through the operator-only
  `GET /observability/incidents/{trace_id}` route, generated OpenAPI/SDK
  contracts, dashboard proxy, and `/logs?trace_id=…` deep link (`b4b7cef`).
  The surface remains read-only, payload-free, and non-gating; live incident
  population and richer chronology remain separate evidence boundaries.
- [x] Render the bounded incident finding references and occurrence timestamps
  in the same operator deep link (`869202c`); no raw trace items or payloads are
  displayed, and live source population remains separate.
- [x] Certify one real `WorkerRunController`/`NativeWorkerAdapter` execution
  against the local Postgres store (`acd3f06`). The checker persists the run
  lifecycle, worker artifact and usage rows, native model/worker/audit spans,
  and a payload-free trace projection; it closes and reopens the store,
  verifies durable read-back, and removes only the reserved fixture namespace.
  Evidence is [`worker_run_postgres_evidence.json`](../../../mas/docs/provenance/worker_run_postgres_evidence.json).
  This closes local durable worker evidence only; live model/provider,
  external callback/bounce, retention, sandbox, canary/rollback, and outage
  evidence remain separate.
- [x] Compose the real gateway worker/controller with the independent
  worker-trace and mail-edge evaluators (`6ebb12c`). The local certificate
  [`gateway_worker_mail_edge_fixture.json`](../../../mas/docs/provenance/gateway_worker_mail_edge_fixture.json)
  proves exact fixture provider/model attribution, observed scalar worker and
  integration sources, and verified delivered/bounced events through a
  payload-free non-mutating report. It is local composition evidence only;
  external provider dispatch, durable provider callback/read-back, live worker
  execution, sandbox, and host-runtime evidence remain open.
- [ ] Run the new checker against a selected live representative model-backed
  worker and provider ingress, then read back a durable provider webhook and
  bounce observation so the remaining SLO targets have deployment evidence
  using [`scripts/check_mail_edge_observations.py`](../../../mas/scripts/check_mail_edge_observations.py);
  direct model/artifact/integration spans, the fail-closed source evaluator,
  delivery-attempt correlation, metadata-only mail-edge contract, and local
  durable worker evidence are durable/queryable, but live source coverage
  remains open.
- Run load, soak, chaos, backup, regional/provider outage, and disaster-recovery exercises.
- Keep LiteLLM/OmniRoute as the model/routing analytics surfaces and AIAT as the canonical operational evidence layer.

## Exit gate

- Storage migration, if selected, is checksum-complete, reversible, and restore-tested.
- Optional memory/workflow services have clear measurable benefit and clean disable/removal paths.
- Multi-host worker loss does not duplicate or lose canonical work.
- Firecracker high-risk execution is independently certified.
- One AIAT self-improvement completes issue-to-canary-to-promotion and a separate exercise proves exact rollback.
- SLO policy/report and capacity forecast contracts are implemented and
  deterministic; production-like/native evidence, load/soak/chaos, and
  disaster-recovery cadence remain release work.
