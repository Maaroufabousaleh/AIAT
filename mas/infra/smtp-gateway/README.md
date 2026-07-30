# AIAT SMTP gateway: VPS edge and home Stalwart

This is a separate production topology for the home Fizz constraint. The home
host can accept TCP/25 locally and through the forwarded public test port
2525, but public TCP/25 times out. It therefore cannot be the public MX edge.
The gateway VPS receives mail and forwards only the owned domain over
WireGuard; the home host remains the mailbox, JMAP, identity-service, and
identity-Postgres authority.

The existing profiles are not changed or combined:

- `mas/infra/compose/docker-compose.stalwart-local.yml` is loopback-only
  development using `agents.aiat.local`.
- `mas/infra/mail-edge/docker-compose.yml` is direct self-hosting using
  `self_hosted_stalwart_resend`. It still requires public home TCP/25 and is
  rejected by its own activation wrapper when this topology is selected.
- This directory is only
  `smtp_gateway_vps_home_stalwart_resend` using `agents.aiat.ca`.

No Stalwart account is renamed. Any identity promotion is a governed
identity-service/Postgres transaction that provisions a new production
address and records the new provider account; local test identities remain in
the local namespace and are never silently aliased into production.

Postfix writes its restricted local log stream to a non-public volume; the
`log-sanitizer` service emits a redacted operator stream with mailbox addresses
and IPs removed. Do not export the raw log volume. Queue IDs and delivery
status remain available for retry diagnostics.

## Minimum infrastructure

The original container profile is sized for 2 vCPU and 2 GiB RAM, but those
resources are not mandatory for a constrained gateway. The supplied live
`OCI VM.Standard.E2.1.Micro` shape is supported by the separate
host-level profile at
`profiles/oci-e2.1-micro-host.env.example`: 1 OCPU, 1 GiB RAM, 1 GiB swap,
and a 45 GiB boot disk. It runs only host Postfix, wg-quick, and the existing
socat forward. Docker Postfix, Caddy, and the log-sanitizer containers are
disabled for this profile.

Constrained host-level minimum:

- Ubuntu/Linux x86_64, WireGuard, `nftables`, Postfix, `socat`, `ss`, `nc`,
  `dig`, `curl`, `jq`, `openssl`, `timeout`, and `systemd`.
- One public IPv4, inbound TCP 25 only after the external gate passes, and UDP
  51820 for WireGuard. SSH remains operator-CIDR-only.
- Outbound DNS and authenticated `smtp.resend.com:465`. Direct outbound TCP/25
  remains blocked. No Docker or public HTTPS ingress is required while the
  identity HTTPS gate is blocked.
- Existing `/var/spool/postfix` must remain intact and persistent. Do not
  impose a new queue path, recreate the queue, run `postsuper -d`, or run
  `docker compose down -v`.

The general container profile still requires its own larger resource budget,
but the constrained Compose override
`docker-compose.oci-e2.1-micro.yml` caps the optional Postfix container at
384 MiB and 0.50 CPU while disabling ingress and log-sanitizer. It is for
static validation or a separately approved container deployment only; never
start it alongside the live host-level Postfix listener.

Home minimum:

- The existing AIAT host with Stalwart, identity-service, identity-Postgres,
  and its existing production secrets; WireGuard; Docker/Compose; and an
  outbound UDP path to the gateway's `51820`.
- No home-router TCP/25 forward. No public TCP `5432`, `8010`, `8080`,
  `18080`, Docker API, local development ports, or Stalwart administration.
- Home-side firewall binds SMTP 25, Stalwart HTTP 8080, and identity-service
  8010 only to `10.77.0.2`, with source `10.77.0.1` for gateway traffic.

## Environment files

Copy the examples to root-owned, mode-0600 files outside Git. The gateway
file is used on the VPS:

```dotenv
DEPLOYMENT_TOPOLOGY=smtp_gateway_vps_home_stalwart_resend
MAS_ENVIRONMENT=production
IDENTITY_PROFILE=production
PRIMARY_DOMAIN=aiat.ca
AGENT_MAIL_DOMAIN=agents.aiat.ca
MAIL_HOSTNAME=mail.aiat.ca
IDENTITY_HOSTNAME=identity.aiat.ca
SMTP_GATEWAY_PUBLIC_IP=<public IPv4 of the self-hosted AIAT machine>
# Shared provider-neutral alias; keep it identical to SMTP_GATEWAY_PUBLIC_IP.
PUBLIC_MAIL_IP=<public IPv4 of the self-hosted AIAT machine>
SMTP_GATEWAY_HOSTNAME=mail-gateway.aiat.ca
ACME_EMAIL=<operator contact email>
WIREGUARD_INTERFACE=aiat-gateway
WIREGUARD_PORT=51820
GATEWAY_WIREGUARD_IP=10.77.0.1
HOME_WIREGUARD_IP=10.77.0.2
HOME_STALWART_SMTP_PORT=25
HOME_STALWART_HTTP_PORT=8080
HOME_IDENTITY_SERVICE_PORT=8010
OUTBOUND_RELAY_HOST=smtp.resend.com
OUTBOUND_RELAY_PORT=465
OUTBOUND_RELAY_TLS_MODE=implicit
DIRECT_MX_OUTBOUND_ENABLED=false
DEFAULT_OUTBOUND_ENABLED=false
OUTBOUND_RELAY_CERTIFIED=false
SSH_ALLOWED_CIDRS=<operator-admin-CIDR>
GATEWAY_QUEUE_LIFETIME=2d
GATEWAY_BOUNCE_QUEUE_LIFETIME=1d
GATEWAY_QUEUE_MIN_FREE_KB=1048576
GATEWAY_QUEUE_MAX_BYTES=10737418240
GATEWAY_QUEUE_PATH=/var/lib/aiat/smtp-gateway-postfix-spool
GATEWAY_QUEUE_LIMIT_MODE=filesystem_quota
GATEWAY_QUEUE_QUOTA_EVIDENCE=/secure/evidence/smtp-gateway-quota.txt
GATEWAY_MESSAGE_SIZE_LIMIT=26214400
```

The home overlay file is
`mas/infra/smtp-gateway/home/.env.gateway-home.example`. It contains the
same topology/domain and WireGuard addresses, the three home bind ports, and
the Resend 465 settings. Merge it only with the existing
`mas/infra/mail-edge/.env.mail-edge` secret environment; do not copy the local
development environment or its Postgres volume.

For the supplied live host, use the constrained profile instead of the
container environment. The live values are host-level Postfix, WireGuard
`10.77.0.1/24`, home `10.77.0.2/24`, and the existing transport target
`smtp:[10.77.0.2]:2525`. Keep `PUBLIC_SMTP25_ACTIVATED=false`,
`IDENTITY_DNS_MODE=blocked`, and `OUTBOUND_RELAY_CERTIFIED=false` until their
individual evidence gates pass.

## Non-destructive adoption of the existing live host

This is the safe migration path for the already-provisioned host. It does not
install packages, rewrite `/etc/postfix`, run `postmap`, restart Postfix,
replace WireGuard keys, change nftables, change DNS, or start/stop containers.
It only reads configuration, queue metadata, service state, and network
listeners, then writes a mode-0600 sanitized evidence report:

```sh
cd mas/infra/smtp-gateway
install -m 0600 /secure/aiat/oci-e2.1-micro-host.env \
  /secure/aiat/oci-e2.1-micro-host.env.active
sh scripts/adopt-host-postfix.sh \
  /secure/aiat/oci-e2.1-micro-host.env.active \
  --evidence /secure/evidence/aiat-smtp-gateway/host-sanitized.txt
```

The adoption audit detects the existing `relay_domains`, the existing
`/etc/postfix/transport` and `.db`, the exact
`agents.aiat.ca smtp:[10.77.0.2]:2525` route, the existing queue, wg-quick
handshake, and socat systemd unit. It validates the current state in place;
it never rebuilds the transport database or copies private keys.

The corresponding read-only checks, if an operator needs individual output,
are:

```sh
sudo postconf -h relay_domains transport_maps relayhost smtpd_relay_restrictions
sudo grep -E '^agents\.aiat\.ca[[:space:]]+smtp:\[10\.77\.0\.2\]:2525$' \
  /etc/postfix/transport
sudo test -f /etc/postfix/transport.db
sudo postqueue -p
sudo systemctl is-active postfix wg-quick@aiat-gateway
sudo wg show aiat-gateway
sudo ss -ltn
sudo systemctl list-units --type=service --all | grep -Ei 'socat|postfix|wg-quick'
```

