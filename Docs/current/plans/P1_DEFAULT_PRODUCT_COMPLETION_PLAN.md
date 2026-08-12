# P1 Default Programme Completion Plan

**Priority:** P1 after P0 exit  
**Outcome:** the complete default AIAT company can execute a governed software project using certified workers and integrations  
**Authority:** [AIAT Target Programme](../../../AIAT_TARGET_PROGRAMME.md)

## Workstream 1 — modular control plane and contracts

- Extract project/evidence, company/budget, workers/stewards, flows, models, integrations, and operations routers/services from the oversized orchestrator module.
- Preserve one public API and existing transactional owners.
- [x] Export deterministic OpenAPI and protocol-schema artifacts with provenance hashes and CI verification.
- [x] Generate deterministic TypeScript dashboard models/operation metadata and enforce generation plus dashboard typecheck in CI.
- [x] Generate deterministic Python `TypedDict` models, operation metadata, and
  an async orchestrator client from the same OpenAPI artifact; CI and transport
  tests enforce compatibility.
- External client-language SDKs remain optional follow-up work.
- [x] Extend the company manifest with typed timezone, retention, privacy, evidence, model, and deployment policies; legacy manifests remain valid.
- [x] Propagate the manifest timezone through runner prompt timestamps (`c955ac8`), the
  `time_now` tool, orchestrator scheduler defaults, dashboard display helpers,
  and Compose/development defaults; no shipped prompt hardcodes a regional
  timezone.
- [x] Reconcile all 11 shipped authority/manager prompts with the concrete
  tool manifest and role/team grants; canonicalize review payloads as
  `REVIEW_RESPONSE` envelopes and expose the CEO-only
  `privileged_ops.request` adapter for the audited control-plane gate.
- [x] Add bounded request trace propagation to the orchestrator API, message
  router, and tool service: accept safe `X-AIAT-Trace-ID`/W3C `traceparent`
  values, generate a fresh root when invalid or absent, return the trace ID,
  forward the bound trace on orchestrator-to-router and SDK-to-tool
  publication, and clear async context after each HTTP request. Worker message
  dispatch context and RouterClient forwarding are covered by the same slice.
  The operator-only `aiat.trace-evidence.v1` query now joins task, usage,
  worker-transition, direct trace-correlated model-usage/worker-artifact/
  integration-evidence records with legacy run fallback, and PM-inbound
  metadata; native transport/model/tool/audit/worker/integration span
  persistence and bounded identity delivery-attempt `trace_id`/`span_id`
  correlation are implemented, while provider mail-edge/live retention remains
  P2 work.

**Done when:** no direct cross-domain table writes bypass the owning service; compatibility and migration suites pass with the same public behaviour.

The checked-in contract export, internal Python SDK, and prompt/tool parity
check are preparatory work only: P1 remains gated on the R1/P0
release-integrity exit and does not claim modular router extraction or
external client-language SDK compatibility yet.

## Workstream 2 — certify default specialists

- [x] Run the actual LangGraph/CrewAI adapter classes through a bounded
  Compose package/lifecycle probe; LangGraph `0.6.11` and CrewAI `1.6.1`
  import and complete the no-model fixture, matching the repository lock.
  Representative model-backed canaries, sandbox, live-run, and rollback
  certification remain open.
- [x] Generate the exact worker/runtime/adapter/security evidence matrix; use
  it to keep declared compatibility separate from live certification.
- [x] Exercise the LangGraph and CrewAI bridge adapters through the shared
  universal conformance suite; keep runtime-package, sandbox, canary, and live
  certification as separate gates.
- [x] Add `scripts/check_worker_runtime_readiness.py` with static declaration
  and fail-closed `--live` import-probe modes; missing packages return exit 2,
  while security, sandbox, canary, live-run, and rollback evidence remain
  separate.
- [x] Add `scripts/check_sandbox_runtime_readiness.py` with static worker
  declaration reconciliation and fail-closed Docker `runsc` registration;
  digest-pinned smoke, network-denial, canary, and Firecracker evidence remain
  separate.
- [x] Add the read-only `scripts/check_runtime_benchmarks.py --live --json`
  probe for orchestrator dependency-backed LangGraph/CrewAI benchmarks. It
  sends deterministic configs, bounds third-party imports off the event loop,
  treats missing/timeout/error evidence as blocked, and retains a local live
  pass; it does not certify a worker canary or live project run (checker/test
  contract `ad31793`).
- [x] Add `scripts/check_runtime_adapter_conformance.py --live --json` and
  retain [`runtime_adapter_conformance_live.json`](../../../mas/docs/provenance/runtime_adapter_conformance_live.json);
  it checks package availability plus actual adapter manifest/message
  translation, bounded completion, health, and shutdown without external
  calls. Framework fixture execution is not a worker canary.
- [x] Add `scripts/check_runtime_install_profile.py` to reconcile the
  `runtime-default` dependency extra, tracked `uv.lock` versions (`2b13d89`),
  runtime-catalogue imports, and production orchestrator Dockerfile install
  command; `uv sync --locked --dev --dry-run` also resolves the tracked
  workspace lock; imports, security, sandbox, canary, live-run, and rollback
  evidence remain separate.
- [x] Add `scripts/check_worker_steward_contract.py` to run the actual steward
  domain through dedicated-steward, immutable-candidate, compatibility-matrix,
  staged-rollout, and rollback transitions for every externally sourced
  default worker; its regression gate and pre-activation rollback path preserve
  the previously active immutable pointers. Database persistence and live
  certification remain separate.
