# SLO, Capacity, and Operational Forecast Feature Specification

**Baseline:** 2026-08-17
**Status:** deterministic policy/report/forecast contracts and the API/storage integration are implemented; operator SLO/capacity routes, payload-free API observation ledger, trace/native-span read-back, fixture checkers, durable usage aggregates, project-state metric reconciliation compatibility (`541d6e0`), and local live transport/tool evidence are verified in `84a1c01`. The shared `aiat.mail-edge-observation.v1` normalizer and fail-closed checker are implemented in `85369fe`, identity-service migration `0003_mail_edge_observations` plus scalar provider-event projection are implemented in `cfafe38`, the Resend/Svix verifier/raw ingress boundary is implemented in `2d21a2f`, signed identity dashboard read-back is implemented in `074ef8a`, the bounded `aiat.trace-incident.v1` summary/checker is implemented in `c357fdf`, its operator API/dashboard deep-link boundary is implemented in `b4b7cef`, and the typed read-only retention-plan API/live checker is implemented in `f8829d6`/`b3fca97`/`9a80c6c`; the retained report is [`mas/docs/provenance/slo_capacity_live.json`](../../mas/docs/provenance/slo_capacity_live.json). Deployed load/soak/chaos, model-backed worker, configured provider callback, live bounce read-back, complete mail-edge evidence, live retention enforcement, and richer incident chronology remain open
**Authority:** [AIAT Target Programme](../../AIAT_TARGET_PROGRAMME.md)

## Purpose

AIAT needs one bounded operational read model for service objectives and
capacity planning. It must show what is observed, what is missing, and how
much confidence a forecast deserves without becoming a second execution or
budget authority. This feature is descriptive: SLO status and forecasts do
not block workers, routing, integrations, or project completion.

## Implemented now

- Versioned `aiat.slo-policy.v1` targets cover orchestrator API availability,
  queue age, worker startup/run, tool latency, model routing, PM/SCM sync,
  mail delivery, and recovery.
- `aiat.slo-report.v1` aggregates bounded observations into `healthy`,
  `attention`, or `no_data` statuses with sample counts, success rates,
  p95-latency evidence, remaining error budget, and explicit missing-source
  notices.
- Durable `project_usage_events` aggregates expose event/call/failure counts,
  token totals, cost, duration summaries, and first/last timestamps per
  project without returning raw provider/tool payloads.
- Existing `pm_inbox_events`/`pm_outbox_events` delivery rows now feed bounded
  `pm_scm_sync` observations, and worker-run recovery transitions feed bounded
  `recovery` observations. Both projections return only status and timing
  fields; company scoping uses persisted project bindings when available.
- The versioned `aiat.api-observation.v1` ledger records one bounded scalar
  observation per orchestrator request: normalized route, method, status,
  outcome, duration, safe trace ID, principal, dashboard section, and UTC
  timestamp. Request/response bodies, headers, query strings, credentials, and
  exception text are never persisted. Platform-wide rows feed the
  `orchestrator_api` SLO only; company-scoped reports do not mix them across
  companies.
- When the signed identity-service client is configured, existing durable
  `outbound_delivery_attempts` rows are projected into bounded
  `mail_delivery` SLO observations (outcome and attempted time only). The
  projection drops recipients, subjects, provider/correlation IDs, relay
  reasons, and message content. Safe trace/span IDs are retained only for the
  trace-evidence join; an unavailable or unconfigured identity edge remains
  explicit `no_data`.
- The shared mail-edge contract and checker normalize identity delivery attempts
  plus adapter-verified provider webhook `delivered`/`bounced`/failure events;
  only bounded scalar metadata survives and conflicting event IDs are reported
  as `attention`. Resend/Svix raw-body verification is implemented at the
  provider-facing identity route, while checker group `29d4da5` recognizes the
  projected provider span operation and optional signed identity dashboard read-back
  in its read-only live mode. Identity-service migration
  `0003_mail_edge_observations` persists normalized events and projects them
  beside delivery attempts; configured provider ingress and selected-worker
  deployment evidence remain open.
- `aiat.capacity-forecast.v1` projects daily cost/token demand over a bounded
  forecast horizon, reports confidence based on event density/time span, and
  compares projected cost to configured company budgets when present.
