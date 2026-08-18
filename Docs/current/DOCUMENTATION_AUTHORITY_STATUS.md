# Documentation Authority Status

**Updated:** 2026-08-18
**Roadmap:** [AIAT Roadmap](../../ROADMAP.md)
**Authority:** [AIAT Target Programme](../../AIAT_TARGET_PROGRAMME.md)
**Scope:** personal/internal AIAT instance

## Audit result

The repository documentation audit read 85 Markdown documents plus the
available PDF/DOCX architecture and research sources. The maintained authority
set is intentionally smaller:

- one normative target programme (`AIAT_TARGET_PROGRAMME.md`);
- one root navigation/delivery roadmap (`ROADMAP.md`);
- thirteen current feature specifications;
- three ordered plans; and
- focused implementation/review status notes linked from the roadmap.

Historical research, live-test ledgers, deployment runbooks, prompts, and
provider setup guides remain useful evidence or operating references. They do
not override the target programme. Where a historical document mentions a
licence allowlist, commercial-use restriction, prohibited component, or
licence-based activation decision, the current policy overlay marks that text
as superseded: licence and stated-use information is metadata only for this
personal/internal instance. Technical source integrity, version, security,
sandbox, privacy, compatibility, budget, approval, and recovery evidence
remain independent controls.

## Machine-checked status

The current workspace reports:

```text
canonical features: 13
canonical plans: 3
maintained documents: 22
licence metadata is a gate: false
```

Run from `mas/`:

```bash
uv run --isolated python scripts/check_docs_index.py --json
```

The checker validates maintained links, roadmap references, and the
metadata-only markers without evaluating or blocking any resource by licence.

The 2026-08-18 model-route refresh (`68e0b03`, repeatable checker `f6ed16f`)
is indexed as read-only evidence: the local `/v1/models` route exposes all
five AIAT aliases, while the API-owned catalogue reports 92/94 approved
covered profile versions and retains two non-registered rows as findings. It
records no provider call, completion, routing mutation, or activation
decision; external provider execution and recovery remain open.

The latest maintained worker-host group is `6cef1b8` plus the pre-claim
consistency hardening in `9a7db70`: the selected model-resolution certificate now records approved profile/version selection,
durable snapshot propagation, exact fixture provider/model attribution, host
execution evidence, Postgres reopen, and scoped cleanup. This is local
control-plane evidence only; external provider, hardened sandbox, and
independent-host evidence remain open. The evidence is indexed in the roadmap
and retained at
[`worker_host_model_resolution_postgres_evidence.json`](../../mas/docs/provenance/worker_host_model_resolution_postgres_evidence.json).