- [x] Add `scripts/check_worker_run_lifecycle.py` and focused regression tests
  that drive the real `WorkerRunController`/`NativeWorkerAdapter` through
  checkpoint persistence, pause/resume with checkpoint reference, cold
  cancellation, cold-crash failure normalization, lease-expiry requeue, and
  artifact/usage-before-terminal ordering;
  the deterministic fixture is not a database, sandbox, canary, or live-run
  certificate.
- [x] Add the read-only `aiat.worker-run-readiness.v1` evaluator and
  `scripts/check_worker_run_readiness.py` (`5553b19`) for one explicitly
  selected model-backed worker/project. It reconciles lifecycle status,
  immutable shell/adapter/skill pointers, source/version and evaluation state,
  project/company/assignment state, approved model-profile versions, bounded
  concurrent/cost budget headroom, sandbox declaration, and health metadata.
  Fixture evidence passes; the current live selection is blocked by inactive
  workers, a terminal project, missing immutable pointers, and missing company
  assignment. It never activates, provisions identity, reserves budget,
  dispatches, or returns payloads; live worker, sandbox runtime, canary, and
  rollback certification remain open.
- [x] Add the read-only `aiat.worker-steward-readiness.v1` evaluator and
  `scripts/check_worker_steward_readiness.py` (`adc7b26`) for one explicitly
  selected external worker/candidate. Fixture evidence passes; the current
  authenticated coding-worker selection is blocked by `PROVISIONING` steward
  state, a pending technical scan, and no candidate. The checker never
  generates or certifies a candidate, approves, activates, rolls out, or
  dispatches, and licence metadata remains non-gating.
- [x] Bind all 39 team-runner agent declarations to exact checked-in worker
  manifests and add `check_team_worker_manifest_refs.py` (`d9b1262`). The
  static contract passes 11 team files/39 agents without inferring missing
  references or registering/activating workers; runtime registration and live
  certification remain separate.
- [x] Make production team-runner startup repeat the read-only reconciliation
  against its mounted worker directory and carry each exact reference into
  `AgentConfig`/health metadata (`569231f`). Missing or mismatched references
  fail closed; this does not register, activate, or certify a worker.
- [x] Add the missing deterministic regression contract for the 39-row worker
  certification matrix (`a62ddb7`). The test checks generated-artifact parity,
  exact manifest coverage, pending security evidence, and metadata-only licence
  handling without claiming live certification.
- [x] Persist the compatibility matrix produced by certification through the
  canonical storage owner, retaining runtime/adapter/contract versions,
  fixtures, capability/model context, and pass/fail status with the
  certification evidence.
- [x] Reconcile the 15 documented default worker slots with their concrete
  department, runtime, transport, isolation, capability, runtime/integration
  adapter entrypoints, adapter, and tool declarations through
  `scripts/check_default_worker_bindings.py`, including the runtime-catalogue
  transport/isolation support pair; this is implementation-coherence evidence
  only and does not claim live runtime, canary, rollback, or provider
  certification.
- [x] Harden the worker registry authority boundary (`d8cafbb`): constrain
  registration and partial updates to the manifest `update_policy` vocabulary,
  and re-run persisted capability `required_tools` grants whenever capability
  IDs or team context changes. Forbidden direct or persisted grants fail closed
  before storage mutation; focused worker configuration coverage is 66/66, with
  adjacent capability, lifecycle, and policy suites passing. Licence metadata
  remains informational only.
- [x] Add deterministic Microsoft Agent Framework adapter compatibility
  coverage for bounded Agent/ChatAgent construction, run/invoke translation,
  shutdown, and fail-closed missing-package/instructions paths; locked
  package/MCP compatibility and activation remain open.
- [x] Publish `aiat.runtime-compatibility.v1` for `agent-framework==1.13.0`
  plus MCP `>=1.27,<2`, and enforce a secret-free fail-closed adapter
  preflight. The current workspace MCP `1.23.3` and missing optional MAF
  package are reported as activation blockers; installation, canary, and live
  certification remain open.
- Install the locked Microsoft Agent Framework/MCP set in an optional runtime
  environment and certify its representative task before activation.
- [x] Make the credential-free AIAT diff reviewer the default code-review
  adapter and publish a catalogue that keeps generic external candidates
  fail-closed until exact repository/revision/version evidence exists.
- Complete OpenCode sandbox/security evidence and optionally certify OpenHands core.
- Certify Docling/Spec Kit/Mermaid, Scrapling, Semgrep/SkillSpector, ccpm/GitHub Issues, OpenTofu/GitHub Actions, code-review, and SRE adapters; the shared bounded Semgrep/SkillSpector/TruffleHog scanner aliases are implemented, while Plane, OpenProject, Ansible, and other technically suitable resources remain selectable through the same normal adapter boundaries and need provider-specific certification.
- [x] Keep `document.ingest` usable before the optional Docling extension is
  installed: the Docling runner is selected when present and the core profile
  returns an explicit degraded `plain_text_fallback` result otherwise. Full
  Docling/Spec Kit/Mermaid adapter certification remains a separate item.
- Provision one dedicated steward and immutable compatibility matrix per external worker.
- [x] Exercise deterministic shadow, read-only canary, promotion, regression
  blocking, and pre-activation rollback for every externally sourced default
  worker through the real steward domain; live canary, project execution, and
  database persistence evidence remain open.
- [x] Rehydrate active immutable bundle/adapter pointers from durable steward
  rows after API restart and fail closed on unknown IDs; live worker
  certification remains open.
- [x] Rehydrate durable compatibility-matrix rows into the steward-owned
  runtime after API restart, and record new certification rows in the same
  process, normalizing the persisted single-profile and structured-capability
  JSON forms; database reconciliation and live worker certification remain
  open.
