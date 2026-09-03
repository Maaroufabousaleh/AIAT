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

The current implementation verification also includes `dc8a19e`: the
supported repository regression suite passes after its storage metadata and
generated API contract assertions were reconciled to the implemented
worker-host tables and 135-model/271-operation artifact set. This is a
contract-test repair only; it does not promote any blocked live release gate.

The same verification pass includes `3edff39`/`35e52e1`: the mail-edge
relay-verifier and SMTP-gateway boundary fixtures now match the JMAP contract,
case-safe traceback provenance works on the lowercase workspace path, and the
mail-edge (11/11) and SMTP-gateway suites pass. External relay certification
remains an operator-owned gate.

Historical research, live-test ledgers, deployment runbooks, prompts, and
provider setup guides remain useful evidence or operating references. They do
not override the target programme. Where a historical document mentions a
licence allowlist, commercial-use restriction, prohibited component, or
licence-based activation decision, the current policy overlay marks that text
as superseded: licence and stated-use information is metadata only for this
personal/internal instance. Technical source integrity, version, security,
sandbox, privacy, compatibility, budget, approval, and recovery evidence
remain independent controls.

The archival [`deep-research-report.md`](../archive/deep-research-report.md) was
reconciled in the current documentation pass: its former legal-risk appendix
now redirects exact resource terms and notices to the provenance catalogue,
and its technical comparison remains design input only.

The live worker evidence set now includes the bounded provider-retry
certificate [`gateway_worker_provider_recovery_live.json`](../../mas/docs/provenance/gateway_worker_provider_recovery_live.json)
(`def4fe9`). It records one injected transient failure, one forwarded provider
completion, durable dual-Postgres read-back, payload-free redaction, and
scoped cleanup. Broader provider outage, external callback/delivery,
independent-host, and sandbox evidence remain explicitly separate roadmap
items.

The same maintained evidence set now includes the Firecracker high-risk worker
contract/readiness certificate
[`firecracker_worker_pool_readiness.json`](../../mas/docs/provenance/firecracker_worker_pool_readiness.json)
(`5ed0a0b`). It records static contract pass and current-host live blocking
because the certified launcher and Firecracker binary are unavailable. This
is a launch-boundary/readiness result only; host-certified microVM smoke,
network, provider, recovery, and gVisor evidence remain separate. The adapter
does not fall back to a weaker runtime.

The object-storage documentation now records the local encrypted-restore
prerequisite (`b0f27f6`/`59294c0`) consistently across the target programme,
feature specification, P2 plan, P0 status, roadmap, and provenance catalogue.
The certificate restores a ciphertext/scalar-manifest bundle in a distinct
fresh Python process and removes its temporary fixtures; it is explicitly
local clean-process evidence, not provider-pair, KMS, clean-host, outage, or
disaster-recovery evidence.

The storage evidence index now also includes the bounded provider-pair
certificate (`351444a`, retained in
[`object_store_provider_pair_evidence.json`](../../mas/docs/provenance/object_store_provider_pair_evidence.json)).
It exercises checksum dual-write and secondary-only clean recovery between
two local MinIO endpoints after an adapter-boundary primary failure probe.
Because both endpoints are MinIO and no provider process is stopped, this does
not establish provider-diverse durability, actual outage recovery, KMS,
clean-host, or disaster-recovery evidence.

The follow-up storage evidence index also retains the operator-observed
provider-diverse MinIO/SeaweedFS adapter rehearsal and bounded benchmark in
[`object_store_provider_pair_provider_diverse_evidence.json`](../../mas/docs/provenance/object_store_provider_pair_provider_diverse_evidence.json)
and
[`object_store_provider_benchmark_evidence.json`](../../mas/docs/provenance/object_store_provider_benchmark_evidence.json).
Those reports are scalar-only disposable comparison evidence; they do not
authorize provider selection or claim provider durability, KMS, actual outage,
clean-host, disaster recovery, or migration cutover.

