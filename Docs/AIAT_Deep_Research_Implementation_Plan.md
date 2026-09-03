# AIAT Deep-Research Reconciliation and Implementation Plan

> **Personal-use policy override (2026-08-09):** This is a historical
> reconciliation plan. [`../AIAT_TARGET_PROGRAMME.md`](../AIAT_TARGET_PROGRAMME.md)
> and [`../ROADMAP.md`](../ROADMAP.md) are authoritative. All licence
> allowlists, prohibited-component rules, commercial-distribution concerns,
> and licence-based activation gates below are superseded. Licence/notices are
> metadata only and never block normal use in the personal AIAT instance.

## Summary

This plan reconciles the archived
[`archive/deep-research-report.md`](archive/deep-research-report.md) with the current AIAT
codebase, architecture documents, deployment manifests, migrations, tests, and
live-test ledger. The research report assumed that the AIAT repository was not
available; that assumption is now obsolete. The report's central conclusion is
retained: AIAT remains the sole deterministic control plane, while external
runtimes are adapter-backed workers and never authorities over AIAT state.

The report is a design reference, not a list of missing features. Existing
implementation includes protocol v1, universal worker contracts, worker
provenance/certification/rollout, governed tools and MCP, credential leases,
PM/SCM integrations, OpenCode deployment, Docling/Semgrep/OpenTofu/Mermaid
adapters, LiteLLM/OmniRoute analytics, project evidence, and operator surfaces.

The remaining gaps are fully certified external runtime execution, complete
typed-model migration for legacy tools, production sandbox/identity rehearsal,
dashboard treatment of the new read models, and ongoing release evidence for
dependency versions, image digests, and live integrations.

Zeenie OpenCompany, Paperclip, and TinyHumans remain design references only.
AIAT will not embed competing control planes, and TinyHumans code, schemas,
templates, and frontend assets will not be copied into the proprietary product.

## Current branch implementation status (2026-08-03)

The first implementation pass for this plan is now present in this branch. It
adds the durable company/manifest control plane, queue and lease persistence,
idempotent usage and budget reservations, schema-aware tool contracts,
truthful optional-runtime readiness, provenance/SBOM automation, and the
corresponding deployment and documentation updates. The migrations currently
end at `0033_usage_budget_ledger` and the default company manifest is
`mas/companies/default-software-company.yaml`.

Delivered control-plane surfaces include company create/list/read, manifest
validation/apply/history/rollback, department and assignment read models,
worker-run queue dispatch/claim/heartbeat/recovery/cancellation, immutable
transition history, idempotent project usage, budget state and reservation
settlement, and a periodic recovery loop. Tool-service mutations now receive a
separately configured operator credential; actor headers remain attribution
only. The default Compose deployment passes that operator credential only to
the tool and orchestrator services that need it.

The implementation deliberately does not claim external certification where
the dependency is unavailable or unverified. In particular, the Microsoft
Agent Framework adapter is registered and reports truthful readiness, but its
package is not installed by default because its current MCP dependency range
conflicts with the tool-service's pinned `mcp==1.23.3`. Letta, Qdrant,
Temporal, OpenHands, Scrapling, ccpm, and code-review integrations remain
optional adapter work until their runtime and operational evidence is added.
Likewise, gVisor/Firecracker execution profiles and production identity
topology require environment-specific live validation; this branch adds the
policy, persistence, and documentation hooks without pretending that a local
test is production proof.