- Exercise shadow, read-only canary, live canary, promotion, regression block, and rollback.

**Done when:** every default worker table entry can execute one representative project-scoped task with exact versions, evidence, budgets, and rollback.

## Workstream 3 — flow and evidence completion

- [x] Introduce a versioned typed node-schema catalogue, persist its version on new/updated definitions, export it through the API, and generate checked-in JSON/dashboard metadata artifacts.
- [x] Surface the generated schema contract (version, descriptions, required-any rules, field types, enums, and defaults) in both flow editors without changing the existing compatibility controls.
- [x] Replace the editor's summary-only surface with a schema-driven form renderer for typed fields, enums, CSV/JSON values, governed worker/profile selectors, defaults, and minimums; keep duplicated compatibility aliases collapsed until migration coverage is complete. Flow dry-run now emits deterministic alias findings and explicit worker-binding recommendations.
- [x] Add deterministic definition publish/deprecate/diff/import/export endpoints with schema/version/hash evidence.
- [x] Add an explicit compatible migration policy for long-running instances: matching schema versions, active-node identity/type checks, preserved execution history, and durable migration context/audit evidence; reset/switch remains an operator-visible action for incompatible changes.
- [x] Add an explicitly opted-in graph-rewrite migration path: one-to-one mappings for every active node, unchanged node types, preserved historical executions, and durable mapping evidence.
- [x] Bind task nodes exclusively to universal worker runs in migrated definitions; the dry-run compatibility audit, dashboard proxy/store method, and `POST /flows/{flow_id}/migrate-legacy-tasks` now require explicit worker UUID mappings, create an immutable version, preserve the source, and record alias-removal evidence. Live worker canary/recovery remains open.
- [x] Add six reusable software-delivery, research, hiring, incident, integration-rollout, and self-improvement templates validated by the canonical flow engine and exposed through discovery/create-from-template APIs.
- [x] Replace the new-flow dashboard's duplicated starter definitions with the canonical `/flow-templates` catalogue, preserving template configs, evidence metadata, and remapped branch references; retain a blank-canvas fallback for catalogue outages.
- [x] Publish built-in evidence-policy metadata, evaluate required artifact kinds, and include worker-run/repository resources in operator dry-run validation.
- [x] Configure evidence policies by company/project/flow/milestone and persist the selected policy at each scope; company-manifest defaults, project defaults/milestone overrides, flow metadata resolution, and core checks are implemented. `resolve_evidence_policy_selection` and `check_evidence_policy_resolution.py` now make the precedence contract deterministic and release-checkable without using licence metadata as a gate.
- [x] Feed first terminal issue completion observations into durable agent profiles exactly once and persist a sprint-level retrospective KPI snapshot with source/profile lineage through the existing project KPI surface.
- Complete live parallel/join, switch, escalation, timeout, cancel, watchdog, cold-crash, and safe retry proofs.
- [x] Add `scripts/check_flow_instance_recovery.py` for read-only flow-instance
  status/execution-history evidence and explicit-confirmation action checks;
  full project, worker canary, UI, and live failure/recovery proofs remain.
- [x] Complete sprint-level retrospective aggregation and profile-lineage evidence after the first terminal-issue learning hook; live transition/recovery proof remains.
- [x] Add the consolidated `aiat.project-evidence-package.v1` project API/dashboard read model, deterministic fixture, and operator-only durable snapshot path covering repository, documents, tests, security, deployment, cost, approvals, flow, worker, artifact, and audit evidence; the deterministic core/resolver/fixture batch is reviewed and committed as `a44a1aa`, package-level workflow exports are isolated in `d0472af`, the isolated API/snapshot/policy route group is committed in `cbf00d9`, and bounded dashboard evidence/proxy surfaces are committed in `82bbaeb`; project-page composition and live provider/worker artifact generation remain separate.
- [x] Preserve the project evidence package after a failed dashboard refresh, label the retained package as last known, and expose a keyboard-visible Retry action; `project-evidence-states.spec.ts` passes the source-built failure → retained package → successful recovery path 1/1 (`bc80ad5`). Full project-page composition and live provider/worker artifact generation remain separate.
- [x] Make project-detail first loads explicit and retryable: non-2xx project reads now preserve backend error detail, show Project unavailable with Retry instead of the misleading not-found state, and recover into the workspace; `project-detail-states.spec.ts` passes the source-built failure → Retry → recovered detail path 1/1 (`f364763`). Full project-page composition and live provider/worker artifact generation remain separate.
- [x] Preserve the project workspace summary through failed `/workspace` or `/repository` refreshes, keep activity/resources/cost data visible, retain the last repository snapshot across partial failure, and expose stale/first-load Retry states; `project-workspace-states.spec.ts` passes the source-built failure → retained workspace → recovered workspace path 1/1 (`cb1c665`). Live workspace/provider/worker generation remains separate.
- [x] Reconcile declared parallel branches, join fan-in, and switch case targets with persisted flow edges; `aiat.flow-topology-check.v1` covers valid/invalid definitions without worker dispatch or storage mutation. Live fan-out/join synchronization and crash/watchdog recovery remain separate evidence gates.
- [x] Add `aiat.flow-execution-semantics.v1` and focused tests over the real
  traversal engine: parallel fan-out, one-branch join waiting, single join
  scheduling, switch case selection, and unknown-case blocking. Duplicate
  join scheduling and completed-join reactivation are fixed; live fan-out,
  join synchronization, watchdog, crash, and recovery evidence remain open.
- [x] Preserve governed flow tasks while asynchronous Worker Runs are queued or
  running through the shared `aiat.flow-worker-binding.v1` contract; terminal
  settlement is authoritative, parallel node bindings are copy-on-write, safe
  retry re-enters governed dispatch, and unknown run states fail closed. Live
  worker canary/recovery remains open.