Do not run the fresh-host key-generation sequence against this deployment.
The existing WireGuard keys are authoritative and must not be overwritten.

## DNS records

For Cloudflare zone `aiat.ca`, use `TTL=Auto`, DNS-only (grey cloud) for the
mail records, and do not change provider accounts from this repository:

| Type | Name | Value | Priority | Proxy |
| --- | --- | --- | ---: | --- |
| A | `mail` | `<SMTP_GATEWAY_PUBLIC_IP>` | — | DNS only |
| A | `identity` | **must remain absent while identity HTTPS is uncertified** | — | DNS only |
| MX | `agents` | `mail.aiat.ca.` | 10 | DNS only |

The already-authorized Resend records remain account/region-specific and must
be copied exactly, not guessed:

| Type | Name | Value | Priority | Proxy |
| --- | --- | --- | ---: | --- |
| TXT | `send.agents` | `v=spf1 include:amazonses.com ~all` | — | DNS only |
| MX | `send.agents` | `<RESEND_BOUNCE_MX_HOST>.` | 10 | DNS only |
| TXT | `resend._domainkey.agents` | `<RESEND_DKIM_VALUE_FROM_VERIFIED_DOMAIN>` | — | DNS only |
| TXT | `_dmarc.agents` | `v=DMARC1; p=none;` | — | DNS only |

Cloudflare Email Routing remains disabled for `agents.aiat.ca`. The live
constrained profile keeps `identity.aiat.ca` DNS blocked. Add its A record only
after a separately configured HTTPS reverse proxy reaches the home identity
service over WireGuard and the `identity-https` gate passes. No home-router
production port is exposed.

## Exact deployment sequence

Run these commands only on infrastructure the operator has authorized. They
do not modify Cloudflare, router, firewall-provider, VPS, or Resend accounts.

The following fresh-host sequence is not the live-host adoption path above.
For the supplied host-level deployment, do not run `activate-gateway.sh`, do
not start the Compose profile, and do not replace the existing WireGuard keys.
Use the adoption audit, then run the individual certification gates:

```sh
# Before public SMTP activation, validate the staging state and fail-closed firewall.
sh scripts/validate-host-gates.sh \
  /secure/aiat/oci-e2.1-micro-host.env.active pre-activation
sh scripts/validate-host-gates.sh \
  /secure/aiat/oci-e2.1-micro-host.env.active internal-relay
sh scripts/validate-host-gates.sh \
  /secure/aiat/oci-e2.1-micro-host.env.active external-inbound
sh scripts/validate-host-gates.sh \
  /secure/aiat/oci-e2.1-micro-host.env.active dns-mx
sh scripts/validate-host-gates.sh \
  /secure/aiat/oci-e2.1-micro-host.env.active identity-https
sh scripts/validate-host-gates.sh \
  /secure/aiat/oci-e2.1-micro-host.env.active resend
sh scripts/validate-host-gates.sh \
  /secure/aiat/oci-e2.1-micro-host.env.active all
```

The `pre-activation` command is the only gate that requires
`PUBLIC_SMTP25_ACTIVATED=false` and rejects a public TCP/25 firewall rule. The
`internal-relay` command validates the immutable host path and remains valid
whether public SMTP activation is false or true. Each certification command
fails closed unless its own evidence file contains the matching gate marker.
The external-inbound command does not use a gateway-to-its-own-public-IP
connection as a reachability decision. Its optional local self-probe is
informational only because public-IP hairpin/NAT reflection may be unavailable.
The operator must provide external SMTP evidence collected off-host.
The DNS/MX command does not create records. The
identity command refuses while `IDENTITY_DNS_MODE=blocked`, so
`identity.aiat.ca` remains absent until an HTTPS reverse proxy is configured
and certified. The Resend command verifies TCP/465 and requires live relay
evidence; it does not set `OUTBOUND_RELAY_CERTIFIED`.

The external evidence file must be mode `0600` and contain the marker plus all
of the following fields. The source IP or probe origin identifies the off-host
test; the remaining fields bind the test to production delivery:

