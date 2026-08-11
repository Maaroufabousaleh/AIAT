# Workers, Stewards, Tools, and Models Feature Specification

**Baseline:** 2026-08-11
**Status:** universal foundation and metadata-only licence boundary implemented; LangGraph/CrewAI dependency benchmarks, exact lock parity, Compose adapter-lifecycle probes, and read-only persisted default-worker reconciliation (39/39) pass; worker certification remains incomplete
**Authority:** [AIAT Target Programme](../../AIAT_TARGET_PROGRAMME.md)

## Purpose

AIAT keeps stable organisational workers while allowing their execution engines to evolve. A specialist is an AIAT shell backed by a certified adapter and pinned OSS runtime. One dedicated steward governs each external worker's documentation, compatibility, candidates, certification, rollout, and rollback.

## Implemented now

- Versioned `aiat.worker.v1` and `aiat.adapter.v1` protocol models and negotiation.
- Normalized requests, capabilities, events, results, errors, artifacts, usage, tool responses, pause/resume/cancel, health, and readiness.
- `WorkerRunController` with durable lifecycle, compare-and-set transitions, evidence persistence, queue leases, heartbeat recovery, and run APIs.
- Native, process, HTTP, MCP/runtime adapter patterns plus LangGraph, CrewAI, MAF, Letta, AutoGen, and OpenCode-specific code paths.
- Dedicated steward records, documentation/capability snapshots, immutable skill bundles and adapters, certification, rollout, canary, monitoring, and rollback.
- Versioned model profiles and deterministic intersection of company/worker/project/task/privacy/capability/budget constraints.
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
- The refreshed local read-only catalogue evidence observes 93 registered
  models, 94 persisted profile versions, and 92 approved covered versions.
  One registered model remains `profile_pending` and two persisted rows are
  not registered; these are explicit operator-visible reconciliation findings,
  not a licence/resource restriction gate. The bounded result is retained at
  [`mas/docs/provenance/model_profile_catalogue_live.json`](../../mas/docs/provenance/model_profile_catalogue_live.json).
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
- 39 non-placeholder worker manifests and two non-seeded placeholders.
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
- `scripts/generate_worker_certification_matrix.py` (worker readiness group
  committed as `4c5fd68`) generates the deterministic
  39-worker declaration/evidence matrix at
  [`docs/provenance/worker_certification_matrix.yaml`](../../mas/docs/provenance/worker_certification_matrix.yaml).
  The matrix never claims live certification: it records exact runtime imports,
  transports, adapter versions, security-evidence state, and the next required
  evidence disposition.
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
- The Microsoft Agent Framework adapter (`fc528a8`) now has a deterministic compatibility
  fixture covering `Agent` construction, async `run` dispatch, shutdown, and
  fail-closed missing-package/instructions paths. This proves the AIAT-side
  translation boundary only. The locked compatibility contract now records
  `agent-framework==1.13.0` with MCP `>=1.27,<2`; the adapter runs a
  secret-free preflight and fails closed when either package is absent,
  mismatched, or missing the required symbols. The current workspace MCP pin
  is `1.23.3`, so MAF activation remains blocked until the optional dependency
  set is updated and installed.
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
- Static and read-only live reconciliation: [`mas/scripts/check_worker_reconciliation.py`](../../mas/scripts/check_worker_reconciliation.py) and [`worker_reconciliation_live.json`](../../mas/docs/provenance/worker_reconciliation_live.json). The default mode validates all 39 declarations; the authenticated local `--live` run matches 39/39 persisted defaults with zero missing rows or binding mismatches. It compares exact adapter, sandbox, model, source-pin, capability, and active immutable-record bindings and reports missing API/configuration as blocked.
- Default worker implementation binding matrix (`4c5fd68`): [`mas/scripts/check_default_worker_bindings.py`](../../mas/scripts/check_default_worker_bindings.py) and [`test_default_worker_bindings.py`](../../mas/packages/mas-core/tests/test_default_worker_bindings.py). The static contract covers all 15 documented default slots, verifies each declared transport/isolation pair against `RUNTIME_CATALOG`, and requires matching runtime/integration adapter entrypoints; `--live` remains an explicit operator/environment boundary and never mutates runtime state.
- Worker-run lifecycle fixture (`fe6fb8d`): [`mas/scripts/check_worker_run_lifecycle.py`](../../mas/scripts/check_worker_run_lifecycle.py) and [`test_worker_run_lifecycle.py`](../../mas/packages/mas-core/tests/test_worker_run_lifecycle.py). The static fixture checks real controller ordering, failure normalization, and recovery invariants; `--live` reports the explicit operator/database boundary.
- Runtime readiness probe (`4c5fd68`): [`mas/scripts/check_worker_runtime_readiness.py`](../../mas/scripts/check_worker_runtime_readiness.py). Static mode reconciles all 39 manifests; the local Compose image import probe passes required LangGraph/CrewAI imports, while host-package, external-adapter, security, sandbox, canary, and live-run evidence remain separate.
- Runtime install-profile contract (`9a10a4b`): [`mas/scripts/check_runtime_install_profile.py`](../../mas/scripts/check_runtime_install_profile.py). The `runtime-default` extra, lock metadata, runtime catalogue, and production Dockerfile install command reconcile to LangGraph `0.6.11` and CrewAI `1.6.1`.
- Sandbox runtime readiness probe (`a24c554`): [`mas/scripts/check_sandbox_runtime_readiness.py`](../../mas/scripts/check_sandbox_runtime_readiness.py) and [`test_sandbox_runtime_readiness.py`](../../mas/packages/mas-core/tests/test_sandbox_runtime_readiness.py). Static reconciliation passes all 39 manifests with 10 hardened external workers; the current Docker host reports `runsc` unavailable and therefore remains blocked without a `runc` fallback.
- Runtime benchmark probe: [`mas/scripts/check_runtime_benchmarks.py`](../../mas/scripts/check_runtime_benchmarks.py)
- Runtime adapter conformance probe (`9a10a4b`): [`mas/scripts/check_runtime_adapter_conformance.py`](../../mas/scripts/check_runtime_adapter_conformance.py) and [`runtime_adapter_conformance_live.json`](../../mas/docs/provenance/runtime_adapter_conformance_live.json). Deterministic LangGraph/CrewAI fixtures pass manifest/message translation, bounded completion, health, and shutdown; package-import and worker-canary evidence remain separate.
- MAF/MCP compatibility contract and preflight (`fc528a8`): [`mas/docs/provenance/runtime_compatibility.yaml`](../../mas/docs/provenance/runtime_compatibility.yaml), [`mas/packages/mas-core/mas_core/worker_registry/maf_compatibility.py`](../../mas/packages/mas-core/mas_core/worker_registry/maf_compatibility.py), [`mas/scripts/check_runtime_compatibility.py`](../../mas/scripts/check_runtime_compatibility.py)
- Code-review adapter catalogue/default (`fc528a8`): [`mas/docs/provenance/code_review_adapters.yaml`](../../mas/docs/provenance/code_review_adapters.yaml), [`mas/scripts/check_code_review_adapters.py`](../../mas/scripts/check_code_review_adapters.py), [`mas/apps/tool-service/tool_service/code_review_runner.py`](../../mas/apps/tool-service/tool_service/code_review_runner.py), and [`CodeReviewTool`](../../mas/apps/tool-service/tool_service/tools/adapters.py)
- Security adapter aliases/fixture (`fc528a8`): [`mas/scripts/check_security_adapters.py`](../../mas/scripts/check_security_adapters.py), [`SecurityScanTool`](../../mas/apps/tool-service/tool_service/tools/adapters.py), and [`ToolRegistry`](../../mas/apps/tool-service/tool_service/registry.py)
- Document ingestion/fallback: [`DocumentIngestTool`](../../mas/apps/tool-service/tool_service/tools/adapters.py), [`test_document_ingest_falls_back_to_text_when_docling_missing`](../../mas/apps/tool-service/tests/test_default_shipped_tool_catalog.py), and the `document.ingest` readiness probe
- Steward lifecycle contract (`c80e339`, fixture coverage `fe6fb8d`): [`mas/scripts/check_worker_steward_contract.py`](../../mas/scripts/check_worker_steward_contract.py) and [`test_worker_steward_contract.py`](../../mas/packages/mas-core/tests/test_worker_steward_contract.py). The deterministic domain exercise covers candidate immutability, compatibility evidence, promotion regression blocking, and rollback without claiming database or live-worker certification.
- Executive reconciliation verifier: [`mas/scripts/check_executive_reconciliation.py`](../../mas/scripts/check_executive_reconciliation.py)
- Model profiles/resolver: [`mas/packages/mas-core/mas_core/llm_gateway/`](../../mas/packages/mas-core/mas_core/llm_gateway/)
- Model health/cooldown state: [`mas/packages/mas-core/mas_core/llm_gateway/rate_limits.py`](../../mas/packages/mas-core/mas_core/llm_gateway/rate_limits.py)
- Model catalogue/export/live check: [`mas/packages/mas-core/mas_core/llm_gateway/model_profiles.py`](../../mas/packages/mas-core/mas_core/llm_gateway/model_profiles.py), [`mas/scripts/check_model_profile_catalogue.py`](../../mas/scripts/check_model_profile_catalogue.py), and [`/model-profiles/catalogue`](../../mas/apps/orchestrator-api/orchestrator_api/main.py)
- Default profile bootstrap: [`mas/packages/mas-core/mas_core/llm_gateway/default_profiles.py`](../../mas/packages/mas-core/mas_core/llm_gateway/default_profiles.py), invoked by orchestrator startup and [`/system/seed-default-company`](../../mas/apps/orchestrator-api/orchestrator_api/main.py)
- Internal gateway alias: [`mas/packages/mas-core/mas_core/llm_gateway/providers/api/litellm.py`](../../mas/packages/mas-core/mas_core/llm_gateway/providers/api/litellm.py)
- Executive reconciliation/views: [`mas/packages/mas-core/mas_core/observability/executive_reconciliation.py`](../../mas/packages/mas-core/mas_core/observability/executive_reconciliation.py), [`/executive/reconciliation`](../../mas/apps/orchestrator-api/orchestrator_api/main.py), and [`/executive/views/{role}`](../../mas/apps/orchestrator-api/orchestrator_api/main.py)
- Worker manifests: [`mas/workers/`](../../mas/workers/)
- Tool service: [`mas/apps/tool-service/tool_service/`](../../mas/apps/tool-service/tool_service/)
- Provenance catalogue: [`mas/docs/provenance/third_party_components.yaml`](../../mas/docs/provenance/third_party_components.yaml)
- OpenCode evidence: [`mas/docs/opencode/phase0b/1.17.13/interface-verification-report.md`](../../mas/docs/opencode/phase0b/1.17.13/interface-verification-report.md)

