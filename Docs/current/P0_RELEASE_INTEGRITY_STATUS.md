# P0 Release Integrity Status

**Updated:** 2026-08-11  
**Status:** in progress — metadata-only policy, section ACL contract, immutable image contract, fail-closed local image identity probe, tool-service profile split, bounded project-state metrics, read-only persisted default-worker binding reconciliation, deterministic worker-run lifecycle fixture, exact-locked LangGraph/CrewAI adapter conformance, deterministic flow traversal semantics, explicit evidence-policy scope resolution, external-account action-policy and lifecycle fixtures, outbound-mail approval/idempotency/retry/outage fixture, built-in YouTrack/GitHub adapter declaration and mocked HTTP conformance fixtures, asynchronous governed flow-task binding, evidence-preserving flow retry, watchdog/recovery fixture, WSL/DrvFS-safe project Git initialization, local dashboard UI golden paths (including shell focus, identity stale-record/retry, PM integration conflict/stale retry, project-detail stale/retry, and system-visualization partial/offline retry states), and the secret-safe release-environment/provenance input group committed as `64771b5` implemented; the evidence-package core/resolver batch is committed as `a44a1aa` with package-level workflow exports isolated in `d0472af`, isolated API/snapshot/policy routes are committed in `cbf00d9`, and bounded dashboard evidence/proxy surfaces are committed in `82bbaeb`; native/live release exit gates, project-page composition, and live provider snapshot evidence remain open
**Plan:** [P0 Release Integrity Plan](plans/P0_RELEASE_INTEGRITY_PLAN.md)  
**Roadmap:** [ROADMAP.md](../../ROADMAP.md)
**Ledger:** [AIAT Current Release Ledger](../../mas/docs/AIAT_CURRENT_RELEASE_LEDGER.md)
**Live exit procedure:** [P0 Native-Linux Exit Runbook](../../mas/docs/P0_NATIVE_LINUX_EXIT_RUNBOOK.md)

This is the current implementation status for the first roadmap phase. It is
an evidence index, not a release approval. The working tree also contains
pre-existing PM/integration changes, so a final release ledger must identify a
single frozen commit before production claims are made.

## Completed in this phase

### Licence metadata boundary

- `ExternalWorkerSteward` keeps `license_id` and `redistribution_status` in
  immutable provenance, but certification no longer derives a licence check.
- The API certification route filters licence/redistribution attestations out
  of blocking checks.
- The evaluator still records detected, missing, unclassified, or restricted
  licence metadata as an operator notice. Its diagnostic key is always present,
  has zero technical score weight, and cannot create a blocker or rejection.
- `LICENSE_REVIEW` remains a compatibility state and metadata-capture
  checkpoint; the normal source-review path can skip it, and the steward
  cannot transition from that label directly to `BLOCKED`, so licence metadata
  cannot become a standalone gate or delay normal use.
- `scripts/check_provenance.py` validates the personal/internal metadata policy
  and source/version inventory without an allowlist or prohibited-component
  decision.
- Default worker manifests no longer carry licence-derived exclusions: the
  security evaluator advertises Semgrep, SkillSpector, and TruffleHog as normal
  bounded scanners, the
  planner exposes Plane/OpenProject provider adapters, and DevOps exposes
  Ansible through its normal CLI adapter. Small starting profiles remain
  technical packaging choices, not resource bans.
- The tool service now routes the `semgrep`, `skillspector`, and `trufflehog`
  compatibility aliases through `security.scan`; each executes only via the
  configured sandbox adapter with bounded output and the existing audit, grant,
  rate, and approval boundaries. SkillSpector may be supplied through
  `TOOL_SKILLSPECTOR_COMMAND`.

### Shared operational predicate

- `operational_promotion_checks()` is shared by the in-process steward, the
  orchestrator certification route, and rollout promotion for immutable
  provenance and security scan checks. Activation/status changes also require
  persisted external provenance with a passed security scan. Documentation and
  capability snapshots remain additional technical evidence at the API boundary.
- Missing or pending security scans continue to fail certification regardless of
  licence metadata.

### Worker manifest truth

- `coding_worker` and `tester` now declare `evaluation_status: pending` and
  `certification_status: pending` while their exact OpenCode source scan is
  recorded as `findings_review_required` (316 findings, 54 engine warnings).
  The scan summary is linked from both manifests; their OpenCode interface
  evidence is not treated as a passed security scan substitute.
- `scripts/check_worker_reconciliation.py` validates all 39 manifests against
  the shared runtime catalogue, transport/isolation contract, default company
  references, external source/version/provenance records, OpenCode Compose
  service/version, production image inventory, and the metadata-only notices
  policy. Its read-only `--live` mode now reconciles the checked-in defaults
  against persisted `/capabilities/workers` adapter, sandbox, model,
  source-pin, capability, and active immutable-record bindings. It reports
  pending security evidence without converting licence data into a gate; its
  package-availability field is advisory and it does not claim live runtime
  certification.
- `scripts/check_runtime_install_profile.py` reconciles the default
  LangGraph/CrewAI extra, `uv.lock` versions, runtime-catalogue imports, and
  production orchestrator Dockerfile install command. This is reproducible
  packaging evidence only; imports, sandbox, security, canary, and live-run
  evidence remain open.
