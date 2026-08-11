# PM ACTIVE dashboard and alerting

Dashboard views should show, for each PM connection and binding:

- lifecycle status and revision;
- doctor readiness and latest reconciliation run/counts;
- trusted actor mappings and authorized scopes;
- inbox status, provider delivery IDs, normalized field names, and conflicts;
- outbox status, delivery attempts, dispositions, and active dead letters;
- immutable lifecycle, actor, command, projection-suppression, and rollback
  evidence IDs.

Alert on any open conflict, active dead letter, pending/processing/failed
projection, stale provider event, revision drift, TLS failure, doctor blocker,
unexpected lifecycle transition, or repeated idempotency collision. Payloads,
credentials, cookies, authorization headers, and secret material must remain
redacted.
