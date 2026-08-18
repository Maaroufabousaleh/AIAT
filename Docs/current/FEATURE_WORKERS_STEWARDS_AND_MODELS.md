# Workers, Stewards, Tools, and Models Feature Specification

**Baseline:** 2026-08-18
**Status:** universal foundation and metadata-only licence boundary implemented (`cbdcfa6`, with certification/rollout enforcement hardening in `9b84af3`); governed model-profile/cooldown/catalogue/bootstrap group `288996e`, persisted default model-profile bootstrap (`09bdd19`), model-override expiry and terminal-settlement replay hardening (`63b2db5`), worker trace/compatibility evidence persistence (`ceb7011`), catalogue dashboard proxy `ab0a0fe`, executive API/dashboard integration `d1b8839`, bounded runtime benchmark readiness hardening (`4d61279`, extending `ad31793`), LangGraph/CrewAI dependency benchmarks, tracked exact workspace lock (`2b13d89`), exact lock parity, Compose adapter-lifecycle probes, read-only persisted default-worker reconciliation (39/39), explicit team-runner manifest bindings (`d9b1262`) with production startup enforcement/runtime metadata (`569231f`), selected worker-run readiness (`5553b19`), unavailable/malformed health-read hardening (`2eea80`, `dac268c`), selected steward certification readiness (`adc7b26`), Hiring Board stale/retry recovery (`7541b84`, source-built `workers-states.spec.ts` 1/1), worker-registry grant/update-policy hardening (`d8cafbb`, focused API coverage 66/66), deterministic worker↔mail-edge evidence join (`1d8aed5`), durable local Postgres worker-run/trace evidence (`acd3f06`), committed worker-plane host execution (`73c0bda`), concurrent two-host native execution (`f9c717b`), fenced host-loss queue recovery (`893293a`), selected model-resolution host execution plus pre-claim snapshot consistency (`6cef1b8`, `9a7db70`), durable production `GatewayWorkerAdapter` host dispatch (`8ed53df`), pre-terminal model usage attribution enforcement (`199eb5b`), and the governed AIAT model-gateway worker adapter fixture (`080ee18`) plus transport registration (`f6baebc`), lifecycle/input hardening (`cec1e4c`), real client HTTP-boundary/retry fixture (`cbbfe56`), local worker/mail-edge composition certificate (`6ebb12c`), gateway failure classification hardening (`b2ae516`), bounded host-executor/gateway composition (`38c99f4`), host-boundary failure classification (`2abc02a`), and protocol schema/runtime reconciliation (`8f46ed1`) pass in fixture/local-deployment scope; external provider-backed model execution, sandbox certification, and full worker certification remain incomplete

Commit `6ebb12c` adds a real `GatewayWorkerAdapter`/`WorkerRunController`
composition certificate. The bounded local fixture evaluates scalar worker
usage/artifact/native-span projections together with verified delivered and
bounced mail-edge observations, including exact provider/model attribution.
It is retained at
[`gateway_worker_mail_edge_fixture.json`](../../mas/docs/provenance/gateway_worker_mail_edge_fixture.json)
and deliberately does not claim durable provider read-back, external provider
execution, live mail delivery, sandbox, or host certification.
Commit `38c99f4` adds a bounded host-composition certificate. It drives the
real `WorkerHostExecutor`, `WorkerRunController`, and `GatewayWorkerAdapter`
through committed worker-plane admission, queued-run claim, exact fixture
model/usage attribution, terminal settlement, and binding release. The
payload-free report includes native worker/model spans, usage, and a synthetic
bounded artifact pointer without persisting generated content. It is retained
at [`gateway_worker_host_fixture.json`](../../mas/docs/provenance/gateway_worker_host_fixture.json)
and remains in-memory fixture evidence only: no durable host, external
provider, independent host, sandbox, or live recovery claim is made.
The fenced host-loss queue-recovery group `893293a` now extends the local
worker-plane evidence: host-filtered fencing expires only the reserved lost
host, the canonical Worker Run recovery loop requeues the expired claim, a
stale executor is rejected before dispatch, and the binding is reassigned to
an alternate host for a native retry at attempt two. Independent deployed
hosts, external provider-backed dispatch, sandbox, provider, and
provider-backed recovery remain incomplete; selected local model-resolution
propagation is certified below.

**Authority:** [AIAT Target Programme](../../AIAT_TARGET_PROGRAMME.md)

The bounded durable lease/recovery certificate `a413997` passes in local
Compose scope; full worker certification and production multi-host evidence
remain incomplete. The concurrent two-host native certificate `f9c717b` now
proves two separately reserved worker-host identities can claim and complete
runs concurrently with durable evidence and release settlement; it is not
independent-machine, sandbox, provider, or host-loss evidence.
The durable host registry certificate `500fc57` also passes in local Compose
scope for authenticated registration, heartbeat lease renewal, redacted
placement snapshots, and connection-reopen read-back. The durable reservation
certificate `232c0bb` passes idempotent capacity reservation, commit/release,
expiry recovery, and reopen read-back. The scheduler certificate `d9917f8`
also passes deterministic multi-host fallback, replay, blocked capacity, and
reopen read-back. The host-fencing/recovery certificate `72e59ec` now passes
split-brain stale-heartbeat rejection, reservation invalidation on host
replacement, expired-host reconciliation to `OFFLINE`, placement exclusion,
and durable reopen read-back; live worker dispatch, host-pool separation, and
Firecracker remain incomplete. Commit `73c0bda` now adds the first local
host-executor certificate: a committed worker-plane binding is admitted,
claimed, executed through the canonical native adapter lifecycle, released, and
read back after a Postgres connection reopen. This does not claim a deployed
sandbox, provider call, or multi-host runtime. Commits `6cef1b8` and `9a7db70`
now add the selected model-resolution host certificate: an approved
profile/version is
resolved deterministically, persisted as a snapshot, carried through the
worker request and durable run, and attributed in exact provider/model usage
evidence. `8ed53df` now runs that durable certificate through the production
`GatewayWorkerAdapter` over one bounded local gateway double. The
provider/model identifiers are local fixtures; external provider execution,
sandbox, and independent-host recovery remain separate boundaries.

## Purpose

AIAT keeps stable organisational workers while allowing their execution engines to evolve. A specialist is an AIAT shell backed by a certified adapter and pinned OSS runtime. One dedicated steward governs each external worker's documentation, compatibility, candidates, certification, rollout, and rollback.

## Implemented now

- Versioned `aiat.worker.v1` and `aiat.adapter.v1` protocol models and negotiation.
- Normalized requests, capabilities, events, results, errors, artifacts, usage, tool responses, pause/resume/cancel, health, and readiness.
- `WorkerRunController` with durable lifecycle, compare-and-set transitions, evidence persistence, queue leases, heartbeat recovery, and run APIs.
- `scripts/check_worker_lease_recovery_postgres.py --json` certifies the existing
  Postgres queue lease boundary against one reserved fixture: competing claims
  are denied while a lease is live, heartbeats require the claimant, one
  explicitly expired lease is requeued, a second owner reclaims it at attempt
  two, terminal runs cannot be claimed again, and eight transitions survive a
  connection reopen. The report is payload-free and removes only its reserved
  rows; it does not implement or certify a host registry, placement service,
  real host loss/split-brain behavior, gVisor, or Firecracker.
- `scripts/check_worker_version_pinning_postgres.py --json` certifies the
  complete durable in-flight version pin boundary at migration
  `0040_worker_run_skill_bundle_pin`: a `RUNNING` version-one run retains its
  shell, adapter, skill bundle, and steward references after the mutable
  registry advances to version two, and a new queued run reads the complete
  replacement set. Durable run creation snapshots the active bundle while
  holding the worker row lock and rejects a bundle belonging to another
  worker or steward. The report survives a Postgres connection reopen, is
  payload-free, and cleans its reserved version/run graph. Live worker
  dispatch, host-loss recovery, and multi-host execution remain separate.
- `mas_core.worker_registry.placement` and `scripts/check_worker_placement.py`
  define the deterministic `aiat.worker-placement.v1` predicate. It filters
  unready or expired hosts, enforces the worker host plane plus
  labels/capabilities/sandbox/isolation and slot/memory/GPU capacity, chooses
  deterministically by priority and remaining capacity, and fails closed on
  duplicate host IDs. The contract is pure and non-mutating; durable capacity
  reservation/settlement is provided by the host-reservation ledger below,
  and `HostScheduler` connects the registry, placement predicate, and
  row-locked ledger for deterministic multi-host selection/fallback without
  dispatch.