- Operator-only read routes:
  - `GET /observability/slo`
  - `GET /observability/capacity/forecast`
- `scripts/check_slo_capacity.py` runs a deterministic fixture and a
  fail-closed live probe. Live endpoint/configuration failures are `blocked`
  (exit 2); absent telemetry is returned as `no_data` or
  `insufficient_data`, never as a false pass. The refreshed local deployment
  currently returns `slo_status: attention`, `capacity_status: clear`, and
  `capacity_confidence: high`; the secret-safe snapshot is retained at
  [`mas/docs/provenance/slo_capacity_live.json`](../../mas/docs/provenance/slo_capacity_live.json).
- Existing LiteLLM/OmniRoute analytics and optional Prometheus-compatible
  metrics remain complementary surfaces. The target-specific monitoring
  adapter now emits a non-networking `aiat.monitoring-analytics-plan.v1` for
  the LiteLLM/OmniRoute health and analytics URLs; the API-owned projections
  still use durable AIAT evidence as their source of truth.
- Resume-time project-state metric reconciliation preserves the bounded
  aggregate contract and falls back to a capped project listing when an older
  lightweight storage double does not expose the state-only query (`541d6e0`).

## Code anchors

- SLO/forecast models and pure builders:
  [`mas/packages/mas-core/mas_core/observability/slo.py`](../../mas/packages/mas-core/mas_core/observability/slo.py)
- Observability exports:
  [`mas/packages/mas-core/mas_core/observability/__init__.py`](../../mas/packages/mas-core/mas_core/observability/__init__.py)
- Durable usage aggregate query:
  [`mas/packages/mas-core/mas_core/memory/storage.py`](../../mas/packages/mas-core/mas_core/memory/storage.py)
- API observation contract and storage table:
  [`mas/packages/mas-core/mas_core/observability/api_observations.py`](../../mas/packages/mas-core/mas_core/observability/api_observations.py),
  [`mas/packages/mas-core/mas_core/memory/models.py`](../../mas/packages/mas-core/mas_core/memory/models.py),
  [`mas/migrations/versions/0034_api_request_observations.py`](../../mas/migrations/versions/0034_api_request_observations.py)
- Signed identity mail projection:
  [`mas/apps/orchestrator-api/orchestrator_api/identity_client.py`](../../mas/apps/orchestrator-api/orchestrator_api/identity_client.py)
- Mail-edge observation contract/checker:
  [`mas/packages/mas-core/mas_core/observability/mail_edge.py`](../../mas/packages/mas-core/mas_core/observability/mail_edge.py),
  [`mas/scripts/check_mail_edge_observations.py`](../../mas/scripts/check_mail_edge_observations.py),
  and [`FEATURE_MAIL_EDGE_OBSERVABILITY.md`](FEATURE_MAIL_EDGE_OBSERVABILITY.md)
- PM/recovery observation readers in the same storage module project
  `pm_inbox_events`/`pm_outbox_events` and `worker_run_transitions` into the
  SLO observation shape without returning payloads.
- Operator routes and telemetry projection:
  [`mas/apps/orchestrator-api/orchestrator_api/main.py`](../../mas/apps/orchestrator-api/orchestrator_api/main.py)
- Deterministic/live checker:
  [`mas/scripts/check_slo_capacity.py`](../../mas/scripts/check_slo_capacity.py)
- Local live snapshot:
  [`mas/docs/provenance/slo_capacity_live.json`](../../mas/docs/provenance/slo_capacity_live.json)
- Core and API tests:
  [`mas/packages/mas-core/tests/test_slo.py`](../../mas/packages/mas-core/tests/test_slo.py),
  [`mas/apps/orchestrator-api/tests/test_slo_capacity.py`](../../mas/apps/orchestrator-api/tests/test_slo_capacity.py),
  [`mas/apps/orchestrator-api/tests/test_trace_evidence.py`](../../mas/apps/orchestrator-api/tests/test_trace_evidence.py),
  [`mas/apps/orchestrator-api/tests/test_trace_propagation.py`](../../mas/apps/orchestrator-api/tests/test_trace_propagation.py)

## Contract semantics

