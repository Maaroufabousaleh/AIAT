# AIAT Cloudflare Email Worker

This Worker is the default v1 inbound edge for `agents.aiat.ca`:

`Cloudflare Email Routing -> Email Worker -> D1-only metadata and chunked raw MIME -> signed AIAT pull API`.

The Worker is not the AIAT identity system of record. AIAT Postgres owns worker
identity state, ownership grants, lifecycle transitions, verification evidence,
audits, and tool authorization. D1 contains the exact recipient registry,
bounded event/message metadata, replay nonces, and retention markers. New raw
MIME is stored in ordered `mail_message_chunks` BLOB rows; it is never placed
in an ordinary metadata or event row.

## Default storage and limits

The default is `MAIL_EDGE_STORAGE_BACKEND=d1`. The Worker streams the incoming
`EmailMessage.raw` body into 256 KiB chunks and retains only a bounded chunk,
header prefix, and hashing state while ingesting. It verifies each chunk's
binary length and SHA-256, then verifies the reconstructed ordered stream's
length and whole-message SHA-256 before creating an `inbound_events` row.

The selected chunk size is deliberately conservative. Cloudflare's current
[D1 limits](https://developers.cloudflare.com/d1/platform/limits/) document a
2,000,000-byte maximum for a string, BLOB, or table row. A 262,144-byte BLOB
leaves substantial room for row and binding overhead. The default 4 MiB raw
message limit therefore uses at most 16 chunks and keeps the D1 verification
query work bounded. These are provider-controlled limits and must be
rechecked before materially changing the bound.

`MAIL_EDGE_MAX_MESSAGE_BYTES` defaults to `4194304` (4 MiB) in the D1 profile.
That comfortably covers normal verification, login-link, recovery, and
transactional messages without turning v1 into a general mailbox. Cloudflare
Email Service currently documents a 25 MiB inbound message limit in its
[platform limits](https://developers.cloudflare.com/email-service/platform/limits/);
AIAT deliberately applies the smaller application limit. A known oversize
message is rejected before storage. If the stream crosses the limit after
ingestion has started, the Worker cancels the stream, deletes partial chunks,
marks only safe metadata as `REJECTED_OVERSIZE`, and emits no normal AIAT event.
It never silently truncates a message.

The D1 free plan currently documents 5 million rows read/day, 100,000 rows
written/day, and 5 GB account storage in the
[D1 pricing documentation](https://developers.cloudflare.com/d1/platform/pricing/).
The per-database limit is currently 500 MB on Workers Free in the
[D1 limits](https://developers.cloudflare.com/d1/platform/limits/) table. As a
raw-capacity order-of-magnitude estimate, 20 KiB verification messages fit in
roughly 25,000 messages before metadata, indexes, SQLite overhead, registry
rows, and cleanup churn; actual retained capacity is lower. D1-only is
intentionally sized for AIAT's small transactional-email workload, not
high-volume permanent attachment storage. No pricing or permanence guarantee
is implied.

## Schema and crash-safe state

Migration `0002_d1_chunked_raw_messages.sql` adds:

- `mail_messages`: recipient-scoped provider identity, sender/subject
  metadata, raw length/hash, selected backend, chunk count, lifecycle state,
  retention markers, and the optional event ID;
- `mail_message_chunks`: `(message_id, chunk_index)` primary key, BLOB data,
  per-chunk length/hash, a foreign key with `ON DELETE CASCADE`, and an order
  index;
- state, retention, identity, and provider-key indexes.

The state sequence is `RECEIVING -> STORED -> EVENT_READY -> ACKNOWLEDGED`.
`REJECTED`, `REJECTED_OVERSIZE`, and `DELETED` are terminal non-deliverable
states. A receiving row may contain zero or some chunks but cannot have an
event. If the final event insert fails, the next signed event-list request
re-verifies the stored payload and publishes it idempotently. Abandoned
`RECEIVING` rows are reclaimed by the bounded scheduled cleanup. Event fetch,
local Postgres commit, and ACK failures remain retryable through the existing
cursor/ACK protocol.

The provider key remains recipient-scoped: normalized envelope recipient plus
RFC Message-ID when present, or the raw content hash otherwise. A repeated
delivery to the same recipient is idempotent; the same RFC Message-ID sent to
two recipients remains two independent messages. The SMTP envelope recipient
(`EmailMessage.to`) is authoritative; the untrusted `To:` header cannot move
ownership. The optional `worker_id` fetch query provides a defense-in-depth
scope check while the existing identity-service authorization remains the
primary worker isolation boundary.

## Local development

```sh
cp .dev.vars.example .dev.vars
npm ci
npm run typecheck
npm test
npm run d1:migrate:local
npm run dev
```

The default local Worker uses only D1. No R2 binding or bucket is needed. The
signed API uses the same HMAC request format as
`identity_service.providers.inbound.cloudflare.CloudflareInboundAdapter`.
The identity-service adapter is the normal caller; do not expose the API to
workers or model runtimes.

## Default deployment boundary

The existing production D1 database is already configured in
`wrangler.toml`:

```text
database_name = "aiat-mail-edge"
database_id   = "f52e0f58-d4b1-443e-9e30-c6b1784608f2"
```

The default `wrangler.toml` has no `r2_buckets` binding. After local checks
pass, the operator-owned default sequence is:

```sh
cd /mnt/c/projects/aiat/mas/infra/cloudflare/email-worker
npm ci
npm run d1:migrate:remote
npx wrangler secret put MAIL_EDGE_AUTH_SECRET
npm run deploy
```

This path does not create or require an R2 bucket and does not require R2
billing activation. It still requires the operator's authenticated Wrangler
session, the existing D1 database, and the Worker secret. Do not enable Email
Routing or mutate DNS until the deployment is deliberately reviewed.

## Optional R2 profile

The old R2 object implementation remains behind the explicit `env.r2` profile
for a future workload that chooses it. It is not part of the default config or
test environment. If intentionally selected, create only the optional bucket
and deploy explicitly:

```sh
npx wrangler r2 bucket create aiat-mail-edge-raw
npm run deploy -- --env r2
```

The optional profile uses D1 metadata plus R2 raw objects and requires the
`MAIL_OBJECTS` binding. The API, recipient ownership, signed sync, ACK, and
retention contracts remain the same.

## HTTP API and privacy boundary

All API requests require the `aiat.mail-edge.v1` HMAC headers and a fresh
nonce. Supported routes are:

- `GET /v1/health`
- `POST /v1/recipients/register`
- `POST /v1/recipients/{activate,suspend,retire}`
- `POST /v1/recipients/alias`
- `GET /v1/events?after=<sequence>&limit=<bounded-limit>`
- `GET /v1/messages/<message-id>`
- `POST /v1/events/<event-id>/ack`
- `POST /v1/messages/<message-id>/{processed,delete,protect}`

The Worker never logs verification codes, sensitive bodies, raw MIME, secrets,
or provider payloads. Raw message cleanup removes D1 chunks together with the
message payload state and retains only bounded metadata. Active
`protected_until` verification holds block deletion. Cleanup scans and deletes
at most a bounded batch per scheduled invocation.
