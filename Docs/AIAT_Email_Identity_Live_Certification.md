# AIAT Cloudflare inbound live certification

Certification scope: the default Cloudflare Email Routing -> Worker -> D1
inbound path and its signed identity-service synchronization boundary. This is
provider evidence for inbound mail only; it is not a release claim for the
complete email subsystem or the full production identity-service runtime.

## Certification result

```text
LIVE_CLOUDFLARE_INBOUND = PASS
CLOUDFLARE_INBOUND_LIVE = PASS
CERTIFICATION_DATE = 2026-09-12
CERTIFICATION_RUN = LIVE-CLOUDFLARE-INBOUND-2026-09-12
```

The operator-provided live smoke run proved this path:

```text
external sender
  -> agents.aiat.ca MX
  -> Cloudflare Email Routing catch-all
  -> aiat-mail-edge
  -> recipient authorization
  -> D1 chunked raw MIME
  -> inbound event
  -> signed HMAC API
  -> AIAT CloudflareInboundAdapter
  -> MIME reconstruction and parsing
```

Secret-safe evidence recorded from that run:

- `agents.aiat.ca` MX routed through Cloudflare and the catch-all invoked
  `aiat-mail-edge`.
- Signed `/v1/health` passed.
- Signed recipient registration passed.
- A real external inbound message was accepted and produced an
  `inbound.message.received` event.
- The event correlated to identity
  `live-smoke-identity-20260912` and worker
  `live-smoke-worker-20260912` for the envelope recipient
  `w-live-smoke-20260912@agents.aiat.ca`.
- D1 raw-message reconstruction and provider-neutral MIME parsing passed.
- The explicitly permitted smoke-test subject matched
  `AIAT live inbound smoke test`.

The external sender address, message body, verification code, raw MIME,
authentication secret, and provider credentials are deliberately absent from
this record.

## Production runtime hardening evidence

The production identity runtime also includes the following remediation,
validated before the inbound certification was treated as canonical:

- The official PostgreSQL image could not initialise its persistent data
  directory while `cap_drop: [ALL]` was applied. The permanent Compose fix
  retains `cap_drop: [ALL]` and restores only `CHOWN`, `DAC_OVERRIDE`,
  `FOWNER`, `SETGID`, and `SETUID`, which are required by the image entrypoint
  for first-time directory ownership and permission setup.
- The identity-service image previously copied Alembic assets as restrictive
  root-owned files. The permanent Dockerfile fix copies `alembic.ini` and the
  migrations with UID/GID `10001:10001` ownership and normal read/execute
  permissions, while retaining the non-root runtime user `10001:10001`.
- The remediation is committed in
  `528ad79199ea5fa2e808ca3f89012166cda57bf9`
  (`fix(identity): harden production database startup and migration assets`).
- The production database migrated through
  `0001_identity_control_plane`, `0002_mail_trace_correlation`,
  `0003_mail_edge_observations`, and `0004_provider_neutral_mail`; Alembic
  reports `0004_provider_neutral_mail (head)`. The identity-service
  `/healthz` and `/readyz` checks passed.

## Temporary certification identity

The temporary recipient was retired through the signed lifecycle API after the
certification run. Its provider reference is a non-secret opaque registration
value returned by the Worker; for the current deterministic recipient binding
it was:

```text
recipient:e2ad9f0588e174b1f82ebef7e389ad1b
```

The supported cleanup command is documented in the domain runbook. The live
call proved the `VERIFYING`/`ACTIVE` binding reaches `RETIRED`; it did not send
another external message. A separate operator-controlled rejection check may
be run after retirement if needed.

## Certification follow-through

```text
TEMPORARY_RECIPIENT_RETIREMENT = PASS
```

## Still pending

```text
IDENTITY_SERVICE_LIVE = PASS (inbound reconciliation boundary only)
REAL_IDENTITY_PROVISIONING = PASS
REAL_INBOUND_RECONCILIATION = PASS
RESEND_API_AUTH = PASS_SENDING_ACCESS
RESEND_DOMAIN_MANAGEMENT_READ = NOT_AVAILABLE_RESTRICTED_API_KEY
RESEND_OUTBOUND_LIVE = NOT_YET_LIVE_CERTIFIED
RESEND_WEBHOOK_LIVE = NOT_YET_LIVE_CERTIFIED
REAL_AIAT_HIRING_LIFECYCLE_INTEGRATION = NOT_YET_LIVE_CERTIFIED
RESEND_OUTBOUND = NOT_YET_LIVE_CERTIFIED
FULL_PRODUCTION_IDENTITY_SERVICE = NOT_YET_STARTED_OR_CERTIFIED
CLOUDFLARE_INBOUND_RETRY_RESTART_RECOVERY = NOT_YET_LIVE_CERTIFIED
```