The storage index now also records the bounded advanced benchmark wave from
`6794b9f` at
[`object_store_benchmark_advanced_provider_diverse_evidence.json`](../../mas/docs/provenance/object_store_benchmark_advanced_provider_diverse_evidence.json).
It covers 1 MiB and 8 MiB payloads with four concurrent cases per size on both
operator-observed endpoints, checksum read-back, and zero remaining fixture
objects. Resource, outage, provider-managed encryption/KMS,
clean-host, disaster-recovery, and provider-selection decisions remain open.

The same index now records the multipart adapter boundary from `a2f35de` at
[`object_store_multipart_provider_diverse_evidence.json`](../../mas/docs/provenance/object_store_multipart_provider_diverse_evidence.json).
It covers 8 MiB and 16 MiB payloads split into 5 MiB parts on both
operator-observed endpoints, verifies checksum read-back and explicit abort
cleanup, and leaves zero fixture objects. Resource, provider outage, KMS,
clean-host, disaster-recovery, and provider-selection decisions remain open.

The storage index now also records the bounded scalar resource profile from
`3791b3f` at
[`object_store_resource_profile_provider_diverse_evidence.json`](../../mas/docs/provenance/object_store_resource_profile_provider_diverse_evidence.json).
It covers eight 1 MiB/8 MiB checksum cases at concurrency four on both
operator-observed endpoints, with procfs RSS and wall/CPU scalars plus zero
cleanup residue. Production resource budgets/portability, outage, KMS,
clean-host, disaster-recovery, and provider-selection decisions remain open.

The same index now includes the verified-copy parity evidence
[`object_store_copy_provider_diverse_evidence.json`](../../mas/docs/provenance/object_store_copy_provider_diverse_evidence.json),
which records three matching checksum/size copies from MinIO to SeaweedFS and
zero reserved objects after explicit cleanup. It is not a migration approval
or a retention/outage/disaster-recovery certificate.

The storage index now also includes the guarded live migration rehearsal from
`ecbef00`, retained at
[`object_store_migration_provider_diverse_evidence.json`](../../mas/docs/provenance/object_store_migration_provider_diverse_evidence.json).
It records reserved-prefix inventory, checksum/read-back copy, one dual write,
explicit human-confirmed workflow cutover and rollback, and zero remaining
fixture objects across disposable MinIO/SeaweedFS endpoints. It is an
AIAT-owned workflow rehearsal only: deployment routing, retention authority,
provider outage, KMS, clean-host, and disaster-recovery evidence remain open.

The worker evidence index now includes the bounded same-host recovery soak
(`424805c`) at
[`worker_host_loss_queue_recovery_soak_postgres_evidence.json`](../../mas/docs/provenance/worker_host_loss_queue_recovery_soak_postgres_evidence.json).
It repeats the production loss/requeue/reassignment checker in three separate
child processes, retains scalar-only output, and confirms durable reopen and
zero cleanup per iteration. It is explicitly not independent-host,
provider-outage, sandbox, deployment-load/chaos, clean-host, or disaster-
recovery evidence.

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

The regression test at
[`test_docs_index.py`](../../mas/packages/mas-core/tests/test_docs_index.py)
now asserts the current thirteen-feature/three-plan authority set, so the
machine check and its test cannot silently drift back to the previous
eleven-feature count (`0dbfdb7`).

