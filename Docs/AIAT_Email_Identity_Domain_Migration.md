# AIAT identity mail-domain migration

AIAT has two intentionally separate mail profiles:

| Profile | Compose bundle | Mail domain | Exposure | Identity Postgres |
| --- | --- | --- | --- | --- |
| `development` / `mail-local` | `mas/infra/compose/docker-compose.stalwart-local.yml` | `agents.aiat.local` | loopback SMTP/JMAP/admin only | `identity_postgres_local_data` |
| `production` / `mail-production` | `mas/infra/mail-edge/docker-compose.yml` | `agents.aiat.ca` | public SMTP/25 and HTTPS/443 only | `identity_postgres_data` |
| `production-gateway` / `smtp-gateway` | `mas/infra/smtp-gateway/` plus the home overlay | `agents.aiat.ca` | gateway public SMTP/25 and HTTPS/443; home only over WireGuard | the existing home `identity_postgres_data` |

The local profile remains a test namespace. Never point it at the production
environment file, production volumes, DNS, or Stalwart instance. Never add
`agents.aiat.local` as an alias of the public production domain: that would
silently mix test and production identities.

## Cloudflare records

Zone: `aiat.ca`. Use `TTL=Auto`. The two origin A records below must be
**DNS-only (grey cloud)**; Cloudflare does not proxy SMTP, and the MX target
must resolve directly to the mail server.

| Type | Name | Content / target | Priority | Proxy |
| --- | --- | --- | ---: | --- |
| A | `mail` | `<public IPv4 of the self-hosted AIAT machine>` | — | DNS only |
| A | `identity` | `<public IPv4 of the self-hosted AIAT machine>` | — | DNS only |
| MX | `agents` | `mail.aiat.ca.` | 10 | DNS only |
| TXT | `send.agents` | `v=spf1 include:amazonses.com ~all` | — | DNS only |
| MX | `send.agents` | `<RESEND_BOUNCE_MX_HOST>.` | 10 | DNS only |
| TXT | `resend._domainkey.agents` | `<RESEND_DKIM_VALUE_FROM_VERIFIED_DOMAIN>` | — | DNS only |
| TXT | `_dmarc.agents` | `v=DMARC1; p=none;` | — | DNS only |

The first three records and the DMARC policy are fixed for this deployment.
`<public IPv4 of the self-hosted AIAT machine>` is the public address assigned
to the deployment, and the Resend MX/DKIM values are account- and
region-specific values that must be copied exactly
from the already-authorized Resend domain record. The repository must not add,
create, rotate, or modify a Cloudflare or Resend account. Do not put an SPF
record at the apex of `agents.aiat.ca` for this Resend setup: Resend's default
return path is the `send` subdomain.

Cloudflare Email Routing must remain disabled for `agents.aiat.ca`; its MX
records would compete with Stalwart. Do not proxy `mail.aiat.ca`. If an
IPv6 address is later assigned, add an `AAAA mail` record only after the same
address is routed and firewall-tested; do not publish a placeholder AAAA.

## Gateway topology for home Fizz TCP/25 constraints

The direct `production` row above is not valid when the home connection cannot
receive public TCP/25. The separate production topology is
`DEPLOYMENT_TOPOLOGY=smtp_gateway_vps_home_stalwart_resend`, with
`SMTP_GATEWAY_PUBLIC_IP=<SMTP_GATEWAY_PUBLIC_IP>` in
`mas/infra/smtp-gateway/.env.smtp-gateway`. In that mode the exact fixed DNS
records are:

```text
A   mail.aiat.ca       <SMTP_GATEWAY_PUBLIC_IP>   DNS-only
A   identity.aiat.ca   <SMTP_GATEWAY_PUBLIC_IP>   DNS-only
MX  agents.aiat.ca     10 mail.aiat.ca.
```

The gateway receives only `agents.aiat.ca`, queues on persistent disk, and
forwards to `10.77.0.2:25` over WireGuard. Caddy terminates TLS for both
public names and proxies identity/JMAP traffic to `10.77.0.2:8010`; the home
router has no production port-forward. The gateway contains no mailbox,
identity database, browser session, or worker data. `OUTBOUND_RELAY_CERTIFIED`
must remain `false` until the live authenticated Resend 465 certification,
inbound delivery, retry, and JMAP evidence are reviewed. See
[`mas/infra/smtp-gateway/README.md`](../mas/infra/smtp-gateway/README.md) for
the exact VPS, WireGuard, firewall, staging, and rollback procedure.