| Result | Meaning | Operational action |
| --- | --- | --- |
| `healthy` / `clear` | Observed data meets the descriptive target or forecast is within configured budget. | Continue normal operation; retain evidence. |
| `attention` | Observed success/latency misses an objective or projected cost exceeds a configured budget. | Review telemetry, routing, worker placement, or budget; no automatic execution block is implied. |
| `no_data` / `insufficient_data` | The relevant source is not persisted, unavailable, or too sparse for a defensible result. | Collect native evidence or keep the service explicitly unmeasured. |

The report intentionally does not expose project lists, raw request payloads,
provider credentials, secrets, or unrestricted metric labels. License and
restriction information remains metadata-only and is not an SLO, forecast, or
execution input for this personal/internal programme.

## Remaining gaps

- The local orchestrator now has a retained live transport/API observation
  probe after migration `0036_native_trace_spans`; see
  [`provenance/trace_observability_live.json`](../../mas/docs/provenance/trace_observability_live.json)
  and [`check_live_trace_observability.py`](../../mas/scripts/check_live_trace_observability.py).
  The rebuilt tool-service usage writer also passes a bounded `time_now` run;
  [`provenance/tool_trace_live.json`](../../mas/docs/provenance/tool_trace_live.json)
  records one project-usage row plus one `tool_service` native span, and
  [`check_live_tool_trace.py`](../../mas/scripts/check_live_tool_trace.py)
  reproduces it. Connect the durable native model/audit/worker/integration
  writers to a live model-backed worker run and add provider/webhook-level
  identity-service mail-edge/bounce/relay timing so the remaining `no_data`
  targets become evidence-backed. Direct model-usage, worker-artifact, integration-evidence,
  and safe identity delivery-attempt correlation rows are trace-queryable,
  while API request, PM/SCM, and worker-recovery projections already consume
  bounded durable observations or existing delivery/transition records.
- The read-only `aiat.trace-incident.v1` projection (`c357fdf`) classifies
  scalar trace failures and independently reports partial/empty instrumentation
  coverage. It is a descriptive incident surface, not an SLO, budget, worker,
  or release gate. Commit `b4b7cef` exposes the operator-only incident route,
  dashboard proxy, and `/logs?trace_id=…` deep link; `869202c` renders the
  bounded finding references/timestamps. Richer chronology and live-populated
  findings remain later evidence slices.
- The operator-only `GET /observability/retention/plan` route and
  `scripts/check_trace_retention.py --live` (`f8829d6`) expose bounded
  retain/archive/delete/invalid candidate counts and policy metadata while
  explicitly reporting `mutation_performed: false`; `9a80c6c` adds a separate
  `legal_hold` count and candidate marker that fail closed on ambiguous values.
  Retention enforcement, authoritative legal holds, erasure, project narrowing,
  audit, and restore parity remain separate storage/recovery gates. `b3fca97`
  makes the response a typed Pydantic/OpenAPI model with bounded counts and
  candidate fields. The guarded `aiat.trace-retention-execution.v1` rehearsal
  (`01996c9`) now proves preview non-mutation and apply prerequisites for
  project scope, the typed authoritative hold snapshot (`15054ba`), typed
  backup/read-back evidence (`57e13cb`), human confirmation, atomic batching,
  and a typed bounded audit envelope (`5d71309`); its live adapter and
  restore/erasure evidence remain open.
- Run native many-project metric/cardinality evidence and compare forecasts to
  production-like windows; fixture output is not deployment evidence.
- Add load, soak, chaos, provider outage, backup/restore, and regional disaster
  recovery exercises with retained bounded reports.
- Add alert delivery and incident-action links once the live operations stack
  is available.

## Acceptance criteria

- Every declared SLO has a stable policy target and an explicit observed,
  missing, or attention state.
- Capacity forecasts identify their durable source, window, horizon,
  confidence, budget basis, and projected headroom without leaking raw data.
- API routes require the operator principal and return versioned models.
- Fixture checks are deterministic; live checks fail closed when deployment
  state cannot be read and never convert unavailable telemetry into a pass.
- SLO/forecast projections remain read-only and cannot change project state,
  routing, credentials, budgets, or worker authorization.
