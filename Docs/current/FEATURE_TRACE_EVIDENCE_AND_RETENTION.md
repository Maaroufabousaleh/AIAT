# Trace Evidence and Retention Feature Specification

**Baseline:** 2026-08-17
**Status:** the pure trace-context, native-span, and secret-safe trace-evidence
contracts are reviewed and committed in `77d5494` with deterministic fixtures;
the bounded API-observation schema/migrations are committed in `9c39919`, the
tool-service HTTP and usage-writer integration is committed in `53d38fc`, and
the API/storage trace writer, native-span, SLO projection, and operator
read-route integration is committed in `84a1c01`. Direct model-usage,
worker-artifact, integration-evidence, API-request correlation, native
transport/model/tool/audit/integration span persistence, and a deterministic
metadata-only retention planner are now queryable through the bounded route.
Worker-run trace/span context propagation into artifact and usage evidence is
now committed in `ceb7011`; the request contract validates bounded correlation
identifiers separately from task input, and task creation/dispatch reuse the
active request trace. The refreshed local orchestrator deployment is at migration
`0036_native_trace_spans`; bounded live transport and representative pure-tool
probes pass in the fresh 2026-08-11 local run. The transport probe observes one
API-request row plus one native `/health` span, while the tool probe proves one
`project_usage_events` row plus one `tool_service` native span and is retained
at [`mas/docs/provenance/tool_trace_live.json`](../../mas/docs/provenance/tool_trace_live.json).
The shared `aiat.mail-edge-observation.v1` normalizer/evaluator and deterministic
checker are implemented in `85369fe`; identity-service migration
`0003_mail_edge_observations`, signed delegated webhook persistence, and scalar
trace/SLO projection are implemented in `cfafe38`; the Resend/Svix raw-body
verifier and provider-facing ingress route are implemented in `2d21a2f`; the
live checker now classifies projected `mail.provider_webhook.<event>` spans in
`29d4da5` and can read the signed identity dashboard projection in `074ef8a`.
The bounded `aiat.trace-incident.v1` summary and fail-closed fixture/live-safe
checker are implemented in `c357fdf`; they classify scalar failure findings
without turning partial coverage into a false pass. Commit `b4b7cef` exposes the
summary through the operator-only `GET /observability/incidents/{trace_id}`
route, checked-in OpenAPI/TypeScript/Python contracts, a dashboard proxy, and
the existing `/logs?trace_id=…` deep link. The dashboard renders status,
severity, coverage, finding count, affected source names, and notice codes only;
commit `869202c` also renders the bounded finding IDs, source/kind, operation or
service, status/HTTP code, and occurrence timestamp as a payload-free
chronology. It never renders incident payloads. Live model/worker/audit/integration source
coverage, provider ingress certification, complete mail-edge spans, and richer
incident chronology remain open. `1d8aed5` adds the reusable
`aiat.worker-mail-edge-coverage.v1` join and deterministic certificate, which
requires the independent worker source and payload-free mail-edge signals to
be explicitly correlated without selecting or dispatching a worker. The
fixture evidence is [`worker_mail_edge_coverage_fixture.json`](../../mas/docs/provenance/worker_mail_edge_coverage_fixture.json);
it does not close live worker/provider evidence. `01996c9` adds a provider-neutral
`aiat.trace-retention-execution.v1` contract with a deterministic in-memory
rehearsal; `57e13cb` types its backup/read-back guard with matching source,
backup, and restored manifest digests, record counts, checked count, and
clean-target verification, while `15054ba` types the authoritative hold
snapshot with source, observed time, active/released state, duplicate
rejection, and project scope. Preview is non-mutating, while apply requires
project scope, the typed hold snapshot, typed parity evidence, and human
confirmation before one atomic adapter call. `5d71309` adds a typed bounded
audit envelope that carries only evidence references and scalar counts. The
`67f5eae` adds the provider-neutral `RetentionLegalHoldRegistry` read
contract and deterministic in-memory source for that snapshot. The live
registry/storage adapter, live archive/delete application, erasure, durable
production audit, and restore rollback remain open.
`f8829d6` adds an operator-only `GET /observability/retention/plan` read model,
generated contracts, and `check_trace_retention.py --live`. `b3fca97` makes
the response a typed Pydantic/OpenAPI model with bounded counts and candidate
fields. `9a80c6c` adds a fail-safe explicit-boolean `legal_hold` marker on
native-span metadata: held rows remain `retain`, are counted separately, and
never enter `deletion_ids`; ambiguous string markers do not activate a hold.
The route and checker classify bounded metadata only and explicitly prove
`mutation_performed: false`; live archive/delete application, hold authority,
erasure, project narrowing, durable audit, and restore parity remain open
gates.

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
- Worker-run requests carry bounded `trace_id`/`span_id` correlation metadata
  outside task input; controller completion copies those identifiers to worker
  usage and artifact records without storing task payloads.