## Target worker lifecycle

1. Register a stable shell with role, department, permissions, tools, budget, sandbox, and model requirements.
2. Assign exactly one steward for each external worker.
3. Capture canonical source, exact version/digest, documentation snapshot, SBOM/lock, and scans; record licence/notices as non-blocking metadata when known.
4. Generate an immutable adapter and skill-bundle candidate.
5. Run contract, compatibility, security, sandbox, budget, and regression gates. Licence metadata cannot fail this step.
6. Obtain the required independent and human approvals.
7. Run shadow, read-only canary, and bounded live canary stages.
8. Promote exact active pointers or roll back to exact prior pointers.
9. Keep in-flight runs pinned to the versions with which they started.

## Default runtime policy

| Use | Default | Current gate |
| --- | --- | --- |
| General specialists | LangGraph 0.6.11 or CrewAI 1.6.1 | Approved provenance; complete worker-by-worker live certification. |
| Microsoft ecosystem | Microsoft Agent Framework `1.13.0` | Locked contract is present; optional package/MCP preflight is currently blocked by missing MAF and workspace MCP `1.23.3` versus required `>=1.27,<2`. |
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
  preflight; package installation, provider configuration, canary, and live
  certification remain open.
- [x] Make the local deterministic diff reviewer the default and record all
  external code-review candidates in an explicit catalogue; exact external
  repository/revision/version pins and representative reviews remain open.
