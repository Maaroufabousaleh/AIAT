# AIAT default Cloudflare email identity runtime

This is the default v1 Docker runtime for the provider-neutral identity
service. The managed edge is deployed from
[`email-worker/`](email-worker/README.md):

`Cloudflare Email Routing -> Email Worker -> D1-only -> signed pull -> AIAT identity-service -> governed worker tools`.

The default Worker stores bounded raw MIME in ordered D1 chunks. R2 is an
explicitly optional Worker profile and is not needed for v1 deployment or
local development. See [`email-worker/README.md`](email-worker/README.md) for
the chunking, limits, recovery, and optional-profile details.

The default Worker HTTPS origin is the single Custom Domain
`https://mail-edge.aiat.ca`. It is declared in `email-worker/wrangler.toml`
with `custom_domain = true`; the production identity-service value is
`CLOUDFLARE_MAIL_EDGE_URL=https://mail-edge.aiat.ca`. The default deployment
does not use `workers.dev` or a traditional `mail-edge.aiat.ca/*` route.

The Compose bundle here runs AIAT Postgres, migrations, identity-service, and a
TLS ingress for `identity.aiat.ca`. It does not run Stalwart and does not give
the identity-service a Cloudflare account token. The runtime receives only the
narrow HMAC secret shared with the Worker and the direct Resend API/webhook
secrets required by the selected outbound adapter.

## Static/local checks

```sh
cd /mnt/c/projects/aiat/mas/infra/cloudflare/email-worker
npm ci
npm run typecheck
npm run test
npm run d1:migrate:local

cd /mnt/c/projects/aiat/mas/infra/cloudflare
docker compose --env-file .env.cloudflare-mail-edge.local config -q

# After the operator has copied the exact Cloudflare/Resend DNS values into
# the shell environment, validate public records without reading any secret:
sh scripts/validate-dns.sh
```

The local Worker tests and Wrangler D1 migration do not contact Cloudflare or
Resend. Copy the example to an ignored local file and keep every placeholder
or real secret outside Git.

`scripts/validate-dns.sh` deliberately requires the exact account/region values
returned by Cloudflare and Resend rather than guessing MX, SPF, DKIM, or
return-path records. It checks the public DNS records only; the exact Email
Routing route still has to be verified in the Cloudflare account.

## Default Worker live commands

After the local checks pass and the operator has reviewed the existing D1
database, the default Worker sequence is:

```sh
cd /mnt/c/projects/aiat/mas/infra/cloudflare/email-worker
npm ci
npm run d1:migrate:remote
npx wrangler secret put MAIL_EDGE_AUTH_SECRET
npm run deploy
```

The default command uses the checked-in D1 ID and has no R2 binding. The
optional `env.r2` profile and its bucket command are documented separately in
[`email-worker/README.md`](email-worker/README.md).

## Operator deployment order

1. The production D1 database `aiat-mail-edge` already exists. Review the
   checked-in database ID in `email-worker/wrangler.toml` and apply the D1
   migration remotely; no R2 bucket is required.
2. Set the Worker secret `MAIL_EDGE_AUTH_SECRET`, deploy the Worker, and set
   `CLOUDFLARE_MAIL_EDGE_URL=https://mail-edge.aiat.ca` for identity-service.
3. Configure Cloudflare Email Routing for the exact `*@agents.aiat.ca` route
   to the deployed Worker. The Worker itself rejects recipients absent from
   the AIAT registry.
4. Verify `agents.aiat.ca` in Resend, copy its provider-issued DNS records,
   and set the real API/webhook secrets in the operator secret store.
5. Populate the AIAT production environment, render this Compose bundle, run
   `identity-migrate`, and start `identity-service` plus `identity-ingress`.
6. Run the signed live inbound/outbound certification and only then set
   `OUTBOUND_RELAY_CERTIFIED=true`; outbound approval remains required for each
   message.

The existing [`../mail-edge/`](../mail-edge/README.md) bundle and
[`../smtp-gateway/`](../smtp-gateway/README.md) remain intact as optional
Stalwart full-mailbox deployments. They are not part of this default path.
