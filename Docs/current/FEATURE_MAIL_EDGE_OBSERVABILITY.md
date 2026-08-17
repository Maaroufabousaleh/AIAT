# Mail-Edge and Provider Observation Feature Specification

**Baseline:** 2026-08-17
**Status:** the payload-free `aiat.mail-edge-observation.v1` contract, provider
webhook normalizer, coverage evaluator, fail-closed fixture/live checker, and
identity-service persistence/projection path are implemented in `85369fe` and
`cfafe38`. The deterministic identity, adapter, orchestrator, and core suites
pass. Live provider ingress verification, selected model-backed worker, bounce,
and deployment read-back evidence remain open.
**Authority:** [AIAT Target Programme](../../AIAT_TARGET_PROGRAMME.md)

## Purpose

Give the operator a consistent way to report identity-service mail-edge
observations alongside model-worker traces and SLOs. This is an observability
read model, not a mail sender, provider selector, worker activator, approval
authority, or second identity database.

## Implemented contract

`aiat.mail-edge-observation.v1` is represented by
[`MailEdgeObservation`](../../mas/packages/mas-core/mas_core/observability/mail_edge.py).
It accepts three sources:

- `delivery_attempt` for the identity service's existing signed outbound
  attempt rows;
- `provider_webhook` for a provider adapter that has verified the webhook
  signature; and
- `provider_poll` for a bounded provider read-back when an adapter supports
  polling.

The event vocabulary is deliberately small: `queued`, `sent`, `delivered`,
`deferred`, `bounced`, `complained`, `failed`, and `unknown`. Outcome and
transient/permanent failure class are derived from that enum rather than
accepted as arbitrary provider text.

`normalize_provider_webhook()` accepts a provider body only at the adapter
boundary. It retains opaque event/message references, a bounded timestamp,
safe trace/span correlation, and an allow-listed scalar metadata set. Bodies,
recipients, subjects, headers, tokens, credentials, provider payloads, and
arbitrary JSON are dropped. The shared model can represent an unsigned
observation for fixture evaluation, but the identity-service persistence route
rejects it; only an adapter-verified webhook can enter the durable evidence
projection.

`evaluate_mail_edge_coverage()` deduplicates identical event IDs and reports a
conflict as `attention`. A passing report requires a verified provider webhook
and a bounce/failure signal; an optional selected trace and worker must also be
correlated when supplied. The report includes source/event counts and missing
signals only. `licence_metadata_is_gate` is always `false`.

## Checker and live boundary

From `mas/`:

```bash
uv run --isolated pytest packages/mas-core/tests/test_mail_edge.py -q
uv run --isolated python scripts/check_mail_edge_observations.py --json
uv run --isolated python scripts/check_mail_edge_observations.py --live --json \
  --url http://127.0.0.1:8000 \
  --api-key "$AIAT_OPERATOR_API_KEY" \
  --worker-id "$AIAT_LIVE_WORKER_ID" \
  --trace-id "$AIAT_LIVE_WORKER_TRACE_ID"
```

Fixture mode performs no network or state mutation. Live mode reads only
`GET /observability/traces/{trace_id}` and requires the operator to choose the
representative worker and its trace. It never dispatches a worker, sends mail,
creates credentials, changes a provider, or selects a worker automatically.
Missing configuration or unavailable authentication returns `blocked` with
exit code 2. Existing delivery-attempt mail spans report `attention` until a
selected live worker and provider supply verified webhook and bounce evidence.

## Integration boundary

The identity service remains the authority for mailbox, outbound request,
provider, and credential state. A provider adapter is responsible for
signature verification and for passing only normalized event metadata into
the signed `POST /v1/mail-edge/provider-webhook` boundary. Migration
`0003_mail_edge_observations` stores one payload-free row per
`(provider,event_id)`, rejects conflicting replays, correlates an outbound
request by opaque provider message reference when possible, and projects
provider events alongside delivery attempts through `mail-relay`. The
orchestrator reduces that dashboard response to scalar trace/SLO rows and
must not import message content or provider secrets.

This feature does not change the personal/internal resource policy. Licence
and stated-use information remains in the provenance catalogue and operator
notices only; it is not an installation, activation, execution, or evidence
predicate.

## Remaining evidence

- run the checker against an explicitly selected live model-backed worker and
  provider ingress;
- verify the deployed provider adapter's signature result and read back a
  durable bounce observation from identity-service/Postgres;
- project the live observations into complete mail native spans and SLO timing;
- retain deployment evidence without claiming provider or worker coverage
  when a source is absent; and
- separately enforce live retention, recovery, and production mail controls.

## Code anchors

- Contract, normalizer, evaluator:
  [`mas/packages/mas-core/mas_core/observability/mail_edge.py`](../../mas/packages/mas-core/mas_core/observability/mail_edge.py)
- Fixture/live checker:
  [`mas/scripts/check_mail_edge_observations.py`](../../mas/scripts/check_mail_edge_observations.py)
- Focused tests:
  [`mas/packages/mas-core/tests/test_mail_edge.py`](../../mas/packages/mas-core/tests/test_mail_edge.py)
- Existing identity delivery projection:
  [`mas/apps/orchestrator-api/orchestrator_api/identity_client.py`](../../mas/apps/orchestrator-api/orchestrator_api/identity_client.py)
- Identity persistence, signed route, and migration:
  [`mas/apps/identity-service/identity_service/service.py`](../../mas/apps/identity-service/identity_service/service.py),
  [`mas/apps/identity-service/identity_service/store.py`](../../mas/apps/identity-service/identity_service/store.py),
  [`mas/apps/identity-service/migrations/versions/0003_mail_edge_observations.py`](../../mas/apps/identity-service/migrations/versions/0003_mail_edge_observations.py)
- Identity and orchestrator coverage:
  [`mas/apps/identity-service/tests/test_identity_service.py`](../../mas/apps/identity-service/tests/test_identity_service.py),
  [`mas/apps/orchestrator-api/tests/test_identity_reconciliation.py`](../../mas/apps/orchestrator-api/tests/test_identity_reconciliation.py)
- Existing trace read route:
  [`mas/apps/orchestrator-api/orchestrator_api/main.py`](../../mas/apps/orchestrator-api/orchestrator_api/main.py)
- Related trace/SLO specifications:
  [`FEATURE_TRACE_EVIDENCE_AND_RETENTION.md`](FEATURE_TRACE_EVIDENCE_AND_RETENTION.md),
  [`FEATURE_SLO_CAPACITY_AND_OPERATIONS.md`](FEATURE_SLO_CAPACITY_AND_OPERATIONS.md)
