# AIAT identity-mail domain and provider migration

This runbook describes the current provider-neutral v1 deployment. The
authoritative architecture and boundary decisions are in
[`AIAT_Email_Identity_Provider_Architecture.md`](AIAT_Email_Identity_Provider_Architecture.md).

## Profiles

| Profile | Entry point | Namespace | Inbound | Outbound |
| --- | --- | --- | --- | --- |
| `development` / `mail-local` | `mas/infra/compose/docker-compose.stalwart-local.yml` | `agents.aiat.local` | loopback Stalwart | disabled |
| `production` / Cloudflare v1 | `mas/infra/cloudflare/docker-compose.yml` plus `email-worker/` | `agents.aiat.ca` | Cloudflare Email Routing + Worker/D1-only (optional Worker R2 profile) | direct Resend API, approval-gated |
| optional full mailbox | `mas/infra/mail-edge/docker-compose.yml` | `agents.aiat.ca` | explicit Stalwart | explicit Stalwart or direct Resend |

The local namespace, Postgres volume, provider registry, and secrets are
separate from production. Never add `agents.aiat.local` as a production alias
and never point a local environment file at a production database or edge.

## Current production flow

Inbound mail follows:

```text
Cloudflare Email Routing -> Email Worker -> D1 metadata + chunked raw MIME
  -> signed pull -> AIAT identity-service/Postgres -> governed clients
```

Outbound mail follows:

```text
governed AIAT request -> signed identity-service gates
  -> direct HTTPS call to Resend /emails
```

Stalwart is not constructed by the default Compose profile. Workers,
orchestrator clients, tool-service, browser runtimes, and dashboards never
receive provider credentials or direct mail access.

## DNS and routing

The exact Cloudflare Email Routing MX targets are account/zone output and must
be copied from the operator's Cloudflare dashboard or reviewed API export.
Do not guess or commit them. Configure the exact route:

```text
*@agents.aiat.ca -> the deployed AIAT mail Worker
```

The Worker HTTP synchronization API is a separate HTTPS Custom Domain at
`https://mail-edge.aiat.ca`. It is declared in
`mas/infra/cloudflare/email-worker/wrangler.toml` as:

```toml
[[routes]]
pattern = "mail-edge.aiat.ca"
custom_domain = true
```

Keep `workers_dev = false`; do not substitute a `workers.dev` URL or a
traditional `mail-edge.aiat.ca/*` route. Set the production identity-service
value to `CLOUDFLARE_MAIL_EDGE_URL=https://mail-edge.aiat.ca`. The subsequent
operator-owned Worker deployment creates/attaches this Custom Domain and its
Cloudflare-managed certificate.

The Worker registry, not a wildcard route, authorizes individual AIAT
recipients. An unknown, suspended, retired, malformed, or oversized recipient
is rejected before message persistence. The default Worker stores raw MIME in
ordered D1 chunks and emits no event until chunk count, lengths, and the whole
message checksum verify. The SMTP envelope recipient is authoritative; a
forged `To:` header cannot redirect ownership.

Create the public identity-service DNS/TLS endpoint required by the chosen
ingress policy, normally `identity.aiat.ca` terminating at the Compose Caddy
ingress. The default bundle has no public SMTP listener and does not require a
`mail.aiat.ca` MX/A record. Keep database, Worker API administration, and
provider account administration private.

For direct Resend sending, verify `agents.aiat.ca` in the Resend account and
publish the exact provider-issued SPF, DKIM, return-path, and DMARC records.
Resend DNS values are account- and region-specific; the repository contains
names/placeholders only. Do not add a guessed SPF, DKIM, or return-path value.
After exporting those exact values into the shell (not a committed env file),
run [`mas/infra/cloudflare/scripts/validate-dns.sh`](../mas/infra/cloudflare/scripts/validate-dns.sh)
to check public records. The script does not verify or mutate the Cloudflare
dashboard route.

## Repository deployment sequence

These commands perform local/static work only until the operator deliberately
uses the remote Wrangler and provider consoles. They do not create accounts,
change DNS, or retrieve secrets:

```sh
cd /mnt/c/projects/aiat/mas/infra/cloudflare/email-worker
npm ci
npm run typecheck
npm run test
npm run d1:migrate:local

cd /mnt/c/projects/aiat/mas/infra/cloudflare
docker compose --env-file .env.cloudflare-mail-edge.example config -q
```

For the operator-owned deployment, in order:

1. Use the existing production D1 database configured in
   `email-worker/wrangler.toml` (`aiat-mail-edge`, ID
   `f52e0f58-d4b1-443e-9e30-c6b1784608f2`) and apply the reviewed D1 migration
   remotely. Do not create or configure an R2 bucket for the default profile.
2. Store one high-entropy `MAIL_EDGE_AUTH_SECRET` in the Worker secret store,
   deploy the Worker, and set
   `CLOUDFLARE_MAIL_EDGE_URL=https://mail-edge.aiat.ca`. Inject the same value into
   `CLOUDFLARE_MAIL_EDGE_AUTH_SECRET` for identity-service; do not expose a
   Cloudflare account token to AIAT.
3. Configure the exact `*@agents.aiat.ca` Email Routing route to the Worker.
4. Populate the production AIAT secret environment, render and migrate the
   default Compose bundle, and start identity-service plus ingress.
5. Create/verify the domain through the signed identity-service workflow,
   provision a fresh worker identity, and deliver an external verification
   message. The service must sync it, verify ownership, and activate the
   identity before worker activation.
6. Keep outbound disabled while completing the live Resend API, delivery,
   webhook, and reply evidence. Set the compatibility activation latch
   `OUTBOUND_RELAY_CERTIFIED=true` only after that evidence is reviewed;
   `DEFAULT_OUTBOUND_ENABLED` remains false and every message still requires a
   durable human approval.

The signed edge protocol acknowledges an event only after local persistence.
The local cursor, encrypted message copy, provider binding, audit, and outbox
records make a restart or temporary edge outage recoverable and idempotent.

## Migration and continuity policy

Do not copy local Stalwart mail or credentials into the Cloudflare v1
namespace. The normal promotion creates fresh production identities in the
dedicated production Postgres database. If a worker needs continuity, freeze
hiring, aliases, outbound requests, and lifecycle transitions; take encrypted
backups; map the worker ID and ownership grant; provision the new address
through identity-service; and retain historical audit/outbox records.

If the edge has accepted a message but local synchronization has not committed,
leave it in D1 (or the explicitly selected optional R2/D1 profile) and retry
from the durable cursor. If a provider call is
ambiguous, reconcile its opaque reference before retrying. Never create a
second recipient binding or manually edit D1/identity rows.

The identity Postgres migration head is
`0004_provider_neutral_mail`. It adds independent provider bindings, encrypted
inbound message storage, sync cursors, content type, and the
`email_identity_provisioning_jobs` name while retaining a compatibility view
for old provisioning SQL. Review the migration before applying it to an
existing database and take an encrypted backup first.

## Optional Stalwart profile

The existing self-hosted Stalwart bundle remains available when full mailbox
storage, JMAP history, or public SMTP ingress is intentionally required. Select
it explicitly:

```text
IDENTITY_INBOUND_PROVIDER=stalwart
IDENTITY_OUTBOUND_PROVIDER=stalwart
```

Use its separate `.env.mail-edge`, database, DNS/MX, firewall, backup, and
live-certification procedure in
[`mas/infra/mail-edge/README.md`](../mas/infra/mail-edge/README.md). The
loopback Stalwart development profile has the same explicit provider override.
Its JMAP and SMTP checks are optional-profile evidence, not evidence for the
default Cloudflare path.

## Live certification still required

Repository tests cover the Worker contract with a deterministic D1-only
emulator, an explicit optional-R2 regression, the signed pull/retry boundary,
lifecycle isolation, encrypted local sync, and Resend with a mocked HTTPS
transport. They do not certify:

- Cloudflare route delivery, D1 migration, Worker deployment, or DNS;
- public TLS and identity ingress reachability;
- Resend domain authorization, direct API acceptance, webhook authenticity,
  external inbox delivery, or reply routing;
- production backup/restore, restart persistence, or operator-owned firewall
  policy.

Record those operator-owned results separately. No repository command or test
creates/fakes a live account, mutates DNS, or prints a real secret.