- When the signed identity-service boundary is configured, outbound delivery
  attempts carrying safe trace/span IDs are projected as bounded `mail` spans.
  This correlates an AIAT request to the identity authority without importing
  recipients, subjects, provider IDs, relay errors, or message content.
- The shared `aiat.mail-edge-observation.v1` contract normalizes delivery
  attempts and adapter-verified provider webhook events into bounded event and
  failure states. The Resend adapter verifies the raw Svix signature before the
  event reaches the provider-facing identity route. Its coverage evaluator
  requires a verified webhook and a bounce/failure signal when those sources
  are claimed; conflicting event IDs remain `attention`. Identity-service
  migration `0003_mail_edge_observations` persists one payload-free row per
  `(provider,event_id)` and projects it beside delivery attempts; the checker
  recognizes those projected provider spans and can perform a signed,
  trace-filtered identity read-back, while configured live callback delivery
  and deployment evidence remain open.

Each item contains only safe operational fields: source, stable record ID,
kind, status, project/agent/team references, worker-run reference, event/model/
tool names, span ID, timestamp, duration, and cost. It never returns task input
or output, tool arguments/results, provider headers, error bodies, credentials,
arbitrary JSON metadata, or API request/response bodies, headers, or query
strings. API routes are normalized so IDs do not become unbounded dimensions.

The response includes per-source counts, project IDs, first/last observed time,
and a `PARTIAL_TRACE_SOURCES` notice that distinguishes the now-queryable
native transport/model/tool/audit/worker/integration span categories and
optional identity delivery-attempt/provider-event spans from the identity
mail-edge projection. Live provider and complete-span coverage remain separate.
Native category coverage is scalar metadata (`observed` or
`empty`); it never exposes native span attributes or payloads.
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

The model-backed worker source gate is now explicit in
`aiat.worker-trace-coverage.v1`. Its deterministic checker requires
`worker_usage_records`, `worker_artifacts`, `native_model_spans`, and
`native_worker_spans`; integration evidence can be added as an explicit
`--require-integration` requirement. Read-only live mode accepts a selected
trace ID. Dispatch mode is fail-closed and requires an explicitly selected
active model-backed worker, project, approved model profile, bounded budget,
and `--confirm-dispatch`; it does not auto-select or activate a worker. The
fixture passes, but no live model-backed run is claimed until an operator
supplies that selection and retains the resulting source-count report.

The cross-surface `aiat.worker-mail-edge-coverage.v1` evaluator now composes
that worker result with `evaluate_mail_edge_coverage()`. A required join must
carry an explicit trace and worker scope, observed worker usage/artifact/model/
worker sources (and integration sources when requested), a verified provider
webhook, and a bounce/failure signal. The deterministic checker
[`check_worker_mail_edge_coverage.py`](../../mas/scripts/check_worker_mail_edge_coverage.py)
passes with six worker/integration source rows plus one delivery attempt, one
verified delivery event, and one verified bounce; its report is payload-free,
non-mutating, and retained at
[`worker_mail_edge_coverage_fixture.json`](../../mas/docs/provenance/worker_mail_edge_coverage_fixture.json).
This is an explicit evidence join, not a live worker/provider certification or
an activation/release gate.

The operator-facing incident projection is derived from one
`aiat.trace-evidence.v1` response through `aiat.trace-incident.v1`. It reports
`clear`, `attention`, or `not_found`, an informational/warning/critical
severity, independent `complete`/`partial`/`empty` coverage, bounded source
counts, and stable finding references. HTTP 4xx/5xx and known failure statuses
become scalar findings; missing instrumentation remains a coverage notice.
`scripts/check_trace_incident.py --live` reads the existing operator trace
route and emits only this bounded summary. It is read-only and descriptive:
the checker does not dispatch workers, mutate records, apply retention, or
make a release decision, and an `attention` incident remains an observed
operator result rather than a gate.

