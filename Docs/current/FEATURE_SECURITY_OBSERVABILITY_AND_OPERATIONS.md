# Security, Observability, and Operations Feature Specification

**Baseline:** 2026-08-11
**Status:** strong implementation foundation; request-level propagation, durable payload-free API observations, bounded trace evidence, native core spans, descriptive SLO/capacity projections, the hardened team-runner control-plane storage boundary (`22fc21a`), sender role/team communication-policy enforcement (`fb39128`), the source-built hierarchy communication-policy overlay (`8b7d9f1`), secret-safe control-plane dependency diagnostics (`2860838`), the API-facing `mas-ctl` operator wrapper (`380daf5`), local API/transport read-back, and dashboard metrics partial/stale/retry recovery (`85596b0`, source-built `metrics-states.spec.ts` 1/1) are verified. The current dashboard image and focused hierarchy E2E now pass locally (`d5f596e`); broader release-image, native-Linux, sandbox, metrics, recovery, model/tool worker, mail-edge, and full cross-service span gates remain
**Authority:** [AIAT Target Programme](../../AIAT_TARGET_PROGRAMME.md)

## Purpose

AIAT must make dangerous automation bounded, attributable, observable, and recoverable. Security is enforced at identity, policy, network, process, data, supply-chain, and human-approval layers. Observability proves behaviour but does not become a second authority.

## Implemented now

- Signed/secret service authentication, role/team tool policy, durable grants, credential approvals, privileged operations, and audit.
- Redis ACL users with separate router and tool-cache key/command permissions.
- Worker-only Docker network separated from the Redis/Postgres internal network in current Compose.
- Non-root containers, resource limits, internal networks, read-only manifest mounts, and no published production Redis/Postgres ports.
- Semgrep CLI and SkillSpector security evaluator policy; gVisor default and optional Firecracker profiles.
- `check_sandbox_runtime_readiness.py` validates the 39 worker sandbox
  declarations and provides a fail-closed Docker `runsc` registration probe;
  optional digest-pinned smoke, network-denial, canary, and Firecracker checks
  remain explicit evidence stages.
- Router recovery using pending entries, reclaim, retry, TTL, durable DLQ, safe trimming, and audited replay.
- Health/metrics endpoints, structured logging helpers, traces/metrics modules, LiteLLM and OmniRoute services/pages, optional Prometheus dev profile, and Playwright/API health tooling.
- The read-only `GET /system/diagnostics` route performs a database `SELECT 1`, router/tool-service `/health` probes, and a non-mutating object-store `head_bucket` check when endpoint credentials are configured. It returns only bounded status, latency, HTTP/connection flags, and exception type; dependency payloads, URLs, credentials, and error text are never returned. A failed dependency yields an HTTP 200 `degraded` report, while missing control-plane storage remains a 503 boundary. Focused API coverage exercises healthy, degraded, unconfigured, unavailable-storage, and payload-redaction cases (`2860838`).
- `scripts/mas-ctl` is the API-facing operator wrapper for `status`, `diagnostics`, `bootstrap`, `resume`, and `shutdown`. It uses the operator API key from an explicit argument or environment, performs no container lifecycle work, never emits upstream error bodies, and makes bootstrap readiness require both `/health` and an `ok` `/system/diagnostics` result (`380daf5`; executable mode `f8df50e`).
- Message-router publication now fails closed when a non-CEO envelope claims a sender team owned by another trust tier. Workers may operate only under department/C-suite parent teams, sub-agents require a known parent team, and direct worker-to-CEO spoofing is rejected before Redis dedupe or enqueue (`fb39128`). Static policy and mocked-router coverage pass; live external-router evidence remains separate.
- The hierarchy graph now exposes an explicit communication-policy overlay (`8b7d9f1`). Operators can select a sender role and see allowed/denied team paths as labeled, color-coded nodes and edges. The dashboard was rebuilt as `mas/dashboard:overlay` from a clean, explicit dashboard context and the focused authenticated Playwright flow passed 1/1 (`d5f596e`); the E2E selectors now use the overlay's accessible name and explicit trace-control IDs. The `mas.sh` wrapper now excludes all disposable `.tmp*` paths and fails closed on staging errors (`45ee42c`); direct unwrapped WSL Docker-context and broader release-image evidence remain separate.
- The dashboard Metrics page reads its six Prometheus query families with `cache: "no-store"`, retains successful series when another query fails, labels partial data as stale, keeps the last-successful timestamp honest, and exposes header Refresh plus banner Retry controls. [`metrics/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/metrics/page.tsx>) and [`metrics-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/metrics-states.spec.ts) cover the partial-failure/recovery path 1/1.
- The target-specific monitoring adapter can render a non-networking
  `aiat.monitoring-analytics-plan.v1` for LiteLLM and OmniRoute health/dashboard
  surfaces; implementation `525a94b` validates http(s) endpoints without
  embedded credentials and emits bounded synthetic checks without probing or
  mutating either service. Prometheus-compatible scrape/rule output remains an
  explicit optional target.