## Router and firewall requirements

For the direct `production` profile, on the home router/NAT gateway, create
only these forwards:

| WAN | Self-hosted AIAT machine | Purpose |
| ---: | ---: | --- |
| TCP 25 | TCP 25 | inbound SMTP from external senders |
| TCP 80 | TCP 80 | ACME HTTP challenge and redirect |
| TCP 443 | TCP 443 | Caddy TLS for `mail.aiat.ca` and `identity.aiat.ca` |

Do not forward TCP `5432`, `8010`, `8080`, `18080`, `465`, or `587` inbound.
Do not forward local development ports `3000`, `5173`, `8000`, `8001`,
`8002`, `8003`, `8011`, `2525`, `4000`, `4001`, `9000`, `9001`, `9090`, or
`20128`.
SSH is an operator maintenance path only: allow TCP 22 from the operator's
fixed administration CIDRs, preferably through a separate bastion or VPN.

For `production-gateway`, create **no home production forwards at all**. The
public VPS admits TCP `25`, TCP `80/443`, and restricted UDP `51820`; the home
host initiates WireGuard and binds Stalwart SMTP/HTTP and identity-service only
to `10.77.0.2`. Never forward home TCP `25` or expose home Postgres, internal
identity, Stalwart admin, Docker API, or local development ports. The gateway
firewall must reject direct outbound TCP/25 and allow only the WireGuard
service path to the home address.

The self-hosted machine's firewall must default-deny inbound traffic, allow TCP 25/80/443
as above, and allow SSH only from the operator allowlist. It must allow
outbound DNS 53 (UDP and TCP), NTP 123/UDP, HTTPS 443 for ACME/provider APIs,
and TCP 465 to `smtp.resend.com`. It must explicitly reject outbound TCP 25
so Stalwart cannot bypass the Resend route. Postgres and Stalwart administration
remain Docker-private; the only bootstrap admin path is a loopback listener
reached through an SSH tunnel.

## Governed migration procedure

The safe default is **not** to migrate local test mailboxes. Keep their
`agents.aiat.local` records and mail data in the local volume, archive the test
run evidence, and provision fresh production identities in the dedicated
production database. This gives production new `w-<worker-id>@agents.aiat.ca`
addresses without copying test mail, credentials, aliases, or outbox history.

If a worker must retain continuity, use a reviewed change ticket and this
two-phase procedure:

1. Freeze hiring, mailbox provisioning, alias changes, outbound requests, and
   lifecycle transitions. Record the source/target profile, worker allowlist,
   backup checksums, operator, and maintenance window. Take encrypted backups
   of the local identity/Postgres state and the target production state.
2. Create and verify `agents.aiat.ca` through the signed identity-service
   domain API. Do not create it by editing Stalwart files or by adding an
   alias in the Stalwart UI.
3. For each explicitly approved worker, provision a **new** target mailbox
   through the identity-service. Stalwart must create a new account at
   `w-<worker-id>@agents.aiat.ca`; an `x:Account/set` rename of an old
   `agents.aiat.local` account is prohibited.
4. Confirm target inbound delivery and JMAP reads. The target identity row in
   production Postgres must contain the new address, target domain row, new
   Stalwart `provider_account_id`, ownership grant, and verification evidence
   before the worker is activated. The identity-service provisioning path is
   the writer for these rows; operators must not update Stalwart alone.
5. Reconcile aliases one at a time through the identity-service. Preserve old
   outbound/audit rows as historical records; do not rewrite message history
   to pretend it was sent from the new domain. Any conflicting alias aborts the
   migration.
6. Reconcile external accounts, browser sessions, leases, outbox events, and
   lifecycle state by worker ID. Do not copy opaque credentials, cookies,
   local private keys, or local Postgres rows into production.
7. Run the two-worker production acceptance test, including cross-worker
   denial, unknown-recipient rejection, restart persistence, and suspension.
   Keep `OUTBOUND_RELAY_CERTIFIED=false` during all inbound-only work.