The latest bounded implementation groups are reflected in the maintained
authority set: team-runner declaration reconciliation (`d9b1262`), production
startup reconciliation and `AgentConfig`/health propagation (`569231f`),
persisted model-profile bootstrap (`09bdd19`), flow schema/retry hardening
(`234adfb`), company-timezone propagation (`ee1361f`), project-evidence
typecheck repair (`fc4f0fa`), team-runner boundary hardening (`22fc21a`),
dashboard operation-selector hardening (`e378f40`), metric reconciliation
compatibility (`541d6e0`), and the isolated project-evidence router boundary
(`33e0384`), bounded artifact/usage evidence reads (`2ca5f3d`), stale
evidence-detail refresh retention (`6c52552`), and governance read-surface
stale/retry recovery (`52de581`), and System Control stale/retry recovery
(`f445c17`), Projects list stale/retry recovery (`d3482ab`), Tools catalogue
stale/retry recovery (`5f4b0eb`), and dead-letter queue stale/retry recovery
(`823fa6d`), credentials metadata stale/retry recovery (`970f09c`), shared
worker registry grant/update-policy hardening (`d8cafbb`), identity-resource
stale/retry recovery (`46eccee`) and table accessibility
(`651ad11`), Metrics
partial/stale/retry recovery (`85596b0`), and Flows list stale/retry recovery
(`a0faf5b`), the credentials render-state lint repair (`e6e6980`), Container
Logs stale/retry recovery (`280d363`), Agent Streams reconnect/history
recovery (`3e8a0ea`), Hiring Board stale/retry recovery (`7541b84`), CEO
Live Feed reconnect/history recovery (`1761429`), and CEO Command Center chat
stream/history recovery (`beabb95`), CEO Command Center chat access-denied
recovery (`038d5f2`), and the secret-safe system diagnostics
route/API contract group (`2860838`) and the API-facing operator CLI group
(`380daf5`, executable-mode follow-up `f8df50e`), and the message-router
sender role/team coherence group (`fb39128`), the hierarchy communication-policy
overlay group (`8b7d9f1`), its evidence-test cleanup (`3dc61ad`), focused live
E2E selector hardening (`d5f596e`), generalized Docker temp-context exclusions
(`b3a2e8e`), and fail-closed staged-context handling (`45ee42c`), each with
separate documentation updates. The storage
safety group `93bf755` now rejects
non-empty restore prefixes before copy and records clean-target verification;
the static contract currently passes 11 team files and
39 exact agent-to-manifest bindings. The Credentials denial-state recovery
group `982c9c0` now preserves only previously loaded redacted metadata and
hides read/mutation controls on 401/403 access loss; its source-built fixture
matrix passes 3/3. The CEO Live Feed denial-state recovery group `a3cbd99`
now applies the same boundary to history, SSE, and composer responses; its
source-built fixture matrix passes 3/3. The Agent Streams denial-state recovery
group `118ff18` now applies the same boundary to history and SSE responses; its
source-built fixture matrix passes 3/3. These checks establish technical
identity only; registration, activation, certification, and licence metadata
remain separate.
The Container Logs denial-state recovery group `156597c` now applies the same
boundary to SSE responses; its source-built fixture matrix passes 3/3.
The Metrics denial-state recovery group `b64b15e` now applies the same boundary
across six Prometheus query families; its source-built fixture matrix passes
3/3.
The dead-letter queue denial-state recovery group `e6ab3a1` now applies the
same boundary to reads and replay responses; its source-built fixture matrix
passes 3/3.
The Tools catalogue denial-state recovery group `b418f8a` now applies the same
boundary to catalogue reads; its source-built fixture matrix passes 3/3.
The Flows list denial-state recovery group `3108b02` now applies the same
boundary to list reads and deletes; its source-built fixture matrix passes 4/4,
covering stale retention, initial denial, retained-read denial, and mutation
denial while keeping retained definitions read-only.
The flow-editor denial-state recovery group `392d264` now applies the same
boundary to canonical flow reads and saves; its source-built fixture matrix
passes 4/4, covering stale recovery, initial denial, retained-read denial, and
mutation denial while keeping the retained canvas read-only.
The Projects list denial-state recovery group `17d25b0` now applies the same
boundary to paired project/active-flow reads and create/archive/delete
mutations; its source-built fixture matrix passes 4/4, covering stale
recovery, initial denial, retained-read denial, and deletion denial while
keeping retained definitions read-only.
The Project Detail denial-state recovery group `0671eaa` now applies the same
boundary to canonical project reads and workflow/mutation responses; its
source-built fixture matrix passes 4/4, covering transient recovery, initial
denial, retained-header denial, and workflow-mutation denial while retaining
only the last-known project header and hiding all tabs and mutation surfaces.
The Project Evidence package denial-state recovery group `00f81b5` now applies
the same boundary to canonical evidence-package reads; its source-built
fixture matrix passes 3/3, covering stale recovery, initial denial, and
retained-package denial while keeping the retained package read-only and
preserving safe Back to project navigation.
The Evidence Detail denial-state recovery group `23e2db9` now applies the same
boundary to bounded scalar reads; its source-built fixture matrix passes 11/11,
covering scalar redaction, stale recovery, initial denial, and retained-read
denial while keeping citation identity and safe navigation available.
The new-flow builder denial-state recovery group `b07299b` now applies the same
boundary to governed catalogue reads and create/readiness mutations; its
source-built matrix passes 3/3 for initial catalogue denial, create denial, and
readiness-validation denial while retaining the local draft canvas read-only.
The System Overview access-denied recovery group `b0ab779` now distinguishes
401/403 control-plane or metrics responses from ordinary outages; its named
access-status region retains available overview values as read-only context,
hides retry and first-run seed actions, and its isolated local 403 fixture
passes 1/1. This is a source-built dashboard boundary, not native/live ACL or
full WCAG evidence.
The CEO Command Center chat access-denied group `038d5f2` extends the same
boundary to history, SSE, and message submission: its named access-status
region retains loaded transcript context read-only, hides Clear/retry and all
message/confirmation controls, and its source-built denial/recovery matrix
passes 3/3. Native/live ACL and full WCAG evidence remain separate.