- `mas_core.worker_registry.host_registry.WorkerHostRegistry` and
  `scripts/check_worker_host_registry_postgres.py` now provide the durable
  `aiat.worker-host-registry.v1` boundary. Registration authenticates a host
  with a token digest, heartbeat renews an AIAT-owned lease, public rows redact
  credential material, and placement snapshots survive connection reopen.
  Migration `0041_worker_host_planes` persists an explicit `control`, `tool`,
  `data`, or `worker` plane; worker placement defaults to and fails closed on
  the `worker` plane, so control/tool/data hosts cannot satisfy a worker-run
  request accidentally.
  Capacity reservation/commit/expiry is provided by
  `mas_core.worker_registry.host_reservations.HostCapacityReservationLedger`
  and its Postgres checker; `mas_core.worker_registry.host_scheduler.HostScheduler`
  provides idempotent schedule replay and fallback. The
  `mas_core.worker_registry.host_recovery.HostLeaseRecovery` boundary advances
  a durable host lease generation on re-registration or expired-lease
  reconciliation, expires reservations from the fenced incarnation, and keeps
  stale heartbeats from reviving a replaced host.
- `scripts/check_worker_host_recovery_postgres.py` certifies the
  `aiat.worker-host-recovery.v1` boundary at migration
  `0039_worker_host_fencing`: re-registration advances generation and fences
  the old reservation, expired READY leases become OFFLINE with the next
  generation, stale heartbeats are rejected, placement excludes the recovered
  host, and connection-reopen read-back remains durable. Evidence is retained
  at [`worker_host_recovery_postgres_evidence.json`](../../mas/docs/provenance/worker_host_recovery_postgres_evidence.json).
- `mas_core.worker_registry.host_scheduler.HostScheduler` and
  `scripts/check_worker_host_scheduler_postgres.py` provide the bounded
  `aiat.worker-host-scheduler.v1` integration. The scheduler ranks public host
  snapshots deterministically, retries row-locked reservation failures on the
  next eligible host, replays a globally unique schedule key, filters draining
  or unleased hosts, and reports blocked capacity without dispatch or provider
  calls. The local certificate is retained at
  [`worker_host_scheduler_postgres_evidence.json`](../../mas/docs/provenance/worker_host_scheduler_postgres_evidence.json).
- `mas_core.worker_registry.host_executor.WorkerHostExecutor` and
  `scripts/check_worker_host_execution_postgres.py` provide the
  `aiat.worker-host-execution.v1` edge between committed host assignment and
  the canonical Worker Run controller. Admission requires a committed binding
  and reservation, worker-plane identity, matching host lease generation, a
  READY host, and a currently valid host lease; the host owner claims the
  queued run, delegates to `WorkerRunController`, and releases the binding in
  terminal paths. The local certificate is retained at
  [`worker_host_execution_postgres_evidence.json`](../../mas/docs/provenance/worker_host_execution_postgres_evidence.json).
  It proves native fixture dispatch only; external provider-backed selected-model dispatch,
  deployed gVisor/Firecracker, provider execution, and multi-host recovery
  remain separate.
- `scripts/check_worker_multi_host_execution_postgres.py` extends the host
  executor certificate to two distinct worker-plane host records and two
  concurrent native fixture runs. At migration `0042_worker_run_host_binding`,
  both runs claim and finish, each retains host-generation/current-lease
  equality, both bindings/reservations release, two traces have complete
  payload-free source coverage, and a second Postgres connection reads the
  durable result before scoped cleanup. Evidence is retained at
  [`worker_multi_host_execution_postgres_evidence.json`](../../mas/docs/provenance/worker_multi_host_execution_postgres_evidence.json).
  This closes only the local concurrent native boundary; independent deployed
  hosts, external provider-backed selected-model dispatch, gVisor/Firecracker, provider execution,
  and host-loss recovery remain separate.
- `WorkerRunHostBindingService.reassign_after_host_loss()` and the optional
  host filter on `HostLeaseRecovery.reconcile_expired_hosts()` implement the
  bounded `aiat.worker-run-host-recovery.v1` edge. The local certificate at
  [`worker_host_loss_queue_recovery_postgres_evidence.json`](../../mas/docs/provenance/worker_host_loss_queue_recovery_postgres_evidence.json)
  fences one expired host, requeues one expired Worker Run claim, rejects a
  stale executor, reassigns the queued binding to an alternate worker host,
  completes the native retry at attempt two, and verifies durable evidence
  after reopen and scoped cleanup. Independent deployed hosts, sandbox,
  provider, and provider-backed recovery remain separate.
- `scripts/check_worker_host_model_resolution_postgres.py` certifies the local
  selected-model control-plane edge at migration `0042_worker_run_host_binding`.
  It persists an approved Model Profile/version and deterministic
  `ModelResolutionSnapshot`, carries requested/resolved references through a
  committed worker-host run, dispatches through the production
  `GatewayWorkerAdapter` over one bounded local gateway double, and verifies
  the exact gateway call, `aiat_gateway` mode, provider/model usage attribution,
  and durable evidence after Postgres reopen. Payload-free trace coverage,
  binding release, and scoped cleanup pass; external provider calls,
  provider-backed recovery, gVisor, Firecracker, and independent hosts remain
  open. Evidence is retained at
  [`worker_host_model_resolution_postgres_evidence.json`](../../mas/docs/provenance/worker_host_model_resolution_postgres_evidence.json).
- `WorkerRunController` now validates a successful result against the immutable
  model-resolution snapshot before usage or terminal evidence persistence
  (`199eb5b`). Missing snapshots, incomplete provider/model identity, and
  provider/model mismatches fail closed with bounded error codes/details;
  legacy/native runs without a resolution snapshot remain compatible. Focused
  coverage is retained in
  [`test_worker_model_attribution.py`](../../mas/packages/mas-core/tests/test_worker_model_attribution.py).
- `GatewayWorkerAdapter` and `adapter_for_transport("aiat_gateway")` now
  provide a bounded model-backed worker shell. It requires an exact resolved
  model profile, normalizes prompt/messages and generation limits, routes
  through the AIAT-owned `LLMGatewayClient`, and emits provider/model usage for
  controller attribution. The deterministic checker and evidence
  [`check_gateway_worker_adapter.py`](../../mas/scripts/check_gateway_worker_adapter.py)
  and [`gateway_worker_adapter_fixture.json`](../../mas/docs/provenance/gateway_worker_adapter_fixture.json)
  pass with one in-process gateway call and no external network/provider or
  sandbox execution. Commit `f6baebc` also registers `aiat_gateway` in the
  `WorkerRuntime` manifest contract, builtin runtime catalogue, and static
  reconciliation checker. Commit `cec1e4c` starts/stops an owned gateway
  client, bounds batches to 64 messages and content to 32,000 characters per
  message, and rejects non-finite temperatures. External provider dispatch,
  durable host execution, and hardened sandbox certification remain separate
  gates.
- The real gateway-client HTTP boundary is exercised by
  [`check_gateway_worker_http_fixture.py`](../../mas/scripts/check_gateway_worker_http_fixture.py)
  and [`gateway_worker_http_fixture.json`](../../mas/docs/provenance/gateway_worker_http_fixture.json)
  (`cbbfe56`). An in-process OpenAI-compatible transport proves the AIAT-owned
  endpoint, bearer-secret boundary, one transient retry, bounded payload, and
  controller terminal result with exact provider/model usage. It does not
  claim an external provider call, provider outage recovery, or sandbox
  execution.
- The local composition checker
  [`check_gateway_worker_mail_edge_fixture.py`](../../mas/scripts/check_gateway_worker_mail_edge_fixture.py)
  (`6ebb12c`) runs the real gateway worker adapter and controller, then joins
  bounded scalar worker trace rows with the independent mail-edge evaluator.
  It requires observed worker/integration sources plus verified delivered and
  bounced events and reads exact fixture provider/model usage. The evidence is
  [`gateway_worker_mail_edge_fixture.json`](../../mas/docs/provenance/gateway_worker_mail_edge_fixture.json).
  This is local composition evidence only: it does not certify a durable
  provider callback/read-back, external provider, live worker, sandbox, or
  host runtime.
- `GatewayWorkerAdapter` now classifies dispatch failures before they reach the
  controller: bounded input errors are terminal validation failures, known
  transient gateway statuses (`408`, `409`, `412`, `425`, `429`, and `5xx`) are
  retryable provider failures, and permanent gateway responses are terminal
  provider rejections. Error reports retain only status/cause type metadata and
  never copy provider response text or credentials (`b2ae516`). Focused tests
  cover transient, permanent, and pre-dispatch input paths; live provider
  recovery remains a separate gate.