- `scripts/check_operator_pins.py --json` reconciles the exact production
  `uv`, Docker CLI, MCP, Semgrep, Docling, Playwright, Mermaid, OpenTofu, and
  OpenCode declarations. Microsoft Agent Framework, gVisor, Firecracker,
  SkillSpector, ccpm, LiteLLM, and OmniRoute are represented as explicitly
  unavailable until their operator/package/host/image identities are supplied;
  no licence field is consulted by this technical check.
- `mas/docs/provenance/security_scan_evidence.yaml` records the exact
  OpenCode `v1.17.13` commit and Semgrep `1.168.0` result. The evidence is
  deliberately non-passing because it contains 19 `ERROR` findings and 54
  engine warnings; activation remains fail-closed until technical triage is
  complete.
- `scripts/check_worker_steward_contract.py` runs the actual steward domain for
  each externally sourced default worker through immutable candidate,
  compatibility-matrix, shadow/read-only-canary promotion, regression blocking,
  and pre-activation rollback transitions. The rollback fixture proves that a
  rejected replacement preserves the previously active immutable pointers. It
  is deterministic domain evidence only and does not turn synthetic security
  or canary observations into live certification; the retained report is
  [`worker_steward_contract.json`](../../mas/docs/provenance/worker_steward_contract.json).
- The certification route now writes the compatibility matrix through the
  canonical storage owner and links its ID into certification/candidate
  evidence. The same-process steward cache records the row immediately, and
  restart rehydration restores persisted matrix rows with
  profile/capability-shape normalization; production database reconciliation
  and live canary evidence are still required.
- API steward rehydration now restores durable active bundle/adapter pointers
  before another rollout and fails closed when a persisted pointer is unknown;
  this preserves restart-time rollback state without claiming live worker
  certification.

### Metrics

- AIAT Prometheus families no longer use raw `project_id` labels. Project-state
  uses the bounded `state` label, while review/infra metrics are aggregate and
  project drill-down remains in structured workflow/audit records. A
  2,000-series platform budget plus per-family budgets are exposed through
  `metric_series_budget_status()`/`metric_label_inventory()` and covered by the
  metrics test suite. `metric_label_policy_inventory()` now classifies every
  declared AIAT label by its bounded source (protocol enum, active registry,
  catalogue, or declared histogram buckets), and the checker rejects unknown
  or non-bounded labels; the live local scrape now folds Prometheus' synthetic
  histogram `_created` sample into its declared family instead of reporting a
  false undeclared-family failure. Project-state presence is maintained as an aggregate
  count across transitions and reconciled from persisted rows on restart; the
  synthetic 10,000-project bounded-label test passes, while the native
  many-project scrape remains open.

### Trace propagation

- The pure trace-context/native-span/trace-evidence core is reviewed and
  committed as `77d5494`; API/storage writer integration remains a separate
  review group. Request-level trace propagation is now verified for the orchestrator API,
  message router, and tool service. Bounded `X-AIAT-Trace-ID` and W3C
  `traceparent` values are accepted, invalid values are replaced with a fresh
  root trace, orchestrator/SDK callers forward the bound trace, responses
  return `X-AIAT-Trace-ID`, agent message dispatch binds envelope context, and
  async context is cleared after each request/handler. The operator-only
  `aiat.trace-evidence.v1` query now joins task logs, project-usage events,
  worker-run transitions, durable API request observations, direct
  trace-correlated model-usage/worker-artifact/integration-evidence metadata
  with legacy run fallback, and PM-inbound correlations with safe source
  coverage and company trace-retention metadata. The native span contract now
  persists payload-free transport/model/tool/audit/worker/integration spans;
  the identity service now persists safe outbound delivery-attempt trace/span
  metadata and the signed client projects matching mail spans without content
  or provider fields. The refreshed local orchestrator is at migration
  `0036_native_trace_spans`; a bounded live `/health` request and operator
  trace read observe one API-request row plus one native transport span,
  retained at [`mas/docs/provenance/trace_observability_live.json`](../../mas/docs/provenance/trace_observability_live.json)
  and reproducible through [`mas/scripts/check_live_trace_observability.py`](../../mas/scripts/check_live_trace_observability.py).
  The rebuilt tool-service usage writer also passes a bounded `time_now` probe:
  one project-usage row plus one `tool_service` native span are retained at
  [`mas/docs/provenance/tool_trace_live.json`](../../mas/docs/provenance/tool_trace_live.json)
  and reproducible through [`mas/scripts/check_live_tool_trace.py`](../../mas/scripts/check_live_tool_trace.py).
  The host-side checker now resolves the Compose-only `tool-service:8002`
  alias to the published loopback port only when the orchestrator is local;
  the aggregate live ledger therefore records both trace children as passing.
  Provider mail-edge, representative model-backed worker, audit/integration,
  and live retention evidence remain outside this bounded local slice.

### SLO and capacity read models

