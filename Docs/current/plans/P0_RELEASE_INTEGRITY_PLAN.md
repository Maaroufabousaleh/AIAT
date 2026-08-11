# P0 Release Integrity Plan

**Priority:** P0  
**Outcome:** repository claims, machine-readable policy, deployed topology, and live evidence agree  
**Authority:** [AIAT Target Programme](../../../AIAT_TARGET_PROGRAMME.md)

**Current status (2026-08-10):** in progress. The licence metadata boundary,
shared operational promotion checks, coding/tester scan-state reconciliation,
bounded project-state metric label, CEO/service identity and persisted section
ACL contract, immutable image-input contract, fail-closed local image identity
runner, tool-service profile split, exact operator-pin manifest, and explicit
bounded-label policy inventory, and read-only persisted default-worker binding
reconciliation
are implemented and statically tested.
The deployed team-runner data-plane
boundary now uses an authenticated control-plane storage API and fails closed
when its startup health probe cannot reach durable storage. See the [P0 status
record](../P0_RELEASE_INTEGRITY_STATUS.md).
The refreshed local Docker-backed network matrix, local image budget probes,
and configured current-ledger run are retained as descriptive evidence; native
image/SBOM, native many-project/live budget, provider, recovery, and frozen
clean-ledger evidence remain open.

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
- [x] Remove team-runner PgBouncer/MinIO/shared-service credentials and private
  network membership; route checkpoint, usage, document, and review persistence
  through an allow-listed control-plane API with a fail-closed startup probe.
- [x] Implement CEO service identity and persisted per-section ACLs.
- [x] Add human/CEO/service/worker negative and positive dashboard/API tests.

### Evidence

- Refreshed local WSL2 post-fix matrix: [`network_boundary_live.json`](../../../mas/docs/provenance/network_boundary_live.json); native-Linux post-fix closure for historical `DEF-2026-07-14-036` remains required.
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
- [x] Add total/per-metric series budgets and scrape tests; many-project live budget evidence remains part of the exit ledger.
- [x] Add `scripts/check_metric_series_budget.py` to exercise a 10,000-project
  bounded fixture and fail closed when the live orchestrator scrape is absent
  or exceeds the total/family/label contract.
- [x] Split Docling/browser/coding dependencies from the general tool-service image through core/extension build profiles.
- [x] Pin the CPU-oriented extension dependency versions and define compressed/uncompressed/startup/memory image budgets; native measurements remain evidence work.

### Evidence

- Metric cardinality remains bounded after creating many projects/workers/runs;
  the refreshed local orchestrator scrape is retained at
  [`metric_series_live.json`](../../../mas/docs/provenance/metric_series_live.json).
- Static evidence includes the complete AIAT label inventory and the declared
  bounded basis for every label; native many-project scrape evidence remains
  separate.
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
- [x] Add `scripts/check_docs_index.py` to keep the canonical target, ten
  feature specifications, three ordered plans, local links, roadmap references,
  and metadata-only policy markers synchronized in CI/release evidence.
- [x] Add `docs/provenance/operator_pins.yaml` and
  `scripts/check_operator_pins.py` so production image CLIs/dependencies use
  exact declarations while host/operator/deployment capabilities carry an
  explicit unavailable reason.
- [x] Create a current ledger run from the current revision and immutable
  environment manifest; the configured live profile records the local
  transport/tool evidence plus any externally blocked checks, pending evidence
  items, and
  `NO-RELEASE` in [`release_ledger_live.json`](../../../mas/docs/provenance/release_ledger_live.json).
- [x] Bound each child checker with a configurable, capped timeout; a timed-out
  live checker is recorded as `blocked` and never upgraded to pass.
- [x] Run independent release-ledger child checks through a bounded concurrent
  pool while preserving inventory order (`fe97b87`); `AIAT_RELEASE_LEDGER_WORKERS`
  is capped at 16 so aggregate evidence does not serialize every static probe or
  create unbounded process fan-out.
- Reuse valid historical fixtures, but rerun every P0 and changed-boundary test.
- Label static, contract, integration, live API, live UI, recovery, security, and externally blocked evidence precisely.
- Carry forward unresolved defects with owner, severity, evidence, and next action.

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
