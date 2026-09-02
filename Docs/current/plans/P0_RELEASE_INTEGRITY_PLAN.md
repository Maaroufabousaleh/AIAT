# P0 Release Integrity Plan

**Priority:** P0  
**Outcome:** repository claims, machine-readable policy, deployed topology, and live evidence agree  
**Authority:** [AIAT Target Programme](../../../AIAT_TARGET_PROGRAMME.md)

**Current status (2026-08-23):** in progress. The static release ledger is
63/63 pass; the latest unconfigured live aggregation at
`2026-08-19T04:47:35Z` is 71 pass, 0 fail, 14 blocked, and 4 pending across
85 checks with `NO-RELEASE`. The retained native Ubuntu gVisor certificate
from workflow run `32541110299` passes native Linux, `runsc` registration,
digest-pinned smoke, sandbox, cleanup, and zero-residue checks; the separate
WSL/native release-host preflight remains open. The licence metadata boundary,
shared operational promotion checks, coding/tester scan-state reconciliation,
bounded project-state metric label, CEO/service identity and persisted section
ACL contract, immutable image-input contract, fail-closed local image identity
runner, tool-service profile split, exact operator-pin manifest, and explicit
bounded-label policy inventory, read-only persisted default-worker binding
reconciliation, and the durable many-project metric scrape are implemented
and statically tested.
The deployed team-runner data-plane
boundary now uses an authenticated control-plane storage API and fails closed
when its startup health probe cannot reach durable storage. See the [P0 status
record](../P0_RELEASE_INTEGRITY_STATUS.md).
The refreshed local Docker-backed network matrix, provider-diverse resource/
multipart/outage certificates, AIAT-owned encrypted-envelope and fresh-process
restore prerequisites, local image budget probes, complete 93/93 model profile
read-back, and configured current-ledger run are retained as descriptive
evidence; native image/SBOM, clean native-Linux release-host metric scale,
provider-managed KMS, independent-host recovery, and frozen clean-ledger
evidence remain open. The prior nonexistent object-store network-alias run is
classified as harness/configuration invalid and excluded from provider
evidence. The latest configured Compose release profile retained in evidence
is 76 pass, 0 fail, 5 blocked, and 4 pending across 81 checks, with
`NO-RELEASE`. Native release host/runsc and immutable image identity,
Firecracker, trace/runtime configuration, outbound mail, self-improvement
source selection, provider KMS, clean-host/disaster recovery, and the
technical security review remain open. These are distinct technical/operator
gates and are not replaced by local fixture evidence or licence metadata.

The host-safe local Compose ledger mode (`38a8ab7`) now supplies the published
loopback endpoints to host-side live children instead of leaking internal
Compose service names. Its refreshed scalar sweep (`5f0c611`) at
`2026-08-21T17:42:45Z` records 79 pass, 0 fail, and 6 blocked across 85 checks
with four pending evidence items;
the summary is retained at
[`release_ledger_live_compose_local_current.json`](../../../mas/docs/provenance/release_ledger_live_compose_local_current.json).
This advances only local live-observability coverage and does not waive native
host, image, sandbox, mail, self-improvement, pending security review, or clean
worktree gates.

The first 2026-08-21 aggregate's single `network_boundary:live` failure is
classified as a transient infrastructure/container-health race: the narrow
rerun and the broader release-ledger rerun both passed, so no provider or code
failure is retained.

The [P0 release-scope and external-prerequisite matrix](../P0_RELEASE_SCOPE_MATRIX.md)
freezes the next boundary: native Linux and default gVisor remain required;
Firecracker is optional/unverified unless promoted; provider-managed KMS/SSE
remains required, external mail is required only if email remains in the
release scope, and live self-improvement is deferred for the current internal
release. Security findings and protected memory files require operator
disposition. Do not repeat identical live checks or change the ledger until a
prerequisite materially changes.

## Why this plan is first

AIAT already implements most core control-plane concepts. The immediate risk is not missing ambition; it is inconsistent truth at activation boundaries. P0 closes contradictions that could activate an insufficiently scanned worker, expose a data service, create unbounded telemetry, deploy mutable code, or give CEO automation operator-level UI access.

