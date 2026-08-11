# Trace Evidence and Retention Feature Specification

**Baseline:** 2026-08-10  
**Status:** the pure trace-context, native-span, and secret-safe trace-evidence
contracts are reviewed and committed in `77d5494` with deterministic fixtures;
the broader API/storage writer integration is present as a separate uncommitted
review group. Direct model-usage, worker-artifact, integration-evidence,
API-request correlation, native transport/model/tool/audit/integration span
persistence, and a deterministic metadata-only retention planner are defined.
The refreshed local orchestrator deployment is at migration
`0036_native_trace_spans`; bounded live transport and representative pure-tool
probes pass. The tool probe proves one
`project_usage_events` row plus one `tool_service` native span and is retained
at [`mas/docs/provenance/tool_trace_live.json`](../../mas/docs/provenance/tool_trace_live.json).
Live model/worker/audit/integration source coverage, mail-edge spans, and live
retention execution remain open  
**Authority:** [AIAT Target Programme](../../AIAT_TARGET_PROGRAMME.md)

## Purpose

Give an operator one safe way to investigate a request or worker message while
keeping AIAT's existing durable tables authoritative. This feature is a read
projection, not a second audit store and not a new completion predicate.

## Implemented contract

`aiat.trace-evidence.v1` is returned by the operator-only
`GET /observability/traces/{trace_id}` route. A bounded trace ID is validated;
malformed IDs are rejected rather than silently creating a new trace. The
projection joins:

- payload-free `api_request_observations` rows for normalized route, method,
  status, outcome, duration, and trace correlation;
- `task_log` rows written by agent execution;
- `project_usage_events` rows for model/tool cost and timing; and
- `worker_run_transitions` rows correlated by the envelope/message ID;
- directly trace-correlated `worker_usage_records` model-usage and
  `worker_artifacts` metadata, with a legacy run-correlation fallback; and
- PM inbound event metadata correlated by the integration/message ID.
- payload-free `integration_evidence_records` rows with project, connection,
  evidence type, timestamp, and optional span correlation.
- bounded `native_trace_spans` rows emitted by the API observation, project
  usage, worker evidence, and integration writers. Span attributes are scalar
  allow-listed metadata only; request bodies, tool/model payloads, provider
  headers, credentials, mail content, and arbitrary JSON are dropped before
  persistence.
- When the signed identity-service boundary is configured, outbound delivery
  attempts carrying safe trace/span IDs are projected as bounded `mail` spans.
  This correlates an AIAT request to the identity authority without importing
  recipients, subjects, provider IDs, relay errors, or message content.

Each item contains only safe operational fields: source, stable record ID,
kind, status, project/agent/team references, worker-run reference, event/model/
tool names, span ID, timestamp, duration, and cost. It never returns task input
or output, tool arguments/results, provider headers, error bodies, credentials,
arbitrary JSON metadata, or API request/response bodies, headers, or query
strings. API routes are normalized so IDs do not become unbounded dimensions.

The response includes per-source counts, project IDs, first/last observed time,
and a `PARTIAL_TRACE_SOURCES` notice that distinguishes the now-queryable
native transport/model/tool/audit/integration spans and optional identity
delivery-attempt spans from still-missing provider mail-edge observations.
Empty source coverage is reported explicitly; it is not treated as a clean or
failed project result.

The local-live checker sends one bounded `GET /health` request with a safe trace
ID and reads the operator projection. It verifies the response trace header,
the API-request ledger row, and the native transport span after the live
database reaches migration `0036_native_trace_spans`; it does not create
project, worker, provider, credential, or deployment state. The retained run
observed one API-request row and one `orchestrator_api` `/health` span. A
separate bounded `time_now` probe uses an existing project context, records
only the normal project-usage telemetry row, and reads back one
`tool_service` native tool span; its secret-safe evidence is retained at
[`mas/docs/provenance/tool_trace_live.json`](../../mas/docs/provenance/tool_trace_live.json).
The `PARTIAL_TRACE_SOURCES` notice is expected because these probes do not run
a model-backed worker or mail provider.

## Sampling and retention metadata

The company manifest exposes:

```yaml
retention:
  trace_days: 3650
  trace_sample_rate: 1.0
  terminal_mode: archive
```

The API projects these values into `aiat.trace-retention-policy.v1`. They are
operator-facing configuration metadata. They do not grant authority, bypass
approvals, or turn resource notices into gates. Project-level narrowing,
legal-hold/erasure workflows, and active retention enforcement require a
separate live storage/recovery slice.

The local retention planner now exposes `aiat.trace-retention-plan.v1`. It
classifies native-span metadata as `retain`, `archive`, `delete`, or `invalid`,
prefers an explicit `retention_until` when present, and never returns invalid
rows as deletion candidates. The planner is non-mutating; an operator or
future recovery worker must separately review and apply a storage action.

## Evidence commands

From `mas/`:

```bash
uv run --isolated pytest \
  packages/mas-core/tests/test_trace_evidence.py \
  packages/mas-core/tests/test_native_trace_spans.py \
  apps/orchestrator-api/tests/test_trace_evidence.py -q
uv run --isolated python scripts/check_trace_evidence.py --json
uv run --isolated python scripts/check_native_trace_spans.py --json
uv run --isolated pytest packages/mas-core/tests/test_trace_retention.py -q
uv run --isolated python scripts/check_trace_retention.py --json
uv run --isolated python scripts/check_trace_evidence.py --live --json
uv run --isolated python scripts/check_live_trace_observability.py --live --json \
  --trace-id aiat-live-trace-check
uv run --isolated python scripts/check_live_tool_trace.py --live --json \
  --orchestrator-url http://127.0.0.1:8000 \
  --tool-service-url http://127.0.0.1:8002 \
  --trace-id aiat-live-tool-trace-check
```

