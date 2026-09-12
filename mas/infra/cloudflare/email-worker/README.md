# AIAT Cloudflare Email Worker

This Worker is the default v1 inbound edge for `agents.aiat.ca`:

`Cloudflare Email Routing -> this Worker -> D1 metadata/registry + R2 raw MIME -> signed AIAT pull API`.

The Worker is not the AIAT identity system of record. AIAT Postgres owns worker
identity state, ownership grants, lifecycle transitions, verification evidence,
audits, and tool authorization. D1 contains only the exact recipient registry,
bounded event/message metadata, cursor-independent sequence numbers, replay
nonces, and retention markers. Raw MIME is stored under deterministic R2 keys
such as `inbound/<identity-id>/<message-id>.eml`; it is never put in D1.

## Local development

```sh
cp .dev.vars.example .dev.vars
npm install
npm run typecheck
npm run d1:migrate:local
npm run dev
```

The local Worker API uses the same HMAC request format as
`identity_service.providers.inbound.cloudflare.CloudflareInboundAdapter`.
The adapter is the normal caller; do not expose the API to workers or model
runtimes. The D1/R2 bindings are local Wrangler emulators in this profile.

## Deployment boundary

Before `npm run deploy`, the operator must replace the placeholder D1
`database_id`, create the production D1 database and R2 bucket, set
`MAIL_EDGE_AUTH_SECRET` as a Worker secret, and bind the Worker to Cloudflare
Email Routing for the exact `*@agents.aiat.ca` route. The Worker needs no
Cloudflare account API token at runtime.

The identity-service receives only the same narrow `MAIL_EDGE_AUTH_SECRET`
through `CLOUDFLARE_MAIL_EDGE_AUTH_SECRET`. Keep the value in the operator's
secret manager and ignored environment files; never put it in source,
fixtures, logs, D1, R2 metadata, or API responses.

## HTTP API

All API requests require the `aiat.mail-edge.v1` HMAC headers and a fresh
nonce. The supported routes are intentionally narrow:

- `GET /v1/health`
- `POST /v1/recipients/register`
- `POST /v1/recipients/{activate,suspend,retire}`
- `POST /v1/recipients/alias`
- `GET /v1/events?after=<sequence>&limit=<bounded-limit>`
- `GET /v1/messages/<message-id>`
- `POST /v1/events/<event-id>/ack`
- `POST /v1/messages/<message-id>/{processed,delete,protect}`

The SMTP envelope recipient (`EmailMessage.to`) is authoritative. A forged
`To:` header can affect only the parsed display content, never registry
ownership. Unknown, suspended, and retired recipients are rejected before R2
or D1 message persistence. Duplicate provider keys are idempotent; the key is
scoped by normalized envelope recipient so the same `Message-ID` can be
delivered independently to two different AIAT identities.

The scheduled cleanup removes expired raw objects while retaining D1 metadata.
Messages with a future `protected_until` are not removed; the identity-service
sets that marker after a verification extraction and the local transaction
remains authoritative if the edge is temporarily unavailable.
