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
private, path-limited Caddy gateway. It does not run Stalwart and does not give
the identity-service a Cloudflare account token. The runtime receives only the
narrow HMAC secret shared with the Worker and the direct Resend API/webhook
secrets required by the selected outbound adapter. Public TLS for the optional
Resend webhook is terminated by Cloudflare Tunnel; the default Compose profile
does not start that tunnel and publishes no host port.

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

## Secret boundary

Real credentials are configuration inputs, never source-controlled defaults.
The repository-level ignore rules cover local .env* files, Wrangler .dev.vars*
files, .secrets/ and secrets/ directories, and common credential-file suffixes.
The tracked *.example files are templates only and must contain placeholders.

For this deployment boundary:

- Docker receives production values from the operator environment or an
  ignored local env file such as .env.cloudflare-mail-edge.local.
- Wrangler receives the Worker HMAC value through wrangler secret put (and
  local development may use the ignored .dev.vars file); no secret belongs in
  wrangler.toml.
- Python adapters and certification scripts read secrets from environment
  variables or the configured external secret store. They must not construct
  production credentials, put them in source, pass them as command-line
  arguments, or write them into the checkout.

Run the secret-boundary check from the repository root before committing a
Cloudflare or identity-runtime change:

~~~sh
python3 mas/scripts/check_secret_boundary.py
~~~

The check reports only counts and safe paths; it never prints secret values.

`scripts/validate-dns.sh` deliberately requires the exact account/region values
returned by Cloudflare and Resend rather than guessing MX, SPF, DKIM, or
return-path records. It checks the public DNS records only; the exact Email
Routing route still has to be verified in the Cloudflare account.

## Optional Cloudflare webhook tunnel

The only public identity-service route intended for the tunnel is:

```text
POST https://identity.aiat.ca/v1/mail-edge/provider-webhook/resend
```

The checked-in Caddy gateway listens only on the private Docker network at
`http://identity-ingress:8080`, forwards that exact POST path to
`identity-service:8010`, and returns 404 for every other path or method. It
does not expose the identity API, Postgres, or a host port. The Cloudflare
Tunnel's remotely configured public hostname must target
`http://identity-ingress:8080`; Cloudflare remains responsible for public TLS.

`CLOUDFLARE_IDENTITY_TUNNEL_TOKEN` must belong to a dedicated identity tunnel
whose public hostname is `identity.aiat.ca`. Do not reuse a connector token for
the PM gateway or any other unrelated tunnel; the remote tunnel configuration
must not route identity traffic to another service.

The tunnel is deliberately opt-in. Inject its connector token through the
ignored operator environment as `CLOUDFLARE_IDENTITY_TUNNEL_TOKEN` and start
only the explicit profile after the Cloudflare named tunnel has been configured:

```sh
cd /mnt/c/projects/aiat/mas/infra/cloudflare
docker compose --env-file /home/maaro/.config/aiat/identity-production.env \
  --profile cloudflare up -d cloudflared
```

The token is read by Compose from the environment and is not accepted as a
command-line argument. A normal `docker compose config -q` and the default
identity stack do not require it. If the profile is selected without a token,
the connector must fail closed rather than exposing another route.

For provider-only readiness (before enabling any outbound latch), use the
adapter-backed Resend certificate:

```sh
cd /mnt/c/projects/aiat/mas
uv run python scripts/certify_resend_live.py --json readiness
```

The readiness result distinguishes a valid Resend `sending_access` key from an
invalid key. Resend may return the structured `restricted_api_key` HTTP 401
when a sending-only key calls domain-management or webhook-management
endpoints. That is expected least-privilege behavior, not invalid
authentication; the bounded `send-once` operation proves this mode through
the actual `/emails` endpoint. A full-access key may additionally report the
provider domain status through `/domains`.

Register or reconcile exactly one Resend webhook at
`https://identity.aiat.ca/v1/mail-edge/provider-webhook/resend`; do not create
a duplicate. Subscribe to `email.sent`, `email.delivered`,
`email.delivery_delayed`, `email.bounced`, `email.complained`, and
`email.failed`, plus `email.suppressed` only if the account currently supports
it. The configured `RESEND_WEBHOOK_SIGNING_SECRET` must correspond to that
webhook, and the identity service verifies the raw request body before storing
any provider observation.

The optional `send-once --confirm-send` operation is a single, no-retry
transport probe for an operator-controlled recipient supplied through
`AIAT_CERTIFICATION_RECIPIENT`; it is not the governed worker send path and it
never changes `OUTBOUND_RELAY_CERTIFIED`. The full certification still has to
exercise signed identity-service allocation, approval, usage accounting, one
approved send, and the public signed webhook.

## Repeatable live inbound certification

After the operator injects `CLOUDFLARE_MAIL_EDGE_URL` and
`CLOUDFLARE_MAIL_EDGE_AUTH_SECRET` through the local secret environment, use
the adapter-backed certificate rather than an ad-hoc request. It makes no
Cloudflare account calls, sends no external email, and prints only bounded
metadata:

```sh
cd /mnt/c/projects/aiat/mas
uv run python scripts/certify_cloudflare_mail_edge_live.py --json health
uv run python scripts/certify_cloudflare_mail_edge_live.py --json inspect-events \
  --recipient w-live-smoke-20260912@agents.aiat.ca \
  --identity-id live-smoke-identity-20260912 \
  --worker-id live-smoke-worker-20260912
```

Use `fetch-and-validate-message` with the safe `message_id` from event output
and the expected recipient/identity/worker correlation. The script can also
run signed `activate`, `suspend`, and `retire` transitions. To finalize the
current temporary smoke identity, run the following after confirming the
operator environment is authenticated:

```sh
cd /mnt/c/projects/aiat/mas
uv run python scripts/certify_cloudflare_mail_edge_live.py finalize-certification \
  --recipient w-live-smoke-20260912@agents.aiat.ca \
  --identity-id live-smoke-identity-20260912 \
  --worker-id live-smoke-worker-20260912 \
  --provider-reference recipient:e2ad9f0588e174b1f82ebef7e389ad1b
```

This retires the registry binding through the signed lifecycle API; it does
not edit D1 directly or send another external message. The live result and
remaining release boundaries are recorded in
[`../../../Docs/AIAT_Email_Identity_Live_Certification.md`](../../../Docs/AIAT_Email_Identity_Live_Certification.md).

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
   `identity-migrate`, and start `identity-service` plus the private
   `identity-ingress` gateway. Start the optional `cloudflare` profile only
   after the named tunnel route has been configured.
6. Run the signed live inbound/outbound certification and only then set
   `OUTBOUND_RELAY_CERTIFIED=true`; outbound approval remains required for each
   message.

The existing [`../mail-edge/`](../mail-edge/README.md) bundle and
[`../smtp-gateway/`](../smtp-gateway/README.md) remain intact as optional
Stalwart full-mailbox deployments. They are not part of this default path.