- Versioned `aiat.slo-policy.v1`, `aiat.slo-report.v1`, and
  `aiat.capacity-forecast.v1` contracts now cover descriptive API, queue,
  worker, tool, model-routing, PM/SCM, mail, and recovery targets. Durable
  usage aggregates provide bounded cost/token forecasts, confidence, and
  budget headroom; the payload-free API request ledger now supplies native
  platform request observations, and the signed identity-service delivery
  projection supplies bounded mail-attempt observations when configured.
  Missing native mail-edge/full-span sources remain explicit `no_data` or
  `insufficient_data` and never block execution.

### Metric-series evidence boundary

- `scripts/check_metric_series_budget.py --json` now exercises the bounded
  metric registry with 10,000 synthetic projects, enforces the 2,000-total and
  per-family ceilings, rejects any `project_id` label, and emits the complete
  label inventory plus bounded classification policy. Its `--live` mode parses
  only AIAT-owned `mas_*` families from the orchestrator scrape, folds the
  client's synthetic histogram timestamp sample into the declared histogram
  family, and passes the current local scrape at 31 series. It still returns
  `blocked` without a configured endpoint and never emits metric payloads,
  credentials, or unbounded label values; native many-project scrape evidence
  remains open.

### Machine-readable release ledger

- `scripts/check_release_ledger.py --json` now aggregates the checked-in
  verifier inventory into `aiat.release-ledger.v1`. The static profile currently
  reports all 48 configured fixture/contract/documentation/release-environment/
  operator-pin/governance checks passing, two worker security findings-review evidence items, and
  `NO-RELEASE` because the worktree is dirty and live evidence was not
  included.
- `scripts/check_release_environment.py --json` now emits the current source
  revision, branch/dirty state, hashes for thirteen release inputs, available tool
  identities, configured-input presence flags, and a deterministic
  `aiat.release-environment.v1` digest (`fc8bdc6423117a7db4a597abe10da3f797c3fbe2919764bc4ae90e2930cb53d5`) without printing values or credentials. The current WSL manifest passes its static identity check; `--require-clean` remains appropriately open until a frozen release worktree exists.
- `scripts/check_docs_index.py --json` passes the canonical target, ten current
  feature specifications, three ordered plans, maintained local links, roadmap
  references, and the personal/internal metadata-only policy markers.
- The live profile was exercised after the local Compose recreation. The
  2026-08-11 64-check profile records 55 passes, zero failures, and nine
  externally blocked probes with a locally configured orchestrator URL and a
  bounded 60-second child-check timeout; the authenticated worker
  reconciliation child matches 39/39 persisted defaults with zero missing rows
  or binding mismatches, while the standalone local network matrix is green and
  retained separately. The profile retains four pending evidence
  items and still yields `NO-RELEASE`; the summary is retained at
  [`provenance/release_ledger_live.json`](../../mas/docs/provenance/release_ledger_live.json).
  These are current evidence, not a release pass.

### CEO/service dashboard boundary

- Compose requires distinct `AIAT_CEO_API_KEY` and `AIAT_WORKER_API_KEY`
  principals; only the CEO runner receives the dedicated CEO key, while other
  runners receive the worker key. The team-runner constructor retains a
  fallback only for non-Compose unit fixtures.
- `system_config.dashboard.section_acl.v1` stores the validated section ACL.
  The human operator is always retained as the repair principal; automation
  principals receive only their bounded default sections.
- Dashboard API proxies send `X-AIAT-Dashboard-Section`; the API enforces the
  persisted ACL, and operator-only `PUT /dashboard/sections/{section}/acl`
  updates it. Positive and negative human/CEO/service/worker tests pass.

### Team-runner data-plane boundary

- Deployed team runners no longer receive `PGBOUNCER_DSN`, MinIO credentials,
  or the shared `MAS_API_KEY` in the Compose team environment. PgBouncer and
  MinIO are internal-only services, and the OpenCode runtime is no longer
  attached to the runner network; runners use the authenticated,
  operation-allowlisted `/internal/team-runners/{team_id}/storage` control
  plane for checkpoints, usage events, documents, and COO review durability.
- `ControlPlaneStorageClient` preserves the small `CheckpointStore` and
  `AgentStorage` method surfaces required by `AgentBase`/`ExecutiveAgent`, so
  resume and review persistence remain functional without direct SQL or S3
  access. Runners fail startup if the durable control-plane storage health
  check is unavailable. Static Compose and API boundary tests pass; native
  DNS/TCP/HTTP denial and positive-path evidence remains open.
- `scripts/check_network_boundary.py` now codifies the Compose credential,
  network, gateway, Docker-socket, and OpenCode isolation contract for CI and
  provides a non-secret native-Docker probe mode. The static report passes;
  live execution remains externally dependent on Docker Engine.

### Local object-store evidence

- The persisted MinIO `mas_agent` IAM secret was reconciled safely after local
  environment rotation without touching object data. The private-network
  conformance probe now passes all 8/8 scoped cases, and the same-provider
  backup/restore rehearsal passes two disposable objects with manifest parity
  and cleanup. Evidence is retained at
  [`object_store_live_conformance.json`](../../mas/docs/provenance/object_store_live_conformance.json)
  and
  [`object_store_backup_restore_live.json`](../../mas/docs/provenance/object_store_backup_restore_live.json).
  Provider-pair, encrypted, clean-environment, and disaster-recovery evidence
  remain open.

### Immutable release inputs and image profiles