## Workstream 1 — certification/provenance coherence

### Deliverables

- [x] Remove licence ID and redistribution status from every worker hiring, certification, activation, candidate, rollout, and CLI validation predicate.
- [x] Keep the existing fields and `LICENSE_REVIEW` transition for compatibility, make them metadata-only and non-blocking, and allow the normal source-review path to skip the label; plan a later rename to `LICENSE_METADATA`.
- [x] Change missing/unusual licence results into operator notices and add explicit personal-internal policy tests.
- [x] Define one machine-checkable operational promotion predicate shared by steward and API certification paths.
- [x] Wire the same predicate's persisted evidence contract into activation/status-change and rollout handlers; pending security provenance now blocks both paths.
- Treat missing or non-passing source/version provenance, security, sandbox, budget, adapter, model, or human gates as blocking. Explicitly exclude licence metadata from the activation predicate.
- [x] Reconcile `coding_worker` and `tester` approval with their security-scan
  fields; exact-source findings are now linked and remain non-passing until
  technical triage completes.
- [x] Add a machine-checked security-finding review register with an owner,
  next action, engine-warning follow-up, and exact rule-count parity for every
  open scan. The checker validates review completeness only; unresolved
  findings remain a technical activation blocker and the register is not a
  waiver (`23e908e`).
- [x] Reconcile every checked-in worker manifest with the canonical runtime catalogue, default company references, OpenCode Compose link, provenance source/version records, and metadata-only notices policy.
- [x] Remove remaining licence-derived resource exclusions from default manifests; expose TruffleHog through the bounded `security.scan` alias and keep Plane/OpenProject/Ansible selectable as normal adapters. Starting profiles remain technical packaging choices.
- [x] Add a read-only live binding reconciliation for every checked-in default worker. `check_worker_reconciliation.py --live` compares persisted `/capabilities/workers` adapter, sandbox, model, source-pin, capability, and active immutable-record bindings; the authenticated local Compose run now matches 39/39 defaults with zero missing rows or binding mismatches (evidence: [`worker_reconciliation_live.json`](../../../mas/docs/provenance/worker_reconciliation_live.json)); a missing URL/auth/API is explicitly blocked and this check does not replace live runtime/security/canary certification.
- [x] Fail CI when checked-in worker YAML, runtime catalogue, Compose, provenance catalogue, or notices disagree; the workspace lock is now tracked and checked with `uv lock --check` (`2b13d89`), while image/SBOM reconciliation remains a separate release-evidence gate.

### Evidence

- Negative classification test: missing, non-commercial, no-modification, copyleft, or unknown licence metadata does not prevent installation, hiring, activation, rollout, updating, or execution.
- Negative test: approved label plus pending scan cannot activate.
- Positive test: fully coherent candidate can reach shadow/canary under policy.
- Generate the reconciliation report in CI/release runs and attach it to release evidence; do not turn it into permanent hand-maintained status.
- Run the live binding report against the freshly seeded database during native release certification; preserve its secret-safe counts/errors with the release ledger.

## Workstream 2 — network and identity boundaries

### Deliverables

- Run live DNS/TCP/HTTP negative tests from CEO, QA, and an external-worker sandbox.
- Prove denial to Redis, Postgres/PgBouncer, object storage, identity database, Docker sockets, and unapproved provider endpoints.
- Prove positive access to the router, tool service, orchestrator, and approved model gateway.
- Add the matrix to CI on a native Linux Compose runner.
- [x] Codify the Compose credential/network/gateway contract and native probe
  harness in `scripts/check_network_boundary.py`; live Docker execution remains
  required evidence.
- [x] Move the deny/allow matrix into the checked-in
  `provenance/network_boundary_policy.yaml` (`aiat.network-boundary-policy.v1`)
  and make static and live checks consume the same protected-service, gateway,
  internal-network, identity, forbidden-mount/env, and external-denial rows.
  Static tests reject runner host-port publication and public `workers`
  networks; native release-host execution remains a separate gate.
