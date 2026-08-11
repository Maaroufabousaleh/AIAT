# SLO, Capacity, and Operational Forecast Feature Specification

**Baseline:** 2026-08-11
**Status:** deterministic policy/report/forecast contracts and the API/storage integration are implemented; operator SLO/capacity routes, payload-free API observation ledger, trace/native-span read-back, fixture checkers, durable usage aggregates, and local live transport/tool evidence are verified in `84a1c01`. The retained report is [`mas/docs/provenance/slo_capacity_live.json`](../../mas/docs/provenance/slo_capacity_live.json); deployed load/soak/chaos, model-backed worker, and mail-edge evidence remain open
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