The repeatable operator command is
[`mas/scripts/certify_cloudflare_mail_edge_live.py`](../mas/scripts/certify_cloudflare_mail_edge_live.py).
It reads only `CLOUDFLARE_MAIL_EDGE_URL` and
`CLOUDFLARE_MAIL_EDGE_AUTH_SECRET` from the environment, uses the production
adapter/HMAC implementation, and emits only bounded metadata and pass/fail
status.

## Canonical AIAT inbound reconciliation

The later live run used a fresh UUID-backed identity through the real
identity-service lifecycle rather than treating the temporary smoke mailbox as
the production test identity. The following evidence is intentionally limited
to identifiers and state transitions:

```text
CANONICAL_RUN = LIVE-CLOUDFLARE-INBOUND-AIAT-2026-09-12
worker_id = 7125b01c-974e-4f73-abf6-b1ac72b5b66e
identity_id = 976a7349-fc76-405e-b395-e60c3911c9eb
address = w-7125b01c-974e-4f73-abf6-b1ac72b5b66e@agents.aiat.ca
provider_event_id = evt-bbf91ddf532b44f9b6f6f4601acc3630d277b36bf7741845
provider_message_id = m-7034ad555f2f4dafa945dde634d6d5f5
```

Secret-safe live evidence for that run:

- The real identity-service/Postgres runtime reached `/healthz` and `/readyz`;
  the production database was at Alembic head `0004_provider_neutral_mail`.
- Governed allocation created the identity, registered its Cloudflare binding,
  and left the identity in `IDENTITY_VERIFYING` until delivery evidence was
  available.
- A real external inbound message reached the canonical envelope recipient;
  the Worker stored it in D1 chunks, the identity-service retrieved and
  reconstructed the raw message, and ownership/correlation matched the worker
  and identity above.
- The normalized `inbound.message.received` event was persisted before the
  signed ACK, and the identity-service reconciliation cursor advanced to `2`.
- Delivery verification persisted `INBOUND_RECEIVED` and
  `DELIVERY_VERIFIED`, then the identity reached `IDENTITY_ACTIVE`.
- The certification identity was subsequently archived through the governed
  lifecycle API and its Cloudflare binding reached `RETIRED`; no registry or
  D1 row was deleted directly.

An earlier historical smoke event was sequence `1` from a standalone smoke
certificate. It used non-UUID temporary identity and worker identifiers that
are incompatible with canonical identity-service synchronization. Its exact
safe envelope metadata was verified, it was ACKed through the signed
Cloudflare provider adapter, and `PostgresIdentityStore.advance_inbound_sync_cursor()`
advanced the durable cursor from `0` to `1`. It was not processed by
canonical identity-service synchronization. The canonical run above is the
sequence-2 proof, and the final cursor is `2`.

The external sender, message body, verification code, raw MIME, authentication
secrets, webhook secrets, and provider credentials are deliberately absent
from this record.

## Current production boundary

The identity database and local production container are healthy, but the full
production runtime is not yet certified. A secret-safe Resend API probe using
the existing injected credential returned the structured provider
classification `restricted_api_key` with HTTP `401`. This confirms the
intentional least-privilege `sending_access` mode; it is not an invalid-key
result, and no credential was rotated or replaced. Domain-management reads are
unavailable in this mode, so transport certification must use the bounded
`/emails` send endpoint with the configured production sending domain. No
outbound message has yet been sent.

```text
IDENTITY_SERVICE_RUNTIME_LOCAL = PASS
PRIVATE_WEBHOOK_GATEWAY = PASS (unrelated paths 404; unsigned POST 401)
PUBLIC_IDENTITY_INGRESS = NOT_YET_REACHABLE
CLOUDFLARE_TUNNEL = CONNECTED_TO_UNRELATED_PM_GATEWAY
RESEND_API_AUTH = PASS_SENDING_ACCESS
RESEND_DOMAIN_MANAGEMENT_READ = NOT_AVAILABLE_RESTRICTED_API_KEY
RESEND_OUTBOUND_LIVE = NOT_YET_LIVE_CERTIFIED
RESEND_WEBHOOK_LIVE = NOT_YET_LIVE_CERTIFIED
REAL_AIAT_HIRING_LIFECYCLE_INTEGRATION = NOT_YET_LIVE_CERTIFIED
FULL_PRODUCTION_IDENTITY_SERVICE = NOT_YET_STARTED_OR_CERTIFIED
OUTBOUND_RELAY_CERTIFIED = false
DEFAULT_OUTBOUND_ENABLED = false
DIRECT_MX_OUTBOUND_ENABLED = false
```