- [x] Make flow retry evidence-preserving: prior node executions are marked
  `SUPERSEDED` rather than deleted, including the no-safe-node storage
  fallback; the new retry attempt becomes the only traversal authority, and
  original inputs/outputs/errors/timestamps remain queryable. Native DB and
  live failure/recovery proof remain open.
- [x] Add `aiat.workflow-watchdog-recovery.v1` deterministic evidence for boot
  grace, downtime-aware watchdog timeout, universal failure transition,
  recorded-safe-state retry, and terminal-state exclusion; native watchdog and
  cold-crash recovery proof remain open.
- [x] Exercise the local Compose project/flow UI golden path: the 59-test
  Playwright suite passes 58 tests with one explicit operator-owned DLQ fixture
  skip, including project workspace creation, schema-driven flow editing,
  branching/rejection/retry/timeout recovery, evidence/cost views, hierarchy
  communication-policy/path tracing, retained hiring evaluation details, shell
  skip-link/mobile focus recovery, identity stale-record/retry state, PM
  integration conflict/stale retry, project-detail stale/retry state, and
  system-visualization partial/offline retry states; native UI, live worker
  recovery, and provider-owned paths remain open. Evidence:
  [`dashboard_e2e_live.json`](../../../mas/docs/provenance/dashboard_e2e_live.json).

**Done when:** a full software project can be created, run, paused, failed, recovered, approved, completed, and archived from the UI with complete evidence.

## Workstream 4 — identity and external collaboration

- [x] Persist bounded delivery-attempt trace/span metadata in the identity
  service and project it through the signed client without crossing mail
  content, recipient, provider, or relay metadata into AIAT evidence.
- Select direct or SMTP-gateway production mail profile and complete DNS/TLS/send/receive/bounce/outage/restore certification.
- Rehearse key rotation and domain migration.
- Complete YouTrack mapped-human ACTIVE command certification.
- Complete GitHub App installation, webhook, branch/PR/review/check/commit/run-credential, retry, revoke, and reconciliation certification.
- [x] Publish the `aiat.provider-conformance.v1` fixture runner and shared
  provider failure classifier (pagination/cursor, idempotency,
  archive/deactivation, renamed-field webhook, rate-limit, stale-revision,
  partial-outage, and permission-loss cases); provider-specific mocked HTTP,
  live outage, and restore evidence remains open.
- [x] Expose the deterministic fixture through
  `scripts/check_provider_conformance.py`; `--live` remains explicitly blocked
  until provider-specific sandbox, mocks, outage, and restore evidence exist.
- [x] Reconcile the real YouTrack and GitHub adapter capability declarations
  through `aiat.provider-adapter-declarations.v1`, covering every GitHub
  `pm`/`delivery`/`checks` profile plus bounded repository/ref/identifier
  helpers without provider HTTP calls; provider-specific mocks/live evidence
  remains separate.
- [x] Run `aiat.provider-adapter-http-conformance.v1` against the real
  YouTrack and GitHub adapter methods using local mocked responses for
  health/configuration, projections/read-back, cursors, deactivation,
  comments/links, GitHub source-control paths, webhook handling, and
  retryable/permanent provider failures; live account/outage/restore evidence
  remains separate.
- [x] Publish a versioned external-account action taxonomy and enforce human approval for credential rotation and closure; retain immediate suspension as a safety revocation and keep local browser sessions behind approved-account/short-lived-lease checks. `check_external_account_action_policy.py` now release-checks all five actions, category dispositions, and fail-closed unknown inputs without using licence metadata.
- [x] Drive the actual `IdentityService` through the deterministic
  `aiat.external-account-lifecycle.v1` in-memory fixture: category approval,
  signup idempotency, one-use browser leases, rotation/session revocation,
  immediate suspension, closure approval/revocation, and secret-safe output
  pass without an external account or provider call; live browser/provider and
  outage/restore evidence remains separate.
- [x] Drive the actual `IdentityService` through the deterministic
  `aiat.outbound-mail-lifecycle.v1` in-memory fixture: approval pause,
  request/submission idempotency, definitive provider-failure retry,
  ambiguous-outage reconciliation hold, and secret-safe output pass without
  an external relay call; live send/receive/bounce/outage/restore evidence
  remains separate.
- Complete provider-specific external-account conformance, outage, and restore evidence.

**Done when:** a project can collaborate through certified mail, PM, and GitHub paths without provider secrets in workers or ambiguity about canonical state.

## Workstream 5 — model, cost, and executive operation

- [x] Export and reconcile the deterministic `aiat.model-profile-catalogue.v1` runtime/profile catalogue with versioned provider/model/capability/price/privacy/region data; local live evidence now observes 92 approved covered versions out of 94 persisted versions, with one pending registered model and two non-registered profile findings retained for reconciliation.
- [x] Add a fail-closed `scripts/check_model_profile_catalogue.py --live` verifier and an explicit `--require-approved` mode; missing API configuration, unavailable API, malformed responses, and zero approved persisted coverage return exit code 2 rather than a false pass.
- [x] Add an idempotent startup/default-seed bootstrap for the checked-in
  `opencode-phase0b-coding` profile, every registered model identity, and the
  current `omniroute-coding` gateway alias; it preserves operator rows and
  blocks conflicting declarations. Live database/provider health, outage,
  and recovery evidence remain open.