- `scripts/check_gateway_worker_host_fixture.py` (`38c99f4`) drives the real
  `WorkerHostExecutor`/`WorkerRunController`/`GatewayWorkerAdapter` chain over
  bounded in-memory binding and storage doubles. It proves committed
  worker-plane admission, claim, exact fixture model/usage attribution,
  terminal settlement, binding release, and payload-free scalar trace coverage;
  the artifact row is an explicit synthetic report pointer, not generated
  output. Evidence is retained at
  [`gateway_worker_host_fixture.json`](../../mas/docs/provenance/gateway_worker_host_fixture.json).
  This is not durable host, external provider, independent-host, sandbox, or
  live recovery certification.
- `scripts/check_gateway_worker_host_failure_fixture.py` (`2abc02a`) extends
  the host composition with real transient (`429`) and permanent (`401`)
  gateway failures. Both runs settle `FAILED`, release the committed binding
  and reservation, retain only status/cause metadata, and keep the injected
  provider detail out of the run evidence. The certificate is retained at
  [`gateway_worker_host_failure_fixture.json`](../../mas/docs/provenance/gateway_worker_host_failure_fixture.json).
  It proves local failure semantics only; it does not claim automatic live
  retries, external provider recovery, durable host storage, or sandbox
  behavior.
- `8f46ed1` reconciles the checked-in `aiat.v1` protocol schema's
  `WorkerManifest.transport` enum with the runtime `aiat_gateway` transport
  and updates its provenance hash. The contract checker passes; this is a
  schema-integrity correction and does not add external provider or sandbox
  certification.
- Native, process, HTTP, MCP/runtime adapter patterns plus LangGraph, CrewAI, MAF, Letta, AutoGen, and OpenCode-specific code paths.
- Dedicated steward records, documentation/capability snapshots, immutable skill bundles and adapters, certification, rollout, canary, monitoring, and rollback.
- Versioned model profiles and deterministic intersection of company/worker/project/task/privacy/capability/budget constraints (implementation group `288996e`).
- The LLM gateway uses one explicit transient-status vocabulary (`408`, `409`,
  `412`, `425`, `429`, `500`, `502`, `503`, `504`) across normal, streaming,
  and fallback dispatch; permanent client/credential `4xx` responses are not
  blindly retried. This aligns model failover evidence with the PM provider
  failure classifier while leaving provider-state refresh to the caller.
- Model override expiry accepts durable datetimes and serialized ISO-8601 values,
  fails closed on malformed values, and focused budget-ledger coverage proves a
  terminal settlement replay cannot commit or release a reservation twice.
- The gateway now records transient model and provider-endpoint failures in a
  bounded, persisted cooldown ledger. Automatic fallback filters active
  cooldowns, keeps provider-wide backoff behind a two-failure threshold, and
  probes the earliest-expiring candidate only when every option is cooling.
  Successful requests clear the model/provider state; permanent client and
  credential failures remain audit/metrics data and do not create a hidden
  availability gate.
- `aiat.model-profile-catalogue.v1` now reconciles the runtime model registry
  with persisted Model Profile bindings deterministically. The API and
  Governance dashboard show registered capabilities, approved/profile-pending
  state, stale model/provider bindings, duplicate bindings, and coverage
  counts; registry-only models remain visible as `profile_pending` rather than
  becoming implicit production routes. `scripts/check_model_profile_catalogue.py
  --live` fetches the API-owned report and fails closed when the orchestrator is
  unavailable; `--require-approved` also blocks when no approved persisted
  profile coverage exists. Licence and restriction metadata is outside this
  operational catalogue and cannot fail it.
- The refreshed 2026-08-18 local read-only catalogue evidence observes 93
  registered models, 94 persisted profile versions, and 92 approved covered
  versions. The `/v1/models` route exposes nine models including all five AIAT
  aliases (`auto`, `omniroute-auto`, `omniroute-free`, `omniroute-coding`, and
  `omniroute-smart`). The repeatable
  [`check_model_gateway_readiness.py`](../../mas/scripts/check_model_gateway_readiness.py)
  performs only this route read and retains
  [`model_gateway_readiness_live.json`](../../mas/docs/provenance/model_gateway_readiness_live.json).
  One registered model remains `profile_pending` and two
  persisted rows are not registered; these are explicit operator-visible
  reconciliation findings, not a licence/resource restriction gate. The
  bounded result is retained at
  [`mas/docs/provenance/model_profile_catalogue_live.json`](../../mas/docs/provenance/model_profile_catalogue_live.json),
  with no dispatch or provider call performed.
- The checked-in `opencode-phase0b-coding` profile is now an explicit,
  evidence-referenced bootstrap declaration. Startup and the legacy default
  company seed endpoint persist it idempotently with the current
  `omniroute-coding` LiteLLM alias; the same bootstrap derives one explicit
  profile/version for every registered model identity. Existing operator rows
  are preserved and conflicting rows are reported as blocked rather than
  overwritten. This is a persisted-profile declaration path, not live
  provider-health or outage certification.
- `aiat.executive-reconciliation.v1` now provides a deterministic, read-only
  CFO/CTO/CEO report over durable projects, project usage, worker runs, budget
  states/reservations, and model-profile coverage. The orchestrator endpoint
  and System Overview dashboard summarize spend, delivery success, portfolio
  activity, budget availability, and findings without creating a second
  authority or treating metadata-only licence fields as a gate.
- Its `aiat.executive-views.v1` projections now provide bounded CFO, CTO, and
  CEO role cards from that same report: budget/settlement posture, delivery and
  model coverage, and portfolio/finding posture. They are read-only views, not
  separate budget, delivery, or approval authorities. Dedicated
  `GET /executive/views/{role}` projections now expose one role view over that
  same canonical report for clients that do not need the full aggregate.
- Its budget section also checks reservation sums against authoritative budget
  usage and reports duplicate idempotency keys, unknown states, reservations
  left behind by terminal runs, negative amounts, and ledger drift as explicit
  findings. It remains read-only; settlement ownership stays in the durable
  storage service.
- `scripts/check_executive_reconciliation.py --live --json` fetches the
  canonical read-only reconciliation endpoint and emits bounded coverage and
  finding counts only. `--require-clean` fails on findings; absent API/DB
  evidence blocks and the check never replaces the underlying ledger.
- Central tool registry with explicit grants, aliases, policy, rate/concurrency limits, circuit breakers, audit, cache, and usage records.
- Worker registration and partial updates constrain `update_policy` to the four
  manifest values (`manual`, `auto-patch`, `auto-minor`, `auto-all`). When
  capability IDs or team context changes, persisted capability `required_tools`
  are rechecked against the canonical tool manifest and role/team policy before
  storage mutation. Unknown policy values return `422`; forbidden direct or
  persisted grants return `403` before the worker is changed. This boundary is
  covered by 66 focused API tests in `test_workers_test4_config.py`, with
  adjacent capability, lifecycle, and policy suites passing; licence metadata
  remains informational only.
- 39 non-placeholder worker manifests and two non-seeded placeholders.
- All 39 team-runner agent declarations now carry an explicit
  `worker_manifest_ref` equal to their checked-in worker ID. The read-only
  `aiat.team-worker-manifest-reconciliation.v1` checker verifies all 11 team
  files and 39 agent bindings without inferring missing references or
  registering/activating workers; runtime registration remains a separate
  integration step.
- Production team-runner startup now reuses that reconciliation against its
  mounted worker directory before instantiating agents (`569231f`). The
  explicit reference is retained in `AgentConfig` and health metadata; missing
  or mismatched declarations fail closed without registering or activating a
  worker.
- OpenCode 1.17.13 live-interface evidence and an approved Phase 0B report.
- `scripts/check_worker_reconciliation.py` statically reconciles all 39 worker
  manifests with the runtime catalogue, company manifest, Compose/OpenCode
  service, provenance inventory, and metadata-only notices. Its read-only
  `--live` mode additionally compares persisted `/capabilities/workers` rows
  with adapter, sandbox, model, source-pin, capability, and active immutable
  record bindings. The authenticated local Compose run now matches all 39
  defaults with zero missing rows or binding mismatches; the secret-safe result
  is retained at [`worker_reconciliation_live.json`](../../mas/docs/provenance/worker_reconciliation_live.json).
  Its pending list is evidence inventory, and its environment-dependent package
  availability field is advisory rather than a certification result.
