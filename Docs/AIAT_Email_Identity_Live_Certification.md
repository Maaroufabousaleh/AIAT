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
IDENTITY_SERVICE_LIVE = NOT_YET_STARTED_OR_CERTIFIED
REAL_IDENTITY_PROVISIONING = NOT_YET_LIVE_CERTIFIED
REAL_INBOUND_RECONCILIATION = NOT_YET_LIVE_CERTIFIED
RESEND_API_AUTH = NOT_YET_CERTIFIED
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
