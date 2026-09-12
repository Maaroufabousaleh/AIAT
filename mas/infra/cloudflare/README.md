# AIAT default Cloudflare email identity runtime

This is the default v1 Docker runtime for the provider-neutral identity
service. The managed edge is deployed from
[`email-worker/`](email-worker/README.md):

`Cloudflare Email Routing -> Email Worker -> D1/R2 -> signed pull -> AIAT identity-service -> governed worker tools`.

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
```

The local Worker tests and Wrangler D1 migration do not contact Cloudflare or
Resend. Copy the example to an ignored local file and keep every placeholder
or real secret outside Git.

## Operator deployment order

1. Create the production D1 database and R2 bucket, replace the placeholder
   `database_id` in `email-worker/wrangler.toml`, and apply the checked-in D1
   migration.
2. Set the Worker secret `MAIL_EDGE_AUTH_SECRET`, deploy the Worker, and record
   its HTTPS origin in `CLOUDFLARE_MAIL_EDGE_URL`.
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