The mail-edge observation groups `85369fe`, `cfafe38`, `2d21a2f`, `29d4da5`, `074ef8a`, `aab6285`, and `2d04b30` add the maintained
[`FEATURE_MAIL_EDGE_OBSERVABILITY.md`](FEATURE_MAIL_EDGE_OBSERVABILITY.md)
specification, the `aiat.mail-edge-observation.v1`/coverage contracts, a
deterministic/fail-closed checker, and identity-service migration
`0003_mail_edge_observations` with signed delegated persistence and scalar
dashboard/trace/SLO projection. `2d21a2f` also adds exact raw-body Resend/Svix
verification and the provider-facing ingress route. `aab6285` adds a real local
ASGI certificate for signed delivered/bounced ingress, normalization,
idempotent/conflicting replay handling, tamper rejection, payload-free
in-memory persistence, and dashboard read-back; its secret-safe evidence is
[`mail_edge_ingress_certification.json`](../../mas/docs/provenance/mail_edge_ingress_certification.json).
This closes only the local application boundary. Configured provider callback
certification, selected worker live read-back, complete mail-span evidence, and
the read-only projected-span checker/read-back boundary remain separate. The
durable local certificate in `2d04b30` additionally rebuilds the identity image
with the current `mas-core` package and migration `0003_mail_edge_observations`,
reopens `PostgresIdentityStore`, verifies payload-free SQL/dashboard read-back,
and cleans its reserved fixture namespace; evidence is
[`mail_edge_postgres_ingress_certification.json`](../../mas/docs/provenance/mail_edge_postgres_ingress_certification.json).
Deployed provider callback, selected worker, live bounce, and outage/restore
evidence remain separate.

The worker/mail-edge evidence-join group `1d8aed5` adds the maintained
`aiat.worker-mail-edge-coverage.v1` evaluator, deterministic checker, focused
regressions, and secret-safe fixture evidence
[`worker_mail_edge_coverage_fixture.json`](../../mas/docs/provenance/worker_mail_edge_coverage_fixture.json).
It composes worker source coverage with explicit trace/worker-scoped,
payload-free mail observations and remains a fixture-only evidence contract;
it does not certify a live worker, provider callback, durable worker record, or
bounce read-back.

The durable worker-run evidence group `acd3f06` adds the local Postgres
certificate and maintained evidence
[`worker_run_postgres_evidence.json`](../../mas/docs/provenance/worker_run_postgres_evidence.json).
The real controller/native adapter lifecycle, worker usage/artifact rows,
native model/worker/audit spans, second-connection read-back, and scoped
cleanup all pass at migration `0036_native_trace_spans`; the report is
counts-only and payload-free. Live model/provider execution, callback/bounce,
sandbox, canary/rollback, retention, and outage evidence remain separate.

The trace incident groups `c357fdf`, `b4b7cef`, and `869202c` add the maintained
`aiat.trace-incident.v1` summary/checker and its operator-only API/dashboard
boundary over the existing payload-free trace evidence. The route,
checked-in generated contracts, dashboard proxy, and `/logs?trace_id=…` deep
link expose bounded status/severity/coverage/finding-count metadata and the
existing finding references/timestamps only; partial/empty instrumentation
remains independent from incident status. Live worker/provider coverage,
retention application, and richer live chronology remain separate.

The retention planning groups `f8829d6`, `b3fca97`, and `9a80c6c` add the
maintained read-only `GET /observability/retention/plan` contract and
`check_trace_retention.py --live` boundary. It reports bounded native-span
candidate counts and policy metadata with `mutation_performed: false`; the
typed response rejects extra fields and true mutation claims, and the planner
keeps explicit-boolean legal-hold rows out of deletion IDs. Destructive
enforcement, authoritative holds, erasure, project narrowing, audit, and
restore parity remain separate. The follow-on execution group `01996c9` adds
the maintained `aiat.trace-retention-execution.v1` provider-neutral contract
and deterministic in-memory preview/apply rehearsal; typed parity, hold
snapshot, and bounded audit evidence are now validated in the fixture
(`57e13cb`, `15054ba`, `5d71309`), with hold acquisition routed through the
typed registry-read adapter (`67f5eae`). `96f5fc0` adds the local
`PostgresNativeTraceRetentionStore` and reserved-fixture certificate with
database-local backup/read-back parity, one trace-scoped delete, held-row
preservation, and cleanup; evidence is
[`mas/docs/provenance/trace_retention_execution_live.json`](../../mas/docs/provenance/trace_retention_execution_live.json).
Production hold-registry authority, durable audit, erasure, archive, provider
recovery, and restore evidence remain separate review gates.