- [x] Add the deterministic `aiat.executive-reconciliation.v1` report, `aiat.executive-views.v1` role projections, dedicated read-only `/executive/views/{role}` endpoints, and System Overview surface for durable spend, delivery, portfolio, budget, and model-coverage evidence.
- [x] Add `scripts/check_executive_reconciliation.py --live --json` as a
  secret-safe API/DB reconciliation verifier with optional `--require-clean`;
  missing live API/DB evidence blocks and the helper reports bounded counts,
  not a replacement accounting authority.
- [x] Add role-scoped `aiat.executive-action.v1` write envelopes: CFO `/executive/actions/cfo/model-overrides` creates the existing durable override request, CTO `/executive/actions/cto/worker-runs` delegates to canonical governed worker dispatch, and CEO `/executive/actions/ceo/privileged-actions` delegates to the audited privileged gate. The dashboard has operator-authenticated proxies and a typed confirmation panel for all three; local profile evidence is partial (92/94 approved covered with one pending and two non-registered findings), while provider-specific live recovery, broader governance forms, and chaos evidence remain open.
- [x] Reconcile budget reservation sums and settlement invariants in the executive report (duplicate idempotency keys, terminal-run leftovers, unknown states, negative amounts, and ledger drift); broader reservation/settlement chaos remains open.
- [x] Prove deterministic constraint intersection/no-candidate denial, keep the LLM gateway fallback vocabulary explicit for transient provider failures versus permanent client failures, and persist bounded model/provider cooldown state that automatic fallback honors; provider-specific live failover and exact usage-settlement chaos evidence remains open.
- [x] Complete bounded CFO/CTO/CEO cost and delivery role views over the reconciled report, including dedicated read-only role endpoints and the role-scoped write endpoints above; live provider/recovery evidence remains.
- [x] Make deterministic API-owned CEO chat actions cite canonical evidence through
  `aiat.ceo-evidence.v1`; the envelope is secret-safe, returned by `/ceo/message`,
  streamed with the response, rendered in the dashboard, and linked to encoded
  project/flow/governance/worker/credential/integration/tool/project-evidence/log
  sections where routes exist; cross-surface IDs remain payload-free.
- [x] Extend CEO evidence to deterministic read responses and the legacy model
  fallback. Read responses cite bounded known record lists; fallback output accepts
  only explicit `AIAT_EVIDENCE: kind=id` markers, strips them from prose, and marks
  the envelope `unverified`. The dashboard adds a dedicated
  `/evidence/{kind}/{id}` record route alongside owning-section links.
- [x] Add focused override-expiry normalization/fail-closed coverage and prove a terminal budget settlement replay is a no-op; broader reservation/settlement chaos remains open.

**Done when:** every model call is attributable to an approved resolution snapshot and settled budget, and executive views reconcile with the ledger.

## Workstream 6 — complete operator UX

