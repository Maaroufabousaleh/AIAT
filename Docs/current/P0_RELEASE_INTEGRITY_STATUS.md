# P0 Release Integrity Status

**Updated:** 2026-08-11  
**Status:** in progress — metadata-only policy and worker evaluator/manifest group (`cbdcfa6`), governed model-profile/cooldown/catalogue/bootstrap group (`288996e`), section ACL contract, immutable image contract (`7d69fbd`), development image-wrapper defaults (`b9a77e9`), CycloneDX SBOM artifact validation (`42b03a3`), tool-service profile/budget contract (`b24ca0c`), runner control-plane storage/network boundary (`43bee16`), fail-closed sandbox runtime readiness contract (`a24c554`), reproducible default-runtime install and adapter-conformance contract (`9a10a4b`), worker readiness/default-binding/matrix contract (`4c5fd68`), deterministic worker-run lifecycle fixture (`fe6fb8d`), bounded runtime/adapter policy checks (`fc528a8`), prompt/tool/review contract (`20f0499`), bounded review/scanner/Git workspace implementation (`5b830e9`), provider conformance contracts (`7f6bfc5`), governed identity/mail lifecycle and trace projection (`f577675`), worker-registry grant/update-policy hardening (`d8cafbb`), fail-closed local image identity probe, bounded project-state metrics (contract `90a7d82`, runtime wiring `cbeb9db`, compatibility `541d6e0`), read-only persisted default-worker binding reconciliation, exact-locked LangGraph/CrewAI adapter conformance, deterministic flow traversal semantics, explicit evidence-policy scope resolution, external-account action-policy and lifecycle fixtures, outbound-mail approval/idempotency/retry/outage fixture, built-in YouTrack/GitHub adapter declaration and mocked HTTP conformance fixtures, asynchronous governed flow-task binding, evidence-preserving flow retry, watchdog/recovery fixture, WSL/DrvFS-safe project Git initialization, local dashboard UI golden paths (including shell focus, identity stale-record/retry, PM integration conflict/stale retry, project-detail stale/retry, system-visualization partial/offline retry, governance read-surface stale/retry recovery `52de581`, System Control stale/retry recovery `f445c17`, Projects list stale/retry recovery `d3482ab`, project evidence package stale/retry recovery `bc80ad5`, Tools catalogue stale/retry recovery `5f4b0eb`, dead-letter queue stale/retry recovery `823fa6d`, credentials metadata stale/retry recovery `970f09c`, identity-resource stale/retry recovery `46eccee`, identity table accessibility `651ad11`, Metrics partial/stale/retry recovery `85596b0`), the CEO Command Center chat recovery group `beabb95`, the secret-safe release-environment/provenance input group committed as `64771b5`, the bounded release-ledger aggregator committed as `eff4eef`, and the native live-ledger gate committed as `4d7a495` implemented; model-profile bootstrap (`09bdd19`), flow schema/retry hardening (`234adfb`), team-runner boundary hardening (`22fc21a`), dashboard operation-selector hardening (`e378f40`), project-evidence typecheck/router fixes (`fc4f0fa`, `33e0384`), company-timezone propagation (`ee1361f`), and deterministic worker certification-matrix regression coverage (`a62ddb7`) are reflected in the maintained rows; native/live release exit gates, project-page composition, and live provider snapshot evidence remain open
- The maintained dashboard evidence now also includes Flows list stale/retry recovery (`a0faf5b`, source-built `flows-states.spec.ts` 1/1), Project evidence package stale/retry recovery (`bc80ad5`, source-built `project-evidence-states.spec.ts` 1/1), Container Logs stale/retry recovery (`280d363`, source-built `logs-states.spec.ts` 1/1), Agent Streams reconnect/history recovery (`3e8a0ea`, source-built `streams-states.spec.ts` 1/1), Hiring Board stale/retry recovery (`7541b84`, source-built `workers-states.spec.ts` 1/1), identity-resource stale-to-recovered retry with obsolete-request cancellation (`46eccee`) and semantic table/action controls (`651ad11`, source-built `identity-states.spec.ts` 1/1), CEO Live Feed reconnect/history recovery (`1761429`, source-built `ceo-states.spec.ts` 1/1), and CEO Command Center chat stream/history recovery (`beabb95`, source-built `ceo-chat-states.spec.ts` 1/1); native/live flow/project-evidence/log/stream/worker/CEO evidence remains separate from these preparatory P1 results.

The secret-safe operational diagnostics group (`2860838`) and API-facing
operator wrapper (`380daf5`) are implemented and covered by focused
API/contract tests; both are bounded operational surfaces and do not turn
dependency or licence metadata into a release gate.
The communication-policy hardening group (`fb39128`) now validates declared
sender role/team coherence before any router enqueue, closing a worker-to-CEO
team-spoof path with static and mocked-router evidence.
The hierarchy visualization group (`8b7d9f1`) adds the corresponding dashboard
overlay for allowed/denied communication paths. A current `mas/dashboard:overlay`
image rebuilt from a clean explicit context passes the focused authenticated
Playwright flow 1/1 (`d5f596e`). The `mas.sh` wrapper now excludes all
disposable `.tmp*` paths and fails closed on incomplete staging (`45ee42c`);
direct unwrapped WSL Docker-context and release-image evidence remain open.

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
  decision (`cbdcfa6`). The evaluator's diagnostic licence result is retained
  for operator visibility, but has zero score weight and cannot create a
  blocker or rejection.
- Default worker manifests no longer carry licence-derived exclusions: the
  security evaluator advertises Semgrep, SkillSpector, and TruffleHog as normal
  bounded scanners, the
  planner exposes Plane/OpenProject provider adapters, and DevOps exposes
  Ansible through its normal CLI adapter. Small starting profiles remain
  technical packaging choices, not resource bans.
- Worker registry authority checks now constrain update-policy values and
  revalidate persisted capability grants on capability/team changes before
  mutation (`d8cafbb`); invalid policy values and forbidden grants fail closed,
  while licence/restriction metadata remains informational only.
- The tools SDK/manifest group `965ba38` now routes the `semgrep`,
  `skillspector`, and `trufflehog`
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
- `scripts/check_worker_reconciliation.py` (runtime catalogue/checker group
  `80e0ca3`) validates all 39 manifests against
  the shared runtime catalogue, transport/isolation contract, default company
  references, external source/version/provenance records, OpenCode Compose
  service/version, production image inventory, and the metadata-only notices
  policy. Its read-only `--live` mode now reconciles the checked-in defaults
  against persisted `/capabilities/workers` adapter, sandbox, model,
  source-pin, capability, and active immutable-record bindings. It reports
  pending security evidence without converting licence data into a gate; its
  package-availability field is advisory and it does not claim live runtime
  certification.