- Production Compose no longer contains mutable application image defaults.
  Fixed infrastructure images and all Dockerfile bases carry OCI digests;
  application/gateway images require digest-bearing `*_IMAGE_REF` values.
- `scripts/check_image_provenance.py` passes the source-level production
  contract. Its `--live --json` mode compares deployment-supplied immutable
  refs with local Docker `RepoDigests`, returns exit 2 when Docker or refs are
  unavailable, and never emits image refs or credentials. The live scope is
  local identity only; it does not claim SBOM, scan, build, or clean-room
  evidence. `production-image-lock.example.env` documents the deployment
  inputs without inventing local OCI digests.
- `Dockerfile.tool-service` now builds a lightweight `core` profile. Browser,
  Docling, Semgrep, and Mermaid/Node payloads are installed only by the
  `extensions` profile; `infra/docker/image-budgets.yaml` defines the live
  ceilings and `scripts/check_image_budgets.py` validates the contract. The
  development-only `mas.sh` wrapper supplies local `:dev` image names when
  deployment refs are absent; direct production Compose still requires real
  immutable references.
- `document.ingest` remains usable in the lightweight profile: it invokes
  Docling when installed and returns source text with an explicit degraded
  `plain_text_fallback` backend when the optional binary is absent. This is
  local fallback evidence only; it does not certify the external Docling
  runtime.

### Contract export (preparatory P1 work)

- `schemas/http/orchestrator.openapi.json` is generated from the FastAPI
  application using canonical JSON ordering and recorded in
  `docs/provenance/api_contract.yaml` with its path count and SHA-256 hash.
- The same checker compares the checked-in `aiat.v1` protocol schema, generated
  dashboard TypeScript surface, and generated Python SDK surface with
  runtime/OpenAPI sources. CI fails on API, protocol, or client type drift
  unless artifacts and provenance are deliberately regenerated together.
- `scripts/generate_typescript_api.py` turns the 129 OpenAPI component schemas
  and 266 operations into a checked-in dashboard type surface; CI checks that
  generated output and `npm run typecheck` remain green.
- `scripts/generate_python_api.py` emits the matching 129 Python `TypedDict`
  models and 266-operation metadata surface under `packages/mas-api-sdk`, and
  `OrchestratorClient` exercises it without a handwritten endpoint fork.
- The company manifest now exposes typed optional timezone, retention (including
  trace days/sample rate), privacy, evidence, model, and deployment policy
  blocks; legacy manifests without those blocks still compile, while the
  default manifest records the current values.
- The manifest timezone is consumed by team-runner prompt headers, the
  `time_now` tool, orchestrator scheduler defaults, dashboard display helpers,
  and Compose/development defaults. Prompt headers and clock results are
  operator-facing; persistence remains UTC.
- This does not claim the P1 modular-control-plane exit; P1 remains gated on
  the still-open native P0 release evidence.

## Verified evidence