8. Only after the Resend authenticated relay test and external delivery/reply
   test pass may the operator set `OUTBOUND_RELAY_CERTIFIED=true` in the
   production secret environment. Run `activate-production.sh activate` for
   direct `self_hosted_stalwart_resend`, or
   `mas/infra/smtp-gateway/scripts/activate-gateway.sh activate` for
   `smtp_gateway_vps_home_stalwart_resend`. `DEFAULT_OUTBOUND_ENABLED` remains
   `false`; every message still requires a durable human approval.
9. Retire the source local identity only after the target row and provider
   account are verified. Never destroy the source volume as part of a routine
   promotion; retain it according to the test-data retention policy.

If a provider account was created but the Postgres transaction failed, stop the
worker and record the provider account ID as an orphan for a reviewed,
idempotent reconciliation. Do not retry by creating an arbitrary second
Stalwart account and do not call the rename API manually.

## Activation gates

The production bundle is staged with outbound disabled. Use the wrapper from
`mas/infra/mail-edge`:

```sh
sh scripts/activate-production.sh stage .env.mail-edge
```

After staging, run the preflight from a network outside the self-hosted
machine's router/firewall and retain the output as controlled evidence. It
must verify the public IPv4, reject unsupported CGNAT, reach inbound 25/80/443,
reach `smtp.resend.com:465`, prove outbound TCP 25 is blocked, and prove that
Postgres, the identity internal port, Stalwart administration, and local
development ports are not public:

```sh
preflight_tmp="$(mktemp)"
sh scripts/preflight-self-hosted.sh .env.mail-edge --external-target mail.aiat.ca --external-only >"$preflight_tmp" && {
  cat "$preflight_tmp"
  printf '%s\n' AIAT_SELF_HOSTED_PREFLIGHT=PASS
} > /secure/evidence/self-hosted-preflight.txt
rm -f "$preflight_tmp"
```

The operator must collect external-host evidence with exactly these marker
lines in separate, access-controlled files:

```text
AIAT_INBOUND_SMTP_TEST=PASS
AIAT_EXTERNAL_DELIVERY_TEST=PASS
```

The first file must prove an external host completed an SMTP connection and
message delivery through the router forward to TCP 25. The second must prove
the approved outbound message was queued by Stalwart, accepted by Resend over
authenticated TLS, delivered to the operator-controlled external inbox, and
received a reply where the live acceptance test requires one. The evidence is
not a substitute for the live test; it is the signed change-record input to
the activation wrapper.

Activation refuses to run unless all of the following pass: exact profile and
domains, Compose/admin exposure checks, Cloudflare DNS/MX/PTR/SPF/DKIM/DMARC,
public TLS, router/firewall and outbound TCP/25 denial, inbound SMTP from an
external host, Resend relay TLS, and external delivery/reply certification.

```sh
sh scripts/activate-production.sh activate .env.mail-edge \
  --preflight-evidence /secure/evidence/self-hosted-preflight.txt \
  --inbound-evidence /secure/evidence/inbound-smtp.txt \
  --delivery-evidence /secure/evidence/resend-delivery.txt
```

For the home Fizz gateway topology, use the separate gateway preflight and
activation gates. The VPS must pass public TCP 25/80/443, WireGuard handshake,
gateway-to-home SMTP, filesystem queue quota, public TLS, no-open-relay, and
management-port checks. Home evidence must prove WireGuard-only service
bindings, no home public TCP/25, Resend TCP/465, and blocked direct TCP/25.
The offline queue/retry and external SMTP-to-JMAP staging tests are required
before the gateway activation wrapper will accept relay certification:

```sh
cd ../smtp-gateway
sh scripts/activate-gateway.sh stage .env.smtp-gateway
sh scripts/preflight-gateway.sh .env.smtp-gateway --external-target mail.aiat.ca
sh scripts/activate-gateway.sh activate .env.smtp-gateway \
  --evidence /secure/evidence/smtp-gateway-external.txt
```

The gateway command remains fail-closed when live evidence is absent; the
example environment deliberately keeps `OUTBOUND_RELAY_CERTIFIED=false`.

No Cloudflare, router, self-hosted machine, or Resend account mutation is performed by these
repository commands. Account, DNS, router, and firewall changes require the
operator's explicit authorization.