- All 39 team-runner agent declarations now carry exact `worker_manifest_ref`
  values; `scripts/check_team_worker_manifest_refs.py` passes 11 teams/39
  agents without registration or activation side effects. Runtime registration
  remains a separate gate.
- Production team-runner startup repeats that read-only reconciliation against
  the mounted worker directory and carries each reference into `AgentConfig` and
  health metadata (`569231f`). Missing or mismatched references fail closed;
  startup does not register or activate workers.
- `scripts/check_runtime_install_profile.py` reconciles the default
  LangGraph/CrewAI extra, `uv.lock` versions, runtime-catalogue imports, and
  production orchestrator Dockerfile install command. This is reproducible
  packaging evidence only; imports, sandbox, security, canary, and live-run
  evidence remain open.
- `scripts/check_operator_pins.py --json` (checker/test group `dd857ae`)
  reconciles the exact production
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
- `scripts/check_worker_steward_readiness.py` and its
  `aiat.worker-steward-readiness.v1` evaluator provide a read-only,
  explicitly selected worker/candidate preflight. Fixture mode passes; the
  authenticated local coding-worker probe is blocked by `PROVISIONING` steward
  state, a pending technical scan, and no candidate. It never generates,
  certifies, approves, activates, rolls out, or dispatches, and licence
  metadata remains informational only.

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
  false undeclared-family failure. The runtime records bounded aggregate
  transitions for project creation, workflow transitions, decisions, retries,
  watchdog recovery, and archive operations, then reconciles the aggregate from
  persisted rows during resume/startup; the synthetic 10,000-project
  bounded-label test passes, while the native many-project scrape remains open.

### Secret-safe operational diagnostics

- `GET /system/diagnostics` provides a read-only control-plane health summary
  across the database, message router, tool service, and optional object store
  (`2860838`). The database probe executes only `SELECT 1`; HTTP probes consume
  `/health`; the object-store probe uses only `head_bucket` and closes its
  client. The response contains bounded status/latency/connection facts and
  exception type, never credentials, URLs, raw dependency payloads, or error
  text.
- Healthy, degraded, object-store-unconfigured, no-storage, and dependency
  payload-redaction behavior is covered by `test_test10_ops_scripts.py`. A
  dependency failure is returned as an explicit aggregate `degraded` report;
  missing storage remains a 503 boundary. This route is diagnostic only and
  does not activate workers, mutate state, or consult licence/restriction
  metadata as a gate.

### Operator control CLI

- `scripts/mas-ctl` wraps the authenticated control-plane API with
  `status`, `diagnostics`, and fail-closed `bootstrap` commands, plus explicit
  `resume` and `shutdown` POST commands (`380daf5`; executable mode `f8df50e`). It does not invoke Docker
  or Compose lifecycle operations, accepts the operator key only from an
  argument/environment, and suppresses upstream error bodies.
- Six deterministic transport cases cover API-key forwarding, base-URL
  normalization, ready/degraded bootstrap, HTTP-error redaction, and explicit
  POST methods. The operational API suite now verifies that the executable
  wrapper is present; only per-service restart remains a documented TODO.

### Communication-policy boundary

- The message router now rejects non-CEO envelopes whose declared sender team
  is not owned by the declared trust tier (`fb39128`). Workers can run under
  department/C-suite parent teams and sub-agents under any known parent team,
  but a worker cannot claim `exec_ceo` to make a direct or intra-team message
  appear authorised. Rejection occurs before Redis dedupe/enqueue.
- `test_policy.py`, `test_phase3.py`, and `test_test12_comms_policy.py` cover
  valid worker/admin/sub-agent paths, spoofed worker/admin teams, role-specific
  message types, and mocked-router HTTP 403 behavior. The remaining hierarchy
  graph item is now implemented by the `HierarchyViz` sender-role overlay and
  a checked-in source-built E2E spec (`8b7d9f1`); its execution and live Compose image evidence remain
  separate because the current image predates the change.

### Runner network and control-plane storage boundary

- `43bee16` routes deployed runner checkpoints, usage events, documents, and
  COO review records through the authenticated orchestrator storage endpoint.
  The operation set is allow-listed, UUID/datetime payloads are normalized at
  the API boundary, and checkpoint reads/deletes are team-scoped.
- Compose runners receive only their worker or CEO identity key and gateway
  configuration; PgBouncer, Postgres, MinIO, and OpenCode remain off the
  `workers` network. Runner startup performs a storage-health request and
  fails closed when the control-plane path is unavailable.
- The static/live boundary checker records no runner data-plane credentials,
  Docker sockets, or unapproved egress. The refreshed WSL2 matrix passes all
  11 runners; native-Linux release-host denial/allow evidence remains open.

### Trace propagation

- The pure trace-context/native-span/trace-evidence core is reviewed and
  committed as `77d5494`; the bounded API-observation schema/migrations are
  committed as `9c39919`; tool-service HTTP and usage-writer integration is
  committed as `53d38fc`, while broader API/storage writer integration remains a separate
  review group. Request-level trace propagation is now verified for the orchestrator API,
  message router, and tool service. Bounded `X-AIAT-Trace-ID` and W3C
  `traceparent` values are accepted, invalid values are replaced with a fresh
  root trace, orchestrator/SDK callers forward the bound trace, responses
  return `X-AIAT-Trace-ID`, agent message dispatch binds envelope context, and
  async context is cleared after each request/handler. Router/agent forwarding
  and envelope cleanup are committed as `5bc0aae`. The operator-only
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
  trace read observe one API-request row plus one native transport span in the
  fresh 2026-08-11 local run,
  retained at [`mas/docs/provenance/trace_observability_live.json`](../../mas/docs/provenance/trace_observability_live.json)
  and reproducible through [`mas/scripts/check_live_trace_observability.py`](../../mas/scripts/check_live_trace_observability.py) (`eac83ae`).
  The rebuilt tool-service usage writer also passes a bounded `time_now` probe:
  one project-usage row plus one `tool_service` native span are retained at
  [`mas/docs/provenance/tool_trace_live.json`](../../mas/docs/provenance/tool_trace_live.json)
  and reproducible through [`mas/scripts/check_live_tool_trace.py`](../../mas/scripts/check_live_tool_trace.py) (`eac83ae`, refreshed 2026-08-11).
  The host-side checker now resolves the Compose-only `tool-service:8002`
  alias to the published loopback port only when the orchestrator is local;
  the aggregate live ledger therefore records both trace children as passing.
  Commit `24c2e35` adds explicit native model/worker source-category coverage
  and a fail-closed selected-worker checker; its fixture passes, but no live
  worker dispatch is claimed. Provider mail-edge, representative live worker,
  audit/integration, and live retention evidence remain outside this bounded
  local slice.

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