| Check | Result | Evidence |
|---|---|---|
| Worker/steward/evaluator regression suite | PASS | `uv run --isolated pytest packages/mas-core/tests/test_worker_governance.py packages/mas-core/tests/test_worker_steward_contract.py packages/mas-core/tests/test_compatibility_matrix_persistence.py packages/mas-core/tests/test_default_shipped_agents.py apps/orchestrator-api/tests/test_workers_test5_lifecycle.py apps/orchestrator-api/tests/test_steward_rehydration.py -q`; the steward fixture also passes `uv run --isolated python scripts/check_worker_steward_contract.py --json`, including regression blocking, pre-activation pointer preservation, compatibility-matrix shape normalization, and restart-safe rehydration coverage |
| Document ingest fallback contract | PASS | `uv run --isolated pytest apps/tool-service/tests/test_default_shipped_tool_catalog.py -q`; Docling execution and explicit degraded plain-text fallback are covered without claiming the optional binary is installed |
| Backend and team-runner regression suite | PASS | `uv run --isolated pytest packages/mas-core/tests apps/orchestrator-api/tests apps/tool-service/tests apps/team-runner/tests -q` |
| Broader worker/observability regression suite | PASS | `uv run --isolated pytest packages/mas-core/tests/test_worker_*.py packages/mas-core/tests/test_observability.py apps/orchestrator-api/tests/test_metrics.py -q` |
| Metrics API and label-policy suite | PASS (static + refreshed local live scrape) | `uv run --isolated pytest packages/mas-core/tests/test_metric_series_budget.py apps/orchestrator-api/tests/test_metrics.py -q`; the static report includes every AIAT label policy, the 10,000-project fixture, and the Prometheus histogram `_created` normalization regression test; the refreshed local live scrape is 31 bounded series with no `project_id` label, retained at [`mas/docs/provenance/metric_series_live.json`](../../mas/docs/provenance/metric_series_live.json), while native many-project evidence remains open |
| HTTP/message/trace-evidence suite | PASS (committed core; local transport + tool read-back; broader sources open) | Core commit `77d5494`; `uv run --isolated pytest packages/mas-core/tests/test_tracing.py packages/mas-core/tests/test_trace_evidence.py packages/mas-core/tests/test_native_trace_spans.py -q`; `uv run --isolated python scripts/check_trace_evidence.py --json` and `scripts/check_native_trace_spans.py --json` pass deterministic contracts. API/storage writer integration, local transport/tool read-back, model-backed worker, mail-edge, and retention probes remain separate evidence boundaries |
| API observation ledger | PASS (bounded static/unit/API) | `uv run --isolated pytest packages/mas-core/tests/test_api_observations.py apps/orchestrator-api/tests/test_trace_propagation.py -q`; `uv run --isolated python scripts/check_api_observability.py --json` passes the payload-free normalized-route fixture; migrations `0034_api_request_observations`, `0035_trace_correlation_evidence`, and `0036_native_trace_spans`, durable table/readers, and trace/SLO projections are implemented |
| SLO/capacity suite | PASS (bounded; native/live open) | `uv run --isolated pytest packages/mas-core/tests/test_slo.py apps/orchestrator-api/tests/test_slo_capacity.py -q`; `uv run --isolated python scripts/check_slo_capacity.py --json` passes the deterministic fixture and `--live --json` returns `blocked` without an API; missing service telemetry remains explicit |
| Provenance inventory | PASS | `uv run --isolated python scripts/check_provenance.py` — 21 components, including the metadata-only operator-supplied SkillSpector record |
| Python compilation | PASS | isolated `compileall` for changed runtime, API, policy, image-contract, and provenance paths |
| CEO/service/dashboard ACL API suite | PASS (unit + authenticated local API matrix; native UI matrix open) | `uv run --isolated pytest apps/orchestrator-api/tests/test_auth_boundary.py -q`; the refreshed local `/dashboard/access` and `/dashboard/sections/{section}` matrix passes for operator/CEO/service/worker identities, with evidence at [`mas/docs/provenance/dashboard_acl_live.json`](../../mas/docs/provenance/dashboard_acl_live.json) |
| Team-runner storage boundary | PASS (static/API) | `uv run --isolated pytest apps/orchestrator-api/tests/test_team_runner_storage_boundary.py apps/team-runner/tests/test_storage_client.py apps/orchestrator-api/tests/test_gamma_hardening.py -q`; runners have no Compose DB/object-storage credentials and the API exposes only allow-listed operations |
| Dashboard ACL policy unit suite | PASS | `uv run --isolated pytest packages/mas-core/tests/test_dashboard_access.py -q` |
| Dashboard operator proxy/type contract | PASS | `npm run typecheck` in `apps/mas-dashboard` |
| Dashboard local Compose E2E matrix | PASS WITH EXPLICIT SKIP (34/35) | `npm run test:e2e -- --workers=1` in `apps/mas-dashboard`; focused shell and identity regressions also pass (`e2e/dashboard-shell-accessibility.spec.ts` 2/2, `e2e/identity-states.spec.ts` 1/1), the targeted system/PM resilience filter passes 4/4, and the one-test flow-builder golden path passes after the project flow catalogue was made `cache: "no-store"`. Authenticated WSL2 Compose coverage includes operational UI, CEO chat/hiring, project workspace, schema-driven flow editing, all eight flow runtime scenarios, hiring board, identity stale-record/retry state, PM integration conflict/stale retry, project-detail stale/retry state, runtime status, shell skip-link/mobile focus recovery, and system-visualization partial/offline retry states. Secret-safe evidence is [`mas/docs/provenance/dashboard_e2e_live.json`](../../mas/docs/provenance/dashboard_e2e_live.json); the live DLQ replay case is skipped pending an operator-owned safe fixture, and native-Linux/mobile/WCAG evidence remains open |
| External-account action policy and mail correlation | PASS (static/API/unit, preparatory P1) | `PYTHONPATH=apps/identity-service uv run --isolated pytest apps/identity-service/tests/test_identity_service.py -q`; versioned action taxonomy, closure human-approval/session-revocation path, and safe delivery-attempt trace/span persistence pass; provider/live outage and mail-edge evidence remains open |
| Production image contract | PASS (static); BLOCKED (live) | `uv run --isolated python scripts/check_image_provenance.py --json`; `--live --json` exits 2 with `live.status=blocked` because Docker Engine/configuration is unavailable; live scope is only local `RepoDigests` identity |
| Operator runtime/CLI pin contract | PASS (static; host-only entries explicit unavailable) | `uv run --isolated python scripts/check_operator_pins.py --json` verifies exact production CLI/dependency declarations and records explicit reasons for unavailable host, optional, and deployment-supplied capabilities; no licence metadata is a gate |
| Worker manifest/runtime/provenance reconciliation | PASS (static + authenticated local live binding; technical findings remain open) | `uv run --isolated python scripts/check_worker_reconciliation.py --json` validates 39 manifests; the authenticated local `--live --json` run matches all 39 persisted defaults with zero missing rows or binding mismatches, retained at [`provenance/worker_reconciliation_live.json`](../../mas/docs/provenance/worker_reconciliation_live.json). Coding/tester rows still link to exact Semgrep evidence with 316 findings and remain `findings_review_required`; host package availability is advisory and Compose import readiness is recorded separately |
| Default worker implementation bindings | PASS (static); BLOCKED (live without operator environment) | `uv run --isolated pytest packages/mas-core/tests/test_default_worker_bindings.py -q`; `uv run --isolated python scripts/check_default_worker_bindings.py --json` reconciles all 15 documented default worker slots across department, runtime, transport, isolation, runtime-catalogue support, runtime/integration adapter entrypoints, capability, adapter configuration, and required tools. `--live --json` is fail-closed and does not mutate runtime state; licence metadata remains informational only |
| Worker-run lifecycle contract | PASS (deterministic fixture); BLOCKED (live without operator-selected run) | `uv run --isolated python scripts/check_worker_run_lifecycle.py --json` drives the real controller/native adapter through checkpoint persistence, pause/resume, cold cancellation, cold-crash failure normalization, lease-expiry requeue, and artifact/usage-before-terminal ordering. `--live --json` returns exit 2 without mutating a live run; database, sandbox, canary, and rollback certification remain open |
| Worker certification matrix | PASS (deterministic static) | `uv run --isolated python scripts/generate_worker_certification_matrix.py --check`; 39 rows record exact runtime imports, transports, adapter versions, and pending evidence without claiming live certification; the shared conformance suite also covers LangGraph/CrewAI bridge classes |
| Default runtime adapter conformance | PASS (adapter fixture + Compose package/lifecycle and exact lock-parity probe; worker certification open) | `uv run --isolated pytest packages/mas-core/tests/test_runtime_adapter_conformance.py -q`; `docker exec mas-orchestrator-api-1 python /app/scripts/check_runtime_adapter_conformance.py --live --json` passes actual LangGraph/CrewAI adapter classes with locked LangGraph `0.6.11` and CrewAI `1.6.1`, without model/tool/provider/project calls; evidence is [`mas/docs/provenance/runtime_adapter_conformance_live.json`](../../mas/docs/provenance/runtime_adapter_conformance_live.json). Sandbox, canary, live-run, and rollback remain open |
| Runtime benchmark readiness | PASS (authenticated bounded local live) | `uv run --isolated pytest packages/mas-core/tests/test_runtime_benchmarks.py -q`; authenticated `scripts/check_runtime_benchmarks.py --live --url http://localhost:8000 --json` passes deterministic LangGraph/CrewAI dependency dry-runs; evidence is [`mas/docs/provenance/runtime_benchmarks_live.json`](../../mas/docs/provenance/runtime_benchmarks_live.json). This remains package benchmark evidence only, not a worker canary, project run, sandbox proof, or rollback result |
| Network boundary contract | PASS (static) | `uv run --isolated python scripts/check_network_boundary.py --json`; all runners use only `workers`, protected data services and OpenCode are off that network, and identity/forbidden-env checks pass |
| Tool-service image budget contract | PASS (static + local Linux probe); native release open | `uv run --isolated python scripts/check_image_budgets.py --budget tool-service-core --image-ref mas/tool-service:dev` and the matching extensions command pass size checks; [`mas/docs/provenance/image_budgets_live.json`](../../mas/docs/provenance/image_budgets_live.json) records both local `/health` and memory probes. Compressed archive, clean native-Linux build/pull, SBOM, and vulnerability evidence remain open |
| API/protocol contract export | PASS (static/preparatory) | `uv run --isolated python scripts/check_api_contract.py --json` — 233 OpenAPI paths and checked-in `aiat.v1` protocol schema match runtime hashes, including native trace-evidence source, SLO/capacity, evidence-package, self-improvement outcome-action, artifact-bundle, and read-back-action fields |
| Python SDK contract generation | PASS (static/unit/preparatory) | `uv run --isolated python scripts/generate_python_api.py --check`; `pytest packages/mas-api-sdk/tests`; 129 models and 266 operations match OpenAPI |
| Company timezone propagation | PASS (static/unit/typecheck) | Runner prompt and invalid-zone fallback tests, `time_now` timezone tests, and dashboard `npm run typecheck`; Compose and `.env.example` defaults use `AIAT_COMPANY_TIMEZONE` |
| Prompt/tool and review contract reconciliation | PASS (static/unit) | `uv run --isolated python scripts/check_prompt_tool_reconciliation.py --json`; 11 shipped prompts resolve to 114 concrete manifest tools; review adapters publish `REVIEW_RESPONSE` and the CEO privileged-action tool targets the audited gate |
| Flow node-schema contract | PASS (static/unit, preparatory P1) | `uv run --isolated python scripts/generate_flow_node_schemas.py --check`; 9 node types at v1.0 match backend validation, JSON artifact, `/flows/node-schemas`, and generated dashboard metadata |
| Dashboard node-schema editor | PASS (static/typecheck, preparatory P1) | `npm run typecheck` in `apps/mas-dashboard`; both flow editors render the generated contract and editable typed form, including governed worker/profile selectors; deprecated `team_id`/`action` fields are primary-form hidden and retained in collapsed compatibility controls, while API dry-run and immutable saved-definition migration report deterministic alias findings and explicit worker mappings |
| Evidence-policy contract | PASS (static/unit, preparatory P1) | Evidence tests cover required artifact kinds; `/evidence-policies` publishes built-ins and policy dry-run validation includes worker-run/repository resources |
| Evidence-policy selection | PASS (static/API/unit, preparatory P1) | Company defaults persist through the active manifest; `PUT /projects/{project_id}/evidence-policy` persists project defaults and milestone overrides; `PUT /companies/{company_id}/evidence-policy` updates the company default; `resolve_evidence_policy_selection` and `check_evidence_policy_resolution.py --json` cover project-milestone → project → flow → company-milestone → company → manual precedence without using licence metadata as a gate; live transition/recovery proof remains open |
| Project evidence package | PASS (committed core/API/dashboard surfaces; project-page/live preparatory) | Commits `a44a1aa`, `d0472af`, `cbf00d9`, and `82bbaeb`; core and clean-checkout API tests pass, including `uv run --isolated pytest packages/mas-core/tests/test_evidence_package_runner.py packages/mas-core/tests/test_evidence_policy_resolution.py packages/mas-core/tests/test_workflow_scaffold.py apps/orchestrator-api/tests/test_project_evidence_routes.py apps/orchestrator-api/tests/test_projects.py -q`; clean-checkout `npm run typecheck` passes for the project evidence page, deep-link record page, and API proxies; `uv run --isolated python scripts/check_project_evidence_package.py --json` and `scripts/check_evidence_policy_resolution.py --json` pass with metadata-only notices and fail-closed live modes. Project-page composition, live durable snapshot/provider/worker generation, and native recovery remain open |
| Reusable flow templates | PASS (static/unit/API, preparatory P1) | Six canonical templates validate through `validate_flow`; `/flow-templates` and `/flows/from-template` are covered by `test_flow_templates.py` and `test_flows.py` |
| Dashboard canonical template consumption | PASS (static/typecheck, preparatory P1) | New-flow starter cards fetch `/api/flow-templates`, preserve canonical configs/evidence metadata, remap branch references, and retain a blank fallback; `npm run typecheck` passes |
| Flow definition lifecycle | PASS (static/API, preparatory P1) | Flow export/hash, deterministic diff, import, publish, deprecate, compatible migration, and explicitly mapped active-node graph-rewrite endpoints are covered by `apps/orchestrator-api/tests/test_flows.py`; live recovery remains open |
| Flow execution semantics contract | PASS (deterministic traversal fixture, preparatory P1) | `uv run --isolated pytest packages/mas-core/tests/test_flow_execution_semantics.py -q`; `uv run --isolated python scripts/check_flow_execution_semantics.py --json` drives real fan-out/join/switch traversal, prevents duplicate/completed join scheduling, and blocks unknown switch cases without worker or storage mutation; live execution/recovery remains open |
| Governed asynchronous flow-task binding | PASS (deterministic contract fixture, preparatory P1) | `uv run --isolated pytest packages/mas-core/tests/test_flow_worker_binding.py packages/mas-core/tests/test_flow_retry_persistence.py apps/orchestrator-api/tests/test_flows.py -q`; `uv run --isolated python scripts/check_flow_worker_binding.py --json` proves queued/claimed/running Worker Runs keep their task active, terminal states settle, parallel bindings remain copy-on-write, safe retry re-enters governed dispatch, and unknown states fail closed; the API and no-safe-node storage fallback preserve prior executions as `SUPERSEDED`; live canary/recovery remains open |
| Watchdog and safe-retry recovery semantics | PASS (deterministic controller fixture, preparatory P1) | `uv run --isolated pytest packages/mas-core/tests/test_workflow_watchdog_recovery.py -q`; `uv run --isolated python scripts/check_workflow_watchdog_recovery.py --json` proves boot grace, downtime-aware timeout, watchdog failure transition, recorded-safe-state retry, and terminal-state exclusion without storage/worker mutation; native watchdog/cold-recovery remains open |
| Docker/Compose live certification | PASS (refreshed local WSL2 runner matrix) / BLOCKED (native release host and broader service evidence) | The current checked-in development Compose stack was recreated without removing volumes. `check_network_boundary.py --live --json` now passes all 11 runners: named gateways are reachable, Redis/Postgres/PgBouncer/MinIO/OpenCode/unapproved egress are denied, control-plane storage health is true, no forbidden runner environment names or Docker sockets are present, and the result is retained at [`mas/docs/provenance/network_boundary_live.json`](../../mas/docs/provenance/network_boundary_live.json). The refreshed local orchestrator image returns health 200 and the trace-evidence route is live at migration head 0036; native-Linux identity/network/dashboard, provider-pair, backup/restore, and broader live probes remain open. |
| Evidence-policy scope resolution fixture | PASS (static/unit, preparatory P1) | `uv run --isolated pytest packages/mas-core/tests/test_evidence_policy_resolution.py -q`; `uv run --isolated python scripts/check_evidence_policy_resolution.py --json` passes seven precedence/fallback cases and explicitly reports `licence_metadata_is_gate: false`; `--live` remains blocked without an authenticated API scenario |
| External-account action policy fixture | PASS (static/unit, preparatory P1) | `uv run --isolated pytest packages/mas-core/tests/test_external_account_action_policy.py -q`; `uv run --isolated python scripts/check_external_account_action_policy.py --json` reconciles all five action rules, four category dispositions, and fail-closed unknown action/category behavior without mutating identity/provider state; `--live` remains blocked until a provider-specific sandbox/outage scenario exists |
| Built-in PM/SCM adapter declarations | PASS (static/unit/fixture; provider live open) | `uv run --isolated pytest packages/mas-core/tests/test_provider_adapter_declarations.py -q`; `uv run --isolated python scripts/check_provider_adapter_declarations.py --json` reconciles the real YouTrack adapter plus GitHub `pm`/`delivery`/`checks` profiles and bounded path guards with zero provider HTTP calls or mutations; provider-specific mock/live/outage/restore evidence remains open |
| Built-in PM/SCM mocked HTTP conformance | PASS (static/unit/fixture; provider live open) | `uv run --isolated pytest packages/mas-core/tests/test_provider_adapter_http_conformance.py -q`; `uv run --isolated python scripts/check_provider_adapter_http_conformance.py --json` drives eight real-adapter YouTrack/GitHub cases for health/configuration, projection/read-back, cursors, deactivation, comments/links, GitHub source-control paths, webhook handling, and retryable/permanent failures using local responses only |
| External-account lifecycle fixture | PASS (static/unit/fixture; provider live open) | `uv run --isolated pytest packages/mas-core/tests/test_external_account_lifecycle.py -q`; `uv run --isolated python scripts/check_external_account_lifecycle.py --json` drives the actual `IdentityService` through eight in-memory cases for category approval/idempotency, one-use browser leases, credential rotation/session revocation, closure approval, immediate suspension, fail-closed unknown categories, and secret-safe output without external account/provider calls |
| Outbound-mail lifecycle fixture | PASS (static/unit/fixture; relay live open) | `uv run --isolated pytest packages/mas-core/tests/test_outbound_mail_lifecycle.py -q`; `uv run --isolated python scripts/check_outbound_mail_lifecycle.py --json` drives the actual `IdentityService` through approval pause, request/submission idempotency, definitive provider-failure retry, ambiguous-outage reconciliation hold, and secret-safe output without external relay calls |
| Self-improvement candidate detection | PASS (static/unit/fixture; live signal sources open) | `uv run --isolated pytest packages/mas-core/tests/test_improvement_candidates.py -q`; `uv run --isolated python scripts/check_self_improvement_candidates.py --json` reconciles defect, metric, upstream-update, cost, and operator-goal signals with deterministic deduplication/risk/budget mapping, conflicting-ID rejection, secret-safe metadata, and zero project/budget/credential/deployment side effects |
| Machine-readable release ledger | PASS (static aggregation); BLOCKED (live profile with local URL) | `uv run --isolated python scripts/check_release_ledger.py --json` reports all 48 static checks passing with pending evidence and `NO-RELEASE`; the current configured `uv run --isolated python scripts/check_release_ledger.py --live --json` profile records 55 pass/9 blocked across 64 checks and never exposes credentials. The authenticated worker-reconciliation child passes 39/39 persisted defaults with zero missing rows or binding mismatches; trace/tool endpoint configuration, default-worker certification, image identity, gVisor, outbound relay, and self-improvement source remain blocked. Each child checker is bounded by the ledger timeout; the object-store child invokes `--compose-local` to execute the checked-in 8/8 MinIO probe inside the private network. The separate local `check_network_boundary.py --live --json` matrix passes all 11 runners; the metric-series scrape still passes when a live endpoint is configured after Prometheus histogram `_created` normalization. |
| Release environment manifest | PASS (secret-safe static identity; committed `64771b5`) | `uv run --isolated python scripts/check_release_environment.py --json` emits `aiat.release-environment.v1` with thirteen input hashes, tool identities, environment-presence flags, and a deterministic per-revision manifest digest without printing values or credentials. The report records the current branch, revision, changed-path count, and dirty state; its digest must be captured again for the eventual frozen release commit. |