- Bounded request-level trace propagation in the orchestrator API, message
  router, and tool service: safe `X-AIAT-Trace-ID`/W3C `traceparent` inputs are
  accepted, invalid values produce a fresh root trace, responses expose
  `X-AIAT-Trace-ID`, orchestrator-to-router publication forwards the bound
  trace header, tool requests/responses retain the trace, the SDK forwards it,
  and request context is cleared after every HTTP request. The focused
  API/router/tool/SDK/core tests pass.
- Agent runtime dispatch binds each message envelope's correlation/message IDs
  into async trace context for the handler lifetime, clears it on success and
  failure, and RouterClient forwards the active trace on publish/broadcast.
- `aiat.trace-evidence.v1` provides an operator-only, bounded read model over
  durable task logs, project usage events, and worker-run transition
  correlations. It returns only safe IDs/status/timing/cost/source coverage,
  carries company-manifest trace sampling/retention metadata, joins direct
  trace-correlated model-usage, worker-artifact, integration-evidence, API
  request, and PM inbound metadata (with legacy run-correlation fallback), and
  projects native transport/model/tool/audit/worker/integration spans with
  scalar sensitive-key filtering. Identity mail-edge coverage remains an
  explicit notice. The refreshed local orchestrator is at migration
  `0036_native_trace_spans`; `scripts/check_live_trace_observability.py` and
  [`provenance/trace_observability_live.json`](../../mas/docs/provenance/trace_observability_live.json)
  verify one bounded `/health` API-request/native transport read-back. The
  deterministic fixtures are `scripts/check_trace_evidence.py` and
  `scripts/check_native_trace_spans.py`.
- `aiat.api-observation.v1` is written by orchestrator request middleware into
  a bounded Postgres ledger and feeds the platform `orchestrator_api` SLO. It
  persists only normalized route/method/status/outcome/duration and safe
  identity/context metadata; bodies, headers, query strings, credentials, and
  exception text remain outside the ledger. `scripts/check_api_observability.py`
  verifies the deterministic contract.
- `aiat.slo-policy.v1`, `aiat.slo-report.v1`, and
  `aiat.capacity-forecast.v1` provide descriptive targets and bounded cost/
  token forecasts over durable usage history. Operator-only routes expose
  observed, attention, and no-data states; `scripts/check_slo_capacity.py`
  passes a deterministic fixture and now reads the local deployment; the
  retained bounded report is [`provenance/slo_capacity_live.json`](../../mas/docs/provenance/slo_capacity_live.json).
  When configured, the signed identity-service client contributes
  payload-free outbound delivery-attempt rows to `mail_delivery`; absent mail
  telemetry remains `no_data` rather than a false pass.
- Shutdown/resume, watchdog, worker queue leases, circuit breakers, rate limits, and usage/budget ledgers.
- Distinct CEO and worker API principals, persisted dashboard section ACLs in `system_config`, operator-only ACL mutation, and dashboard proxy section context.
- Deployed team runners use one identity-specific CEO or worker control-plane
  key and a typed allow-listed storage API for checkpoints, usage, documents,
  and COO review persistence; they receive no PgBouncer, MinIO, or shared
  service credentials and are not attached to those private networks; the
  OpenCode runtime remains reachable only through the control-plane/tool
  internal network. A
  startup health check fails closed if the durable control-plane storage path
  is down.