- [x] Remove team-runner PgBouncer/MinIO/shared-service credentials and private
  network membership; route checkpoint, usage, document, and review persistence
  through an allow-listed control-plane API with a fail-closed startup probe.
- [x] Implement CEO service identity and persisted per-section ACLs.
- [x] Add human/CEO/service/worker negative and positive dashboard/API tests.
- [x] Enforce sender role/team coherence at the message-router policy boundary
  (`fb39128`). Non-CEO envelopes cannot claim a team owned by another trust
  tier; workers are limited to department/C-suite parent teams, sub-agents
  require a known parent team, and spoofed direct worker-to-CEO messages fail
  before Redis dedupe/enqueue. Static policy and mocked-router tests pass;
  live external-router evidence remains separate.
- [x] Add a hierarchy-graph communication-policy overlay (`8b7d9f1`) so an
  operator can select a sender role and see allowed/denied team paths without
  changing policy. Dashboard typecheck, focused lint/build, and the focused
  authenticated Playwright flow pass 1/1 against a current locally rebuilt
  `mas/dashboard:overlay` image (`d5f596e`). The image used a clean explicit
  context because direct unwrapped WSL Docker contexts can traverse protected
  `.tmp-*` paths. The `mas.sh` wrapper now excludes all disposable `.tmp*`
  paths and fails closed on incomplete staging (`45ee42c`); direct unwrapped
  context and release-image evidence remain separate.

### Evidence

- Refreshed local WSL2 post-fix matrix: [`network_boundary_live.json`](../../../mas/docs/provenance/network_boundary_live.json), generated against [`network_boundary_policy.yaml`](../../../mas/docs/provenance/network_boundary_policy.yaml); native-Linux post-fix closure for historical `DEF-2026-07-14-036` remains required.
- Signed/authenticated access matrix and container-network inspection.
- CEO-denied/human-allowed test for at least one restricted section.

## Workstream 3 — immutable release inputs

### Deliverables

- [x] Replace production `latest`, `main-stable`, and other mutable image references with digest-pinned infrastructure refs or required immutable `*_IMAGE_REF` inputs.
- Record source revision, build recipe, lock hash, OCI digest, SBOM, and scan result; attach licence/notices as non-blocking metadata when known.
- [x] Separate development convenience tags from production profiles; only the
  development wrapper injects local `:dev` fallbacks.
- [x] Add `check_image_provenance.py --live --json` as a fail-closed local
  Docker `RepoDigests` identity probe; it never claims SBOM, scan, build, or
  clean-room evidence and returns exit 2 when Docker or deployment refs are
  unavailable.
- [x] Extend the same helper's `--require-sbom` path to validate the minimum
  CycloneDX artifact shape (format/version, metadata component, named
  components, and unique `bom-ref` values); missing deployment refs or release
  artifacts remain blocked, and licence fields remain metadata only.
- [x] Tighten static image-inventory reconciliation (`2804a9f`) so image IDs
  and ref variables are unique, local build recipes resolve inside the
  repository, and non-pending digest/lock/SBOM/scan metadata has a bounded
  shape/path. Deployment-supplied digests and native build/SBOM/scan evidence
  remain separate release gates.
- [x] Lock production operator-pinned runtime/CLI versions and mark host-,
  optional-, and deployment-supplied capabilities unavailable until an exact
  identity is supplied; `scripts/check_operator_pins.py` checks the source
  declarations without reading licence metadata.
- Validate that Compose digests and the provenance catalogue match.

### Evidence

- Clean-room pull/build produces the same identified artifacts.
- Image inventory has no mutable production reference.
- The live identity runner records pass/fail/blocked evidence without exposing
  private image references; blocked means the external native/Docker evidence
  is unavailable, not that the image passed.
- `check_operator_pins.py --json` records exact production tool/dependency
  declarations and explicit unavailable reasons for host/operator/deployment
  capabilities; this technical contract is separate from licence metadata.
- SBOM/source inventory covers every active image/dependency; the metadata report may contain missing/unknown licence notices without failing the gate.

## Workstream 4 — telemetry and image budgets

