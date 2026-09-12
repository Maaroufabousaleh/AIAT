# AIAT email identity provider architecture

Status: repository implementation complete for the provider-neutral v1 path.
Cloudflare, DNS, Resend account, and external delivery certification remain
operator-owned live boundaries and are not implied by repository tests.

## Default v1 topology

The default production configuration is deliberately split by direction:

```text
Inbound:
Cloudflare Email Routing
  -> Cloudflare Email Worker
  -> D1 recipient registry + bounded event metadata + chunked raw MIME
  -> signed HMAC pull/sync
  -> AIAT identity-service/Postgres
  -> signed orchestrator/tool-service/browser clients

Outbound:
worker/tool request
  -> signed identity-service
  -> ownership + approval + policy + credit/quota/rate/idempotency gates
  -> direct Resend HTTPS API
```

AIAT remains the authority for worker identity, company ownership, lifecycle,
approvals, usage, credentials, browser sessions, audit, outbox, reconciliation,
and dashboard state. Cloudflare is an untrusted transport/storage edge. Resend
is an untrusted outbound transport. Neither provider is a worker-facing
credential boundary.

The default bundle is [`mas/infra/cloudflare/docker-compose.yml`](../mas/infra/cloudflare/docker-compose.yml).
The managed edge source is [`mas/infra/cloudflare/email-worker/`](../mas/infra/cloudflare/email-worker/).
The signed identity-service synchronization origin is the Worker Custom Domain
`https://mail-edge.aiat.ca`, configured with `custom_domain = true` while
`workers_dev = false` remains disabled. The default design does not depend on a
`workers.dev` hostname or a traditional `mail-edge.aiat.ca/*` route.

## Provider-neutral contracts

Identity-service code depends on inbound and outbound capability contracts, not
on JMAP accounts or SMTP mailboxes. Each identity has independent provider
bindings for `INBOUND` and `OUTBOUND`, with an opaque provider reference,
lifecycle state, and redacted metadata. The historical
`provider_account_id` field remains only for Stalwart compatibility.

Inbound provisioning registers the exact normalized envelope recipient with
the edge and persists the binding before granting mailbox access. The edge
accepts only `VERIFYING` or `ACTIVE` registry rows. Suspension and retirement
revoke AIAT access before attempting provider lifecycle changes; provider
failure is retained as retryable audit state.

Inbound synchronization is cursor-based and acknowledges an edge event only
after the raw/normalized message copy, local mail event, and ownership checks
have committed. Retries are idempotent. Provider event identity is scoped to
the recipient, so one provider `Message-ID` cannot merge two workers' mail.

Outbound sends retain the existing identity-service controls: signed caller,
worker ownership, recipient policy, durable approval, credit/usage holds,
provider rate limits, durable claim, provider idempotency key, delivery attempt,
outbox, reconciliation, and sanitized correlation evidence. Resend receives
only the direct API call made after those gates; it is never called by a worker
or browser runtime.

## Edge data and retention

The Worker uses the SMTP envelope recipient (`EmailMessage.to`) as the sole
ownership key. The `To:` MIME header is parsed only for display content. An
unknown, suspended, retired, malformed, or oversized recipient/message is
rejected before raw persistence.

D1 stores the recipient registry, event ordering, message metadata, hashes,
acknowledgements, replay nonces, retention markers, and (by default) raw MIME
as ordered, checksummed `mail_message_chunks` BLOB rows. No ordinary metadata
or event row contains raw MIME. The default D1 path uses no R2 binding. An
explicit optional R2 profile can store raw MIME at deterministic keys such as
`inbound/<identity-id>/<message-id>.eml`; its API and state machine are the
same. Verification extraction may place a short-lived protection marker; the
AIAT Postgres transaction remains authoritative if the edge protection call
is unavailable.

The identity service keeps an encrypted, bounded local read copy so existing
mail list/read and verification tools continue to work offline after a
successful sync. Plaintext MIME, message bodies, provider payloads, API keys,
and raw credentials are not written to D1 metadata, audit, outbox, dashboard,
or worker responses.

## Signed edge boundary

Identity-service calls the narrow Worker API using
`aiat.mail-edge.v1` with HMAC over version, method, path/query, timestamp,
nonce, and body hash. The Worker stores used nonces in D1 and rejects stale or
replayed requests. Requests are bounded, redirects are not followed, response
errors are sanitized, and the Worker has no Cloudflare account API token.

Supported edge operations are health, exact-recipient registration and
lifecycle, alias registration, event listing, message fetch, event
acknowledgement, processed state, deletion, and temporary retention
protection at `https://mail-edge.aiat.ca`. The identity service is the only
intended caller.

## Optional Stalwart profile

Stalwart is retained as an optional full-mailbox provider for cases that need
JMAP mailbox history, SMTP ingress, or a self-hosted mailbox server. Select it
explicitly with:

```text
IDENTITY_INBOUND_PROVIDER=stalwart
IDENTITY_OUTBOUND_PROVIDER=stalwart
```

The preserved bundle is [`mas/infra/mail-edge/`](../mas/infra/mail-edge/),
and the loopback development profile is
[`mas/infra/compose/docker-compose.stalwart-local.yml`](../mas/infra/compose/docker-compose.stalwart-local.yml).
Those profiles retain their separate Stalwart management key and mail JMAP
service credential. They do not change the default Cloudflare/Resend path and
must not share its database, namespace, or environment file.

## Operational status

Repository tests use a deterministic D1-only Worker fixture, an explicit
optional-R2 regression fixture, and mocked provider HTTP. They prove the local
protocol and governance boundaries, not live Cloudflare routing, DNS, Resend
account authorization, or external inbox delivery. The final activation latch
remains false until the operator
records the live certification described in
[`Docs/AIAT_Email_Identity_Domain_Migration.md`](AIAT_Email_Identity_Domain_Migration.md).