- Production Compose image inputs are structurally digest-pinned: fixed infrastructure refs carry OCI digests, application refs are required `*_IMAGE_REF` values, Dockerfile bases are pinned, and `check_image_provenance.py` validates the contract. Commit `1d373ee` adds a non-secret lock template and verifies that it covers every Compose image variable; populated release locks remain operator-supplied.
- `check_image_provenance.py --live --json` now provides a fail-closed native/Docker
  evidence boundary: it compares deployment-supplied digests with local
  `RepoDigests` without printing image references or credentials. The live result
  is deliberately scoped to local identity; SBOM, scan, build, and clean-room
  evidence remain separate release artifacts.
- The general tool-service profile no longer installs browser/Docling/Semgrep/Mermaid extension payloads; those dependencies are isolated behind the separately budgeted `extensions` build profile.
- The tools SDK forwards an active bounded trace ID on tool calls and keeps the
  `semgrep`, `skillspector`, and `trufflehog` names as aliases of the canonical
  `security.scan` contract (`965ba38`). Scanner execution remains sandboxed and
  governed by the existing grants, audit, rate, and approval controls.
- The deployment image lock template, provenance inventory, and image budgets
  are checked in under `mas/infra/compose/production-image-lock.example.env`
  (template coverage regression committed as `1d373ee`),
  `mas/docs/provenance/production_images.yaml`, and
  `mas/infra/docker/image-budgets.yaml`; `mas.sh` keeps local `:dev` defaults
  isolated from direct production Compose usage. Local runtime/release wrapper
  hardening is committed as `fd41874`: `mas.sh validate` passes with distinct
  CEO/worker development principals, the company IANA timezone is propagated
  to runner/tool/dashboard containers, and the wrapper's `uv` bootstrap pins
  align with the operator-pin contract. These conveniences do not weaken
  direct production image-ref or credential requirements.
- Technical operator pins are maintained separately in
  [`mas/docs/provenance/operator_pins.yaml`](../../mas/docs/provenance/operator_pins.yaml)
  and checked by [`check_operator_pins.py`](../../mas/scripts/check_operator_pins.py)
  (`dd857ae`). Exact production declarations are required; host-, optional-,
  and deployment-supplied capabilities remain explicitly unavailable until
  identified.
  Exact production declarations are required; host-, optional-, and
  deployment-supplied capabilities are explicitly unavailable until identified.
  This check does not read licence/restriction metadata.

## Code anchors