- [x] Establish the mobile shell accessibility baseline: semantic header/navigation/main landmarks, a keyboard-visible skip link, 44px menu target, focus transfer/restoration, Escape recovery, and an exposed interactive backdrop close action are covered by `e2e/dashboard-shell-accessibility.spec.ts` (2/2 focused tests pass). This does not close the full WCAG, theme, native-Linux, or page-level visual gates.
- [x] Harden the unauthenticated operator sign-in baseline with a named main/operator-sign-in structure, explicit busy/status announcements, labeled credential fields, password-visibility state, and 44px password/sign-in targets; `e2e/login-accessibility.spec.ts` passes the focused landmark, status, autocomplete, toggle, and target assertions 1/1 against the source-built dashboard (`d928834`). Full WCAG/native-Linux visual certification remains separate.
- [x] Preserve identity-resource last-known records on refresh failure, label the stale state, abort obsolete refreshes, expose semantic table/action controls with 44px targets, and expose a retry action; the shared page also exposes named main/status/metadata/table regions, explicit busy state, and decorative-icon suppression. `e2e/identity-states.spec.ts` passes the authenticated failure → retained rows → successful recovery and accessibility assertions 1/1 without rendering sensitive fields (`46eccee`, `651ad11`, `a260e04`). Provider/live identity evidence remains separate.
- [x] Preserve the Hiring Board worker catalogue on refresh failure, label the last-known workers, keep rows visible while retrying, and expose Retry; `e2e/workers-states.spec.ts` passes the source-built failure/recovery path 1/1. Native/live worker certification evidence remains separate (`7541b84`).
- [x] Harden the Hiring Board accessibility baseline with a named main/table/filter structure, integration/runtime/policy regions, captioned/scoped worker table semantics, keyboard-expandable rows, associated registration-dialog fields, and 44px refresh/register/filter/selection/row-action/dialog targets; `e2e/workers-states.spec.ts` covers the focused semantics and target sizes 1/1 (`826b4c5`). This remains a focused page-level baseline, not the full WCAG/native-Linux audit.
- [x] Preserve the CEO Live Feed history/messages on reconnect or history refresh failure, label the last-known feed, guard obsolete connections, and expose Reconnect plus Retry without altering the governed composer; `e2e/ceo-states.spec.ts` passes the source-built failure/recovery path 1/1. Native/live Redis/router evidence remains separate (`1761429`).
- [x] Harden the CEO Live Feed accessibility baseline with named main/composer/summary/filter/feed/status regions, 44px stream/composer/filter/recovery targets, and a busy feed state while preserving keyboard-expandable messages; `e2e/ceo-states.spec.ts` covers the focused semantics and target sizes 1/1 (`1f947a9`). This remains a focused page-level baseline, not the full WCAG/native-Linux audit.
- [x] Preserve the CEO Command Center chat transcript through live-stream or history failures, keep history visible as last-known data, prevent late history responses from hiding stream errors, and expose a keyboard-visible Retry action; `e2e/ceo-chat-states.spec.ts` passes the source-built failure/recovery path 1/1. Native/live Redis/router evidence remains separate (`beabb95`).
- [x] Harden the CEO Command Center chat accessibility baseline with a named main/workspace/transcript/composer structure, a live transcript log with busy state, 44px navigation/composer/quick-command/recovery targets, explicit chat guidance regions, and a mobile-safe accessible activity link; `e2e/ceo-chat-states.spec.ts` covers the focused semantics and target sizes 1/1 (`8ffb5df`). This remains a focused page-level baseline, not the full WCAG/native-Linux audit.
- [x] Harden the Governance accessibility baseline with a named main/read-surface structure, explicit executive/model-profile/WorkerRun/steward/catalogue regions, a captioned/scoped WorkerRun table, accessible catalogue status, and 44px refresh, retry, executive-form, and confirmation controls; `e2e/governance-states.spec.ts` covers the focused semantics and target sizes 1/1 (`f4ae7eb`). This remains a focused page-level baseline, not the full WCAG/native-Linux audit.
- [x] Preserve the combined governance model-profile/catalogue/WorkerRun/steward read surface on refresh failure, label it as the last known governance state, and expose header Refresh plus banner Retry controls; `e2e/governance-states.spec.ts` passes the source-built failure/recovery path 1/1. Provider/live governance evidence remains separate (`52de581`).
- [x] Preserve the canonical System Control runtime status on refresh failure, label it as the last known system status, and expose a retry action without hiding the retained status; `e2e/system-status-states.spec.ts` passes the source-built failure/recovery path 1/1. Native/live runtime evidence remains separate (`f445c17`).
- [x] Harden the System Control accessibility baseline with a named main/loading state, explicit runtime-status/schedule/control/dialog regions, scheduled-event semantics, and 44px refresh, retry, shutdown/resume, schedule-input/save, and confirmation controls; `e2e/system-status-states.spec.ts` covers the focused semantics and target sizes 1/1 (`543f392`). This remains a focused page-level baseline, not the full WCAG/native-Linux audit.
- [x] Preserve the paired Projects list project/active-flow read on refresh failure, label the last known list, keep create/action errors separate, and expose a retry action; `e2e/projects-states.spec.ts` passes the source-built failure/recovery path 1/1. Native/live project evidence remains separate (`d3482ab`).
- [x] Harden the Projects list accessibility baseline with a table caption, scoped column headers, explicit description disclosure, responsive overflow, and 44px selection/filter/sort/link/action targets; `e2e/projects-states.spec.ts` covers the focused semantics and target sizes 1/1 (`7828b48`). This remains a focused page-level baseline, not the full WCAG/native-Linux audit.
- [x] Preserve the Project evidence package on refresh failure, label it as showing last-known evidence, keep the package visible while retrying, and expose a keyboard-visible Retry action; `e2e/project-evidence-states.spec.ts` passes the source-built failure/recovery path 1/1. Full project-page composition and live provider/worker generation remain separate (`bc80ad5`).
- [x] Harden the Project evidence package accessibility baseline with named main/package sections, labeled 44px back/refresh actions, and a captioned evidence table with scoped column headers; `e2e/project-evidence-states.spec.ts` covers the focused semantics and target sizes 1/1 (`89091c1`). This remains a focused page-level baseline, not the full WCAG/native-Linux audit.
- [x] Make project-detail first-load API failures explicit and retryable, preserving the backend detail and recovering into the workspace; `e2e/project-detail-states.spec.ts` passes the source-built failure/recovery path 1/1. Existing-project stale/retry recovery remains covered by the workspace golden path (`f364763`).
- [x] Preserve the project workspace read model after failed workspace/repository refreshes, label retained data, preserve the last repository snapshot across partial failure, and expose Retry; `e2e/project-workspace-states.spec.ts` passes the source-built stale/recovery path 1/1 (`cb1c665`).
- [x] Harden the project workspace Activity/Resources/Cost nested tabs with semantic tab/tabpanel relationships, roving `tabIndex`, Arrow/Home/End keyboard navigation, and 44px targets; the source-built `e2e/project-workspace-states.spec.ts` covers keyboard tab recovery 1/1 (`fcb0f4b`). This is a focused accessibility baseline, not the full WCAG/native-Linux audit.
- [x] Harden the Project Detail page accessibility baseline with named page/loading landmarks, explicit project status, 44px refresh/retry/back and primary project-view tab targets, semantic project/workspace tab-panel relationships, and an `aria-busy` workspace panel; `e2e/project-detail-states.spec.ts` covers the focused semantics and target sizes 1/1 (`40b87dd`). This remains a focused page-level baseline, not the full WCAG/native-Linux audit.
- [x] Harden the dedicated evidence-detail page accessibility baseline with a named page/canonical-citation region, a semantic bounded-detail region with an explicit `aria-busy` refresh state, decorative-icon suppression, and 44px CEO-chat/canonical-link/Refresh targets; the existing `e2e/dashboard-evidence-detail.spec.ts` covers these semantics and target sizes 9/9 (`32f3a76`). This remains a focused page-level baseline, not the full WCAG/native-Linux audit.
- [x] Preserve the Tools catalogue definitions and circuit-breaker summaries on refresh failure, label the last known catalogue, keep the catalogue visible while retrying, and expose header Refresh plus banner Retry controls; `e2e/tools-states.spec.ts` passes the source-built failure/recovery path 1/1. Native/live tool evidence remains separate (`5f4b0eb`).
- [x] Harden the Tools catalogue accessibility baseline with named main/search/group regions, captioned/scoped group tables, keyboard-visible tool expansion, and 44px refresh/group/search/copy/retry/empty-state targets; `e2e/tools-states.spec.ts` covers the focused semantics and target sizes 1/1 (`83e39e6`). This remains a focused page-level baseline, not the full WCAG/native-Linux audit.
- [x] Preserve dead-letter queue messages and replay context on refresh failure, label the last known queue, keep cards visible while retrying, and expose header Refresh plus banner Retry controls; `e2e/dlq-states.spec.ts` passes the source-built failure/recovery path 1/1. Native/live DLQ evidence remains separate (`823fa6d`).
- [x] Harden the dead-letter queue accessibility baseline with named main/summary/filter/list/disclosure regions, `aria-pressed` severity filters, keyboard-visible envelope inspection, and 44px refresh/retry/selection/replay/filter/inspection targets; `e2e/dlq-states.spec.ts` covers the focused semantics and target sizes 1/1 (`99a19a2`). This remains a focused page-level baseline, not the full WCAG/native-Linux audit.
- [x] Preserve redacted credential metadata (names, placeholders, policy, usage) on refresh failure, label the last known credentials list, keep rows visible while retrying, and expose header Refresh plus banner Retry controls without rendering secret values; `e2e/credentials-states.spec.ts` passes the source-built failure/recovery path 1/1. The render-safe loading/error repair is committed in `e6e6980`; native/live credential expiry/revocation evidence remains separate (`970f09c`).
- [x] Harden the Credentials accessibility baseline with named main/security/data regions, a captioned/scoped credentials table, a labeled creation dialog with explicit field associations, and 44px refresh/audit/selection/copy/delete/dialog targets; `e2e/credentials-states.spec.ts` covers the focused semantics and target sizes 1/1 (`93fdfbc`). This remains a focused page-level baseline, not the full WCAG/native-Linux audit.
- [x] Preserve successful Metrics query series on partial Prometheus refresh failure, label the surface as stale, keep charts and the last-successful timestamp honest, and expose header Refresh plus banner Retry controls; `e2e/metrics-states.spec.ts` passes the source-built partial-failure/recovery path 1/1. Native/live telemetry evidence remains separate (`85596b0`).
- [x] Harden the Metrics accessibility baseline with named main/summary/chart regions, a semantic time-range control, and 44px range/refresh/retry/empty-state targets; `e2e/metrics-states.spec.ts` covers the focused semantics and target sizes 1/1 (`da113af`). This remains a focused page-level baseline, not the full WCAG/native-Linux audit.
- [x] Preserve Flows list definitions on refresh failure, label the last known definitions, keep rows visible while retrying, and expose header Refresh plus banner Retry controls; `e2e/flows-states.spec.ts` passes the source-built failure/recovery path 1/1. Native/live flow evidence remains separate (`a0faf5b`).
- [x] Harden the Flows list accessibility baseline with an accessible table name/caption, scoped column headers, responsive overflow, and 44px refresh/create/search/filter/selection/link/delete targets; `e2e/flows-states.spec.ts` covers the focused semantics and target sizes 1/1 (`6b0413b`). This remains a focused page-level baseline, not the full WCAG/native-Linux audit.
- [x] Harden flow-editor loading and recovery: route-selected canonical fetches now expose an explicit first-load unavailable state with Retry, retain the last-known flow/canvas after a failed refresh, and provide Refresh plus Retry recovery; `e2e/flow-editor-states.spec.ts` passes the source-built failure/recovery path 1/1. Full live flow/runtime recovery remains separate (`b5098e7`).
- [x] Harden the flow-editor accessibility baseline with semantic header/main/palette/canvas/config landmarks, 44px toolbar/palette/config targets, and 44px generated schema-form controls; `e2e/flow-editor-states.spec.ts` covers the focused semantics and target sizes 1/1 (`140af1c`). This remains a focused page-level baseline, not the full WCAG/native-Linux audit.
- [x] Preserve the Container Logs buffer when an SSE reload emits an error, label the last known lines, keep them visible while retrying, and expose a Retry action; `e2e/logs-states.spec.ts` passes the source-built failure/recovery path 1/1. Native/live container log evidence remains separate (`280d363`).
- [x] Harden the Container Logs accessibility baseline with named main/filter/legend/output/status regions, 44px stream/filter/recovery targets, and an `aria-busy` log output; `e2e/logs-states.spec.ts` covers the focused semantics and target sizes 1/1 (`993b1cb`). This remains a focused page-level baseline, not the full WCAG/native-Linux audit.
- [x] Preserve Agent Stream history/messages when reconnect or history refresh fails, label the last known stream data, guard obsolete team connections, and expose Reconnect plus Retry controls; `e2e/streams-states.spec.ts` passes the source-built failure/recovery path 1/1. Native/live Redis/router stream evidence remains separate (`3e8a0ea`).
- [x] Harden the Agent Streams accessibility baseline with a named main/filter/feed/status structure, a captioned message table, keyboard-accessible expandable rows, 44px stream/filter/action targets, and an `aria-busy` feed state; `e2e/streams-states.spec.ts` covers the focused semantics and target sizes 1/1 (`d320383`). This remains a focused page-level baseline, not the full WCAG/native-Linux audit.
- [x] Add explicit stale/partial/offline recovery for system visualisation and PM integrations: independent source failures retain available data, expose a warning and retry action, and preserve open conflicts; the targeted `app-operations.spec.ts` resilience checks pass. Native-Linux and provider-owned evidence remain separate.
- [x] Harden the system-visualisation accessibility baseline with named loading/error/ready landmarks, horizontal visualization tabs and semantic tab/tabpanel links, and 44px breadcrumb, refresh, Mermaid-copy, path-trace, graph/detail, policy, retry, and back-link targets; deterministic fixture-backed `app-operations.spec.ts` coverage passes 1/1 (`ed5e551`). This remains a focused page-level baseline, not the full WCAG/native-Linux audit.
- [x] Harden the PM integrations accessibility baseline with a named busy main landmark, explicit summary/connections/reconciliation/lifecycle regions, labeled lifecycle inputs, and 44px refresh/retry/generation/approval/apply controls; the existing fixture-backed conflict/stale-retry test covers these semantics 1/1 (`bbd6ba3`). This remains a focused page-level baseline, not the full WCAG/native-Linux audit.
- [x] Harden the System Overview accessibility baseline with a named main and hero/status surface, explicit health/overview-metrics/first-run/company-project-state/Quick Links regions, decorative-icon suppression, and 44px graph/Quick Links/seed controls; the source-built first-run test covers these semantics and target sizes 1/1 for both `seeded` and `not_seeded` local deterministic orchestrator fixtures (`c07b4a6`). The live backend was unavailable during this run, so this remains a focused page-level baseline rather than full control-plane/WCAG/native-Linux certification.
- [x] Add explicit System Overview source-status recovery: classify the seven independent control-plane/metrics reads as healthy, partial, or offline, name failed sources without inferring unavailable state, and expose a bounded GET retry; the source-built recovery fixture passes 1/1 for explicit offline and partial-source runs (`50cee61`). Retained stale history and full page/WCAG/native-Linux certification remain separate.
- [x] Make the project flow selector read the active catalogue with `cache: "no-store"` so newly created/versioned flows are selectable immediately; the one-test flow-builder golden path and the aggregate local dashboard matrix pass again.
- [x] Add the dashboard theme preference foundation (`5e3cc13`): persisted `system`/`light`/`dark` selection, no-flash bootstrap, system media changes, light-palette migration tokens, compact mobile control, and reduced-motion defaults; source-built focused Playwright coverage passes 2/2 while full page parity remains open.
- [x] Add the bounded `aiat.evidence-detail.v1` dashboard read model (`8fefc8b`, trace extension `c8505eb`, model/integration expansion and scalar hardening `dc50719`, recovery regression `5357166`, tool catalogue extension `ec8cf67`, artifact/usage authority `2ca5f3d`, stale-refresh retention `6c52552`) for project, flow, flow-instance, worker, worker-run, credential, dead-letter, runtime, trace, model, integration, tool, artifact, and usage citations; the proxy selects matching list/catalogue/manifest records or operator-authenticated artifact/usage reads, allow-lists scalar fields and backend paths, bounds values, strips nested payloads/trace items/metadata/pricing/resource/configuration/profile bindings/tool schemas/credential requirements, retains the last successful scalar projection through failed refreshes, exposes a retry control, and preserves identity-only behavior for unsupported kinds. Source-built focused coverage passes 9/9, including artifact/usage scalar checks and stale-refresh identity recovery; broader stale/offline recovery remains open.
- Finish light/dark/system themes and mobile parity.
- Complete WCAG 2.2 AA audit and remediation.
- Add stale/offline/partial/denied/conflict/rollback designs. System
  visualization, identity tables, PM integrations, project list/detail/workspace, the
  combined governance read surface, System Control, Tools catalogue,
  dead-letter queue, credentials list, Metrics, Project evidence package
  page, and flow editor now expose explicit
  stale/partial/conflict endpoint failures; the Flows list, Container Logs, and
  Agent Streams now expose explicit stale endpoint failures and retryable
  first-load/offline states;
  broader page-specific denial and rollback coverage remains.