The guarded self-improvement durability group `10983c8` adds a maintained
local Compose Postgres certificate at
[`self_improvement_postgres_evidence.json`](../../mas/docs/provenance/self_improvement_postgres_evidence.json).
It exercises the canonical project/lifecycle writers through six technical
gates, stale-revision rejection, human approval, five checksum/size
read-backs, exact rollback, terminal outcome/history read-back, and scoped
cleanup. This is local control-plane evidence only; selected worker/provider,
budget, deployment, and live issue reconciliation remain separate gates.

The bounded worker lease/recovery group `a413997` adds the maintained local
Postgres certificate
[`worker_lease_recovery_postgres_evidence.json`](../../mas/docs/provenance/worker_lease_recovery_postgres_evidence.json).
It proves competing-claim denial, claimant-bound heartbeat, one explicitly
simulated expiry/requeue, second-owner reclaim, terminal claim denial, durable
transition/health read-back after connection reopen, and scoped cleanup. This
is a queue-lease API certificate only; live worker dispatch, real host-loss/
split-brain, gVisor/Firecracker, and live worker/provider evidence remain
separate. Durable host-capacity reservation/commit/expiry is certified by
`232c0bb` below. Licence metadata remains
informational and non-gating.

The durable version-pinning group `6a10b0e` adds migration
`0040_worker_run_skill_bundle_pin` and refreshes the maintained local
certificate
[`worker_version_pinning_postgres_evidence.json`](../../mas/docs/provenance/worker_version_pinning_postgres_evidence.json).
It proves that a version-one `RUNNING` run retains its shell, adapter, skill
bundle, and steward references after the registry advances to version two,
while a new queued run uses the complete replacement set. Run creation
snapshots the active bundle under the worker-row lock and validates worker /
steward ownership. Multi-host execution and live dispatch remain separate;
licence metadata remains informational and non-gating.

The deterministic placement group `db22e60`, extended by `3fb15db`, adds the maintained
[`worker_placement_contract.json`](../../mas/docs/provenance/worker_placement_contract.json)
certificate and the `aiat.worker-placement.v1` policy module. Its pure
predicate filters explicit host snapshots by worker-plane identity,
readiness/lease, labels, capabilities, sandbox/isolation, and slot/memory/GPU
capacity, applies stable priority/free-capacity ordering, and rejects duplicate
host IDs without mutation or dispatch. Migration `0041_worker_host_planes`
rejects control/tool/data hosts with an explicit `host_plane_mismatch` decision.
The multi-host scheduler certificate `d9917f8` below connects this predicate to
the durable reservation ledger; host-loss/split-brain, worker dispatch, and
Firecracker evidence remain separate;
licence metadata remains informational and non-gating.

The durable host-registry group `500fc57`, extended by `3fb15db`, adds migration
`0037_worker_host_registry` and `0041_worker_host_planes` plus the maintained
[`worker_host_registry_postgres_evidence.json`](../../mas/docs/provenance/worker_host_registry_postgres_evidence.json)
certificate. It proves token-digest registration, wrong-token rejection,
heartbeat lease renewal, credential-redacted public projections, placement
snapshot and worker-plane read-back after connection reopen, and expired-lease
visibility with scoped cleanup. The current host projection includes a durable
host plane and lease generation; fencing and recovery are certified by `72e59ec`
below. Licence metadata remains informational and non-gating.

The durable host-reservation group `232c0bb` adds migration
`0038_worker_host_reservations` and the maintained
[`worker_host_reservations_postgres_evidence.json`](../../mas/docs/provenance/worker_host_reservations_postgres_evidence.json)
certificate. It proves READY/lease enforcement, row-locked capacity
rejection, idempotent reservation-key replay, commit/release/expiry
transitions, bounded scalar capacity projection, connection-reopen
read-back, and scoped cleanup. The scheduler integration below uses this
ledger; host fencing/recovery is certified by `72e59ec`; Firecracker and
provider/worker dispatch remain separate. Licence metadata remains
informational and non-gating.

The multi-host scheduler group `d9917f8` adds the maintained
[`worker_host_scheduler_postgres_evidence.json`](../../mas/docs/provenance/worker_host_scheduler_postgres_evidence.json)
certificate and `aiat.worker-host-scheduler.v1` module. It proves deterministic
preferred-host selection, row-locked fallback after a concurrent capacity
rejection, globally idempotent schedule replay, draining/unleased filtering,
blocked full-capacity output, connection-reopen read-back, and scoped cleanup
without worker/provider dispatch. Durable host-loss/split-brain fencing and
recovery are now certified separately; live worker dispatch and Firecracker
evidence remain separate. Licence metadata remains informational and
non-gating.