```text
EXTERNAL_INBOUND_SMTP_CERTIFIED=PASS
EXTERNAL_SOURCE_IP=52.103.2.17
EXTERNAL_PROBE_ORIGIN=Outlook SMTP server
DESTINATION_HOSTNAME=mail.aiat.ca
DESTINATION_TCP_PORT=25
SMTP_ACCEPTANCE=250 2.0.0 Message queued
PRODUCTION_RECIPIENT=gateway-test@agents.aiat.ca
POSTFIX_QUEUE_ID=<postfix queue id>
DOWNSTREAM_RELAY_TARGET=10.77.0.2:2525
FINAL_STATUS=sent
```

`EXTERNAL_SOURCE_IP` may be omitted when `EXTERNAL_PROBE_ORIGIN` is a
non-empty external origin, but at least one must be present. The validator
requires the production hostname, TCP/25, a `250 2.0.0` acceptance, an
`@agents.aiat.ca` recipient, a queue ID, the WireGuard relay target, and
`FINAL_STATUS=sent`.

## Safe one-message Resend certification

Certification runs locally in the home WSL instance only. The only supported
URLs are `http://127.0.0.1:18080` for JMAP and
`http://127.0.0.1:18080/api` for management. It never exposes JMAP or Stalwart
administration over WireGuard, the VPS, or the public Internet.

Provision the credentials first, using Stalwart's local bootstrap/admin path:

- Create a restricted management API key for route read/write only.
- Create a separate mail/JMAP service credential that can use the approved
  production sender account.
- Add the exact protected Resend secret to Stalwart's existing environment and
  restart/reload only under a separately authorized Stalwart maintenance
  procedure. Do not put the value in route JSON.

Store the credentials in this root-owned, mode-0600 file outside Git:

```dotenv
RESEND_API_KEY=<Resend SMTP/API secret; never copy into evidence>
STALWART_API_KEY=<Stalwart management JMAP key>
STALWART_JMAP_SERVICE_TOKEN=<Stalwart mail JMAP bearer token>
```

The local preflight compares non-reversible SHA-256 fingerprints in memory:
the protected `RESEND_API_KEY` versus the `RESEND_API_KEY` in the exact running
Stalwart container. It records only `RELAY_SECRET_SOURCE_MATCH=PASS`, never a
key or fingerprint. It also checks the local listener, both credentials, the
production sender, exactly one environment-backed `resend-relay`, no Mx/direct
route, and implicit TLS on `smtp.resend.com:465`.

```sh
sudo sh scripts/preflight-resend-certification.sh \
  profiles/oci-e2.1-micro-host.env.active \
  --secret-file /etc/aiat/resend-certification.env \
  --stalwart-container <running-stalwart-container> \
  --account-id <existing-production-stalwart-account-id> \
  --sender gateway-test@agents.aiat.ca
```

If the route is not yet configured, make a configuration backup before the
explicit apply operation. These commands alter only Stalwart remote route and
outbound-strategy objects; they do not recreate containers, volumes, accounts,
mailboxes, or messages.

```sh
sudo sh scripts/configure-stalwart-resend-route.sh backup \
  profiles/oci-e2.1-micro-host.env.active \
  --secret-file /etc/aiat/resend-certification.env \
  --stalwart-container <running-stalwart-container> \
  --backup /secure/rollback/stalwart-remote-route.json

sudo sh scripts/configure-stalwart-resend-route.sh apply \
  profiles/oci-e2.1-micro-host.env.active \
  --secret-file /etc/aiat/resend-certification.env \
  --stalwart-container <running-stalwart-container> \
  --backup /secure/rollback/stalwart-remote-route.json

sudo sh scripts/configure-stalwart-resend-route.sh verify \
  profiles/oci-e2.1-micro-host.env.active \
  --secret-file /etc/aiat/resend-certification.env \
  --stalwart-container <running-stalwart-container> \
  --backup /secure/rollback/stalwart-remote-route.json
```

Rollback restores only the backed-up remote route/strategy objects:

```sh
sudo sh scripts/configure-stalwart-resend-route.sh rollback \
  profiles/oci-e2.1-micro-host.env.active \
  --secret-file /etc/aiat/resend-certification.env \
  --stalwart-container <running-stalwart-container> \
  --backup /secure/rollback/stalwart-remote-route.json
```

Run only after an operator has approved the exact sender and external mailbox:

```sh
sudo sh scripts/certify-resend.sh \
  profiles/oci-e2.1-micro-host.env.active \
  --secret-file /etc/aiat/resend-certification.env \
  --stalwart-container <running-stalwart-container> \
  --account-id <existing-production-stalwart-account-id> \
  --sender gateway-test@agents.aiat.ca \
  --external-recipient <operator-external-mailbox> \
  --approve-one-message \
  --output /secure/evidence/aiat-smtp-gateway/resend-submission.txt
```

This sends exactly one message and writes a pending record. Its
`STALWART_SUBMISSION_ID` is only a local JMAP submission identifier;
`RESEND_PROVIDER_MESSAGE_ID` remains pending until actual Resend/provider
correlation is available. Do not retry after an ambiguous submission.

After the external mailbox has received the original message, a real provider
correlation has been obtained, and a reply containing the one-time token has
been received, create the final evidence. Supply the reply token through stdin
so it is not placed in process arguments:

```sh
sudo sh scripts/complete-resend-certification.sh \
  /secure/evidence/aiat-smtp-gateway/resend-submission.txt \
  --output /secure/evidence/aiat-smtp-gateway/resend.txt \
  --resend-provider-message-id <actual-resend-provider-id> \
  --external-receipt-id <external-mailbox-receipt-correlation> \
  --reply-message-id '<reply-rfc5322-message-id>' \
  --reply-in-reply-to '<original-rfc5322-message-id>' \
  --reply-token-stdin \
  --approve-completion < /secure/evidence/aiat-smtp-gateway/reply-token.txt
```

The final `GATE_RESEND_EVIDENCE` file is a separate mode-0600 operator record
and must contain every field below. It must never contain `RESEND_API_KEY`:

```text
RESEND_OUTBOUND_RELAY_CERTIFIED=PASS
RELAY_HOST=smtp.resend.com
RELAY_PORT=465
TLS_MODE=implicit
TLS_VERIFICATION=PASS
SMTP_AUTHENTICATION=PASS
AUTH_USERNAME=resend
PRODUCTION_SENDER=gateway-test@agents.aiat.ca
EXTERNAL_RECIPIENT=operator@example.net
STALWART_ROUTE=resend-relay
DIRECT_MX_OUTBOUND_ENABLED=false
STALWART_SUBMISSION_ID=<local-jmap-submission-id>
RESEND_PROVIDER_MESSAGE_ID=<actual-resend-provider-id>
ORIGINAL_MESSAGE_ID=<original-rfc5322-message-id>
EXTERNAL_RECEIPT_ID=<external-mailbox-receipt-correlation>
DELIVERY_STATUS=delivered
REPLY_RECEIVED=PASS
REPLY_MESSAGE_ID=<reply-rfc5322-message-id>
REPLY_IN_REPLY_TO=<original-rfc5322-message-id>
REPLY_TOKEN_VERIFIED=PASS
CERTIFIED_AT=2026-07-29T20:00:00Z
```

The validator requires an external recipient, a production sender, exact
Resend route/TLS settings, separate local/provider IDs, external receipt,
delivered status, a correlated reply, the verified reply token, and an RFC3339
certification time. It rejects pending records, a local JMAP ID reused as a
provider ID, or any incomplete completion evidence.

Cleanup is limited to the sanitized pending artifact; it does not recall a
delivered message or alter mail state:

```sh
sudo rm -f -- /secure/evidence/aiat-smtp-gateway/resend-submission.txt
```

Leave `OUTBOUND_RELAY_CERTIFIED=false` and `DEFAULT_OUTBOUND_ENABLED=false`
until the complete evidence record is reviewed and the separately governed
activation gate passes.

Only after all five gates pass may an operator update the controlled
environment state and perform the separately authorized activation change.
This repository does not perform that change.

For static validation of the constrained optional container override only:

```sh
docker compose \
  --env-file .env.smtp-gateway.example \
  -f docker-compose.yml \
  -f docker-compose.oci-e2.1-micro.yml \
  --profile oci-e2-1-micro config -q
```