### Model routing and profile evidence

- Commit `288996e` adds one explicit transient-status vocabulary across normal,
  streaming, and fallback gateway paths; permanent client/credential errors are
  not blindly retried. Model and provider cooldown state is bounded and
  persisted, SmartRouter/fallback selection excludes active cooldowns, and a
  successful request clears the affected scope.
- The same commit adds deterministic `aiat.model-profile-catalogue.v1`
  reconciliation, a fail-closed `--live` verifier, an idempotent conflict-
  preserving profile bootstrap, and the internal `omniroute-coding` LiteLLM
  alias. Current local evidence retains 93 registered models, 94 persisted
  versions, 92 approved covered entries, one pending model, and two stale
  profile findings (fresh read-only evidence refreshed 2026-08-11 in
  `3e111ac`). These are operator-visible reconciliation findings, not
  licence/resource restrictions; provider outage/recovery evidence remains open.

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

- `scripts/check_release_ledger.py --json` (base aggregator `eff4eef`, native live-ledger gate `4d7a495`) now
  aggregates the checked-in verifier inventory into `aiat.release-ledger.v1`.
  The latest static run reports 48/48 configured
  fixture/contract/documentation/release-environment/operator-pin/governance
  checks passing, two worker security findings-review evidence items, and
  `NO-RELEASE` because the worktree is dirty and live evidence was not
  included. Child-check timeouts and live unavailability remain explicit
  blocked evidence rather than passes.
- `scripts/check_release_environment.py --json` (inputs committed as
  `64771b5`) emits the current source revision, branch/dirty state, hashes for
  thirteen release inputs, available tool identities, configured-input
  presence flags, and a deterministic per-revision
  `aiat.release-environment.v1` digest without printing values or credentials.
  The current WSL manifest passes its static identity check;
  `--require-clean` remains appropriately open until a frozen release worktree
  exists.
- `scripts/check_docs_index.py --json` passes the canonical target, eleven current
  feature specifications, three ordered plans, maintained local links, roadmap
  references, and the personal/internal metadata-only policy markers.
- The current unconfigured local 2026-08-11 65-check profile records 51
  passes, zero failures, 14 externally blocked probes, and four pending
  evidence items with a bounded 60-second child-check timeout. The native
  release-host preflight is now the `release_environment:live` child and
  reports WSL2, missing `runsc`, dirty worktree, and absent immutable image
  refs as safe blockers. The current summary is retained at
  [`provenance/release_ledger_live_current.json`](../../mas/docs/provenance/release_ledger_live_current.json).
  The configured loopback 64-check profile remains retained at
  [`provenance/release_ledger_live.json`](../../mas/docs/provenance/release_ledger_live.json)
  with 59 passes and five blocked probes; both profiles yield
  `NO-RELEASE`. These are evidence records, not a release pass.

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
  and cleanup. Evidence was refreshed 2026-08-11 in `22c736d` and is retained at
  [`object_store_live_conformance.json`](../../mas/docs/provenance/object_store_live_conformance.json)
  and
  [`object_store_backup_restore_live.json`](../../mas/docs/provenance/object_store_backup_restore_live.json).
  The pinned, credential-safe reconciliation helper is committed as `5558f3c`.
  Restore-copy safety hardening `93bf755` now rejects a non-empty target prefix
  before mutation and records `clean_target_verified` in restore evidence.
  Provider-pair, encrypted, clean-host, and disaster-recovery evidence remain
  open.

### Immutable release inputs and image profiles

- Production Compose no longer contains mutable application image defaults.
  Fixed infrastructure images and all Dockerfile bases carry OCI digests;
  application/gateway images require digest-bearing `*_IMAGE_REF` values.
- `scripts/check_image_provenance.py` (committed as `7d69fbd`) passes the source-level production
  contract. Its `--live --json` mode compares deployment-supplied immutable
  refs with local Docker `RepoDigests`, returns exit 2 when Docker or refs are
  unavailable, and never emits image refs or credentials. The live scope is
  local identity only; it does not claim SBOM, scan, build, or clean-room
  evidence. `production-image-lock.example.env` documents the deployment
  inputs without inventing local OCI digests; its complete Compose-variable
  coverage regression is committed as `1d373ee`; `b9a77e9` keeps development-only
  `:dev` defaults in the wrapper instead of weakening direct production
  Compose. Runtime-wrapper hardening is committed as `fd41874`: local
  validation requires distinct CEO/worker principals, propagates the company
  timezone to runner/tool/dashboard environments, pins wrapper `uv` bootstrap
  versions, and documents the identity migration head without supplying
  production image identities or release credentials.
- `Dockerfile.tool-service` now builds a lightweight `core` profile. Browser,
  Docling, Semgrep, and Mermaid/Node payloads are installed only by the
  `extensions` profile; `infra/docker/image-budgets.yaml` defines the live
  ceilings and `scripts/check_image_budgets.py --json` validates supplied size,
  startup, and memory measurements (contract committed as `b24ca0c`). The
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
- `scripts/generate_typescript_api.py` turns the 130 OpenAPI component schemas
  and 269 operations into a checked-in dashboard type surface; CI checks that
  generated output and `npm run typecheck` remain green.