- [x] Reconcile sandbox declarations and add a fail-closed gVisor runtime
  registration probe; prove gVisor smoke/network behaviour and optional
  Firecracker with real host evidence.
- [x] Add a deterministic real-controller lifecycle fixture for checkpoint persistence, pause/resume/checkpoint reference, cold cancellation, cold-crash failure normalization, lease expiry/requeue, and artifact/usage-before-terminal ordering; database, sandbox, live worker, canary, and rollback proof remain separate evidence gates.
- [x] Reconcile worker manifests, runtime catalogue, Compose/OpenCode links, provenance, and notices in CI; the read-only live binding checker compares persisted default-worker rows without treating licence metadata as a gate; installed-package availability, image digests/SBOMs, and live worker-run certification remain separate gates.
- [x] Reconcile the 15 documented default worker slots with their implementation
  declarations (department, runtime, transport, isolation, capability,
  adapter configuration, and tools) through a deterministic binding matrix;
  `--live` remains blocked until an operator selects an environment and does
  not mutate runtime state.
- Measure both tool-service profiles on native Linux and publish cold-build, compressed/uncompressed size, startup, memory, and vulnerability evidence against the checked-in budget.
- [x] Add focused model-override expiry and terminal budget-settlement replay tests, align gateway retry/fallback status classification with the shared transient provider vocabulary, and persist model/provider cooldown evidence; broader reservation/settlement chaos and live provider failover evidence remain open.
- [x] Export and reconcile the deterministic runtime/profile catalogue, add the bounded executive reconciliation report, `aiat.executive-views.v1` role projections, dedicated read-only `/executive/views/{role}` endpoints, role-scoped `aiat.executive-action.v1` CFO/CTO/CEO writes, and audit reservation/settlement invariants; local live evidence now observes approved persisted coverage (92/94) while one pending model and two non-registered rows remain findings; provider-specific live failover/recovery, broader governance forms, and broader budget-settlement chaos tests remain.
- [x] Add a fail-closed `--live` model-profile catalogue verifier with an explicit `--require-approved` gate; the local API passes the approval requirement and retains bounded reconciliation findings, while provider health/recovery and clean environment evidence remain operator work.
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