The run-host binding group `08f1610` adds migration
`0042_worker_run_host_binding` and the maintained
[`worker_run_host_binding_postgres_evidence.json`](../../mas/docs/provenance/worker_run_host_binding_postgres_evidence.json)
certificate. It binds a durable Worker Run to the scheduler's worker-plane
reservation, preserves the host lease generation, enforces run/worker identity,
replays assignment keys, settles owner-bound commit/release transitions, and
survives connection reopen. It is assignment authority only: live runtime,
provider, sandbox, and worker dispatch remain separate; licence metadata is
informational and non-gating.

The committed worker-plane host-execution group `73c0bda` adds the maintained
[Worker-Plane Host Execution feature](FEATURE_WORKER_HOST_EXECUTION.md),
`aiat.worker-host-execution.v1`, and the local certificate
[`worker_host_execution_postgres_evidence.json`](../../mas/docs/provenance/worker_host_execution_postgres_evidence.json).
The certificate admits a committed binding, claims and executes a queued run
through the canonical native fixture controller, releases the binding and
reservation, reopens Postgres, verifies payload-free usage/artifact/trace
evidence, and cleans the reserved namespace. Selected model-backed execution,
deployed sandbox, provider, and multi-host recovery remain open; licence
metadata is informational and non-gating.

The concurrent multi-host native execution group `f9c717b` adds the maintained
Postgres certificate
[`worker_multi_host_execution_postgres_evidence.json`](../../mas/docs/provenance/worker_multi_host_execution_postgres_evidence.json)
and checker. It proves two separately reserved worker-plane host identities can
claim and complete two native fixture runs concurrently, preserve host lease
generation/current-lease equality, release both bindings/reservations, reopen
Postgres, retain payload-free usage/artifact/trace coverage, and clean the
fixture namespace. It is local two-identity fixture evidence, not independent
machine, sandbox, provider, selected-model, or host-loss recovery evidence;
licence metadata remains informational and non-gating.

The fenced host-loss queue-recovery group `893293a` adds the maintained
[`worker_host_loss_queue_recovery_postgres_evidence.json`](../../mas/docs/provenance/worker_host_loss_queue_recovery_postgres_evidence.json)
certificate, the `aiat.worker-run-host-recovery.v1` binding edge, and a
host-filtered recovery API. It fences one expired host/reservation, requeues an
expired Worker Run claim, rejects stale execution, reassigns the queued binding
to an alternate host, completes a native retry at attempt two, reopens
Postgres, and cleans the fixture namespace. It is local AIAT-owned recovery
evidence only; independent hosts, sandbox/provider recovery, and licence
metadata remain separate/non-gating.

The host-fencing/recovery group `72e59ec` adds migration
`0039_worker_host_fencing` and the maintained
[`worker_host_recovery_postgres_evidence.json`](../../mas/docs/provenance/worker_host_recovery_postgres_evidence.json)
certificate. It proves re-registration generation fencing, stale-heartbeat
rejection, atomic expiry of reservations from a lost host incarnation,
expired-host transition to `OFFLINE`, placement exclusion, connection-reopen
read-back, and scoped cleanup. It does not claim live worker dispatch,
provider-backed recovery, or Firecracker operation; licence metadata remains
informational and non-gating.

The optional Microsoft Agent Framework phase is now reflected in the authority
set by `b937a89`: the isolated profile pins MAF `1.13.0` with MCP `1.29.0`, the
real adapter accepts an explicit AIAT client boundary, and the deterministic
fake-client certificate is retained at
[`mas/docs/provenance/maf_runtime_certification.json`](../../mas/docs/provenance/maf_runtime_certification.json).
The default workspace MCP `1.23.3` remains unchanged, so provider-backed MAF
canary/live activation is still an open technical gate; licence metadata stays
in the provenance catalogue and is not consulted by this phase.

## Clean-checkout verification

The focused clean-checkout flow verification at commit `2a41b7b` passed the
template, node-schema, portability, and migration tests, the generated-schema
check, and the topology check. The current workspace and a clean Git archive
both pass `check_docs_index.py`; the workspace lock is now tracked at
`mas/uv.lock`, so the default runtime contract is reproducible from source.