## Still open before P0 exit

1. Repeat the network denial/allow matrix on a clean native-Linux release host
   and close the historical `DEF-2026-07-14-036` record with that evidence
   using the `check_network_boundary.py --live --json` harness. The refreshed
   local WSL2 recreation and 11-runner matrix pass are retained, but they do
   not substitute for the native release-host result.
2. Run the native-Linux network and dashboard ACL matrix, including the actual
   provisioned CEO/worker keys.
3. Resolve application `*_IMAGE_REF` values to recorded OCI digests and
   generate the image/source/SBOM ledger from a clean pull/build; run the live
   image identity helper first and retain its blocked/pass/fail result.
4. Run the native many-project aggregate-state scrape and repeat both
   tool-service profile measurements on the clean native-Linux release host;
   the local 31-series scrape and both local image-budget probes already pass
   and are retained as descriptive evidence.
5. Certify each default runtime/adapter against the installed lock and live
   worker-run lifecycle. The bounded local LangGraph/CrewAI dependency
   benchmark now passes and is retained at
   [`mas/docs/provenance/runtime_benchmarks_live.json`](../../mas/docs/provenance/runtime_benchmarks_live.json),
   but it is not sandbox, canary, worker-run, or rollback evidence.
6. Freeze a clean release commit/environment manifest and publish the current
   release ledger with static, contract, integration, live, recovery, and
   externally blocked evidence labels; use `check_release_ledger.py` as the
   machine-readable aggregation boundary.

Until those items are evidenced, the programme remains **not release-certified**
even though the licence policy implementation is complete for personal,
internal use.