1. On a fresh gateway VPS, check out the reviewed repository commit and install
   the secret environment:

   ```sh
   git checkout --detach <reviewed-commit-sha>
   cd mas/infra/smtp-gateway
   install -m 0600 /secure/aiat/smtp-gateway.env .env.smtp-gateway
   sudo install -d -o root -g root -m 0750 /var/lib/aiat/smtp-gateway-postfix-spool
   sh scripts/validate-gateway-compose.sh .env.smtp-gateway
   docker compose --env-file .env.smtp-gateway config -q
   ```

   Configure the host filesystem/project quota so the queue path cannot exceed
   `GATEWAY_QUEUE_MAX_BYTES`, inspect the quota report, and write the signed
   evidence marker only after that inspection:

   ```text
   GATEWAY_QUEUE_QUOTA=PASS
   ```

   Save it as `/secure/evidence/smtp-gateway-quota.txt` with mode `0600`.

2. Generate one WireGuard key on each host. Never copy a private key to the
   other peer or commit it:

   ```sh
   # On the gateway VPS
   umask 077
   sh scripts/generate-wireguard-keys.sh gateway /secure/aiat/wireguard
   # On the home AIAT host
   umask 077
   sh scripts/generate-wireguard-keys.sh home /secure/aiat/wireguard
   ```

   Put the gateway public key in the home peer template and the home public
   key in the gateway peer template. Substitute the real public gateway IPv4,
   install each private-key-bearing file as
   `/etc/wireguard/aiat-gateway.conf` mode `0600`, then start the tunnel:

   ```sh
   sudo install -m 0600 gateway.conf /etc/wireguard/aiat-gateway.conf
   sudo systemctl enable --now wg-quick@aiat-gateway
   sudo wg show aiat-gateway
   ```

3. Review and load `wireguard/nftables-gateway.example` on the VPS and
   `wireguard/nftables-home.example` at home. Public gateway ingress is only
   TCP `25`, TCP `80/443`, and UDP `51820`; SSH is only the explicit operator
   CIDR. The home filter accepts the overlay service ports only from
   `10.77.0.1` and rejects outbound TCP/25.

4. On the home host, from `mas/infra/mail-edge`, render and start only the
   home authority services with the gateway overlay. The home `ingress` is
   deliberately not started:

   ```sh
   docker compose \
     --env-file .env.mail-edge \
     --env-file ../smtp-gateway/home/.env.gateway-home \
     -f docker-compose.yml \
     -f ../smtp-gateway/home/docker-compose.gateway-home.yml \
     config -q
   docker compose \
     --env-file .env.mail-edge \
     --env-file ../smtp-gateway/home/.env.gateway-home \
     -f docker-compose.yml \
     -f ../smtp-gateway/home/docker-compose.gateway-home.yml \
     up -d identity-postgres stalwart
   docker compose \
     --env-file .env.mail-edge \
     --env-file ../smtp-gateway/home/.env.gateway-home \
     -f docker-compose.yml \
     -f ../smtp-gateway/home/docker-compose.gateway-home.yml \
     run --rm identity-migrate
   docker compose \
     --env-file .env.mail-edge \
     --env-file ../smtp-gateway/home/.env.gateway-home \
     -f docker-compose.yml \
     -f ../smtp-gateway/home/docker-compose.gateway-home.yml \
     up -d identity-service
   ```

   Run `sh scripts/validate-home-gateway.sh .env.gateway-home .env.mail-edge`
   from this directory after copying the home example and before acceptance.
   This uses the existing Postgres schema and migration path; it does not
   rename or directly edit a Stalwart account.

5. On the gateway, start inbound-only staging, keeping the relay certification
   flag false:

   ```sh
   sh scripts/activate-gateway.sh stage .env.smtp-gateway
   ```

6. Run the external preflight from a network outside the gateway and home
   LAN. It fails closed on missing public IPv4, CGNAT/private addresses,
   public TCP 25/80/443, DNS/MX, TLS, open relay, WireGuard, management
   exposure, Resend 465, or direct TCP/25 reachability:

   ```sh
   sh scripts/preflight-gateway.sh .env.smtp-gateway \
     --external-target mail.aiat.ca
   ```

7. Run the opt-in live staging test. It reuses the governed two-worker
   identity acceptance, sends inbound mail through `mail.aiat.ca:25`, reads
   it through identity-service JMAP, rejects an unknown recipient, and tests
   the approved Resend send/reply path. Provide the existing signed staging
   identity variables required by
   `apps/identity-service/tests/test_live_identity_acceptance.py`:

   ```sh
   AIAT_RUN_LIVE_GATEWAY_TESTS=1 \
   LIVE_EXTERNAL_SENDER_CONFIRMED=1 \
   LIVE_MAIL_HOST=mail.aiat.ca \
   LIVE_SMTP_PORT=25 \
   LIVE_IDENTITY_SERVICE_URL=https://identity.aiat.ca \
   sh scripts/run-gateway-staging-acceptance.sh .env.smtp-gateway
   ```

