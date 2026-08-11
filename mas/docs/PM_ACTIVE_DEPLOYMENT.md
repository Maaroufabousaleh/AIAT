# PM ACTIVE deployment gates

The orchestrator API must load the provider-neutral ACTIVE policy and the
transactional PM evidence path before activation. Restart the governed API
service through the project Compose configuration, then verify health before
using lifecycle endpoints.

Required production gates are doctor ready, cursor-reset reconciliation with
zero drift/conflicts/hash/version/scope mismatches, zero unresolved active
dead letters, zero pending/processing/failed projections, verified TLS
certificate and hostname checks, and a 401 response to unauthenticated
management requests.

Lifecycle transitions use persisted digest-bound plans only. Rollback evidence
and the current timeout disposition are recorded in
[PM_ACTIVE_CERTIFICATION_LEDGER.md](PM_ACTIVE_CERTIFICATION_LEDGER.md).