- `scripts/generate_python_api.py` emits the matching 130 Python `TypedDict`
  models and 269-operation metadata surface under `packages/mas-api-sdk`, and
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
| Worker registry grant/update policy | PASS (static/API fixture) | Commit `d8cafbb`; `uv run --isolated pytest apps/orchestrator-api/tests/test_workers_test4_config.py -q` passes 66 focused cases, while adjacent capability, lifecycle, and policy suites pass. Registration and partial updates constrain `manual`/`auto-patch`/`auto-minor`/`auto-all`; persisted capability `required_tools` are rechecked on capability/team changes and forbidden grants fail before storage mutation. Licence metadata is not a gate |
| Document ingest fallback contract | PASS | `uv run --isolated pytest apps/tool-service/tests/test_default_shipped_tool_catalog.py -q`; Docling execution and explicit degraded plain-text fallback are covered without claiming the optional binary is installed |
| Backend and team-runner regression suite | PASS | `uv run --isolated pytest packages/mas-core/tests apps/orchestrator-api/tests apps/tool-service/tests apps/team-runner/tests -q` |
| Broader worker/observability regression suite | PASS | `uv run --isolated pytest packages/mas-core/tests/test_worker_*.py packages/mas-core/tests/test_observability.py apps/orchestrator-api/tests/test_metrics.py -q` |
| Metrics API and label-policy suite | PASS (static + refreshed local live scrape) | Contract `90a7d82`, runtime wiring `cbeb9db`, and bounded legacy-storage reconciliation fallback `541d6e0`; the secret-safe local scrape evidence was refreshed 2026-08-11 in `76a32c0`; `uv run --isolated pytest packages/mas-core/tests/test_metric_series_budget.py apps/orchestrator-api/tests/test_metrics.py apps/orchestrator-api/tests/test_projects.py -q`; the static report includes every AIAT label policy, the 10,000-project fixture, and the Prometheus histogram `_created` normalization regression test; the refreshed local live scrape is 31 bounded series with no `project_id` label, retained at [`mas/docs/provenance/metric_series_live.json`](../../mas/docs/provenance/metric_series_live.json), while native many-project evidence remains open |
| HTTP/message/trace-evidence suite | PASS (committed core; fresh local transport + tool read-back; broader sources open) | Core commit `77d5494`; router/agent propagation `5bc0aae`; worker source coverage contract `24c2e35`; `uv run --isolated pytest packages/mas-core/tests/test_tracing.py packages/mas-core/tests/test_trace_evidence.py packages/mas-core/tests/test_native_trace_spans.py packages/mas-core/tests/test_phase4_5.py apps/message-router/tests/test_trace_propagation.py -q`; `uv run --isolated python scripts/check_trace_evidence.py --json`, `scripts/check_native_trace_spans.py --json`, and `scripts/check_worker_trace_coverage.py --json --require-integration` pass deterministic contracts. The configured 2026-08-11 local probes pass and are retained in the two provenance artifacts; selected live model-backed worker, mail-edge, and retention probes remain separate evidence boundaries |
| API observation ledger | PASS (bounded static/unit/API) | `uv run --isolated pytest packages/mas-core/tests/test_api_observations.py apps/orchestrator-api/tests/test_trace_propagation.py -q`; `uv run --isolated python scripts/check_api_observability.py --json` passes the payload-free normalized-route fixture; migrations `0034_api_request_observations`, `0035_trace_correlation_evidence`, and `0036_native_trace_spans`, durable table/readers, and trace/SLO projections are implemented |
| Secret-safe system diagnostics | PASS (static/unit/API) | Commit `2860838`; `uv run --isolated pytest apps/orchestrator-api/tests/test_system.py apps/orchestrator-api/tests/test_test10_ops_scripts.py -q` covers database, router, tool-service, optional object-store, degraded aggregation, no-storage 503, and dependency-payload redaction. The route is read-only and returns only bounded status/latency/connection facts or exception type |
| Operator control CLI | PASS (static/unit) | Commits `380daf5` and `f8df50e`; `uv run --isolated pytest scripts/tests/test_mas_ctl.py -q` passes six deterministic cases, and `test_test10_ops_scripts.py` verifies the executable `scripts/mas-ctl` wrapper. `bootstrap` requires healthy `/health` plus `ok` diagnostics; error bodies are never returned |
| Communication-policy sender identity | PASS (static/unit/mocked router) | Commit `fb39128`; `uv run --isolated pytest packages/mas-core/tests/test_policy.py apps/message-router/tests/test_phase3.py apps/message-router/tests/test_publish_auth.py apps/orchestrator-api/tests/test_test12_comms_policy.py -q` covers sender role/team coherence, spoofed worker-to-CEO/admin paths, role-specific message types, and HTTP 403 before enqueue. Live external-router and dashboard hierarchy evidence remain separate |
| Hierarchy communication-policy overlay | PASS (source type/lint/build + focused live E2E) | Implementation `8b7d9f1`; evidence-test wording cleanup `3dc61ad`; selector hardening `d5f596e`; fail-closed staged-context handling `45ee42c`; `npm run typecheck`, focused ESLint, and `npm run build` pass for the dashboard. The focused authenticated `npm run test:e2e -- --workers=1 --grep "system visualization exposes hierarchy"` passes 1/1 against a current `mas/dashboard:overlay` image built from a clean explicit context. The `mas.sh` wrapper excludes all disposable `.tmp*` paths and rejects incomplete staging; direct unwrapped WSL Docker-context and native/release-image evidence remain separate, and the API-only hierarchy suites retain two explicit live-evidence skips |
| SLO/capacity suite | PASS (bounded; native/live open) | `uv run --isolated pytest packages/mas-core/tests/test_slo.py apps/orchestrator-api/tests/test_slo_capacity.py -q`; `uv run --isolated python scripts/check_slo_capacity.py --json` passes the deterministic fixture and `--live --json` returns `blocked` without an API; missing service telemetry remains explicit |
| Provenance inventory | PASS | `uv run --isolated python scripts/check_provenance.py` — 21 components, including the metadata-only operator-supplied SkillSpector record |
| Python compilation | PASS | isolated `compileall` for changed runtime, API, policy, image-contract, and provenance paths |
| CEO/service/dashboard ACL API suite | PASS (unit + authenticated local API matrix; native UI matrix open) | `uv run --isolated pytest apps/orchestrator-api/tests/test_auth_boundary.py -q`; the refreshed local `/dashboard/access` and `/dashboard/sections/{section}` matrix passes for operator/CEO/service/worker identities, with evidence at [`mas/docs/provenance/dashboard_acl_live.json`](../../mas/docs/provenance/dashboard_acl_live.json) |
| Team-runner storage boundary | PASS (static/API; local live matrix) | Commit `43bee16`; `uv run --isolated pytest apps/orchestrator-api/tests/test_team_runner_storage_boundary.py apps/team-runner/tests/test_storage_client.py packages/mas-core/tests/test_network_boundary.py -q`; `uv run --isolated python scripts/check_network_boundary.py --json` passes the static contract and `--live --json` passes all 11 current WSL2 runners with storage-health read-back; runners have no Compose DB/object-storage credentials and the API exposes only allow-listed operations |
| Dashboard ACL policy unit suite | PASS | Core policy/test group `d405ccb`; `uv run --isolated pytest packages/mas-core/tests/test_dashboard_access.py -q` covers finite sections, deny-by-default unknowns, deterministic persistence, and operator recovery invariants |
| Dashboard operator proxy/type contract | PASS | `npm run typecheck` in `apps/mas-dashboard` |
| Agent Streams stale/retry recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `3e8a0ea`; `npm run lint`, `npm run typecheck`, `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4123 npx playwright test streams-states.spec.ts --reporter=line` pass (focused browser coverage 1/1). The stream page retains history/messages across failed reconnect or history refresh, guards obsolete generations, labels last-known data, and exposes Reconnect/Retry; native/live Redis/router stream evidence remains open |
| Hiring Board stale/retry recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `7541b84`; `npm run lint`, `npm run typecheck`, `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4124 npx playwright test workers-states.spec.ts --reporter=line` pass (focused browser coverage 1/1). The worker catalogue retains its last successful rows after a failed refresh, labels the view as showing last-known workers, keeps rows visible, and exposes Retry; first-load failures show an unavailable state. Native/live worker certification evidence remains open |
| CEO Live Feed reconnect/history recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `1761429`; `npm run lint`, `npm run typecheck`, `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4125 npx playwright test ceo-states.spec.ts --reporter=line` pass (focused browser coverage 1/1). The CEO feed retains bounded history/messages across failed reconnect or history refresh, guards obsolete generations, labels last-known data, and exposes Reconnect/Retry without changing the governed composer. Native/live Redis/router CEO evidence remains open |
| Container Logs stale/retry recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `280d363`; `npm run lint`, `npm run typecheck`, `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4122 npx playwright test logs-states.spec.ts --reporter=line` pass (focused browser coverage 1/1). The SSE route retains the last log buffer after an error payload, labels it as last known, exposes Retry, and replaces the retained buffer on the first successful event after retry; native/live container log evidence remains open |
| Project evidence package stale/retry recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `bc80ad5`; `npm run typecheck`, targeted ESLint, full `npm run lint` (two unrelated hook warnings), `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4110 npx playwright test project-evidence-states.spec.ts --workers=1 --reporter=line` pass (focused browser coverage 1/1). The project evidence page reads with `cache: "no-store"`; after a successful package load, a failed refresh retains the last package, labels it as last known, and exposes a keyboard-visible Retry control that clears after successful recovery. Initial failures remain explicit; full project-page composition and live provider/worker evidence remain open |
| Dashboard local Compose E2E matrix | PASS WITH EXPLICIT SKIP (58/59) | `npm run test:e2e -- --workers=1` in `apps/mas-dashboard` passes 58/59; focused hierarchy/path-tracing and hiring-board evaluation flows pass after selector/state repairs (`d5f596e`, `514aeeb`). Focused shell and identity regressions also pass (`e2e/dashboard-shell-accessibility.spec.ts` 2/2, `e2e/identity-states.spec.ts` 1/1), the targeted system/PM resilience filter passes 4/4, the source-built governance, System Control, Projects list, Tools catalogue, dead-letter queue, credentials metadata, Metrics partial/stale, Flows stale/recovery, and Agent Streams stale/recovery tests pass 1/1 (`e2e/governance-states.spec.ts`, `e2e/system-status-states.spec.ts`, `e2e/projects-states.spec.ts`, `e2e/tools-states.spec.ts`, `e2e/dlq-states.spec.ts`, `e2e/credentials-states.spec.ts`, `e2e/metrics-states.spec.ts`, `e2e/flows-states.spec.ts`, `e2e/streams-states.spec.ts`), and the one-test flow-builder golden path passes after the project flow catalogue was made `cache: "no-store"`. Authenticated WSL2 Compose coverage includes operational UI, hierarchy communication-policy/path tracing, CEO chat/hiring, project workspace, schema-driven flow editing, all eight flow runtime scenarios, hiring-board evaluation details, identity stale-record/retry state, PM integration conflict/stale retry, project-detail stale/retry state, runtime status, shell skip-link/mobile focus recovery, and system-visualization partial/offline retry states. Secret-safe evidence is [`mas/docs/provenance/dashboard_e2e_live.json`](../../mas/docs/provenance/dashboard_e2e_live.json); the live DLQ replay case is skipped pending an operator-owned safe fixture, and native-Linux/mobile/WCAG evidence remains open |
| External-account action policy and mail correlation | PASS (static/API/unit, preparatory P1) | `PYTHONPATH=apps/identity-service uv run --isolated pytest apps/identity-service/tests/test_identity_service.py -q`; versioned action taxonomy, closure human-approval/session-revocation path, and safe delivery-attempt trace/span persistence pass; provider/live outage and mail-edge evidence remains open |
| Production image contract | PASS (static + SBOM schema); BLOCKED (live) | Commit `42b03a3` adds `--require-sbom` validation for CycloneDX format/version, metadata component, named components, and duplicate `bom-ref` detection; `uv run --isolated pytest packages/mas-core/tests/test_image_provenance_runner.py -q` (6 passed) and `uv run --isolated python scripts/check_image_provenance.py --json` pass the static contract. `--live --require-sbom --json` exits 2 because deployment-supplied immutable `*_IMAGE_REF` values and release artifacts are absent; no SBOM licence field is used as a gate |
| Operator runtime/CLI pin contract | PASS (static; host-only entries explicit unavailable) | `uv run --isolated python scripts/check_operator_pins.py --json` verifies exact production CLI/dependency declarations and records explicit reasons for unavailable host, optional, and deployment-supplied capabilities; no licence metadata is a gate |
| Worker manifest/runtime/provenance reconciliation | PASS (static + authenticated local live binding; technical findings remain open) | `uv run --isolated python scripts/check_worker_reconciliation.py --json` validates 39 manifests; the authenticated local `--live --json` run (evidence refreshed 2026-08-11 in `180f9e0`) matches all 39 persisted defaults with zero missing rows or binding mismatches, retained at [`provenance/worker_reconciliation_live.json`](../../mas/docs/provenance/worker_reconciliation_live.json). Coding/tester rows still link to exact Semgrep evidence with 316 findings and remain `findings_review_required`; host package availability is advisory and Compose import readiness is recorded separately |
| Team-runner manifest identity bindings | static/unit | PASS | Commit `d9b1262`; `uv run --isolated pytest packages/mas-core/tests/test_team_worker_manifest_refs.py apps/team-runner/tests/test_team_config.py -q` and `uv run --isolated python scripts/check_team_worker_manifest_refs.py --json` reconcile 11 team files and 39 exact agent→manifest IDs; no registration/activation mutation and licence metadata remains informational |
| Team-runner startup manifest enforcement | unit/startup contract | PASS | Commit `569231f`; `uv run --isolated pytest apps/team-runner/tests/test_team_config.py apps/team-runner/tests/test_shutdown.py -q` verifies production-style mounted-manifest reconciliation, fail-closed missing references, exact `AgentConfig` propagation, and health metadata; startup remains read-only and does not register or activate workers |
| Default worker implementation bindings | PASS (static); BLOCKED (live without operator environment) | `uv run --isolated pytest packages/mas-core/tests/test_default_worker_bindings.py -q`; `uv run --isolated python scripts/check_default_worker_bindings.py --json` reconciles all 15 documented default worker slots across department, runtime, transport, isolation, runtime-catalogue support, runtime/integration adapter entrypoints, capability, adapter configuration, and required tools. `--live --json` is fail-closed and does not mutate runtime state; licence metadata remains informational only |
| Worker-run lifecycle contract | PASS (deterministic fixture); BLOCKED (live without operator-selected run) | `uv run --isolated python scripts/check_worker_run_lifecycle.py --json` drives the real controller/native adapter through checkpoint persistence, pause/resume, cold cancellation, cold-crash failure normalization, lease-expiry requeue, and artifact/usage-before-terminal ordering. `--live --json` returns exit 2 without mutating a live run; database, sandbox, canary, and rollback certification remain open |
| Worker trace source coverage | PASS (fixture; live dispatch explicitly gated) | Commit `24c2e35`; `uv run --isolated pytest packages/mas-core/tests/test_worker_trace_coverage.py -q` and `uv run --isolated python scripts/check_worker_trace_coverage.py --json --require-integration` require model-usage, worker-artifact, native model, native worker, and optional integration source categories. Read-only live mode requires a selected trace; dispatch requires an active model-backed worker/project/profile and `--confirm-dispatch`, is bounded to a small deterministic task/budget, and emits no raw payloads or credentials. No live worker-run evidence is claimed yet; mail-edge, retention, sandbox, canary, and rollback remain open; licence metadata is informational only |
| Selected worker-run readiness preflight | PASS (fixture + read-only live diagnostic; dispatch blocked) | Commit `5553b19`; `uv run --isolated pytest packages/mas-core/tests/test_worker_run_readiness.py -q` and `uv run --isolated python scripts/check_worker_run_readiness.py --json` pass the complete snapshot fixture. The authenticated live read requires explicit worker/project UUIDs and reports status, immutable shell/adapter/skill pointers, project/company/assignment state, approved profile/version, bounded budget headroom, declared sandbox, and health without selecting or mutating state. The current coding-worker/terminal-project selection exits 2 with inactive worker, missing immutable pointers, terminal project, and missing company assignment blockers; identity, sandbox runtime, canary, live-run, rollback, and licence metadata remain separate/not-gated |
| Selected steward certification readiness preflight | PASS (fixture + authenticated read-only live diagnostic; certification blocked) | Commit `adc7b26`; `uv run --isolated pytest packages/mas-core/tests/test_worker_steward_readiness.py -q` and `uv run --isolated python scripts/check_worker_steward_readiness.py --json` pass the complete candidate fixture. The authenticated live read requires explicit worker/candidate UUIDs and reads only worker, steward, and candidate models. The current coding-worker selection exits 2 with `steward_not_ready`, `security_scan_not_passed`, and `candidate_not_found`; it never generates/certifies/approves/activates/rolls out/dispatches, and licence metadata remains separate/not-gated |
| Worker certification matrix | PASS (deterministic static/unit) | Commit `a62ddb7`; `uv run --isolated pytest packages/mas-core/tests/test_worker_certification_matrix.py -q` and `uv run --isolated python scripts/generate_worker_certification_matrix.py --check`; 39 rows record exact runtime imports, transports, adapter versions, and pending evidence without claiming live certification; the regression checks generated-artifact parity, exact manifest coverage, and metadata-only licence handling |
| Default runtime adapter conformance | PASS (adapter fixture + Compose package/lifecycle and exact lock-parity probe; worker certification open) | `uv run --isolated pytest packages/mas-core/tests/test_runtime_adapter_conformance.py -q`; `docker exec mas-orchestrator-api-1 python /app/scripts/check_runtime_adapter_conformance.py --live --json` passes actual LangGraph/CrewAI adapter classes with locked LangGraph `0.6.11` and CrewAI `1.6.1`, without model/tool/provider/project calls; evidence is [`mas/docs/provenance/runtime_adapter_conformance_live.json`](../../mas/docs/provenance/runtime_adapter_conformance_live.json). Sandbox, canary, live-run, and rollback remain open |
| Runtime benchmark readiness | PASS (fresh local dependency dry-run; certification boundary remains open) | Checker/test group `ad31793`; `uv run --isolated pytest packages/mas-core/tests/test_runtime_benchmarks.py -q` and static mode pass. The authenticated local LangGraph/CrewAI dependency dry-run was refreshed 2026-08-11 in `3f15e28` and is retained at [`mas/docs/provenance/runtime_benchmarks_live.json`](../../mas/docs/provenance/runtime_benchmarks_live.json). Unavailable API/package/validation paths still return `blocked`/exit 2. This remains package benchmark evidence only, not a worker canary, project run, sandbox proof, or rollback result |
| Network boundary contract | PASS (static + refreshed local live); native release open | `uv run --isolated python scripts/check_network_boundary.py --json` and `--live --json`; all runners use only `workers`, protected data services and OpenCode are off that network, gateway reachability/storage health pass, and identity/forbidden-env/socket checks pass. Secret-safe evidence is [`mas/docs/provenance/network_boundary_live.json`](../../mas/docs/provenance/network_boundary_live.json); native release-host denial/allow evidence remains open |
| Tool-service image budget and dependency profile | PASS (static + local Linux probe); native release open | Contracts `b24ca0c` and `e6ee8b8`; `apps/tool-service/pyproject.toml` keeps Playwright in the opt-in `browser` extra, while `Dockerfile.tool-service` keeps browser/Docling/Semgrep in the separately budgeted `extensions` profile and pins uv `0.4.30`; `uv run --isolated pytest packages/mas-core/tests/test_image_budgets.py apps/tool-service/tests/test_default_shipped_tool_catalog.py -q` and `uv run --isolated python scripts/check_image_budgets.py --json` pass the checked-in ceilings. The local profile measurements remain in [`mas/docs/provenance/image_budgets_live.json`](../../mas/docs/provenance/image_budgets_live.json). Compressed archive, clean native-Linux build/pull, SBOM, and vulnerability evidence remain open |
| API/protocol contract export | PASS (static/preparatory) | Commit `2860838` regenerates the checked-in artifacts; `uv run --isolated python scripts/check_api_contract.py --json` — 236 OpenAPI paths/130 schemas and checked-in `aiat.v1` protocol schema match runtime/provenance hashes, including the new read-only system diagnostics route, native trace-evidence source, SLO/capacity, evidence-package, self-improvement outcome-action, artifact-bundle/read-back-action, and bounded artifact/usage evidence-read fields |
| Python SDK contract generation | PASS (static/unit/preparatory) | Commit `2860838`; `uv run --isolated python scripts/generate_python_api.py --check`; `uv run --isolated pytest packages/mas-api-sdk/tests -q`; 130 models and 269 operations match OpenAPI |
| Company timezone propagation | PASS (tool-service + dashboard/Compose/default-wrapper) | Commits `8bcff1a` and `ee1361f`; focused time/schedule tests and dashboard typecheck pass. The effective company IANA timezone propagates through schedule persistence, CEO schedule views, dashboard datetime fallback, and the development wrapper; invalid input still fails closed to UTC |
| Prompt/tool and review contract reconciliation | PASS (static/unit) | Contracts `20f0499` and `5b830e9`; `uv run --isolated python scripts/check_prompt_tool_reconciliation.py --json` plus the focused tool-service review/catalogue tests; 11 shipped prompts resolve to 114 concrete manifest tools, review adapters publish signed-context `REVIEW_RESPONSE` envelopes, scanner aliases remain bounded, and the CEO privileged-action tool targets the audited gate |
| Flow node-schema contract | PASS (static/unit, preparatory P1) | `uv run --isolated python scripts/generate_flow_node_schemas.py --check`; 9 node types at v1.0 match backend validation, JSON artifact, `/flows/node-schemas`, and generated dashboard metadata |
| Dashboard node-schema editor | PASS (static/typecheck, preparatory P1) | `54ad710` plus project-evidence typecheck repair `fc4f0fa`; both flow editors render the generated contract and editable typed form, including governed worker/profile selectors; deprecated `team_id`/`action` fields are primary-form hidden and retained in collapsed compatibility controls, while API dry-run and immutable saved-definition migration report deterministic alias findings and explicit worker mappings |
| Evidence-policy contract | PASS (static/unit, preparatory P1) | Evidence tests cover required artifact kinds; `/evidence-policies` publishes built-ins and policy dry-run validation includes worker-run/repository resources |
| Evidence-policy selection | PASS (static/API/unit, preparatory P1) | Company defaults persist through the active manifest; `PUT /projects/{project_id}/evidence-policy` persists project defaults and milestone overrides; `PUT /companies/{company_id}/evidence-policy` updates the company default; `resolve_evidence_policy_selection` and `check_evidence_policy_resolution.py --json` cover project-milestone → project → flow → company-milestone → company → manual precedence without using licence metadata as a gate; live transition/recovery proof remains open |
| Project evidence package | PASS (committed core/API/dashboard surfaces; project-page/live preparatory) | Commits `a44a1aa`, `d0472af`, `cbf00d9`, `33e0384`, `82bbaeb`, `1112d5e`, `fc4f0fa`, and `bc80ad5`; core and clean-checkout API tests pass, including `uv run --isolated pytest packages/mas-core/tests/test_evidence_package_runner.py packages/mas-core/tests/test_evidence_policy_resolution.py packages/mas-core/tests/test_workflow_scaffold.py apps/orchestrator-api/tests/test_project_evidence_routes.py apps/orchestrator-api/tests/test_projects.py -q`; `npm run typecheck` passes for the project evidence page, deep-link record page, and API proxies; `uv run --isolated python scripts/check_project_evidence_package.py --json` and `scripts/check_evidence_policy_resolution.py --json` pass with metadata-only notices and fail-closed live modes. The source-built project evidence page now retains its last successful package through a failed refresh and recovers through Retry (`project-evidence-states.spec.ts` 1/1). Project-page composition, live durable snapshot/provider/worker generation, and native recovery remain open |
| Reusable flow templates | PASS (static/unit/API, preparatory P1) | Six canonical templates validate through `validate_flow`; `/flow-templates` and `/flows/from-template` are covered by `test_flow_templates.py` and `test_flows.py` |
| Dashboard canonical template consumption | PASS (static/typecheck, preparatory P1) | New-flow starter cards fetch `/api/flow-templates`, preserve canonical configs/evidence metadata, remap branch references, and retain a blank fallback; `npm run typecheck` passes |
| Flow definition lifecycle | PASS (static/API, preparatory P1) | Flow export/hash, deterministic diff, import, publish, deprecate, compatible migration, and explicitly mapped active-node graph-rewrite endpoints are covered by `apps/orchestrator-api/tests/test_flows.py`; live recovery remains open |
| Flow execution semantics contract | PASS (deterministic traversal fixture, preparatory P1) | `uv run --isolated pytest packages/mas-core/tests/test_flow_execution_semantics.py -q`; `uv run --isolated python scripts/check_flow_execution_semantics.py --json` drives real fan-out/join/switch traversal, prevents duplicate/completed join scheduling, and blocks unknown switch cases without worker or storage mutation; live execution/recovery remains open |
| Governed asynchronous flow-task binding | PASS (deterministic contract fixture, preparatory P1) | `uv run --isolated pytest packages/mas-core/tests/test_flow_worker_binding.py packages/mas-core/tests/test_flow_retry_persistence.py apps/orchestrator-api/tests/test_flows.py -q`; `uv run --isolated python scripts/check_flow_worker_binding.py --json` proves queued/claimed/running Worker Runs keep their task active, terminal states settle, parallel bindings remain copy-on-write, safe retry re-enters governed dispatch, and unknown states fail closed; the API and no-safe-node storage fallback preserve prior executions as `SUPERSEDED`; live canary/recovery remains open |
| Watchdog and safe-retry recovery semantics | PASS (deterministic controller fixture, preparatory P1) | `uv run --isolated pytest packages/mas-core/tests/test_workflow_watchdog_recovery.py -q`; `uv run --isolated python scripts/check_workflow_watchdog_recovery.py --json` proves boot grace, downtime-aware timeout, watchdog failure transition, recorded-safe-state retry, and terminal-state exclusion without storage/worker mutation; native watchdog/cold-recovery remains open |
| Docker/Compose live certification | PASS (refreshed local WSL2 runner matrix) / BLOCKED (native release host and broader service evidence) | The `43bee16` boundary contract and current `check_network_boundary.py --live --json` run pass all 11 runners: named gateways are reachable, Redis/Postgres/PgBouncer/MinIO/OpenCode/unapproved egress are denied, control-plane storage health is true, no forbidden runner environment names or Docker sockets are present, and the result is retained at [`mas/docs/provenance/network_boundary_live.json`](../../mas/docs/provenance/network_boundary_live.json). Native-Linux identity/network/dashboard, provider-pair, backup/restore, and broader live probes remain open. |
| Evidence-policy scope resolution fixture | PASS (static/unit, preparatory P1) | `uv run --isolated pytest packages/mas-core/tests/test_evidence_policy_resolution.py -q`; `uv run --isolated python scripts/check_evidence_policy_resolution.py --json` passes seven precedence/fallback cases and explicitly reports `licence_metadata_is_gate: false`; `--live` remains blocked without an authenticated API scenario |
| External-account action policy fixture | PASS (static/unit, preparatory P1) | `uv run --isolated pytest packages/mas-core/tests/test_external_account_action_policy.py -q`; `uv run --isolated python scripts/check_external_account_action_policy.py --json` reconciles all five action rules, four category dispositions, and fail-closed unknown action/category behavior without mutating identity/provider state; `--live` remains blocked until a provider-specific sandbox/outage scenario exists |
| Built-in PM/SCM adapter declarations | PASS (static/unit/fixture; provider live open) | `uv run --isolated pytest packages/mas-core/tests/test_provider_adapter_declarations.py -q`; `uv run --isolated python scripts/check_provider_adapter_declarations.py --json` reconciles the real YouTrack adapter plus GitHub `pm`/`delivery`/`checks` profiles and bounded path guards with zero provider HTTP calls or mutations; provider-specific mock/live/outage/restore evidence remains open |
| Built-in PM/SCM mocked HTTP conformance | PASS (static/unit/fixture; provider live open) | `uv run --isolated pytest packages/mas-core/tests/test_provider_adapter_http_conformance.py -q`; `uv run --isolated python scripts/check_provider_adapter_http_conformance.py --json` drives eight real-adapter YouTrack/GitHub cases for health/configuration, projection/read-back, cursors, deactivation, comments/links, GitHub source-control paths, webhook handling, and retryable/permanent failures using local responses only |
| External-account lifecycle fixture | PASS (static/unit/fixture; provider live open) | `uv run --isolated pytest packages/mas-core/tests/test_external_account_lifecycle.py -q`; `uv run --isolated python scripts/check_external_account_lifecycle.py --json` drives the actual `IdentityService` through eight in-memory cases for category approval/idempotency, one-use browser leases, credential rotation/session revocation, closure approval, immediate suspension, fail-closed unknown categories, and secret-safe output without external account/provider calls |
| Outbound-mail lifecycle fixture | PASS (static/unit/fixture; relay live open) | `uv run --isolated pytest packages/mas-core/tests/test_outbound_mail_lifecycle.py -q`; `uv run --isolated python scripts/check_outbound_mail_lifecycle.py --json` drives the actual `IdentityService` through approval pause, request/submission idempotency, definitive provider-failure retry, ambiguous-outage reconciliation hold, and secret-safe output without external relay calls |
| Self-improvement candidate detection | PASS (static/unit/fixture; live signal sources open) | Commit `4d8dddf`; `uv run --isolated pytest packages/mas-core/tests/test_improvement_candidates.py -q`; `uv run --isolated python scripts/check_self_improvement_candidates.py --json` reconciles defect, metric, upstream-update, cost, and operator-goal signals with deterministic deduplication/risk/budget mapping, conflicting-ID rejection, secret-safe metadata, and zero project/budget/credential/deployment side effects |
| Machine-readable release ledger | PASS (static aggregation; native live-ledger gate `4d7a495`); BLOCKED (current unconfigured live profile and retained configured profile) | `uv run --isolated python scripts/check_release_ledger.py --json` reports 48/48 static checks passing, two pending worker security findings-review items, and `NO-RELEASE`. The current unconfigured 65-check `--live --json` snapshot records 51 pass/14 blocked/0 fail with four pending items and is retained at [`provenance/release_ledger_live_current.json`](../../mas/docs/provenance/release_ledger_live_current.json); the configured loopback 64-check snapshot remains at [`provenance/release_ledger_live.json`](../../mas/docs/provenance/release_ledger_live.json) with 59 pass/5 blocked. The native preflight is included in the live aggregate and never exposes credentials; licence metadata remains non-gating. |
| Release environment manifest | PASS (secret-safe static identity; committed `64771b5`) | `uv run --isolated python scripts/check_release_environment.py --json` emits `aiat.release-environment.v1` with thirteen input hashes, tool identities, environment-presence flags, and a deterministic per-revision manifest digest without printing values or credentials. The report records the current branch, revision, changed-path count, and dirty state; its digest must be captured again for the eventual frozen release commit. |
| Native release-host preflight | BLOCKED (current local WSL2 host) | The opt-in `uv run --isolated python scripts/check_release_environment.py --require-native-linux --json` check fails closed unless the host is native Linux, Docker/Compose v2 and `runsc` are available, the tree is clean, and all ten deployment image refs are digest-pinned. The current WSL2 result is retained at [`provenance/native_release_preflight.json`](../../mas/docs/provenance/native_release_preflight.json) with safe blockers for host identity, `runsc`, dirty state, and absent image refs. It is a prerequisite diagnostic only; native network, image/SBOM, scan, recovery, and provider evidence remain open, and licence metadata is non-gating. |

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
   image identity helper with `--require-sbom` first and retain its
   blocked/pass/fail result. The checked-in CycloneDX shape validator is not a
   substitute for clean artifact generation or scan reconciliation.
4. Run the native many-project aggregate-state scrape and repeat both
   tool-service profile measurements on the clean native-Linux release host;
   the local 31-series scrape and both local image-budget probes already pass
   and are retained as descriptive evidence.
5. Certify each default runtime/adapter against the installed lock and live
   worker-run lifecycle. The bounded local LangGraph/CrewAI dependency
   benchmark now passes and is retained at
   [`mas/docs/provenance/runtime_benchmarks_live.json`](../../mas/docs/provenance/runtime_benchmarks_live.json),
   but it is not sandbox, canary, worker-run, or rollback evidence. The new
   read-only `check_worker_run_readiness.py` preflight reports the selected
   worker/project blockers individually; its current exit-2 result does not
   substitute for activation, identity, sandbox, canary, live-run, or rollback
   evidence.
6. Freeze a clean release commit/environment manifest and publish the current
   release ledger with static, contract, integration, live, recovery, and
   externally blocked evidence labels; use `check_release_ledger.py` as the
   machine-readable aggregation boundary.

Until those items are evidenced, the programme remains **not release-certified**
even though the licence policy implementation is complete for personal,
internal use.