The same check now reports the licence detail surface as metadata-only and
fails if a concrete SPDX-style identifier is added to maintained feature,
plan, or status prose (`dee1a7e`). Exact identifiers remain in
[`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md) and
[`third_party_components.yaml`](../../mas/docs/provenance/third_party_components.yaml);
technical security, compatibility, sandbox, privacy, budget, and approval
controls are unaffected.

The checker validates maintained links, roadmap references, and the
metadata-only markers without evaluating or blocking any resource by licence.

The API/protocol contract reconciliation `8f46ed1` is now recorded across the
maintained authority set: the checked-in `aiat.v1` schema's
`WorkerManifest.transport` enum includes the runtime `aiat_gateway` transport,
and `scripts/check_api_contract.py --json` passes against the updated protocol
provenance hash. The OpenAPI, dashboard, and Python SDK counts remain 238
paths, 135 models, and 271 operations; this is contract-integrity evidence,
not a live-provider or release approval claim.

The 2026-08-18 model-route refresh (`68e0b03`, repeatable checker `f6ed16f`)
is indexed as read-only evidence: the local `/v1/models` route exposes all
five AIAT aliases, while the API-owned catalogue then reported complete 93/93
approved covered profile versions with no findings after the exact
unreferenced local smoke fixture was removed. The active catalogue now has 92
entries after the retired Groq `llama-3.3-70b-versatile` registration was
removed; the retained 93/93 report remains immutable historical evidence. It records no provider call,
completion, routing mutation, or activation decision; external provider
execution and recovery remain open.

The latest maintained worker-host group is `6cef1b8` plus the pre-claim
consistency hardening in `9a7db70` and durable adapter completion `8ed53df`:
the selected model-resolution certificate now records approved profile/version
selection, durable snapshot propagation, one production `GatewayWorkerAdapter`
call through a bounded local gateway double, exact fixture provider/model
attribution, host execution evidence, Postgres reopen, and scoped cleanup. The
terminal guard `199eb5b` additionally rejects missing, incomplete, or mismatched
result provider/model usage against that immutable snapshot before durable usage
or terminal evidence persistence; no-snapshot legacy/native runs remain
compatible. This is local control-plane/gateway-fixture evidence only; external
provider, hardened sandbox, and independent-host evidence remain open. The
evidence is indexed in the roadmap and retained at
[`worker_host_model_resolution_postgres_evidence.json`](../../mas/docs/provenance/worker_host_model_resolution_postgres_evidence.json).

The follow-on gateway-worker groups `080ee18`, `f6baebc`, `cec1e4c`, and `cbbfe56` add the maintained
`GatewayWorkerAdapter`, `aiat_gateway` transport factory path, deterministic
fixture checker, manifest/runtime-catalogue/reconciliation registration,
loopback HTTP client-boundary/retry certificate, and evidence for bounded
controller runs with exact provider/model usage. The owned gateway client
lifecycle and prompt/message/temperature bounds are enforced before dispatch;
the loopback certificate also checks the AIAT-owned endpoint and bearer-secret
header. It deliberately records no external provider call, network mutation,
or sandbox execution; those live gates remain open. The fixtures are indexed
in the roadmap and retained at
[`gateway_worker_adapter_fixture.json`](../../mas/docs/provenance/gateway_worker_adapter_fixture.json)
and [`gateway_worker_http_fixture.json`](../../mas/docs/provenance/gateway_worker_http_fixture.json).

Commit `6ebb12c` adds the maintained local gateway-worker/mail-edge
composition certificate and checker. It runs the real gateway adapter and
controller, records exact fixture provider/model usage, and joins scalar
worker/integration sources with verified delivered and bounced observations.
The payload-free report is indexed at
[`gateway_worker_mail_edge_fixture.json`](../../mas/docs/provenance/gateway_worker_mail_edge_fixture.json);
durable provider callback/read-back, external provider execution, live worker,
and sandbox evidence remain explicitly open.

Commit `fa42284` adds the durable dual-Postgres composition certificate, and
`67f1599` extends it through the signed delegated identity-service HTTP route.
The production gateway adapter/controller persists payload-free worker
evidence in the worker store, normalized delivery/webhook/bounce observations
in the identity store, and rebuilds the cross-store evaluator after independent
connection reopen. The ingress mode checks replay, conflict, and tamper
rejection; scoped cleanup leaves zero fixture rows. This is normalized
identity-store/delegated-ingress evidence only; raw external-provider callback,
external provider delivery, selected live worker, recovery, and sandbox gates
remain open. The evidence is indexed in the roadmap and retained at
[`gateway_worker_mail_edge_postgres_evidence.json`](../../mas/docs/provenance/gateway_worker_mail_edge_postgres_evidence.json).

Commit `0e0a76f` adds the raw-provider follow-up. A durable outbound delivery
attempt is the sole worker/trace correlation authority for the provider message;
the real Resend/Svix raw-body route then projects delivered/bounced events and
passes replay, conflict, tamper, reopen, payload-free join, and scoped cleanup
checks. This closes the local provider-facing application boundary only;
configured external callback, provider delivery/recovery, selected worker, and
sandbox evidence remain open. The evidence is retained at
[`gateway_worker_mail_edge_provider_postgres_evidence.json`](../../mas/docs/provenance/gateway_worker_mail_edge_provider_postgres_evidence.json).

Commit `b2ae516` hardens the gateway-worker failure boundary. The maintained
adapter now separates bounded input validation, retryable transient gateway
statuses, and terminal provider rejections while retaining only status and
exception-type metadata. Focused tests cover each classification without
copying provider response text or credentials; live provider recovery remains
open.

Commit `f999695` adds the maintained explicit-opt-in live worker-plane provider
runner. It reads a configured gateway's model listing, requires an exact
operator-selected model and explicit external-dispatch opt-in, and can drive
one bounded completion through the real host executor/controller/adapter chain.
Its default invocation is blocked. The retained live certificate
[`gateway_worker_provider_live.json`](../../mas/docs/provenance/gateway_worker_provider_live.json)
(`90c3e5d`) records one listed-model check, one successful completion,
`SUCCEEDED` controller settlement, and binding/reservation release while
retaining only scalar usage/status/error metadata. Durable Postgres,
independent-host, sandbox, recovery, and mail-edge callback/bounce evidence
remain separate gates.

Commit `17f6547` extends the maintained boundary into the durable
`check_gateway_worker_mail_edge_postgres.py` checker. Its explicit
`--live-provider` mode validates the configured exact model listing, runs the
real gateway client through durable worker/controller/Postgres state, redacts
generated content before `result_json` persistence, and can combine with
`--provider-ingress` for local durable webhook/bounce read-back. The default is
fail-closed. Retained evidence
[`gateway_worker_provider_mail_edge_live.json`](../../mas/docs/provenance/gateway_worker_provider_mail_edge_live.json)
(`17f6547`) records one configured live run with dual-Postgres reopen,
delivered/bounced raw provider-ingress read-back, payload-free projection, and
zero residual fixture rows; external provider callback/delivery, recovery, and
sandbox remain separate.

Commit `38c99f4` adds the maintained bounded host-composition certificate. It
drives the real `WorkerHostExecutor`, `WorkerRunController`, and
`GatewayWorkerAdapter` through an in-memory committed worker-plane binding,
claim, exact fixture model/usage attribution, terminal settlement, release,
and payload-free scalar trace coverage. Its artifact row is a synthetic
report pointer only; the evidence makes no durable host, external provider,
independent-host, sandbox, or live recovery claim. The certificate is indexed
in the roadmap and retained at
[`gateway_worker_host_fixture.json`](../../mas/docs/provenance/gateway_worker_host_fixture.json).

Commit `2abc02a` extends the maintained host certificate with transient and
permanent gateway failure settlement. The real host/controller/adapter path
settles a `429` and a `401` case as `FAILED`, releases each committed binding
and reservation, and keeps provider detail text out of run evidence while
retaining only status/cause metadata. This is local failure semantics only;
automatic live retry, external provider recovery, durable host, and sandbox
evidence remain open. The certificate is indexed in the roadmap and retained
at [`gateway_worker_host_failure_fixture.json`](../../mas/docs/provenance/gateway_worker_host_failure_fixture.json).

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

The follow-on local composition group `6ebb12c` is also indexed by the
gateway-worker, trace-evidence, and mail-edge feature specifications. It is a
real adapter/controller fixture plus scalar evaluator composition, not a claim
of live provider delivery, durable webhook/bounce read-back, or sandbox
execution.

The durable composition group `fa42284` and signed-ingress extension `67f1599`
are indexed alongside it. Their dual-Postgres certificate reopens the worker
and identity stores independently, rebuilds the payload-free cross-store
evaluator, exercises replay/conflict/tamper behavior when requested, and cleans
reserved rows; it does not claim raw external-provider callback, external
delivery/recovery, selected live worker execution, or sandbox evidence. The
raw-provider extension `0e0a76f` additionally proves the provider-facing
Resend/Svix route derives worker/trace scope from a durable provider-message
attempt; external callback and delivery remain separate.

The local durable provider-shaped recovery group `1679341`/`9c7e76d` is now
indexed alongside the gateway/mail-edge certificates. Its Compose certificate
injects one transient `429` into the local fixture, retries through the real
`GatewayWorkerAdapter` and controller, reopens both Postgres stores, passes raw
provider-ingress replay/conflict/tamper checks, and cleans the reserved rows.
It proves the local retry boundary only; external provider outage/restore,
callback/delivery confirmation, independent host/process recovery, and sandbox
evidence remain separate. Evidence is retained at
[`gateway_worker_mail_edge_provider_recovery_postgres_evidence.json`](../../mas/docs/provenance/gateway_worker_mail_edge_provider_recovery_postgres_evidence.json).

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

The durable version-pinning group `7c1ef74` extends `6a10b0e` at migration
`0042_worker_run_host_binding` and refreshes the maintained local certificate
[`worker_version_pinning_postgres_evidence.json`](../../mas/docs/provenance/worker_version_pinning_postgres_evidence.json).
It proves that a version-one `RUNNING` run retains its shell, adapter,
skill-bundle, steward identity, worker source/version metadata, and immutable
model-resolution snapshot after the registry advances to version two, while a
new queued run uses the replacement shell/adapter/bundle/model snapshot. The
certificate reopens Postgres, remains payload-free, and cleans every reserved
worker/steward/model-profile/snapshot row. Independent host/process execution,
live dispatch, provider recovery, and sandbox evidence remain separate;
licence metadata remains informational and non-gating.

The local process-isolation group `cec6558`/`520c6bf` adds the maintained
[`worker_independent_process_execution_postgres_evidence.json`](../../mas/docs/provenance/worker_independent_process_execution_postgres_evidence.json)
certificate. Two separate child Python processes reconnect to Postgres and
settle separate committed worker-host bindings through the production executor
and controller; the parent verifies distinct process IDs, durable reopen,
payload-free usage/artifact/trace coverage, and zero-row cleanup. It is a local
same-host process prerequisite only; independent deployed hosts, host-loss,
provider, and sandbox evidence remain open.

The encrypted object-store group `91504dd` adds the maintained
[`object_store_encryption_evidence.json`](../../mas/docs/provenance/object_store_encryption_evidence.json)
certificate and the provider-neutral
[`object_store_encryption.py`](../../mas/packages/mas-core/mas_core/memory/object_store_encryption.py)
envelope. AES-256-GCM ciphertext replication, opaque key-ID-only manifests,
authenticated read-back, wrong-key/tamper rejection, clean-target preflight,
and scoped cleanup pass without retaining fixture payloads or key material.
Provider-managed SSE/KMS, external backend, key custody, clean-environment,
and outage evidence remain separate; licence metadata remains informational and
non-gating.

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

The concurrent multi-host native execution group `f9c717b`, extended by
duplicate-effect/replay certification `d45e4dd`, adds the maintained
Postgres certificate
[`worker_multi_host_execution_postgres_evidence.json`](../../mas/docs/provenance/worker_multi_host_execution_postgres_evidence.json)
and checker. It proves two separately reserved worker-plane host identities can
claim and complete two native fixture runs concurrently, preserve host lease
generation/current-lease equality, release both bindings/reservations, reopen
Postgres, retain payload-free usage/artifact/trace coverage, and clean the
fixture namespace. The upgraded run races a second host claim, records one
`worker_run_claim_failed` rejection and exactly two adapter dispatches, then
replays terminal and alternate-run-ID requests without redispatch. It is local
two-identity fixture evidence, not independent machine, sandbox, provider,
selected-model, or host-loss recovery evidence;
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

The follow-on soak group `424805c` indexes the same-host repeat certificate
without changing the authority boundary: it is a local consistency measure,
not an activation/release gate and not a substitute for independent deployed
host or provider recovery evidence.

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