### Deliverables

- [x] Remove raw `project_id` from Prometheus labels; use logs/traces/exemplars/query endpoints for drill-down.
- [x] Inventory every AIAT metric label and classify its cardinality basis;
  `metric_label_policy_inventory()` and `check_metric_series_budget.py` reject
  an unclassified or non-bounded `mas_*` label while retaining the live scrape
  as separate evidence.
- [x] Add total/per-metric series budgets and scrape tests; the durable local
  many-project live budget certificate is retained separately from the clean
  native-Linux release-host exit evidence.
- [x] Add `scripts/check_metric_series_budget.py` to exercise a 10,000-project
  bounded fixture and fail closed when the live orchestrator scrape is absent
  or exceeds the total/family/label contract.
- [x] Split Docling/browser/coding dependencies from the general tool-service image through core/extension build profiles.
- [x] Pin the CPU-oriented extension dependency versions and define compressed/uncompressed/startup/memory image budgets; native measurements remain evidence work.

### Evidence

- Metric cardinality remains bounded after creating many projects/workers/runs;
  the refreshed local orchestrator scrape is retained at
  [`metric_series_live.json`](../../../mas/docs/provenance/metric_series_live.json),
  and the durable local 10,000-project certificate is retained at
  [`metric_series_many_projects.json`](../../../mas/docs/provenance/metric_series_many_projects.json).
- Static evidence includes the complete AIAT label inventory and the declared
  bounded basis for every label; clean native-Linux release-host scale evidence
  remains separate.
- Tool-service cold build, image size, startup, memory, and vulnerability counts meet explicit budgets.

## Workstream 5 — current release ledger

### Deliverables

- [x] Add `scripts/check_release_ledger.py` and the checked-in
  `docs/provenance/release_ledger.yaml` inventory to assemble current revision,
  worktree, static/contract/recovery, and optional live evidence without
  exposing credentials; the report remains `NO-RELEASE` when live evidence,
  pending scans, or a clean worktree are absent.
- [x] Add `scripts/check_release_environment.py` to emit a secret-safe
  `aiat.release-environment.v1` manifest with source/configuration hashes,
  tool-version identities, environment-presence flags, and a deterministic
  manifest digest; it does not mutate deployment state or replace native
  release evidence.
- [x] Add `scripts/check_docs_index.py` to keep the canonical target, thirteen
  feature specifications, three ordered plans, local links, roadmap
  references, and metadata-only policy markers synchronized in CI/release
  evidence.
- [x] Add `docs/provenance/operator_pins.yaml` and
  `scripts/check_operator_pins.py` so production image CLIs/dependencies use
  exact declarations while host/operator/deployment capabilities carry an
  explicit unavailable reason.
- [x] Create a current ledger run from the current revision and immutable
  environment manifest; the configured live profile records the local
  transport/tool evidence plus any externally blocked checks, pending evidence
  items, and
  `NO-RELEASE` in [`release_ledger_live.json`](../../../mas/docs/provenance/release_ledger_live.json).
- [x] Add the fail-closed `--require-native-linux` release-environment
  preflight. It checks native-Linux identity, Docker/Compose v2, registered
  `runsc`, clean-tree state, and all ten digest-bearing deployment image refs
  without retaining values; the current WSL run is explicitly blocked and is
  retained at [`native_release_preflight.json`](../../../mas/docs/provenance/native_release_preflight.json).
- [x] Include the native preflight as the `release_environment:live` child in
  the aggregate ledger (`4d7a495`) and retain the current unconfigured 81-check
  result at [`release_ledger_live_current.json`](../../../mas/docs/provenance/release_ledger_live_current.json);
  the configured 81-check profile (76 pass, 5 blocked, 4 pending) remains
  descriptive evidence at [`release_ledger_live.json`](../../../mas/docs/provenance/release_ledger_live.json).
- [x] Bound each child checker with a configurable, capped timeout; a timed-out
  live checker is recorded as `blocked` and never upgraded to pass.