The operator API route `GET /observability/incidents/{trace_id}` reuses the
same authenticated trace-evidence authority and returns `aiat.trace-incident.v1`.
The dashboard proxy `/api/observability/incidents/[trace_id]` assigns the
request to the operations section ACL and the canonical CEO evidence link
`/logs?trace_id=…` loads the summary without fetching or displaying raw trace
items. The chronology is limited to the bounded finding references already
returned by the API; it is a read-only operator deep link, not a release or
activation decision.

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
legal-hold/erasure authority, and active retention enforcement require a
separate live storage/recovery slice.

The local retention planner now exposes `aiat.trace-retention-plan.v1`. It
classifies native-span metadata as `retain`, `archive`, `delete`, or `invalid`,
prefers an explicit `retention_until` when present, and never returns invalid
rows as deletion candidates. An explicit boolean `legal_hold` marker on a row
or its scalar `attributes_json` metadata forces `retain`, increments the
separate legal-hold count, and cannot be overridden by expiry. The planner is
non-mutating; an operator or future recovery worker must separately review and
apply a storage action. The `aiat.trace-retention-execution.v1` contract now
provides that boundary as a provider-neutral adapter interface. Its
deterministic `InMemoryRetentionStore` rehearses preview and apply without
selecting a database or provider, validates project scope and the typed
authoritative hold snapshot, requires typed checksum/count/clean-target
backup-parity evidence and human confirmation for apply, and records one
typed bounded audit envelope (`5d71309`). The fixture obtains its hold snapshot
through `InMemoryRetentionLegalHoldRegistry` (`67f5eae`). It is a
fixture/review boundary only;
`scripts/check_trace_retention_execution.py --live` fails closed until a
production storage/recovery adapter is configured.

The operator-only `GET /observability/retention/plan` route reads a bounded set
of native-span metadata, applies the company retention policy, and returns the
plan with `mode: read-only-plan` and `mutation_performed: false`. An optional
`trace_id` narrows the read without changing authority. The live checker
(`scripts/check_trace_retention.py --live`) requires the operator endpoint,
validates the plan schema and non-mutation invariant, and emits only counts,
policy scalars, and notice totals; it never returns candidate payloads or
retention IDs. Generated clients expose the bounded `legal_hold` count and
candidate marker without turning the metadata into a live approval authority.
The generated API contract names the response, policy, count, and candidate
schemas, and rejects extra fields or a true mutation flag before serialization.

## Evidence commands

From `mas/`:

```bash
uv run --isolated pytest \
  packages/mas-core/tests/test_trace_evidence.py \
  packages/mas-core/tests/test_native_trace_spans.py \
  apps/orchestrator-api/tests/test_trace_evidence.py -q
uv run --isolated python scripts/check_trace_evidence.py --json
uv run --isolated python scripts/check_native_trace_spans.py --json
uv run --isolated pytest packages/mas-core/tests/test_trace_incident.py -q
uv run --isolated python scripts/check_trace_incident.py --json
uv run --isolated python scripts/check_trace_incident.py --live --json \
  --trace-id aiat-live-trace-check
uv run --isolated pytest packages/mas-core/tests/test_trace_retention.py -q
uv run --isolated python scripts/check_trace_retention.py --json
uv run --isolated python scripts/check_trace_retention.py --live --json \
  --trace-id aiat-live-trace-check
uv run --isolated pytest \
  packages/mas-core/tests/test_retention_execution.py \
  scripts/tests/test_check_trace_retention_execution.py -q
uv run --isolated python scripts/check_trace_retention_execution.py --json
uv run --isolated python scripts/check_trace_retention_execution.py --live --json
uv run --isolated python scripts/check_trace_evidence.py --live --json
uv run --isolated python scripts/check_live_trace_observability.py --live --json \
  --trace-id aiat-live-trace-check
uv run --isolated python scripts/check_live_tool_trace.py --live --json \
  --orchestrator-url http://127.0.0.1:8000 \
  --tool-service-url http://127.0.0.1:8002 \
  --trace-id aiat-live-tool-trace-check
uv run --isolated pytest packages/mas-core/tests/test_worker_trace_coverage.py -q
uv run --isolated python scripts/check_worker_trace_coverage.py --json
uv run --isolated python scripts/check_worker_trace_coverage.py --json \
  --require-integration
```

The fixture command passes without services and mutates no state. The live
commands require an orchestrator URL and operator API key; the live
observability command additionally performs one bounded health request. Missing
configuration, authentication, an outdated database migration, or unavailable
storage returns `blocked` with exit code 2 and no secret material.