Validation evidence for this branch: the complete `mas` pytest suite passes
(with the repository's existing live/infrastructure skips), focused manifest,
schema-contract, worker-control, runtime, and project tests pass, Python
compilation succeeds for the changed packages, Alembic reports
`0033_usage_budget_ledger` as the sole head, all checked-in Compose/YAML files
parse, the provenance checker validates 12 components, and `uv lock --check`
is clean.  The live-test dotenv loader is opt-in so local secrets cannot leak
into unrelated unit-test configuration.

## Current-state assessment

| Area | Current disposition |
|---|---|
| Controller, approvals, evidence, project workflows | Implemented; preserve |
| Versioned protocol and universal worker contract | Implemented |
| Worker provenance, certification, rollout, rollback | Implemented |
| Tool authorization, MCP, audit, rate limits | Implemented |
| PM/SCM provider control plane | Implemented |
| Credential leases and identity control plane | Implemented; operator/service credential boundary is wired in Compose, while production mail-edge identity still needs environment-specific rehearsal |
| OpenCode, Docling, Semgrep, OpenTofu, Mermaid | Present and pinned in images |
| LiteLLM, OmniRoute, optional Prometheus metrics | Implemented |
| Durable asynchronous task checkout/heartbeats | Implemented in worker-run persistence and API; production worker deployment still needs live rehearsal |
| First-class companies and versioned company manifests | Implemented with migrations, YAML bootstrap, compiler, APIs, and idempotent apply |
| Schema-generated tool/plugin UI | Contract metadata and runtime input/output validation implemented; shipped legacy tools remain explicitly marked legacy until typed models are migrated |
| LangGraph/CrewAI execution | Truthful readiness implemented; adapters return unavailable instead of reporting stub success when dependencies/configuration are absent |
| Microsoft Agent Framework, OpenHands core, Scrapling, ccpm | Adapter/readiness or provenance declarations exist; external runtime certification remains pending |
| Letta, Qdrant, Temporal | Missing or placeholder-only |
| gVisor/Firecracker production execution | Policy exists; live runtime proof is incomplete |
| SBOM, third-party notices, automated CI | Implemented with provenance checker, SBOM script, notices, and CI workflow |
| Documentation consistency | Reconciled in this branch; external research citations and live deployment evidence still require periodic refresh |

## Implementation changes

### 1. Documentation and supply-chain authority

- Add a status notice to the root research report linking here and identifying
  repository-gap claims as historical.
- Reconcile root/`mas/` READMEs, active plans, architecture docs, and the live
  ledger with current services, workers, OpenCode, identity, and license rules.
- Add `THIRD_PARTY_NOTICES`, component provenance records, SBOM generation,
  dependency/security scanning, and CI checks that preserve licence/notices as
  metadata without an allowlist or prohibited-licence gate.
- Pin mutable production image tags to reviewed versions/digests.
- Add CI for Python, dashboard, protocol, migration, Compose, dependency, and
  image checks.

### 2. First-class company and manifest control plane

- Add durable companies, manifest versions, departments, company-worker
  assignments, and company policy/budget records.
- Keep worker provenance/certification global; make activation, department,
  grants, model profile, budget, and rollout eligibility company-scoped.
- Backfill all existing aggregates into a deterministic default company before
  enforcing required company scope.
- Replace hardcoded `system_config` bootstrap JSON with a versioned
  `mas/companies/default-software-company.yaml` manifest.
- Validate schema versions, unknown fields, reporting cycles, missing chiefs,
  privilege escalation, unavailable workers, and migrations before compilation.
- Persist manifest digest, compiler version, source, actor, generated IDs, and
  compilation evidence; repeated application of one digest is idempotent.
- Add company creation/listing, manifest validate/preview/apply/history/rollback,
  company-scoped assignments, and org-graph APIs.

### 3. Durable worker runs, usage, and budgets

- Add queue priority, attempt count, claim owner, claim/lease/heartbeat times,
  retry scheduling, cancellation requests, and recovery reasons to worker runs.
- Enqueue new runs and return an acceptance record; retain explicit inline mode
  only for controlled tests and migration compatibility.
- Claim with transactional `FOR UPDATE SKIP LOCKED` and compare-and-set state
  transitions; renew leases and recover expired claims safely.
- Add idempotent canonical cost events containing company/project/run/worker,
  provider/model/tool, pricing snapshot, billing code, resources, and traces.
- Add transactional hierarchical budget reservations, incremental enforcement,
  final reconciliation, and rebuildable raw-event rollups.

### 4. Schema-driven tool and adapter SDK

- Extend tool manifests with versioned input/output JSON Schema, side-effect
  class, risk tier, approval policy, credential requirements, timeout, artifact
  policy, and transport metadata.
- Migrate shipped tools and reject new schema-less tools after compatibility
  migration.
- Validate inputs before execution and outputs before evidence persistence.
- Generate dashboard forms and flow-node configuration from the same schemas.
- Add registration/conformance checks for unsafe schemas, missing approvals,
  undeclared credentials, timeouts, cancellation, and oversized output.

### 5. Runtime and integration completion

- Make LangGraph, CrewAI, and Microsoft Agent Framework adapters emit normalized
  events, mediated tool calls, checkpoints, usage, artifacts, and terminal
  results; unavailable packages must never look successful.
- Complete OpenCode queued-run/workspace integration and add OpenHands core as
  an optional pinned adapter excluding enterprise code.
- Add certified adapters for GitHub Spec Kit, Scrapling, ccpm, and exact-pinned
  code-review tools; remove unsupported capability labels until certified.
- Keep GitHub Actions approval/audit governed and Ansible user-installed only.
- Put Letta/Qdrant behind AIAT memory APIs. Use Temporal only for approved
  long-running execution classes; AIAT remains the state authority.
- Expose truthful readiness states: declared, installed, configured, certified,
  active, degraded, and unavailable.

### 6. Deployment, isolation, and operator workflows

- Keep the existing mail-local identity profile and production mail-edge
  deployment split explicit; add identity and migration/health dependencies to
  whichever profile is selected without exposing the service publicly.
- Prevent peer team containers from communicating directly unless explicitly
  allowed by policy.
- Provide Linux gVisor and optional Firecracker profiles, failing closed when a
  required sandbox is unavailable.
- Add company/manifest, department, run lease, budget, adapter readiness, and
  execution-overlay dashboard surfaces.
- Add TLS/reverse-proxy, authentication throttling, signed alert, backup, and
  restore guidance.
- Rerun the live ledger from current code and require current evidence for
  release approval.

## Public interfaces and migrations

- Add `/companies`, `/companies/{id}`, manifest validate/apply/history/rollback,
  and company org-graph routes. Department, assignment, and budget data are
  returned in the company read model; dedicated mutation routes remain a
  follow-up if policy requires independently managed records.
- Worker dispatch returns queued acceptance with run/status/event/cancel URLs;
  existing synchronous callers use an explicit compatibility mode.
- Worker-run APIs expose attempts, claims, heartbeats, recovery, checkpoints,
  usage, artifacts, and immutable transitions.
- Tool catalog entries expose versioned schemas and governance metadata.
- Usage APIs expose immutable events, rollups, reservations, and budget state.
- Use expand/backfill/validate/contract migrations with clean-install and
  upgraded-snapshot rehearsals.

## Test and acceptance plan

- Manifest compile/idempotency/preview/rollback/version/cycle/privilege/isolation
  tests; failed compilation must be atomic.
- At least 50 concurrent run claimers with one winner; lease renewal/expiry,
  recovery, cancellation races, checkpoint replay, and leader failover.
- Duplicate usage rejection, reservation contention, hierarchical budget stop,
  final reconciliation, and raw-ledger rebuild.
- Python/TypeScript schema parity, generated-form round trip, malformed
  input/output, MCP bypass denial, approval parking, timeout, cancellation, and
  artifact limits.
- Real runtime tasks for every certified adapter; no stub may report success.
  The branch adds truthful unavailable states and a Microsoft Agent Framework
  adapter contract, but does not certify unavailable external runtimes.
- Company isolation, signed caller attribution, credential revocation, sandbox
  escape, network/filesystem, secret-redaction, provenance, and license tests.
- Fresh deployment golden path: compile company → CEO → certify worker → queue
  task → governed tool → artifact → approval → cost/evidence.
- Migration, restart, database/Redis/object-store interruption, TLS deployment,
  SBOM, and alert delivery rehearsals.

## Assumptions and defaults

- AIAT remains proprietary and is the sole authoritative control plane.
- Zeenie and Paperclip are not default dependencies or sidecars; TinyHumans
  source is not copied.
- The default company preserves current single-company behavior while enabling
  tenant isolation.
- LangGraph, CrewAI, and Microsoft Agent Framework are default runtimes;
  AutoGen and OpenClaw remain experimental and disabled by default.
- Semgrep is the default scanner; TruffleHog is optional/user-installed.
- GitHub Issues/ccpm is the default planning path; Plane/OpenProject remain
  external-only.
- LiteLLM and OmniRoute remain default analytics; Prometheus metrics are
  optional.
- gVisor is the default hardened sandbox; Firecracker is for high-risk work.
- Postgres/MinIO remain authoritative until a separately approved storage
  migration has benchmark, rollback, and recovery evidence.