- [x] Run independent release-ledger child checks through a bounded concurrent
  pool while preserving inventory order (`fe97b87`); `AIAT_RELEASE_LEDGER_WORKERS`
  is capped at 16 so aggregate evidence does not serialize every static probe or
  create unbounded process fan-out.
- Reuse valid historical fixtures, but rerun every P0 and changed-boundary test.
- Label static, contract, integration, live API, live UI, recovery, security, and externally blocked evidence precisely.
- Carry forward unresolved defects with owner, severity, evidence, and next action.

## Workstream 6 — secret-safe operational diagnostics

### Deliverables

- [x] Add the read-only `GET /system/diagnostics` control-plane route. It
  checks the database with `SELECT 1`, consumes router and tool-service health
  endpoints, and performs a non-mutating object-store `head_bucket` probe when
  configured. The response retains only bounded status, latency, connection
  flags, and exception type; it never returns credentials, URLs, dependency
  payloads, or raw error text (`2860838`).
- [x] Keep dependency failure observable without turning diagnostics into a
  mutating or release-authority path: the route returns HTTP 200 with an
  aggregate `degraded` status for dependency failures, `not_configured` for an
  absent optional object store, and HTTP 503 when control-plane storage itself
  is unavailable.
- [x] Cover healthy, degraded, unconfigured, unavailable-storage, and
  payload-redaction behavior in the operational API suite; generated OpenAPI,
  dashboard TypeScript, Python SDK, and contract provenance are regenerated
  together (238 paths, 135 schemas, 271 operations); the current generated
  contract includes the typed read-only retention-plan response.
- [x] Add the API-facing `scripts/mas-ctl` wrapper for `status`,
  `diagnostics`, and a fail-closed `bootstrap` preflight, plus explicit
  authenticated `resume`/`shutdown` commands. The wrapper is independent of
  container lifecycle, accepts an operator key from an argument or environment,
  and never prints upstream error bodies (`380daf5`; executable mode
  `f8df50e`).

### Evidence

- `uv run --isolated pytest apps/orchestrator-api/tests/test_system.py apps/orchestrator-api/tests/test_test10_ops_scripts.py -q`
  passes the system and diagnostics API suites; the standalone bootstrap
  wrapper is now implemented; `test_test10_ops_scripts.py` also verifies that
  per-service restart remains in the Compose/systemd host boundary
  (`2360e07`) instead of adding Docker authority to the API.
- `uv run --isolated pytest packages/mas-api-sdk/tests -q` passes the generated
  SDK transport/contract tests; `scripts/check_api_contract.py --json` reports
  238 OpenAPI paths, 135 models, and 271 operations with matching generated
  TypeScript/Python/protocol hashes, including the typed read-only
  retention-plan response. Commit `8f46ed1` reconciles the checked-in
  `aiat.v1` `WorkerManifest.transport` enum with the runtime `aiat_gateway`
  transport without changing the API counts.
- `uv run --isolated pytest scripts/tests/test_mas_ctl.py -q` passes six
  deterministic CLI cases; the focused operational API suite now verifies the
  executable bootstrap wrapper and the host-owned per-service restart boundary.
- `npm run typecheck`, focused ESLint, and `npm run build` in
  `apps/mas-dashboard` pass for the hierarchy policy overlay. The focused
  authenticated E2E now passes 1/1 against the current `mas/dashboard:overlay`
  image (`d5f596e`). The `mas.sh` wrapper stages a complete context and fails
  closed on tar errors (`45ee42c`); native/release image verification remains
  open.

### Exit gate

- No open Critical defect.
- No inconsistent activation/certification record.
- No mutable production image.
- Network and CEO ACL negative matrices pass.
- Metrics and image budgets pass.
- Current release ledger is complete and explicitly says whether the build is releasable.

## Dependencies and sequencing

1. Freeze the candidate commit and environment manifest.
2. Implement shared certification predicate.
3. Pin images/dependencies and generate provenance.
4. Implement CEO ACL and telemetry changes.
5. Build the native-Linux certification environment.
6. Execute boundary, resource, recovery, and regression tests.
7. Correct defects and rerun affected evidence.
8. Publish the release ledger and release/no-release decision.