8. Run the offline queue test with an operator-approved SSH path to the home
   host. `JMAP_COUNT_COMMAND` must query the governed identity/JMAP surface
   for `$GATEWAY_OFFLINE_TOKEN` and print exactly `1`; this is the exact-once
   assertion, not a Postgres shortcut:

   ```sh
   AIAT_RUN_LIVE_GATEWAY_TESTS=1 \
   HOME_SSH_TARGET=<operator>@<home-host> \
   HOME_REMOTE_COMPOSE_DIR=<home-mail-edge-directory> \
   HOME_REMOTE_MAIL_ENV_FILE=<home-secret-env-file> \
   HOME_REMOTE_GATEWAY_OVERRIDE_FILE=<home-gateway-override-file> \
   GATEWAY_TEST_RECIPIENT=<approved-agents-aiat-ca-recipient> \
   GATEWAY_TEST_ENVELOPE_FROM=<approved-sender> \
   JMAP_COUNT_COMMAND='<governed JMAP count command for $GATEWAY_OFFLINE_TOKEN>' \
   sh scripts/run-offline-queue-retry.sh .env.smtp-gateway
   ```

9. Keep `OUTBOUND_RELAY_CERTIFIED=false` until the live Resend evidence and
   the inbound/queue/JMAP evidence are reviewed. Only then set it to `true`
   in the root-owned secret file and activate:

   ```sh
   sh scripts/activate-gateway.sh activate .env.smtp-gateway \
     --evidence /secure/evidence/smtp-gateway-external.txt
   ```

## Rollback

The host-level adoption audit has no service rollback because it performs no
live mutation. If a separately authorized Postfix change later needs rollback,
restore only the operator-held configuration snapshot, validate it, and reload
Postfix without touching `/var/spool/postfix`:

```sh
sudo install -m 0644 /secure/rollback/postfix/main.cf /etc/postfix/main.cf
sudo install -m 0644 /secure/rollback/postfix/transport /etc/postfix/transport
sudo install -m 0644 /secure/rollback/postfix/transport.db /etc/postfix/transport.db
sudo test -f /etc/postfix/transport.db
sudo postfix check
sudo systemctl reload postfix
sudo postqueue -p
```

Do not run `postsuper -d ALL`, `postmap` against the live queue migration, or
`wg-quick down/up` as a rollback shortcut. Restore the existing WireGuard
configuration only from an operator-held snapshot if it was explicitly
changed; never generate replacement keys. Remove any separately authorized
public TCP/25 firewall rule and leave `PUBLIC_SMTP25_ACTIVATED=false` until a
new external evidence cycle completes. Remove identity DNS again if HTTPS
ingress is withdrawn.

If gateway activation must be withdrawn, keep the queue bind mount and stop only
the gateway services:

```sh
docker compose --env-file .env.smtp-gateway stop ingress log-sanitizer postfix-gateway
docker compose --env-file .env.smtp-gateway ps
```

Do not run `down -v`; the disk-backed queue is the recovery source. Keep the
MX pointed at the gateway until an operator has drained or deliberately
expired queued mail. Do not point the MX at the home address while Fizz public
TCP/25 remains unavailable. DNS rollback is a separately authorized change.

If the home service is unavailable, leave the gateway running so Postfix
queues and retries within the configured lifetime. Restore WireGuard/home
Stalwart, confirm `gateway-queue-status`, and use the offline retry evidence
before re-enabling normal operations. If the gateway VPS itself is lost,
preserve its disk snapshot/filesystem before rebuilding; never recreate the
queue path as an empty directory and claim delivery continuity.

## Acceptance boundary

Repository checks can prove the topology, pinned images, recipient allow-list,
WireGuard-only home bindings, no Docker socket, and fail-closed activation
logic. They cannot prove a real VPS, DNS, TLS certificate, WireGuard
handshake, inbound SMTP path, Fizz router state, or Resend account. The final
production decision is therefore `GATEWAY_REPOSITORY_BLOCKED` until the
operator runs and records the live gates above. No live infrastructure test is
claimed by this repository change.