The fixture command passes without services and mutates no state. The live
commands require an orchestrator URL and operator API key; the live
observability command additionally performs one bounded health request. Missing
configuration, authentication, an outdated database migration, or unavailable
storage returns `blocked` with exit code 2 and no secret material.

## Code anchors

- Read model: [`mas/packages/mas-core/mas_core/observability/trace_evidence.py`](../../mas/packages/mas-core/mas_core/observability/trace_evidence.py)
- Trace validation/context: [`mas/packages/mas-core/mas_core/observability/tracing.py`](../../mas/packages/mas-core/mas_core/observability/tracing.py)
- Native span contract/normalizer: [`mas/packages/mas-core/mas_core/observability/native_spans.py`](../../mas/packages/mas-core/mas_core/observability/native_spans.py)
- Core review batch: commit `77d5494`; `test_tracing.py`, `test_native_trace_spans.py`, `test_trace_evidence.py`, `check_native_trace_spans.py`, and `check_trace_evidence.py` pass without database/provider mutation.
- Retention planner: [`mas/packages/mas-core/mas_core/observability/retention.py`](../../mas/packages/mas-core/mas_core/observability/retention.py)
- Durable reads: [`mas/packages/mas-core/mas_core/memory/storage.py`](../../mas/packages/mas-core/mas_core/memory/storage.py)
- API observation contract/table/migration: [`mas/packages/mas-core/mas_core/observability/api_observations.py`](../../mas/packages/mas-core/mas_core/observability/api_observations.py), [`mas/packages/mas-core/mas_core/memory/models.py`](../../mas/packages/mas-core/mas_core/memory/models.py), [`mas/migrations/versions/0034_api_request_observations.py`](../../mas/migrations/versions/0034_api_request_observations.py)
- Direct model/artifact/integration trace columns and migration: [`mas/packages/mas-core/mas_core/worker_contract/models.py`](../../mas/packages/mas-core/mas_core/worker_contract/models.py), [`mas/packages/mas-core/mas_core/memory/storage.py`](../../mas/packages/mas-core/mas_core/memory/storage.py), [`mas/migrations/versions/0035_trace_correlation_evidence.py`](../../mas/migrations/versions/0035_trace_correlation_evidence.py)
- Native span table and migration: [`mas/packages/mas-core/mas_core/memory/models.py`](../../mas/packages/mas-core/mas_core/memory/models.py), [`mas/migrations/versions/0036_native_trace_spans.py`](../../mas/migrations/versions/0036_native_trace_spans.py)
- API route: [`mas/apps/orchestrator-api/orchestrator_api/main.py`](../../mas/apps/orchestrator-api/orchestrator_api/main.py)
- Fixture/live checker: [`mas/scripts/check_trace_evidence.py`](../../mas/scripts/check_trace_evidence.py)
- Local live transport checker/evidence: [`mas/scripts/check_live_trace_observability.py`](../../mas/scripts/check_live_trace_observability.py), [`mas/docs/provenance/trace_observability_live.json`](../../mas/docs/provenance/trace_observability_live.json)
- Local live tool checker/evidence: [`mas/scripts/check_live_tool_trace.py`](../../mas/scripts/check_live_tool_trace.py), [`mas/docs/provenance/tool_trace_live.json`](../../mas/docs/provenance/tool_trace_live.json), and [`mas/apps/tool-service/tool_service/usage.py`](../../mas/apps/tool-service/tool_service/usage.py)
- Core/API tests: [`test_trace_evidence.py`](../../mas/packages/mas-core/tests/test_trace_evidence.py), [`test_trace_evidence.py`](../../mas/apps/orchestrator-api/tests/test_trace_evidence.py)
- API observation fixture/check: [`test_api_observations.py`](../../mas/packages/mas-core/tests/test_api_observations.py), [`check_api_observability.py`](../../mas/scripts/check_api_observability.py)
- Native span fixture/check: [`test_native_trace_spans.py`](../../mas/packages/mas-core/tests/test_native_trace_spans.py), [`check_native_trace_spans.py`](../../mas/scripts/check_native_trace_spans.py)
- Identity delivery correlation: [`0002_mail_trace_correlation.py`](../../mas/apps/identity-service/migrations/versions/0002_mail_trace_correlation.py), [`identity_client.py`](../../mas/apps/orchestrator-api/orchestrator_api/identity_client.py), [`test_identity_service.py`](../../mas/apps/identity-service/tests/test_identity_service.py), and [`test_identity_reconciliation.py`](../../mas/apps/orchestrator-api/tests/test_identity_reconciliation.py)

## Remaining gates

- Connect native model/audit/worker/integration span writers to a live
  representative model-backed worker run and add provider/webhook-level
  identity-service mail-edge/bounce spans. The local transport/API writer and
  pure tool writer/read-back are now verified; delivery-attempt trace
  correlation is implemented but provider coverage remains unverified.
- Enforce sampling/retention and project-level narrowing in the live storage
  and recovery workers, including backup/restore parity.
- Add dashboard deep links and incident views after the API evidence is
  populated by a native/live deployment.
- Run multi-service load/soak and outage exercises; static fixtures do not
  certify production availability.