- Compose/network boundary: [`mas/infra/compose/docker-compose.yml`](../../mas/infra/compose/docker-compose.yml)
- Boundary verifier: [`mas/scripts/check_network_boundary.py`](../../mas/scripts/check_network_boundary.py)
- Sandbox runtime verifier: [`mas/scripts/check_sandbox_runtime_readiness.py`](../../mas/scripts/check_sandbox_runtime_readiness.py)
- Image contract/live identity verifier: [`mas/scripts/check_image_provenance.py`](../../mas/scripts/check_image_provenance.py)
- Team-runner storage adapter: [`mas/apps/team-runner/team_runner/storage_client.py`](../../mas/apps/team-runner/team_runner/storage_client.py)
- Control-plane storage boundary: [`mas/apps/orchestrator-api/orchestrator_api/main.py`](../../mas/apps/orchestrator-api/orchestrator_api/main.py)
- Operational diagnostics: [`GET /system/diagnostics`](../../mas/apps/orchestrator-api/orchestrator_api/main.py), [`test_test10_ops_scripts.py`](../../mas/apps/orchestrator-api/tests/test_test10_ops_scripts.py)
- Communication policy boundary: [`engine.py`](../../mas/packages/mas-core/mas_core/policy/engine.py), [`routes_publish.py`](../../mas/apps/message-router/message_router/routes_publish.py), [`test_policy.py`](../../mas/packages/mas-core/tests/test_policy.py), [`test_phase3.py`](../../mas/apps/message-router/tests/test_phase3.py)
- Hierarchy policy overlay: [`HierarchyViz.tsx`](<../../mas/apps/mas-dashboard/components/system-viz/HierarchyViz.tsx>), [`system-viz/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/system-viz/page.tsx>), [`app-operations.spec.ts`](../../mas/apps/mas-dashboard/e2e/app-operations.spec.ts)
- Operator CLI: [`mas-ctl`](../../mas/scripts/mas-ctl), [`mas_ctl.py`](../../mas/scripts/mas_ctl.py), [`test_mas_ctl.py`](../../mas/scripts/tests/test_mas_ctl.py)
- Redis ACL policy: [`mas/infra/compose/redis.conf`](../../mas/infra/compose/redis.conf)
- Sandbox assets: [`mas/infra/sandbox/`](../../mas/infra/sandbox/)
- Policy: [`mas/packages/mas-core/mas_core/policy/`](../../mas/packages/mas-core/mas_core/policy/)
- Observability: [`mas/packages/mas-core/mas_core/observability/`](../../mas/packages/mas-core/mas_core/observability/)
- Router: [`mas/apps/message-router/message_router/`](../../mas/apps/message-router/message_router/)
- Request trace middleware: [`mas/apps/orchestrator-api/orchestrator_api/main.py`](../../mas/apps/orchestrator-api/orchestrator_api/main.py), [`mas/apps/message-router/message_router/main.py`](../../mas/apps/message-router/message_router/main.py), [`mas/apps/tool-service/tool_service/main.py`](../../mas/apps/tool-service/tool_service/main.py), [`mas/packages/mas-tools-sdk/mas_tools_sdk/client.py`](../../mas/packages/mas-tools-sdk/mas_tools_sdk/client.py)
- Worker message trace boundary: [`mas/packages/mas-core/mas_core/agent_runtime/base.py`](../../mas/packages/mas-core/mas_core/agent_runtime/base.py), [`mas/packages/mas-core/mas_core/agent_runtime/router_client.py`](../../mas/packages/mas-core/mas_core/agent_runtime/router_client.py)
- Trace evidence read model: [`mas/packages/mas-core/mas_core/observability/trace_evidence.py`](../../mas/packages/mas-core/mas_core/observability/trace_evidence.py), [`/observability/traces/{trace_id}`](../../mas/apps/orchestrator-api/orchestrator_api/main.py), [`mas/scripts/check_trace_evidence.py`](../../mas/scripts/check_trace_evidence.py)
- Release evidence aggregation: [`mas/scripts/check_release_ledger.py`](../../mas/scripts/check_release_ledger.py), [`mas/docs/provenance/release_ledger.yaml`](../../mas/docs/provenance/release_ledger.yaml)
- API request observation ledger: [`mas/packages/mas-core/mas_core/observability/api_observations.py`](../../mas/packages/mas-core/mas_core/observability/api_observations.py), [`mas/migrations/versions/0034_api_request_observations.py`](../../mas/migrations/versions/0034_api_request_observations.py), [`mas/scripts/check_api_observability.py`](../../mas/scripts/check_api_observability.py)
- SLO/capacity read models: [`mas/packages/mas-core/mas_core/observability/slo.py`](../../mas/packages/mas-core/mas_core/observability/slo.py), [`/observability/slo`](../../mas/apps/orchestrator-api/orchestrator_api/main.py), [`/observability/capacity/forecast`](../../mas/apps/orchestrator-api/orchestrator_api/main.py), [`mas/scripts/check_slo_capacity.py`](../../mas/scripts/check_slo_capacity.py)
- Dashboard metrics surface: [`mas/apps/mas-dashboard/app/(dashboard)/metrics/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/metrics/page.tsx>) and [`metrics-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/metrics-states.spec.ts)
- Monitoring adapter: [`mas/apps/tool-service/tool_service/devops_adapter.py`](../../mas/apps/tool-service/tool_service/devops_adapter.py), [`test_real_adapter_backends.py`](../../mas/apps/tool-service/tests/test_real_adapter_backends.py)
- Tool registry: [`mas/apps/tool-service/tool_service/registry.py`](../../mas/apps/tool-service/tool_service/registry.py)
- Historical live evidence: [`Docs/AIAT_LIVE_TEST_LEDGER.md`](../AIAT_LIVE_TEST_LEDGER.md)

## Security target

- Default deny at every trust boundary.
- External workers run under gVisor; Firecracker is required where the high-risk policy says so.
- No fallback to an uncertified weaker sandbox.
- Workers cannot reach Redis, Postgres, PgBouncer, object storage, provider
  APIs, or container sockets directly. Checkpoints, usage, and review metadata
  cross the boundary only through the authenticated allow-listed control-plane
  storage API.
- Egress is named, purpose-bound, and audited.
- Every third-party runtime has exact source/version provenance, SBOM/lock, scan, and a bounded integration profile. Licence/notices are adjacent metadata only.
- Semgrep and TruffleHog are external process boundaries exposed through the
  shared `security.scan` adapter (`semgrep`/`trufflehog` aliases); SkillSpector
  remains part of the same evidence contract. Other scanners are available
  normally through that boundary, and the manifest records a small starting
  profile without excluding any scanner for licence reasons.
- Operator, human, CEO, service, provider, and worker identities are distinct.

## Observability target

- AIAT dashboard is authoritative for projects, flows, workers, decisions, evidence, integrations, identity, budgets, audit, and DLQ.
- LiteLLM UI covers gateway/model cost and usage.
- OmniRoute covers routing/provider analytics.
- Optional Prometheus-compatible metrics cover bounded platform health.
- Project-state gauges use bounded workflow-state labels with aggregate counts,
  so moving one project cannot hide other projects in the same state; startup
  reconciliation refreshes the cache from persisted project rows.
- Trace/correlation identifiers connect HTTP, messages, worker runs, tools, models, integrations, artifacts, and audit.
- The current operator query is intentionally bounded to durable task,
  project-usage, worker-transition, direct model/artifact/integration evidence,
  API-request, PM-inbound, and native transport/model/tool/audit/worker/
  integration spans; scalar allow-listing drops sensitive attributes before
  persistence. When configured, the signed identity boundary adds only safe
  outbound delivery-attempt trace/span metadata; the response exposes an
  explicit partial-span notice until provider mail-edge spans exist.
- The SLO/capacity contracts (`14b4e4b`) consume the durable API request ledger, optional
  signed identity-service outbound delivery attempts, and PM/SCM/worker-
  recovery projections where available. Native mail-edge/bounce and complete
  span sources still intentionally return `no_data` until their observations
  exist. Missing telemetry is not interpreted as healthy.
  Capacity forecasts report confidence and budget headroom over
  `project_usage_events` aggregates.
- `/system/diagnostics` is the bounded operator read-back for control-plane
  dependency health. It is diagnostic only: it does not activate workers,
  mutate configuration, expose provider payloads, or treat licence/restriction
  metadata as a gate. Missing or unavailable dependencies are explicit
  `degraded`, `error`, or `not_configured` states.
- Alerts link directly to the relevant AIAT evidence/recovery action.
- Grafana is not part of the default target; LiteLLM and OmniRoute remain the
  model/routing surfaces, with Prometheus-compatible metrics optional.

## Known open defects/gaps

- The July live ledger's direct-Redis Critical defect has a current Compose fix.
- The refreshed local WSL2 team-runner matrix now passes all 11 runners for
  DNS/TCP/HTTP denial, positive gateway checks, storage health, forbidden-env
  absence, and Docker-socket absence; the secret-safe result is
  [`network_boundary_live.json`](../../mas/docs/provenance/network_boundary_live.json).
  Native-Linux denial/allow evidence and post-fix closure of
  `DEF-2026-07-14-036` remain release evidence.
- [x] Raw `project_id` labels were removed from AIAT Prometheus families; aggregate review/infra metrics and bounded project-state labels use audit/log drill-down instead. Project-state transition counts are reconciled on restart. `metric_label_policy_inventory()` classifies every AIAT label by its bounded source, and `scripts/check_metric_series_budget.py --json` exercises a synthetic 10,000-project population, the complete label inventory, and the 2,000-total/per-family series budget. The live parser now folds Prometheus' synthetic histogram `_created` sample into its declared family, and the refreshed local scrape passes at 31 series with no `project_id` label; the secret-safe result is [`metric_series_live.json`](../../mas/docs/provenance/metric_series_live.json). Native many-project scrape evidence remains open.
- The production image contract is now immutable at source level and has a
  fail-closed local `RepoDigests` probe; actual application OCI digest
  reconciliation, SBOMs, vulnerability results, and clean-room pulls still
  need a native-Linux release run. A blocked Docker/configuration result is not
  a pass.
- The sandbox declaration and `runsc` registration probe are implemented, but
  gVisor was unavailable in the current host evidence; digest-pinned smoke,
  network-denial, and Firecracker live certification remain open.
- A historical tool-service image was approximately 19.3 GB because Docling/Torch/CUDA and browser assets were combined. The core/extension profile split and explicit image ceilings are now checked in. A local Linux engine probe measured core at 267,957,904 bytes with 26,836 ms/112.3 MiB health startup and extensions at 4,155,668,123 bytes with 29,913 ms/137.7 MiB; the secret-safe record is [`mas/docs/provenance/image_budgets_live.json`](../../mas/docs/provenance/image_budgets_live.json). Compressed archive size, clean native-Linux build/pull, SBOM, and vulnerability evidence remain open.
- [x] HTTP request trace propagation is bounded and context-safe in the
  orchestrator API, message router, and tool service; envelope correlation IDs
  continue to be carried into message and worker records, agent dispatch
  contexts are cleared safely, and SDK/RouterClient calls preserve the active
  trace.
- [x] The operator-only `aiat.trace-evidence.v1` query joins task logs,
  project-usage events, worker-run transition correlations, direct
  trace-correlated model-usage/worker-artifact/integration-evidence rows with
  legacy run fallback, and PM inbound metadata without raw payloads;
  `trace_days`, `trace_sample_rate`, and terminal mode are projected from the
  company manifest as metadata. `check_trace_evidence.py` passes its
  deterministic fixture and blocks without a live API.
- Native span persistence and projection for transport/model/tool/audit/
  worker/integration boundaries is implemented. Mail-edge/bounce observations,
  live retention enforcement, and incident views remain incomplete; the
  current query reports those remaining gaps explicitly as non-gating notices.
  The optional identity-service
  delivery projection, API request ledger, and PM/SCM/worker-recovery
  projections are bounded read-model inputs, not a replacement for full
  distributed spans.
- Native-Linux browser E2E, cold-crash, backup/restore, and disaster-recovery evidence is incomplete.

## Acceptance criteria

- Network negative tests from CEO, QA, and an external worker deny Redis/Postgres/object-store/provider access while authorised APIs work.
- `runsc` is verified as the active runtime for default external workers; absence blocks execution.
- All production images are digest-pinned and match provenance/SBOM records;
  the live helper may only claim the local digest identity portion and never
  substitutes for SBOM or vulnerability evidence.
- Metric series stay below a defined budget with no unbounded IDs in labels.
- Secrets scans and redaction tests cover repository, images, logs, APIs, artifacts, prompts, and model payloads.
- Router/DLQ/worker/flow recovery survives service loss without duplicate canonical work.
- Backup restoration proves data and artifact checksums in a clean environment.
- An operator can query one bounded task trace through `/observability/traces/{trace_id}`;
  native core span persistence and local API/transport read-back are covered by
  static/unit/live evidence, while live model/tool worker,
  mail-edge/full-distributed coverage remains a separate release gate.
- An operator can query versioned `/observability/slo` and
  `/observability/capacity/forecast` reports; missing telemetry is explicit,
  forecasts are bounded and read-only, and the deterministic checker passes.
- An operator can query `/system/diagnostics` for secret-safe database,
  router, tool-service, and optional object-store health; the route remains
  read-only and reports degraded dependencies without masking them as a
  release pass.
- An operator can run `scripts/mas-ctl bootstrap` or `scripts/mas-ctl status`
  without curl/httpx; bootstrap fails closed on unavailable or degraded
  diagnostics, and explicit `resume`/`shutdown` commands remain API-authenticated.