## Code anchors

- Read model: [`mas/packages/mas-core/mas_core/observability/trace_evidence.py`](../../mas/packages/mas-core/mas_core/observability/trace_evidence.py)
- Worker source evaluator: [`mas/packages/mas-core/mas_core/observability/worker_trace_coverage.py`](../../mas/packages/mas-core/mas_core/observability/worker_trace_coverage.py)
- Trace validation/context: [`mas/packages/mas-core/mas_core/observability/tracing.py`](../../mas/packages/mas-core/mas_core/observability/tracing.py)
- Native span contract/normalizer: [`mas/packages/mas-core/mas_core/observability/native_spans.py`](../../mas/packages/mas-core/mas_core/observability/native_spans.py)
- Mail-edge observation contract/checker and identity persistence: [`Docs/current/FEATURE_MAIL_EDGE_OBSERVABILITY.md`](FEATURE_MAIL_EDGE_OBSERVABILITY.md), [`mas/packages/mas-core/mas_core/observability/mail_edge.py`](../../mas/packages/mas-core/mas_core/observability/mail_edge.py), [`mas/scripts/check_mail_edge_observations.py`](../../mas/scripts/check_mail_edge_observations.py) (`85369fe`), and migration `0003_mail_edge_observations`/signed route (`cfafe38`)
- Core review batch: commit `77d5494`; `test_tracing.py`, `test_native_trace_spans.py`, `test_trace_evidence.py`, `check_native_trace_spans.py`, and `check_trace_evidence.py` pass without database/provider mutation.
- Retention planner: [`mas/packages/mas-core/mas_core/observability/retention.py`](../../mas/packages/mas-core/mas_core/observability/retention.py)
- Retention execution contract/rehearsal: [`mas/packages/mas-core/mas_core/observability/retention_execution.py`](../../mas/packages/mas-core/mas_core/observability/retention_execution.py), [`mas/scripts/check_trace_retention_execution.py`](../../mas/scripts/check_trace_retention_execution.py), and [`mas/packages/mas-core/tests/test_retention_execution.py`](../../mas/packages/mas-core/tests/test_retention_execution.py) (`01996c9`, typed parity evidence `57e13cb`, hold snapshot `15054ba`, typed audit `5d71309`, registry read adapter `67f5eae`)
- Durable reads: [`mas/packages/mas-core/mas_core/memory/storage.py`](../../mas/packages/mas-core/mas_core/memory/storage.py)
- API observation contract/table/migration: [`mas/packages/mas-core/mas_core/observability/api_observations.py`](../../mas/packages/mas-core/mas_core/observability/api_observations.py), [`mas/packages/mas-core/mas_core/memory/models.py`](../../mas/packages/mas-core/mas_core/memory/models.py), [`mas/migrations/versions/0034_api_request_observations.py`](../../mas/migrations/versions/0034_api_request_observations.py)
- Direct model/artifact/integration trace columns and migration: [`mas/packages/mas-core/mas_core/worker_contract/models.py`](../../mas/packages/mas-core/mas_core/worker_contract/models.py), [`mas/packages/mas-core/mas_core/memory/storage.py`](../../mas/packages/mas-core/mas_core/memory/storage.py), [`mas/migrations/versions/0035_trace_correlation_evidence.py`](../../mas/migrations/versions/0035_trace_correlation_evidence.py)
- Native span table and migration: [`mas/packages/mas-core/mas_core/memory/models.py`](../../mas/packages/mas-core/mas_core/memory/models.py), [`mas/migrations/versions/0036_native_trace_spans.py`](../../mas/migrations/versions/0036_native_trace_spans.py)
- API route: [`mas/apps/orchestrator-api/orchestrator_api/main.py`](../../mas/apps/orchestrator-api/orchestrator_api/main.py)
- Fixture/live checker: [`mas/scripts/check_trace_evidence.py`](../../mas/scripts/check_trace_evidence.py)
- Incident projection/checker: [`mas/packages/mas-core/mas_core/observability/trace_incident.py`](../../mas/packages/mas-core/mas_core/observability/trace_incident.py), [`mas/scripts/check_trace_incident.py`](../../mas/scripts/check_trace_incident.py), and [`mas/scripts/tests/test_check_trace_incident.py`](../../mas/scripts/tests/test_check_trace_incident.py) (`c357fdf`)
- Model-backed worker source checker: [`mas/scripts/check_worker_trace_coverage.py`](../../mas/scripts/check_worker_trace_coverage.py)
- Worker/mail-edge evidence join (`1d8aed5`): [`mas/packages/mas-core/mas_core/observability/worker_trace_coverage.py`](../../mas/packages/mas-core/mas_core/observability/worker_trace_coverage.py), [`mas/scripts/check_worker_mail_edge_coverage.py`](../../mas/scripts/check_worker_mail_edge_coverage.py), [`mas/scripts/tests/test_check_worker_mail_edge_coverage.py`](../../mas/scripts/tests/test_check_worker_mail_edge_coverage.py), and [`mas/docs/provenance/worker_mail_edge_coverage_fixture.json`](../../mas/docs/provenance/worker_mail_edge_coverage_fixture.json)
- Local live transport checker/evidence (`eac83ae`, refreshed 2026-08-11): [`mas/scripts/check_live_trace_observability.py`](../../mas/scripts/check_live_trace_observability.py), [`mas/docs/provenance/trace_observability_live.json`](../../mas/docs/provenance/trace_observability_live.json)
- Local live tool checker/evidence (`eac83ae`, refreshed 2026-08-11): [`mas/scripts/check_live_tool_trace.py`](../../mas/scripts/check_live_tool_trace.py), [`mas/docs/provenance/tool_trace_live.json`](../../mas/docs/provenance/tool_trace_live.json), and [`mas/apps/tool-service/tool_service/usage.py`](../../mas/apps/tool-service/tool_service/usage.py). Both probes are fail-closed and emit no payloads, credentials, or project identifiers.
- Core/API tests: [`test_trace_evidence.py`](../../mas/packages/mas-core/tests/test_trace_evidence.py), [`test_trace_evidence.py`](../../mas/apps/orchestrator-api/tests/test_trace_evidence.py), [`test_trace_propagation.py`](../../mas/apps/orchestrator-api/tests/test_trace_propagation.py), and [`test_slo_capacity.py`](../../mas/apps/orchestrator-api/tests/test_slo_capacity.py)
- Worker source coverage tests: [`test_worker_trace_coverage.py`](../../mas/packages/mas-core/tests/test_worker_trace_coverage.py)
- API observation fixture/check: [`test_api_observations.py`](../../mas/packages/mas-core/tests/test_api_observations.py), [`check_api_observability.py`](../../mas/scripts/check_api_observability.py)
- Native span fixture/check: [`test_native_trace_spans.py`](../../mas/packages/mas-core/tests/test_native_trace_spans.py), [`check_native_trace_spans.py`](../../mas/scripts/check_native_trace_spans.py)
- Identity delivery/provider correlation: [`0002_mail_trace_correlation.py`](../../mas/apps/identity-service/migrations/versions/0002_mail_trace_correlation.py), [`0003_mail_edge_observations.py`](../../mas/apps/identity-service/migrations/versions/0003_mail_edge_observations.py), [`identity_client.py`](../../mas/apps/orchestrator-api/orchestrator_api/identity_client.py), [`test_identity_service.py`](../../mas/apps/identity-service/tests/test_identity_service.py), and [`test_identity_reconciliation.py`](../../mas/apps/orchestrator-api/tests/test_identity_reconciliation.py)

## Remaining gates

- Use the new fail-closed worker source checker and mail-edge checker against a selected live
  model-backed worker run and retain model-usage, artifact, native model, and
  native worker source counts; add native audit/integration evidence where the
  run exercises those adapters, plus provider/webhook-level identity-service
  mail-edge/bounce spans. The deterministic source contract, identity
  persistence, and worker/mail-edge evidence join are implemented, but no
  live worker dispatch or provider coverage is claimed yet.
- Connect the guarded execution contract to live storage and recovery workers;
  source legal holds from a live authoritative registry, persist audit records,
  prove backup/restore parity, and add erasure/rollback. The provider-neutral
  registry read contract (`67f5eae`), planner metadata guard, and
  `InMemoryRetentionStore` rehearsal are not live enforcement.
- Extend the bounded dashboard summary with richer chronology only after the
  incident projection is populated by native/live deployment evidence; the
  current `aiat.trace-incident.v1` API, proxy, and deep-link boundary is
  intentionally read-only, summary-only, payload-free, and non-gating.
- Run multi-service load/soak and outage exercises; static fixtures do not
  certify production availability.