- The Hiring Board now retains the last successful worker catalogue through a
  failed refresh, labels the view as showing last-known workers, keeps rows
  visible while retrying, and provides a Retry action. First-load failures show
  an explicit unavailable state instead of an empty registry claim. Focused
  source-built coverage passes 1/1 in
  [`workers-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/workers-states.spec.ts)
  against the dashboard build (`7541b84`); native/live worker certification
  remains a separate gate.
- `scripts/check_default_worker_bindings.py --json` reconciles the 15 documented
  default worker slots with their department, runtime tier, transport,
  isolation mode, runtime-catalogue transport/isolation support, capability
  namespace, runtime/integration adapter entrypoints, adapter configuration, and
  required tools. Its deterministic report protects implementation/documentation
  coherence; installed runtimes, provider conformance, security, canary,
  live-run, rollback, and certification evidence remain separate.
- `scripts/check_worker_run_lifecycle.py --json` drives the real
  `WorkerRunController` and `NativeWorkerAdapter` through checkpoint
  persistence, pause/resume with checkpoint reference, cold cancellation,
  cold crash failure normalization, lease-expiry requeue, and
  artifact/usage-before-terminal ordering. It is a
  deterministic in-memory fixture: database, sandbox, live worker, canary,
  and rollback certification remain explicit boundaries. `--live` is
  fail-closed and makes no mutation.
- `scripts/check_worker_run_postgres_evidence.py --json` drives the same real
  controller and native adapter against the local Compose Postgres store. It
  registers one reserved fixture worker, persists a successful run with
  artifact/usage evidence and a payload-free worker trace span, closes the
  first connection, reads the run and trace sources back through a second
  connection, and removes only its reserved rows. The retained certificate is
  [`worker_run_postgres_evidence.json`](../../mas/docs/provenance/worker_run_postgres_evidence.json)
  (`acd3f06`). This proves local durable lifecycle/evidence wiring; it does
  not select or activate a real worker, call an external model/provider, or
  close live sandbox, canary, rollback, retention, or outage gates.
- `evaluate_worker_mail_edge_coverage()` and
  `scripts/check_worker_mail_edge_coverage.py --json --require-integration`
  compose the worker source evaluator with the payload-free mail-edge
  evaluator under `aiat.worker-mail-edge-coverage.v1`. The joined certificate
  requires worker usage/artifact/model/worker sources, optional integration
  sources when requested, an explicitly scoped worker and trace, a verified
  provider webhook, and a bounce/failure signal. It returns only counts and
  missing-signal names; the deterministic certificate is retained at
  [`worker_mail_edge_coverage_fixture.json`](../../mas/docs/provenance/worker_mail_edge_coverage_fixture.json)
  (`1d8aed5`). It does not select, activate, dispatch, or certify a live
  worker, and external provider delivery remains a separate gate.
- `scripts/check_worker_run_readiness.py` and
  `worker_registry/worker_run_readiness.py` provide a read-only, fail-closed
  preflight for one explicitly selected model-backed worker and project. The
  evaluator reconciles worker lifecycle status, immutable shell/adapter/skill
  pointers, source/version and evaluation state, project/company state, active
  company assignment, approved model-profile version, bounded concurrent/cost
  budget headroom, sandbox declaration, and health metadata. Fixture mode
  passes with a complete snapshot; live mode never auto-selects, activates,
  provisions identity, reserves a budget, dispatches a run, or reads task or
  provider payloads. Identity, provider, sandbox runtime, retention,
  canary, and rollback are reported as separate `not_checked` boundaries.
  The readiness hardening groups `2eea80a` and `dac268c` now convert an
  unavailable or malformed selected-worker health response, including a
  successful response with no usable `health_status`, into a stable
  `read_worker_health_unavailable` blocker instead of silently treating health
  as `not_checked`; fixture and live-read paths remain non-mutating.
  The current local read-only selection is blocked because all persisted
  workers are inactive, the selected project is terminal, the selected worker
  has no active immutable pointers, and it has no company assignment. Licence
  metadata remains informational only.
- `scripts/check_worker_steward_readiness.py` and
  `worker_registry/worker_steward_readiness.py` provide the next bounded gate
  for one explicitly selected external worker and candidate. The evaluator
  checks dedicated-steward readiness, immutable source/version provenance,
  passed technical security evidence, candidate stage, skill-bundle and
  adapter bindings, documentation/capability snapshots, and any supplied
  compatibility result. Fixture mode passes; the authenticated local live
  selection is blocked because the steward is `PROVISIONING`, its scan is
  `pending`, and no candidate exists. It never generates or certifies a
  candidate, approves, activates, rolls out, provisions identity, or invokes a
  provider; licence metadata remains informational only.
- `scripts/generate_worker_certification_matrix.py` (worker readiness group
  committed as `4c5fd68`) generates the deterministic
  39-worker declaration/evidence matrix at
  [`docs/provenance/worker_certification_matrix.yaml`](../../mas/docs/provenance/worker_certification_matrix.yaml).
  The matrix never claims live certification: it records exact runtime imports,
  transports, adapter versions, security-evidence state, and the next required
  evidence disposition. Its regression contract is covered by
  [`test_worker_certification_matrix.py`](../../mas/packages/mas-core/tests/test_worker_certification_matrix.py)
  (`a62ddb7`), which checks deterministic output, exact 39-manifest coverage,
  and metadata-only licence handling.
- `scripts/check_worker_runtime_readiness.py` provides a static declaration
  report and an explicit `--live` import probe for every runtime tier used by
  the 39 manifests. The aggregate release profile uses
  `--live --json --compose-local` to probe the running orchestrator image over
  Docker exec; LangGraph/CrewAI imports now pass in that image, while missing
  required packages still return `blocked`/exit 2. The report explicitly
  leaves security scans, sandbox proof, canaries, live runs, and rollback
  outside the package-availability check.
- `scripts/check_runtime_install_profile.py` reconciles the default
  LangGraph/CrewAI extra in `apps/orchestrator-api/pyproject.toml`, the locked
  versions in `uv.lock`, the runtime catalogue imports, and the production
  orchestrator Dockerfile install command. It proves reproducible packaging;
  it does not claim imports, worker certification, or live execution.
- `scripts/check_sandbox_runtime_readiness.py` reconciles all 39 sandbox
  declarations, requires hardened profiles for external workers, and provides
  a fail-closed `--live` Docker runtime-registration probe. It requires
  `runsc`, never falls back to `runc`, and keeps optional digest-pinned smoke,
  network-denial, canary, and rollback evidence separate.
- `scripts/check_runtime_benchmarks.py --live --json` exercises the existing
  orchestrator runtime catalogue and dependency-backed benchmark endpoints for
  selected runtimes (LangGraph/CrewAI by default). It sends deterministic,
  side-effect-free validation configurations, runs third-party imports off the
  API event loop, and returns a bounded `benchmark_timeout`/`benchmark_error`
  result instead of hanging or claiming evidence. The 2026-08-11 local live
  report passed both LangGraph and CrewAI (each runtime `tasks_run=1`,
  `tasks_passed=1`; two tasks in aggregate);
  the retained report is
  [`runtime_benchmarks_live.json`](../../mas/docs/provenance/runtime_benchmarks_live.json).
  This remains package benchmark evidence only, not a worker canary, live
  project run, sandbox proof, or rollback result.
- `scripts/check_runtime_adapter_conformance.py --live --json` now runs the
  actual `LangGraphAdapter` and `CrewAIAdapter` classes in the Compose
  orchestrator image. LangGraph `0.6.11` and CrewAI `1.6.1` are importable and
  match the declared lock; both adapters pass manifest/message translation,
  bounded fixture completion, health, and shutdown checks without
  model/tool/provider/project calls. The retained report is
  [`runtime_adapter_conformance_live.json`](../../mas/docs/provenance/runtime_adapter_conformance_live.json).
  This is framework-package, exact-lock, and adapter-lifecycle evidence only;
  sandbox, canary, live-run, and rollback certification remain separate.
- `scripts/check_worker_steward_contract.py --json` runs the real steward
  domain through dedicated-steward creation, immutable candidate generation,
  compatibility-matrix recording, certification, approval, shadow/read-only
  canary promotion, regression blocking, and pre-activation rollback for each
  externally sourced default worker. The rollback assertion preserves the
  previously active immutable pointers when a replacement is rejected before
  activation. The retained report is
  [`worker_steward_contract.json`](../../mas/docs/provenance/worker_steward_contract.json).
  It is deterministic domain evidence only; database persistence, security,
  sandbox, live canary, and worker-run evidence remain separate.
- The API rehydration path restores the durable active skill-bundle and adapter
  IDs into the in-memory steward before accepting another rollout. Unknown
  non-null pointer IDs fail closed, so a restart cannot turn a valid active
  worker into an empty rollback target.
- Certification now persists a compatibility-matrix row through
  `AgentStorage.create_compatibility_matrix`, linking runtime, adapter, and
  contract versions plus fixtures, capability/model context, and pass/fail
  status to certification evidence. The same-process steward cache records the
  row immediately, and API restart rehydration restores durable rows into the
  steward-owned compatibility history, normalizing the persisted single-profile
  and structured-capability JSON shapes without dropping evidence. This does
  not replace live certification or database reconciliation.
- The shared `security.scan` adapter now routes `semgrep`, `skillspector`, and
  `trufflehog` aliases through the bounded sandbox boundary. SkillSpector uses
  an optional `TOOL_SKILLSPECTOR_COMMAND` (or its conventional CLI shape),
  reports bounded JSON/line findings, and returns honest unavailable status
  when the scanner or sandbox is absent; no scanner choice is licence-driven.
- The universal `ConformanceRunner` now exercises both framework bridge classes
  (`LangGraphAdapter` and `CrewAIAdapter`) through the same health, readiness,
  acceptance, ordered-event, and normalized-terminal-result contract as native
  workers. This is deterministic adapter-contract evidence only; installed
  runtime, sandbox, canary, and live recovery certification remain separate.
- The Microsoft Agent Framework adapter group (`b937a89`, extending
  `fc528a8`) now has deterministic compatibility coverage for `Agent`/fallback
  construction, async `run`/`invoke` dispatch, explicit chat-client injection,
  response normalization, shutdown, and fail-closed missing-package,
  missing-client, and missing-instructions paths. The locked compatibility
  contract records `agent-framework==1.13.0` with MCP `>=1.27,<2`; the isolated
  profile pins MAF `1.13.0` plus MCP `1.29.0` and passes the real adapter with a
  local fake client. The secret-safe evidence is
  [`maf_runtime_certification.json`](../../mas/docs/provenance/maf_runtime_certification.json).
  The current workspace MCP pin remains `1.23.3`, so default-profile/provider
  activation remains blocked.
- Code review (`fc528a8`, implementation hardening `5b830e9`) now has a reproducible AIAT deterministic diff reviewer as its
  default adapter when no external command is configured. The worker manifest
  points to a versioned adapter catalogue; PR-Agent, open-code-review, and
  stage-cli remain metadata-only external candidates until each receives an
  exact source/revision/version and representative review evidence.
- Security scanner aliases (`fc528a8`, implementation hardening `5b830e9`) now have a deterministic contract fixture: the
  `security.scan` path accepts Semgrep, SkillSpector, and TruffleHog, keeps
  SkillSpector's command configurable through `TOOL_SKILLSPECTOR_COMMAND`, and
  routes all three through the same bounded sandbox/audit boundary.
- `document.ingest` uses the Docling runner when its binary is installed and
  returns an explicit, usable `plain_text_fallback` result when it is not:
  `available` and `configured` stay true, `degraded` is true, and the response
  identifies the missing Docling binary instead of falsely reporting the
  document tool as unavailable. This is fallback behaviour, not Docling
  certification; the external runtime remains an optional extension.
- Tool-service image profiles now separate the general gateway from browser/Docling/Semgrep/Mermaid extensions; the browser dependency is opt-in in the core package and `infra/docker/image-budgets.yaml` records compressed, uncompressed, startup, and memory ceilings for both profiles.

## Code anchors

- Worker contract: [`mas/packages/mas-core/mas_core/worker_contract/`](../../mas/packages/mas-core/mas_core/worker_contract/)
- Protocol model: [`mas/packages/mas-core/mas_core/protocols/worker_contract.py`](../../mas/packages/mas-core/mas_core/protocols/worker_contract.py)
- Worker registry/stewards: [`mas/packages/mas-core/mas_core/worker_registry/`](../../mas/packages/mas-core/mas_core/worker_registry/)
- Runtime catalogue: [`mas/packages/mas-core/mas_core/worker_registry/runtime_catalog.py`](../../mas/packages/mas-core/mas_core/worker_registry/runtime_catalog.py)
- Hiring Board stale/retry state: [`mas/apps/mas-dashboard/app/(dashboard)/workers/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/workers/page.tsx>), [`workers-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/workers-states.spec.ts)
- Worker registry grant/update boundary (`d8cafbb`): [`orchestrator_api/main.py`](../../mas/apps/orchestrator-api/orchestrator_api/main.py), [`test_workers_test4_config.py`](../../mas/apps/orchestrator-api/tests/test_workers_test4_config.py), and the shared tool-policy helper [`tool_access.py`](../../mas/packages/mas-core/mas_core/policy/tool_access.py). Registration and authority-bearing updates fail closed for invalid update policies or forbidden persisted capability grants before storage mutation; licence/restriction metadata is not consulted as a gate.
- Team-runner manifest binding contract (`d9b1262`, runtime binding `569231f`): [`mas/scripts/check_team_worker_manifest_refs.py`](../../mas/scripts/check_team_worker_manifest_refs.py), [`team_manifest_refs.py`](../../mas/packages/mas-core/mas_core/worker_registry/team_manifest_refs.py), [`team_runner/main.py`](../../mas/apps/team-runner/team_runner/main.py), [`agent_runtime/config.py`](../../mas/packages/mas-core/mas_core/agent_runtime/config.py), [`test_team_worker_manifest_refs.py`](../../mas/packages/mas-core/tests/test_team_worker_manifest_refs.py), and [`test_team_config.py`](../../mas/apps/team-runner/tests/test_team_config.py). Static mode reconciles 11 team files/39 agent declarations; production startup repeats the read-only check against mounted manifests and carries the exact reference into agent config/health without registering or activating workers.
- Static and read-only live reconciliation: [`mas/scripts/check_worker_reconciliation.py`](../../mas/scripts/check_worker_reconciliation.py) and [`worker_reconciliation_live.json`](../../mas/docs/provenance/worker_reconciliation_live.json). The default mode validates all 39 declarations; the authenticated local `--live` run (evidence refreshed 2026-08-11 in `180f9e0`) matches 39/39 persisted defaults with zero missing rows or binding mismatches. It compares exact adapter, sandbox, model, source-pin, capability, and active immutable-record bindings and reports missing API/configuration as blocked.
- Default worker implementation binding matrix (`4c5fd68`): [`mas/scripts/check_default_worker_bindings.py`](../../mas/scripts/check_default_worker_bindings.py) and [`test_default_worker_bindings.py`](../../mas/packages/mas-core/tests/test_default_worker_bindings.py). The static contract covers all 15 documented default slots, verifies each declared transport/isolation pair against `RUNTIME_CATALOG`, and requires matching runtime/integration adapter entrypoints; `--live` remains an explicit operator/environment boundary and never mutates runtime state.
- Worker-run lifecycle fixture (`fe6fb8d`): [`mas/scripts/check_worker_run_lifecycle.py`](../../mas/scripts/check_worker_run_lifecycle.py) and [`test_worker_run_lifecycle.py`](../../mas/packages/mas-core/tests/test_worker_run_lifecycle.py). The static fixture checks real controller ordering, failure normalization, and recovery invariants; `--live` reports the explicit operator/database boundary.
- Selected worker-run readiness preflight (`5553b19`, health-read hardening `2eea80a`, empty-payload hardening `dac268c`): [`mas/scripts/check_worker_run_readiness.py`](../../mas/scripts/check_worker_run_readiness.py), [`worker_run_readiness.py`](../../mas/packages/mas-core/mas_core/worker_registry/worker_run_readiness.py), [`test_worker_run_readiness.py`](../../mas/packages/mas-core/tests/test_worker_run_readiness.py), and [`test_check_worker_run_readiness.py`](../../mas/scripts/tests/test_check_worker_run_readiness.py). Fixture mode passes a complete model-backed snapshot; `--live` reads only selected control-plane records and returns stable blockers without activation, identity provisioning, budget reservation, dispatch, or payload access. Health transport/HTTP/shape failures, including a successful response with no usable `health_status`, now produce `read_worker_health_unavailable` rather than an unqualified `not_checked` result. The current local selection is blocked by worker/project/immutable-pointer/assignment state, not by licence metadata.
- Selected steward certification readiness (`adc7b26`): [`mas/scripts/check_worker_steward_readiness.py`](../../mas/scripts/check_worker_steward_readiness.py), [`worker_steward_readiness.py`](../../mas/packages/mas-core/mas_core/worker_registry/worker_steward_readiness.py), and [`test_worker_steward_readiness.py`](../../mas/packages/mas-core/tests/test_worker_steward_readiness.py). Fixture mode passes; authenticated `--live` requires explicit worker/candidate IDs and reads only the worker catalogue plus steward/candidate read models. The current coding-worker selection is blocked by `steward_not_ready`, `security_scan_not_passed`, and `candidate_not_found`; it does not certify or mutate state and never treats licence metadata as a gate.
- Runtime catalogue and manifest reconciliation (`80e0ca3`): [`mas/packages/mas-core/mas_core/worker_registry/runtime_catalog.py`](../../mas/packages/mas-core/mas_core/worker_registry/runtime_catalog.py), [`mas/scripts/check_worker_reconciliation.py`](../../mas/scripts/check_worker_reconciliation.py), and [`test_worker_reconciliation.py`](../../mas/packages/mas-core/tests/test_worker_reconciliation.py). Static mode reconciles all 39 manifests and the read-only live mode compares persisted adapter/model/sandbox/source bindings without treating licence metadata as a gate; package availability, security, sandbox, canary, and live-run evidence remain separate.
- Runtime readiness probe (`4c5fd68`): [`mas/scripts/check_worker_runtime_readiness.py`](../../mas/scripts/check_worker_runtime_readiness.py). Static mode reconciles all 39 manifests; the local Compose image import probe passes required LangGraph/CrewAI imports, while host-package, external-adapter, security, sandbox, canary, and live-run evidence remain separate.
- Runtime install-profile contract (`9a10a4b`): [`mas/scripts/check_runtime_install_profile.py`](../../mas/scripts/check_runtime_install_profile.py). The `runtime-default` extra, lock metadata, runtime catalogue, and production Dockerfile install command reconcile to LangGraph `0.6.11` and CrewAI `1.6.1`.
- Sandbox runtime readiness probe (`a24c554`): [`mas/scripts/check_sandbox_runtime_readiness.py`](../../mas/scripts/check_sandbox_runtime_readiness.py) and [`test_sandbox_runtime_readiness.py`](../../mas/packages/mas-core/tests/test_sandbox_runtime_readiness.py). Static reconciliation passes all 39 manifests with 10 hardened external workers; the current Docker host reports `runsc` unavailable and therefore remains blocked without a `runc` fallback.
- Runtime benchmark probe (`ad31793`, bounded endpoint hardening `4d61279`): [`mas/scripts/check_runtime_benchmarks.py`](../../mas/scripts/check_runtime_benchmarks.py), [`test_runtime_benchmarks.py`](../../mas/packages/mas-core/tests/test_runtime_benchmarks.py), and [`test_epsilon_runtimes.py`](../../mas/apps/orchestrator-api/tests/test_epsilon_runtimes.py). It runs third-party imports off the API event loop, enforces a capped timeout, and reports explicit `blocked`/`benchmark_timeout`/`benchmark_error` status when the API, package, validation, or dependency path is unavailable; a dependency dry-run does not certify a worker canary or rollback.
- Runtime adapter conformance probe (`9a10a4b`): [`mas/scripts/check_runtime_adapter_conformance.py`](../../mas/scripts/check_runtime_adapter_conformance.py) and [`runtime_adapter_conformance_live.json`](../../mas/docs/provenance/runtime_adapter_conformance_live.json). Deterministic LangGraph/CrewAI fixtures pass manifest/message translation, bounded completion, health, and shutdown; package-import and worker-canary evidence remain separate.
- MAF/MCP compatibility contract, isolated profile, and deterministic certification (`b937a89`, extending `fc528a8`): [`mas/docs/provenance/runtime_compatibility.yaml`](../../mas/docs/provenance/runtime_compatibility.yaml), [`mas/infra/runtime/maf/README.md`](../../mas/infra/runtime/maf/README.md), [`mas/infra/runtime/maf/requirements.txt`](../../mas/infra/runtime/maf/requirements.txt), [`mas/packages/mas-core/mas_core/worker_registry/maf_compatibility.py`](../../mas/packages/mas-core/mas_core/worker_registry/maf_compatibility.py), [`mas/packages/mas-core/mas_core/worker_registry/microsoft_agent_framework_adapter.py`](../../mas/packages/mas-core/mas_core/worker_registry/microsoft_agent_framework_adapter.py), [`mas/scripts/check_runtime_compatibility.py`](../../mas/scripts/check_runtime_compatibility.py), [`mas/scripts/check_maf_runtime.py`](../../mas/scripts/check_maf_runtime.py), and [`maf_runtime_certification.json`](../../mas/docs/provenance/maf_runtime_certification.json)
- Code-review adapter catalogue/default (`fc528a8`): [`mas/docs/provenance/code_review_adapters.yaml`](../../mas/docs/provenance/code_review_adapters.yaml), [`mas/scripts/check_code_review_adapters.py`](../../mas/scripts/check_code_review_adapters.py), [`mas/apps/tool-service/tool_service/code_review_runner.py`](../../mas/apps/tool-service/tool_service/code_review_runner.py), and [`CodeReviewTool`](../../mas/apps/tool-service/tool_service/tools/adapters.py)
- Security adapter aliases/fixture (`fc528a8`): [`mas/scripts/check_security_adapters.py`](../../mas/scripts/check_security_adapters.py), [`SecurityScanTool`](../../mas/apps/tool-service/tool_service/tools/adapters.py), and [`ToolRegistry`](../../mas/apps/tool-service/tool_service/registry.py)
- Document ingestion/fallback: [`DocumentIngestTool`](../../mas/apps/tool-service/tool_service/tools/adapters.py), [`test_document_ingest_falls_back_to_text_when_docling_missing`](../../mas/apps/tool-service/tests/test_default_shipped_tool_catalog.py), and the `document.ingest` readiness probe
- Steward lifecycle contract (`c80e339`, fixture coverage `fe6fb8d`): [`mas/scripts/check_worker_steward_contract.py`](../../mas/scripts/check_worker_steward_contract.py) and [`test_worker_steward_contract.py`](../../mas/packages/mas-core/tests/test_worker_steward_contract.py). The deterministic domain exercise covers candidate immutability, compatibility evidence, promotion regression blocking, and rollback without claiming database or live-worker certification.
- Metadata-only provenance/evaluator group (`cbdcfa6`, enforcement hardening `9b84af3`): [`mas/scripts/check_provenance.py`](../../mas/scripts/check_provenance.py), [`worker_registry/evaluator.py`](../../mas/packages/mas-core/mas_core/worker_registry/evaluator.py), [`operational_promotion_checks`](../../mas/packages/mas-core/mas_core/worker_registry/steward.py), and the default-manifest regression tests. Source/version provenance and technical security remain operational gates; detected, missing, unclassified, or restricted licence values are retained as operator notices only and cannot block certification, rollout, activation, or normal internal use. The same group records the current coding/tester security findings state and keeps both manifests pending until technical triage passes.
- Executive reconciliation verifier: [`mas/scripts/check_executive_reconciliation.py`](../../mas/scripts/check_executive_reconciliation.py)
- Model profiles/resolver: [`mas/packages/mas-core/mas_core/llm_gateway/`](../../mas/packages/mas-core/mas_core/llm_gateway/)
- Model health/cooldown state: [`mas/packages/mas-core/mas_core/llm_gateway/rate_limits.py`](../../mas/packages/mas-core/mas_core/llm_gateway/rate_limits.py)
- Model catalogue/export/live check: [`mas/packages/mas-core/mas_core/llm_gateway/model_profiles.py`](../../mas/packages/mas-core/mas_core/llm_gateway/model_profiles.py), [`mas/scripts/check_model_profile_catalogue.py`](../../mas/scripts/check_model_profile_catalogue.py), [`/model-profiles/catalogue`](../../mas/apps/orchestrator-api/orchestrator_api/main.py), and the operator dashboard proxy [`catalogue/route.ts`](<../../mas/apps/mas-dashboard/app/api/governance/model-profiles/catalogue/route.ts>) (`ab0a0fe`)
- Default profile bootstrap: [`mas/packages/mas-core/mas_core/llm_gateway/default_profiles.py`](../../mas/packages/mas-core/mas_core/llm_gateway/default_profiles.py), invoked idempotently by orchestrator startup and [`/system/seed-default-company`](../../mas/apps/orchestrator-api/orchestrator_api/main.py) (`09bdd19`)
- Internal gateway alias: [`mas/packages/mas-core/mas_core/llm_gateway/providers/api/litellm.py`](../../mas/packages/mas-core/mas_core/llm_gateway/providers/api/litellm.py)
- Executive reconciliation/views: [`mas/packages/mas-core/mas_core/observability/executive_reconciliation.py`](../../mas/packages/mas-core/mas_core/observability/executive_reconciliation.py), [`/executive/reconciliation`](../../mas/apps/orchestrator-api/orchestrator_api/main.py), and [`/executive/views/{role}`](../../mas/apps/orchestrator-api/orchestrator_api/main.py)
- Worker manifests: [`mas/workers/`](../../mas/workers/)
- Tool service: [`mas/apps/tool-service/tool_service/`](../../mas/apps/tool-service/tool_service/)
- Provenance catalogue: [`mas/docs/provenance/third_party_components.yaml`](../../mas/docs/provenance/third_party_components.yaml)
- OpenCode evidence: [`mas/docs/opencode/phase0b/1.17.13/interface-verification-report.md`](../../mas/docs/opencode/phase0b/1.17.13/interface-verification-report.md)

## Target worker lifecycle

1. Register a stable shell with role, department, permissions, tools, budget, sandbox, model requirements, and an explicit `worker_manifest_ref`; team-runner startup must reconcile that reference against its mounted manifest before instantiation.
2. Assign exactly one steward for each external worker.
3. Capture canonical source, exact version/digest, documentation snapshot, SBOM/lock, and scans; record licence/notices as non-blocking metadata when known.
4. Generate an immutable adapter and skill-bundle candidate.
5. Run the read-only selected steward/candidate readiness preflight; it must pass before the separate server-side conformance operation and does not mutate state.
6. Run contract, compatibility, security, sandbox, budget, and regression gates. Licence metadata cannot fail this step.
7. Run the read-only selected worker/project readiness preflight; it must pass before any optional dispatch confirmation and does not mutate state.
8. Obtain the required independent and human approvals.
9. Run shadow, read-only canary, and bounded live canary stages.
10. Promote exact active pointers or roll back to exact prior pointers.
11. Keep in-flight runs pinned to the exact shell, adapter, skill bundle, and
    steward versions with which they started.

## Default runtime policy

| Use | Default | Current gate |
| --- | --- | --- |
| General specialists | LangGraph 0.6.11 or CrewAI 1.6.1 | Approved provenance; complete worker-by-worker live certification. |
| Microsoft ecosystem | Microsoft Agent Framework `1.13.0` | Isolated profile (`agent-framework==1.13.0`, MCP `1.29.0`) is deterministically certified; default workspace/provider activation remains blocked by MCP `1.23.3` versus required `>=1.27,<2`. |
| Coding/testing | OpenCode 1.17.13; OpenHands core optional | OpenCode interface approved; manifest scan evidence requires reconciliation. |
| Documents | Docling + Spec Kit + Mermaid | Bounded extension image/subprocess; `document.ingest` remains usable through an explicit, degraded plain-text fallback when Docling is absent. |
| Security | Semgrep CLI + SkillSpector baseline | TruffleHog and other bounded scanners are normal selectable adapters; scanner choice is technical, not licence-driven. |
| Planning | ccpm + GitHub Issues starting profile | Plane and OpenProject are normal selectable provider adapters; AIAT remains canonical. |
| DevOps | OpenTofu + GitHub Actions starting profile | Ansible and other CLI/IaC adapters remain normally selectable behind the same boundary. |
| Memory/workflow | Letta, Qdrant, Temporal | Optional until certified and operationally proven. |

## Model rules

- Governed runs reference an approved versioned profile; `auto`, `latest`, and direct provider names are not production identities.
- The resolver intersects constraints and never lets a less-specific layer broaden a stricter layer.
- A no-candidate outcome denies or pauses the run.
- Provider keys remain in LiteLLM/OmniRoute or the credential boundary, not team containers.
- Every model run persists resolution, token/cost usage, budget settlement, and
  safe `trace_id`/`span_id` correlation evidence; worker artifact and usage
  metadata are available to the operator trace projection without raw runtime
  payloads.

## Remaining gaps

- [x] Remove the current steward/certification requirement for a detected licence and `redistribution_status: approved`; keep those database/API fields as metadata and notices only.
- [x] Update `check_provenance.py` so licence classification cannot fail validation in personal-internal mode.
- [x] Keep the historical `LICENSE_REVIEW` intake label metadata-only; it cannot transition directly to `BLOCKED`, and the normal path can skip it.
- [x] Resolve the prior coding/tester `certification_status: approved` versus
  security evidence contradiction; the exact OpenCode `v1.17.13` Semgrep
  evidence is recorded as `findings_review_required` (316 findings, 54 engine
  warnings), manifests remain pending, and activation is blocked meanwhile.
- [x] Add Compose package/lifecycle conformance and lock-parity evidence for
  the LangGraph and CrewAI adapters (LangGraph `0.6.11`, CrewAI `1.6.1`);
  complete live conformance matrices for all default specialist adapters,
  sandbox, canary, live-run, and rollback evidence remain open.
- [x] Add a read-only, fail-closed API benchmark probe for the required
  LangGraph/CrewAI runtime tiers; live worker-run/canary and rollback evidence
  remain separate.
- [x] Generate a deterministic declaration/evidence matrix for every checked-in
  worker; package availability, Docker, canary, and recovery proof remain
  environment-dependent gates.
- [x] Reconcile the default runtime extra, lockfile versions, runtime-catalogue
  imports, and production Dockerfile install command; package/import,
  security, sandbox, canary, and live-run evidence remain separate gates.
- [x] Exercise the actual steward domain for every externally sourced default
  worker, including immutable candidate, compatibility matrix, staged rollout,
  regression blocking, and pre-activation rollback transitions; database/live
  certification remains open.
- [x] Rehydrate active immutable bundle/adapter pointers from durable steward
  rows after API restart and fail closed on unknown IDs; database/live worker
  certification remains open.
- [x] Keep the normal Semgrep, SkillSpector, and TruffleHog scanner paths
  available through the shared bounded `security.scan` adapter; scanner
  availability and findings remain technical evidence, while provider-specific
  PM/DevOps adapters still require their own conformance evidence.
- [x] Publish an exact MAF/MCP compatibility lock and fail-closed activation
  preflight, then certify the isolated MAF/MCP profile through the real adapter
  with a deterministic fake client (`b937a89`); provider configuration,
  model-backed canary, and live certification remain open.
- [x] Make the local deterministic diff reviewer the default and record all
  external code-review candidates in an explicit catalogue; exact external
  repository/revision/version pins and representative reviews remain open.
- [x] Reconcile sandbox declarations and add a fail-closed gVisor runtime
  registration probe. The current host reports no registered `runsc`, so no
  weaker `runc` fallback is accepted.
- [ ] Prove gVisor smoke/network behaviour and optional Firecracker with real
  host evidence; these remain release gates.
- [x] Add a deterministic real-controller lifecycle fixture for checkpoint persistence, pause/resume/checkpoint reference, cold cancellation, cold-crash failure normalization, lease expiry/requeue, and artifact/usage-before-terminal ordering; database, sandbox, live worker, canary, and rollback proof remain separate evidence gates.
- [x] Add bounded local Postgres lease/recovery evidence (`a413997`) for claim
  exclusivity, owner-bound heartbeat renewal, one explicitly simulated host-loss
  expiry/requeue, second-owner reclaim, terminal claim denial, durable transition
  read-back, and scoped cleanup. Durable host registration and authenticated
  host heartbeat/lease state are now covered by `500fc57`; durable capacity
  reservation/commit/expiry is covered by `232c0bb`; deterministic multi-host
  selection/fallback is covered by `d9917f8`; host fencing/recovery is covered
  by `72e59ec`; external provider-backed selected-model dispatch, gVisor, and Firecracker
  evidence remain separate gates.
- [x] Certify durable in-flight shell/adapter/skill-bundle/steward version
  pinning (`6a10b0e`, migration `0040_worker_run_skill_bundle_pin`) while the
  worker registry advances to a replacement version. Run creation snapshots
  the active bundle under the worker-row lock and validates ownership; live
  dispatch and full multi-host rollout/version evidence remain separate gates.
- [x] Define the deterministic `aiat.worker-placement.v1` policy and fixture
  checker with host-plane isolation, host health/lease, labels, capabilities,
  sandbox/isolation, capacity, priority ordering, and duplicate-ID rejection
  (`3fb15db`, building on `db22e60`; evidence at
  [`worker_placement_contract.json`](../../mas/docs/provenance/worker_placement_contract.json)).
  It is a pure read-only contract; scheduler settlement is covered by
  `d9917f8`, and host fencing/recovery is covered by `72e59ec`.
- [x] Add durable authenticated host registration, heartbeat lease renewal,
  redacted public host projections, and placement snapshot read-back through
  migration `0037_worker_host_registry` (`500fc57`), then persist explicit
  host planes through migration `0041_worker_host_planes` (`3fb15db`; evidence at
  [`worker_host_registry_postgres_evidence.json`](../../mas/docs/provenance/worker_host_registry_postgres_evidence.json)).
  Host fencing and recovery are covered by `72e59ec`; scheduler integration is
  covered by `d9917f8`.
- [x] Add the AIAT-owned durable host capacity reservation ledger through
  migration `0038_worker_host_reservations` (`232c0bb`; evidence at
  [`worker_host_reservations_postgres_evidence.json`](../../mas/docs/provenance/worker_host_reservations_postgres_evidence.json)).
  It enforces host lease/readiness, row-locked capacity limits, idempotent
  keys, commit/release/expiry transitions, scalar capacity read-back, and
  scoped cleanup; scheduler selection/fallback and replay are covered by
  `d9917f8`, while external provider-backed selected-model dispatch remains open.
- [x] Connect the durable registry and reservation ledger through the
  authenticated `aiat.worker-host-scheduler.v1` layer (`d9917f8`; evidence at
  [`worker_host_scheduler_postgres_evidence.json`](../../mas/docs/provenance/worker_host_scheduler_postgres_evidence.json)).
  The checker proves deterministic preferred-host selection, row-locked
  fallback, idempotent replay, draining/unleased filtering, blocked full
  capacity, connection-reopen read-back, and cleanup; selected model-backed
  dispatch and Firecracker remain open.
- [x] Bind a durable Worker Run to the selected worker-plane host reservation
  through migration `0042_worker_run_host_binding` (`08f1610e`; evidence at
  [`worker_run_host_binding_postgres_evidence.json`](../../mas/docs/provenance/worker_run_host_binding_postgres_evidence.json)).
  The binding preserves the host lease generation, enforces run/worker
  identity, supports assignment-key replay and owner-bound commit/release
  settlement, and survives connection reopen; it does not invoke a runtime or
  provider.
- [x] Add the committed-binding `aiat.worker-host-execution.v1` edge
  (`73c0bda`; evidence at
  [`worker_host_execution_postgres_evidence.json`](../../mas/docs/provenance/worker_host_execution_postgres_evidence.json)).
  `WorkerHostExecutor` admits only a committed worker-plane binding with a
  matching current host lease generation, claims the queued run, delegates to
  the canonical controller, releases the binding, and preserves payload-free
  durable evidence through connection reopen. This certifies a local native
  fixture path; external provider-backed selected-model dispatch, deployed sandbox, provider,
  and multi-host recovery remain open.
- [x] Certify concurrent native execution across two distinct durable
  worker-plane host identities (`f9c717b`; evidence at
  [`worker_multi_host_execution_postgres_evidence.json`](../../mas/docs/provenance/worker_multi_host_execution_postgres_evidence.json)).
  The checker commits two reservations, executes two runs concurrently through
  `WorkerHostExecutor`, verifies host-specific lease fencing, durable usage,
  artifact, and trace coverage after Postgres reopen, releases both bindings,
  and cleans its fixture namespace. It does not claim independent machines,
  gVisor/Firecracker, external provider-backed selected-model dispatch, provider recovery, or
  host-loss/split-brain recovery.
- [x] Add bounded fenced host-loss queue recovery (`893293a`; evidence at
  [`worker_host_loss_queue_recovery_postgres_evidence.json`](../../mas/docs/provenance/worker_host_loss_queue_recovery_postgres_evidence.json)).
  Host recovery can be scoped to explicit host IDs; a committed binding whose
  reservation is expired is reassigned only after the canonical Worker Run
  claim is requeued, and the stale host executor is rejected before dispatch.
  The alternate binding then commits and completes a native retry at attempt
  two with durable usage/artifact/trace evidence and cleanup. This does not
  certify independent machines, gVisor/Firecracker, providers, or provider
  recovery.
- [x] Add durable host lease-generation fencing and expired-host recovery
  (`72e59ec`, migration `0039_worker_host_fencing`; evidence at
  [`worker_host_recovery_postgres_evidence.json`](../../mas/docs/provenance/worker_host_recovery_postgres_evidence.json)).
  Re-registration and expired-lease reconciliation fence stale heartbeats and
  expire reservations from the lost incarnation before placement can select a
  host; external provider-backed selected-model dispatch and high-risk sandbox evidence remain
  separate.
- [x] Add the read-only `aiat.worker-run-readiness.v1` evaluator and
  `check_worker_run_readiness.py` preflight (`5553b19`). It requires an
  operator-selected worker/project, fails closed on missing activation
  pointers, non-dispatchable projects, assignment/model/budget gaps, or
  unhealthy workers, and emits no mutation or payload evidence. The live
  worker-run, identity, sandbox runtime, canary, and rollback gates remain
  open.
- [x] Fail closed when the selected worker health read is unavailable or
  malformed (`2eea80a`, `dac268c`), including a successful response without
  `health_status`; the live checker emits the stable
  `read_worker_health_unavailable` blocker and retains the no-mutation boundary.
- [x] Add the read-only `aiat.worker-steward-readiness.v1` evaluator and
  `check_worker_steward_readiness.py` (`adc7b26`) for one explicitly selected
  external worker/candidate. It fails closed on missing dedicated-steward
  readiness, source/version provenance, technical security evidence, candidate
  stage, documentation/capability snapshots, or immutable adapter/bundle
  bindings. Fixture evidence passes; the current live coding-worker selection
  is blocked by `PROVISIONING` steward state, a pending security scan, and no
  candidate. It never generates, certifies, approves, activates, rolls out, or
  dispatches, and licence metadata remains non-gating.
- [x] Bind all 39 team-runner agent declarations to exact checked-in worker
  manifests and add `check_team_worker_manifest_refs.py` (`d9b1262`). The
  declaration check passes 11 team files/39 agents; control-plane registration,
  worker activation, and live run certification remain separate gates.
- [x] Make production team-runner startup repeat the read-only manifest
  reconciliation and carry each exact reference into `AgentConfig`/health
  metadata (`569231f`). Missing or mismatched references fail closed; no
  registration, activation, or certification is implied.
- [x] Reconcile worker manifests, runtime catalogue, Compose/OpenCode links, provenance, and notices in CI; the read-only live binding checker compares persisted default-worker rows without treating licence metadata as a gate; installed-package availability, image digests/SBOMs, and live worker-run certification remain separate gates.
- [x] Reconcile the 15 documented default worker slots with their implementation
  declarations (department, runtime, transport, isolation, capability,
  adapter configuration, and tools) through a deterministic binding matrix;
  `--live` remains blocked until an operator selects an environment and does
  not mutate runtime state.
- Measure both tool-service profiles on native Linux and publish cold-build, compressed/uncompressed size, startup, memory, and vulnerability evidence against the checked-in budget.
- [x] Add focused model-override expiry and terminal budget-settlement replay tests, align gateway retry/fallback status classification with the shared transient provider vocabulary, and persist model/provider cooldown evidence; broader reservation/settlement chaos and live provider failover evidence remain open.
- [x] Export and reconcile the deterministic runtime/profile catalogue, add the bounded executive reconciliation report, `aiat.executive-views.v1` role projections, dedicated read-only `/executive/views/{role}` endpoints, role-scoped `aiat.executive-action.v1` CFO/CTO/CEO writes, and audit reservation/settlement invariants; local live evidence now observes approved persisted coverage (92/94) while one pending model and two non-registered rows remain findings; provider-specific live failover/recovery, broader governance forms, and broader budget-settlement chaos tests remain.
- [x] Add a fail-closed `--live` model-profile catalogue verifier with an explicit `--require-approved` gate; the local API passes the approval requirement and the read-only `/v1/models` route exposes all five AIAT aliases (`68e0b03`, repeatable checker `f6ed16f`), while provider health/recovery and clean environment evidence remain operator work.
- [x] Add an idempotent, conflict-preserving bootstrap for the shipped
  `opencode-phase0b-coding` profile plus every registered model identity, and
  register the current internal `omniroute-coding` alias; live database/provider
  health, outage, and recovery evidence remain open.

## Third-party metadata

`license_provenance_evaluator` records source, version, declared/detected licence, notices, and stated restrictions. Its report is informational and never blocks hiring, activation, rollout, updating, or execution. Exact version/source, malicious-content checks, security scans, sandbox compatibility, and adapter conformance remain operational gates for reasons unrelated to licence classification.

## Acceptance criteria

- No external worker becomes ACTIVE with a failed or missing mandatory operational gate; licence metadata is explicitly excluded from that predicate.
- The universal conformance suite passes for exact adapter/runtime pairs.
- Duplicate idempotency keys do not execute work twice.
- Pause, cancel, timeout, retry, checkpoint, crash, and recovery preserve one canonical lifecycle.
- Artifacts and usage are queryable before success becomes terminal.
- Model resolution is deterministic and explains every rejection and selection.
- Workers can call only explicitly granted tools; identity/mail tools require dedicated grants.
- A canary regression automatically blocks promotion or initiates governed rollback.