- Deep-link statuses to evidence, traces, decisions, and recovery; the CEO
  citation route is implemented, including bounded tool/model/integration/
  artifact/usage/worker-run/runtime/trace links. Resource-specific detail now
  covers all fourteen canonical kinds through scalar-only reads and retains
  safe detail across a failed refresh; governance, System Control, and Projects
  list, Tools catalogue, dead-letter queue, credentials list, and Metrics read-state recovery are also
  covered by focused 1/1 source-built tests; Flows list, Container Logs, and
  Agent Streams, flow editor, and Project evidence package recovery are also
  covered by `flows-states.spec.ts`, `flow-editor-states.spec.ts`,
  `logs-states.spec.ts`, `streams-states.spec.ts`, and
  `project-evidence-states.spec.ts` and `project-workspace-states.spec.ts`;
  the project workspace spec also covers nested tab semantics and keyboard
  recovery;
  broader stale/offline recovery and
  golden-path coverage remain.
- [x] Run the stable local Compose Playwright matrix: 58/59 pass with one
  explicit safe DLQ-fixture skip, including shell skip-link/mobile focus
  recovery, identity stale-record/retry state, PM integration conflict/stale
  retry, project-detail stale/retry state, hierarchy communication-policy/path
  tracing, retained hiring evaluation details, and system-visualization
  partial/offline retry states. The source-built flow-editor recovery path
  also passes 1/1 for first-load unavailable, retained-canvas, stale-label,
  and Retry states; project-detail first-load unavailable and Retry recovery
  also pass 1/1; project-workspace stale/retry retention and recovery pass
  1/1, including project workspace nested tab semantics and keyboard recovery
  (`fcb0f4b`). Native-Linux
  Playwright, broader WCAG, mobile-page parity, and visual regression remain
  open.

## Exit gate

- P0 remains green.
- All default specialist workers have coherent current certification.
- Golden-path project completion and exceptional recovery pass from UI and API.
- Production identity/mail, one PM provider, and GitHub App are certified.
- Model usage and budgets reconcile exactly.
- Accessibility/mobile/golden-path gates pass.
