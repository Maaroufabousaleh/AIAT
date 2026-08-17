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

- Separate control/tool/data hosts from worker pools.
- Add authenticated worker registration, leases, placement constraints, capacity, and health.
- Certify gVisor across supported hosts.
- Add Firecracker worker pools for high-risk tasks with image/rootfs, network, secrets, artifact, and cleanup controls.
- Prove host loss, split-brain avoidance, queue recovery, and exact run-version pinning.

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
  `test_self_improvement_api.py`, and storage lifecycle tests). Live
  issue/worker, provider, and database execution remains work.
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
  views, and retention enforcement remain separate review/live gates.
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
  atomic adapter call and bounded audit record. The
  `check_trace_retention_execution.py --live` check remains blocked until a
  reviewed registry/storage recovery adapter is configured; `5d71309` validates
  the bounded audit envelope used by the fixture, while live erasure, durable
  audit, and restore rollback remain open.
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
- [ ] Run the new checker against a selected live representative model-backed
  worker and provider ingress, then read back a durable provider webhook and
  bounce observation so the remaining SLO targets have deployment evidence
  using [`scripts/check_mail_edge_observations.py`](../../../mas/scripts/check_mail_edge_observations.py);
  direct model/artifact/integration spans, the fail-closed source evaluator,
  delivery-attempt correlation, and the metadata-only mail-edge contract are
  durable/queryable, but live source coverage remains open.
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
