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

The live container provenance labels identify the original Compose project:

```text
project=mas
working_dir=/mnt/c/projects/aiat/mas/infra/compose
config_files=docker-compose.yml,docker-compose.stalwart-local.yml
service=stalwart
config_hash=2eddc16570b6c181bd0f0fbb2079d9aa0b9799ab15665d226e8df6492986fc68
```

Those aggregate files now parse unrelated MAS dependencies and secrets and can
be invalid when used as a partial validator bundle. Migration therefore uses
only `home/docker-compose.stalwart-canonical.yml`. The canonical service must
reproduce the live config hash, and `docker compose ps -q stalwart` must return
the exact inspected container ID. Secret injection adds only
`home/docker-compose.stalwart-resend-secret.yml`.

The live container currently has no `RESEND_API_KEY`; Docker cannot add an
environment variable in place. Secret injection is therefore a separate
maintenance migration before route configuration. It recreates only the
Compose `stalwart` service. It never runs `docker compose down`, deletes a
volume, changes an account, or renders the secret through `docker compose
config`.

Create two separate root-owned files. The injected file contains exactly one
variable and is referenced by
`home/docker-compose.stalwart-resend-secret.yml`:

```sh
sudo install -d -o root -g root -m 0700 /etc/aiat
sudo test ! -e /etc/aiat/stalwart-resend.env
sudo install -o root -g root -m 0600 /dev/null /etc/aiat/stalwart-resend.env
sudoedit /etc/aiat/stalwart-resend.env

sudo test ! -e /etc/aiat/resend-certification.env
sudo install -o root -g root -m 0600 /dev/null /etc/aiat/resend-certification.env
sudoedit /etc/aiat/resend-certification.env
```

`/etc/aiat/stalwart-resend.env`:

```dotenv
RESEND_API_KEY=<Resend SMTP secret>
```

`/etc/aiat/resend-certification.env`:

```dotenv
STALWART_API_KEY=<restricted Stalwart management JMAP key>
STALWART_JMAP_SERVICE_TOKEN=Basic <base64 of gateway-test@agents.aiat.ca:application-password>
```

### Provision the Stalwart v0.16.7 certification credentials

These are two different Stalwart credential types. An API key is valid only
for management JMAP and cannot authenticate to mailbox JMAP. The mailbox
credential must be an application password created by the
`gateway-test@agents.aiat.ca` account. See the official Stalwart
[API key](https://stalw.art/docs/auth/authentication/api-key/) and
[application password](https://stalw.art/docs/auth/authentication/app-password/)
documentation. The permission identifiers below are the exact serialized
v0.16.7 names, confirmed against the tagged
[v0.16.7 permission registry](https://github.com/stalwartlabs/stalwart/blob/v0.16.7/crates/registry/src/schema/enums_impl.rs).

The repository uses these management calls:

| Caller | Read-only management calls |
| --- | --- |
| `stalwart_secret_migration.py` | `x:Domain/query`, `x:Account/query` |
| `preflight-resend-certification.sh` | `x:MtaRoute/get`, `x:MtaOutboundStrategy/get` through `verify-stalwart-relay.sh` |
| `certify-resend.sh` | none |

Do not add an “API key for programmatic access” under **Management ›
Directory › Accounts › account › Credentials**. In v0.16.7 the `secret` and
`createdAt` members exposed there are server-set; attempting to patch the
embedded `Account.credentials` representation fails with `Cannot modify
server set property`. API keys are created as standalone `ApiKey` registry
objects using `x:ApiKey/set`.

The WebUI bundle currently served by the pinned live instance was inspected
read-only at both `/account/` and `/admin/`. Both serve
`assets/index-DEWCe6TU.js`, which has no `ApiKey`, **API Keys**, or
`sysApiKeyCreate` route/menu entry. Therefore the dedicated API Keys UI is
unavailable in this installed WebUI bundle; this is not fixed by editing the
account Credentials form. The supported repository procedure below checks
the authenticated operator's effective `sysApiKeyCreate` permission before
making one standalone create call. It never changes the operator account,
role, or password. Thus the observed missing menu is a route/bundle issue;
the procedure does not assume the role permission and verifies it
independently before creation.

Create the mailbox application password:

1. Sign out of the operator account. At
   `http://127.0.0.1:18080/account`, sign in specifically as
   `gateway-test@agents.aiat.ca`.
2. Select **Credentials**, then **App Passwords**, and select
   **Create App Password**. Stalwart does not permit an administrator to
   create this credential on the mailbox user's behalf.
3. Set the description to `AIAT one-message Resend certification`.
4. Set **Permissions** to **Replace**. Select exactly:
   `authenticate`, `jmapMailboxGet`, `jmapIdentityGet`, `jmapEmailGet`,
   `jmapEmailCreate`, `jmapEmailUpdate`, and
   `jmapEmailSubmissionCreate`.
5. Leave **Allowed IPs** empty for the same Docker source-address reason, set
   an expiry covering only the certification window, create it, and retain
   the one-time application password for the protected prompt. Do not use a
   management API key here.

After creating the mailbox application password, create the standalone API
key and the exact two-line, root-owned mode-0600 certification file. Run this
locally in WSL; all three secrets are read from no-echo prompts and are never
placed in arguments or shell history:

```sh
cd /mnt/c/projects/AIAT/mas/infra/smtp-gateway
sudo python3 scripts/provision-stalwart-certification-api-key.py \
  --output /etc/aiat/resend-certification.env \
  --expires-in-hours 24
sudo awk -F= '{print $1"=<redacted>"}' /etc/aiat/resend-certification.env
```

If provisioning fails, rerun it normally from the terminal with bounded
diagnostics. Do not redirect stdin/stdout through `sudo sh -c`; both passwords
must continue to use the controlling terminal's no-echo prompts. The
administrator address is non-secret and may be supplied as an argument:

```sh
sudo python3 scripts/provision-stalwart-certification-api-key.py \
  --administrator-address admin@agents.aiat.local \
  --output /etc/aiat/resend-certification.env \
  --expires-in-hours 24 \
  --diagnose
```

On failure, diagnostic output is limited to endpoint path, authentication
mechanism, HTTP status, JMAP method, bounded sanitized error type and
description, and independent preflight states. For example:

```text
ENDPOINT_PATH=local-docker-inspect
AUTHENTICATION_MECHANISM=none
HTTP_STATUS=not-applicable
JMAP_METHOD=not-attempted
JMAP_ERROR_TYPE=unsafe-stalwart-version/scopedCredentialEscalation
DESCRIPTION=ApiKey provisioning requires the pinned v0.16.15 security-patched image
ACCOUNT_PERMISSION_PERSISTED=NOT_ATTEMPTED
TOKEN_SCOPE_CONTAINS_SYS_API_KEY_CREATE=NOT_ATTEMPTED
API_KEY_CREATE_CAPABILITY=NOT_ATTEMPTED
ADMINISTRATOR_AUTHENTICATION=NOT_ATTEMPTED
MAILBOX_APPLICATION_PASSWORD_VALIDATION=NOT_ATTEMPTED
```

The diagnostic mode never prints the administrator password, application
password, Authorization value, bearer token, generated `API_` secret, raw
request body, or raw response body. It distinguishes authentication failure,
missing `sysApiKeyCreate`, unsupported method, invalid `x:ApiKey/set`
payload, forbidden permission assignment, and other server-side validation
errors. Any reserved but incomplete output file is removed before failure is
returned.

### Required v0.16.15 security upgrade

Do not run API-key provisioning on v0.16.7. The v0.16.15 release fixes an
authorization defect where a scoped credential holding `SysApiKeyCreate` or
`SysApiKeyUpdate` could regain its owning account's full rights. In v0.16.7,
the `Inherit` and `Disable` branches of credential validation return without
checking the resulting effective permission set. v0.16.15 computes the
effective permissions for every mode and passes them through
`can_grant_permissions`; see the official
[v0.16.15 release note](https://github.com/stalwartlabs/stalwart/releases/tag/v0.16.15)
and [security-fix commit](https://github.com/stalwartlabs/stalwart/commit/aa9f47cdb6073eea90835945ac6a5f62c17c79f8).
Because this workflow necessarily exercises
`SysApiKeyCreate`, the repository refuses every image except the approved
v0.16.15 digest before it prompts for credentials or sends a request.

The upgrade is an in-place v0.16 patch upgrade. It recreates only the
Stalwart container and preserves the two existing named volumes, ports,
networks, restart policy, healthcheck, Resend secret source, accounts,
mailboxes, messages, and configuration. It never runs `docker compose down`
or deletes a volume.

Pull the approved target by digest, then run the dedicated read-only
pre-upgrade inspection. This action is intentionally separate from
`migrate-stalwart-resend-secret.sh inspect`: the latter must continue refusing
a container that already has `RESEND_API_KEY`.

```sh
cd /mnt/c/projects/AIAT/mas/infra/smtp-gateway
export AIAT_STALWART_SECURITY_BACKUP=/secure/rollback/stalwart-v01615-$(date -u +%Y%m%dT%H%M%SZ)
export STALWART_RESEND_SECRET_FILE=/etc/aiat/stalwart-resend.env
sudo install -d -o root -g root -m 0700 "$AIAT_STALWART_SECURITY_BACKUP"

sudo docker pull \
  "ghcr.io/stalwartlabs/stalwart:v0.16.15@sha256:4f926193e5dd9ceb1e24ba48160702310381b12e51972c2fb0cc9de020388136"

sudo sh scripts/stalwart-security-upgrade.sh diagnose \
  --backup-dir "$AIAT_STALWART_SECURITY_BACKUP"

sudo sh scripts/stalwart-security-upgrade.sh inspect \
  --backup-dir "$AIAT_STALWART_SECURITY_BACKUP"
```

`diagnose` is read-only and reports the source semantic result, independent
config-hash result, bounded safe field names for every material mismatch, and
one drift classification. `COMPOSE_METADATA` means the runtime-significant
definition is identical and only the Compose provenance hash differs, such as
after a Compose hash-algorithm/version change. `REPOSITORY_CHANGE` means the
semantics still match but Git records a working-tree or committed source change
after container creation. `MATERIAL_DRIFT` blocks inspection and identifies
each differing field. Secret values and secret fingerprints are never printed.
The sanitized manifest records only the ignored label category names
(`com.docker.compose.*`, `desktop.docker.io/*`, and
`org.opencontainers.image.*`), never label values. Compose project/service,
working-directory, and source-file provenance remain independently validated;
unknown or explicit configured-label differences remain material.

`inspect` verifies the exact live `mas-stalwart-1` source semantics, protected
secret source, canonical source-only Compose resolution, and separately cached
target digest. The target check inspects the exact tag-plus-digest Compose
reference but compares Docker's normalized `repository@digest` metadata, and
requires `linux/amd64`; it does not use the stale platform-manifest digest.
The v0.16.15 override does not participate in source comparison.
An otherwise identical source is permitted to have a different
`com.docker.compose.config-hash`; both live and rendered hashes, the drift
classification, Git provenance result, and semantic result are recorded in
the sanitized manifest. Any image, command, entrypoint, environment-name,
mount, port, network, restart, healthcheck, security, DNS, user, or meaningful
label difference remains fail-closed.
It writes only a root-owned mode-`0600` sanitized
`pre-upgrade-manifest.json`; it does not stop, restart, recreate, or write into
the live volumes. Rerunning it is read-only with respect to Stalwart and only
accepts the identical manifest.

Make the stopped, consistent backup:

```sh
sudo sh scripts/stalwart-security-upgrade.sh backup \
  --backup-dir "$AIAT_STALWART_SECURITY_BACKUP"
```

The backup action stops only `mas-stalwart-1`, copies `/etc/stalwart` and
`/var/lib/stalwart` to new root-only trees, and restarts the same v0.16.7
container. A copy failure triggers an automatic restart and records a
fail-closed partial state. A completed or partial backup is never silently
overwritten.

The explicit operator-approved cutover is:

```sh
sudo sh scripts/stalwart-security-upgrade.sh cutover \
  --backup-dir "$AIAT_STALWART_SECURITY_BACKUP" \
  --stop-timeout 45 \
  --approve-security-upgrade
```

Cutover completes all source and target preconditions before issuing
`docker stop --time 45`. It records each lifecycle phase, never runs the
source running/health validator after the intentional stop, and automatically
restores the v0.16.7 definition if target recreation or verification fails.
SIGKILL-derived exit 137 fails closed and is recorded. It cannot execute twice
after target recreation begins. Post-start validation is split into
`TARGET_RECREATION_COMMAND_PASS`, `TARGET_HEALTH_PASS`,
`TARGET_SEMANTIC_VALIDATION_PASS`, `TARGET_COMPOSE_IDENTITY_PASS`,
`TARGET_SECRET_MATCH_PASS`, and `POST_CUTOVER_VERIFICATION`. A target
config-hash mismatch is accepted only when the field-by-field target semantics
match and the mismatch is classified as Compose metadata; the hash remains
recorded provenance and is never a blanket bypass. If Compose recreated the
target but the process was interrupted before the success artifact was
recorded, use verification-only recovery; this command never recreates the
container:

```sh
sudo sh scripts/stalwart-security-upgrade.sh verify \
  --backup-dir "$AIAT_STALWART_SECURITY_BACKUP"
```

To validate the existing stopped consistent backup after an interrupted or
failed cutover, without contacting Docker or modifying the live host:

```sh
sudo sh scripts/stalwart-security-upgrade.sh backup-integrity \
  --backup-dir "$AIAT_STALWART_SECURITY_BACKUP"
```

If cutover writes `cutover-failed.json`, inspect the bounded failure reason and
verify that the recovered source is the approved v0.16.7 definition:

```sh
sudo sh scripts/stalwart-security-upgrade.sh failure-diagnose \
  --backup-dir "$AIAT_STALWART_SECURITY_BACKUP"
```

The diagnostic is read-only. It prints only an allowlisted failure stage,
error code, sanitized message, recovery state, and `SOURCE_RECOVERED`; it
never prints Docker output, environment values, secret fingerprints, or a
target container's config. A cutover that created a target is not eligible
for retry; use `verify` or the rollback procedure instead.

For the governed state represented by a failed attempt with no target,
passed source auto-recovery, and an intact backup, authorize a new attempt
without deleting artifacts or taking another backup:

```sh
sudo sh scripts/stalwart-security-upgrade.sh retry \
  --backup-dir "$AIAT_STALWART_SECURITY_BACKUP" \
  --stop-timeout 45 \
  --approve-security-upgrade
```

`retry` first validates the existing manifest, backup trees, target image, and
healthy recovered v0.16.7 source. It then archives the previous attempt's
protected phase files under `attempt-history/` and invokes the normal cutover
state machine. It refuses a completed cutover, an existing target, partial
backup state, source/volume drift, or a failed source recovery. `resume` is an
equivalent action name for automation; neither action deletes an artifact or
recreates a Docker volume.

An older `cutover-failed.json` from the previous lifecycle version may lack
the newer bounded error fields. It is not silently normalized into retry
authority. Use this exact read-only adoption sequence:

```sh
sudo sh scripts/stalwart-security-upgrade.sh failure-diagnose \
  --backup-dir "$AIAT_STALWART_SECURITY_BACKUP"

sudo sh scripts/stalwart-security-upgrade.sh backup-integrity \
  --backup-dir "$AIAT_STALWART_SECURITY_BACKUP"

sudo sh scripts/stalwart-security-upgrade.sh diagnose \
  --backup-dir "$AIAT_STALWART_SECURITY_BACKUP"

sudo sh scripts/stalwart-security-upgrade.sh adopt-legacy-failure \
  --backup-dir "$AIAT_STALWART_SECURITY_BACKUP"

sudo sh scripts/stalwart-security-upgrade.sh failure-diagnose \
  --backup-dir "$AIAT_STALWART_SECURITY_BACKUP"

sudo sh scripts/stalwart-security-upgrade.sh retry \
  --backup-dir "$AIAT_STALWART_SECURITY_BACKUP" \
  --stop-timeout 45 \
  --approve-security-upgrade

sudo sh scripts/stalwart-security-upgrade.sh verify \
  --backup-dir "$AIAT_STALWART_SECURITY_BACKUP"
```

`adopt-legacy-failure` is read-only with respect to Docker and creates only a
sanitized `legacy-failure-adoption.json` plus a protected copy of the original
legacy artifacts under `attempt-history/legacy-failure-adoption/`. It requires
the legacy artifact itself to record mutation, passed source recovery, and
preserved volumes; current source state alone cannot authorize adoption. It
also independently verifies the exact source container, backup, target
absence, source semantics, target image identity, and secret match. Repeating
adoption returns `ADOPTION_ALREADY_VERIFIED=PASS` only while those hashes and
live read-only checks still agree.

Successful Compose source recovery may recreate `mas-stalwart-1`, so the
recovered container ID can legitimately differ from the ID recorded in the
original pre-cutover manifest. Adoption preserves that original ID as
provenance and binds the idempotent adoption artifact to the recovered ID.
The acceptance rule is therefore canonical source-definition equality,
including volume source, destination, read/write mode and propagation, plus
healthy runtime, Compose identity, protected secret-source equality, target
absence, and the legacy failure/target-recreation evidence. A changed ID
without that evidence fails with
`ERROR_CODE=source-recovery-identity-unverified`; a material definition drift
never becomes acceptable merely because recovery was recorded.

Docker may also regenerate the default container hostname from the new
container ID during that Compose recreation. Adoption permits this single
hostname difference only when Compose has no explicit `hostname`, the original
and recovered hostnames exactly equal the first 12 characters of their
corresponding verified container IDs, and every other canonical field matches.
The adoption artifact records the sanitized original/recovered hostnames and
`hostname_source=DOCKER_CONTAINER_ID`; arbitrary or explicitly configured
hostname changes remain fail-closed.

After adoption, `failure-diagnose` reports
`SOURCE_RECOVERED=PASS`, `SOURCE_CONTAINER_RECREATED=PASS`, the original and
recovered source IDs, `SOURCE_HOSTNAME_REGENERATED=PASS` when applicable, and
`GOVERNED_RETRY_ELIGIBLE=PASS`. It remains a verification-only diagnostic and
performs no Docker mutation.

If verification fails, recreate the old image definition against the same
volumes:

```sh
sudo --preserve-env=STALWART_RESEND_SECRET_FILE docker compose \
  --project-name mas \
  --project-directory /mnt/c/projects/AIAT/mas/infra/compose \
  --profile mail-local \
  -f home/docker-compose.stalwart-canonical.yml \
  -f home/docker-compose.stalwart-resend-secret.yml \
  up -d --no-deps --force-recreate stalwart
```

If a data restore is also required, stop the rollback container, copy the
root-only backup trees back into the already-mounted paths, then restart it:

```sh
sudo docker stop mas-stalwart-1
sudo docker cp "$AIAT_STALWART_SECURITY_BACKUP/etc-stalwart/." \
  mas-stalwart-1:/etc/stalwart/
sudo docker cp "$AIAT_STALWART_SECURITY_BACKUP/var-lib-stalwart/." \
  mas-stalwart-1:/var/lib/stalwart/
sudo docker start mas-stalwart-1
```

Do not remove or recreate either named volume. The rollback image remains
security-blocked for API-key provisioning; rollback is only an availability
recovery while the v0.16.15 cutover issue is investigated.

At the prompts, provide:

1. The existing regular Stalwart administrator address.
2. Its existing password.
3. The one-time application password previously created by
   `gateway-test@agents.aiat.ca`.

The administrator password is not sent as Basic authentication to
`GET /api/account`. The pinned v0.16.7 WebUI is an OAuth client named
`stalwart-webui`: it uses an authorization-code flow with PKCE, exchanges
the one-time code at `/auth/token`, and sends the resulting access token as
Bearer authentication on management requests. The local script follows the
same server-supported flow without launching a browser or using browser
cookies:

1. `POST /api/auth` authenticates the administrator address/password and
   obtains a one-time authorization code protected by an S256 PKCE challenge.
2. `POST /auth/token` exchanges the code and verifier for an in-memory Bearer
   token.
3. Bearer `GET /auth/userinfo` must report both `preferred_username` and
   `email` as the exact requested administrator address.
4. Read-only `x:Domain/query`, `x:Account/query`, and `x:Account/get` prove
   that the exact persisted administrator account explicitly enables
   `sysApiKeyCreate`.
5. `GET /api/account` is recorded separately as the Bearer token's current
   scope. It is not treated as authoritative persisted-account evidence.
6. Read-only `x:ApiKey/query` and `x:ApiKey/get` refuse a duplicate
   certification key before creation.
7. The gateway mailbox application password is separately validated with
   HTTP Basic authentication. This mailbox credential is not the
   administrator management session.
8. Bearer management JMAP `POST /api` sends exactly one `x:ApiKey/set`
   request. Its authorization result is the create-capability evidence.

v0.16.15 has no separate non-mutating method guarded by
`sysApiKeyCreate`. Consequently, the script proves persisted account state
and current token scope with read-only calls first, then treats the one
operator-approved create response itself as the capability result. It never
attempts that mutation on v0.16.7.

These endpoints and behaviors are confirmed in the official tagged v0.16.7
sources for the
[management login route](https://github.com/stalwartlabs/stalwart/blob/v0.16.7/crates/http/src/api/mod.rs),
[OAuth password authentication and PKCE code issuance](https://github.com/stalwartlabs/stalwart/blob/v0.16.7/crates/http/src/auth/oauth/auth.rs),
[token exchange](https://github.com/stalwartlabs/stalwart/blob/v0.16.7/crates/http/src/auth/oauth/token.rs),
and [OpenID user-info identity](https://github.com/stalwartlabs/stalwart/blob/v0.16.7/crates/http/src/auth/oauth/openid.rs).

The create request contains:

```text
description: AIAT Resend certification read-only
permissions mode: Replace
permissions:
  authenticate
  sysAccountQuery
  sysDomainQuery
  sysMtaOutboundStrategyGet
  sysMtaRouteGet
allowedIps: empty
expiresAt: current UTC time plus 24 hours
```

`secret` and `createdAt` are omitted because they are server-set. The
one-time `API_...` secret returned by Stalwart is written directly to the
reserved mode-0600 output file and is never printed. If authentication,
permission validation, creation, or file writing fails, the incomplete
output is removed. If the key was created but the protected local write
fails, the script immediately destroys that exact new key. A retry is refused
while a key with the certification description already exists, because its
one-time secret cannot be recovered safely.

The final `awk` output must contain exactly these two names, in this order:

```text
STALWART_API_KEY=<redacted>
STALWART_JMAP_SERVICE_TOKEN=<redacted>
```

Obtain the server-assigned JMAP accountId. This validates the management
key's exact effective permission set, resolves `agents.aiat.ca`, and then
queries `gateway-test` using that domain ID:

```sh
sudo python3 scripts/validate-stalwart-certification-credentials.py \
  --secret-file /etc/aiat/resend-certification.env \
  --lookup-account-id
```

Record only the reported `ACCOUNT_ID`. Then validate both credentials,
ownership of that accountId, mailbox access, sender identity, and exact
effective permissions:

```sh
sudo python3 scripts/validate-stalwart-certification-credentials.py \
  --secret-file /etc/aiat/resend-certification.env \
  --account-id <reported-ACCOUNT_ID>
```

The validator is read-only. It remains on `127.0.0.1:18080`, never prints a
secret, rejects missing permissions, and fails if either credential has any
detectable permission beyond the exact sets above.

Create one new backup directory name and reuse it for every migration command:

```sh
export AIAT_STALWART_MIGRATION_BACKUP=/secure/rollback/stalwart-resend-secret-20260729T000000Z
sudo install -d -o root -g root -m 0700 /secure/rollback
```

The shared arguments for all migration actions are:

```sh
stalwart_migrate() {
  sudo sh scripts/migrate-stalwart-resend-secret.sh "$@" \
    --container mas-stalwart-1 \
    --project-name mas \
    --project-directory /mnt/c/projects/AIAT/mas/infra/compose \
    --compose-profile mail-local \
    --compose-file /mnt/c/projects/AIAT/mas/infra/smtp-gateway/home/docker-compose.stalwart-canonical.yml \
    --secret-file /etc/aiat/stalwart-resend.env \
    --backup-dir "$AIAT_STALWART_MIGRATION_BACKUP"
}
```

Read-only diagnosis emits only a categorized, bounded, sanitized Compose error:

```sh
stalwart_migrate diagnose
```

First inspect the exact live container and write the sanitized manifest. This
refuses an unpinned image, unhealthy container, missing `/var/lib/stalwart`
mount, anonymous volume, untracked volume/bind, stale Compose definition, or
an already-present key:

```sh
stalwart_migrate inspect
```

Run the non-mutating dry-run and review its sanitized output and both mode-0600
JSON artifacts in the backup directory:

```sh
stalwart_migrate dry-run
```

Only after explicit operator approval, recreate the Stalwart service with
`--no-deps --force-recreate --no-build --pull never`. The script compares the
post-recreation definition to the backup and permits only the new environment
name plus Compose's expected internal config-hash/config-file replacement
metadata. All configured labels must remain unchanged:

```sh
stalwart_migrate apply --approve-recreate-stalwart
```

If the approved recreation completed but the command stopped during
post-recreation checks, do not run `apply` again. Resume with the
verification-only recovery action. It requires the existing manifest and
dry-run artifact, a different healthy container ID, the approved Compose
config hash, and the injected secret; it never invokes Compose `up`:

```sh
stalwart_migrate recover \
  --verification-secret-file /etc/aiat/resend-certification.env \
  --account-id <existing-gateway-test-stalwart-account-id>
```

Verify the preserved image, mounts, ports, networks, labels, restart policy,
health, key-source fingerprint, production domain/account, local SMTP/JMAP,
and WireGuard-only SMTP forward. `recover` and `verify` share the same strict
checks and write `post-migration-success.json` only after every check passes:

```sh
stalwart_migrate verify \
  --verification-secret-file /etc/aiat/resend-certification.env \
  --account-id <existing-gateway-test-stalwart-account-id>
```

### Adopt an already-injected container after the security upgrade

If the v0.16.15 image upgrade was completed and separately verified before
secret-migration evidence was available, use the explicitly separate
`adopt-existing` action. It never runs Compose `up`, stops a container, or
changes a volume. The existing v0.16.7 evidence directory remains immutable;
use a new directory that does not exist yet. The command below uses the
approved pinned v0.16.15 Compose definition and the existing secret override:

```sh
export AIAT_STALWART_ADOPTION_BACKUP=/secure/rollback/stalwart-resend-secret-adopt-20260731T171900Z
sudo install -d -o root -g root -m 0700 /secure/rollback

stalwart_adopt_existing() {
  sudo sh scripts/migrate-stalwart-resend-secret.sh "$@" \
    --container mas-stalwart-1 \
    --project-name mas \
    --project-directory /mnt/c/projects/AIAT/mas/infra/compose \
    --compose-profile mail-local \
    --compose-file /mnt/c/projects/AIAT/mas/infra/smtp-gateway/home/docker-compose.stalwart-canonical.yml \
    --compose-file /mnt/c/projects/AIAT/mas/infra/smtp-gateway/home/docker-compose.stalwart-v0.16.15-security-upgrade.yml \
    --override-file /mnt/c/projects/AIAT/mas/infra/smtp-gateway/home/docker-compose.stalwart-resend-secret.yml \
    --secret-file /etc/aiat/stalwart-resend.env \
    --backup-dir "$AIAT_STALWART_ADOPTION_BACKUP"
}

stalwart_adopt_existing adopt-existing \
  --approve-adopt-existing-secret \
  --verification-secret-file /etc/aiat/resend-certification.env \
  --account-id w
```

The directory must be absent before the command starts. Adoption requires the
exact v0.16.15 image and image ID, healthy `mas-stalwart-1`, tracked named
mounts, loopback ports `127.0.0.1:2525` and `127.0.0.1:18080`, `mas_internal`
and `mas_public`, the approved restart policy/healthcheck/security settings,
Compose project/service/file provenance, the injected secret source match,
the production domain/account, local SMTP/JMAP, WireGuard SMTP, and the exact
least-privilege certification credentials. It rejects unknown service labels,
stale Compose hashes, reused/partial backup directories, wrong account IDs,
and overprivileged credentials.

The two mode-0600 JSON files use adoption artifact schema `2` and are:

* `adopted-existing-baseline.json`: schema `2`, `action_type:
  "adopt-existing"`, live container/image identity, normalized definition
  fingerprint, the ordered `compose_file_order` below, Compose config/source
  hashes, sanitized live snapshot, account ID/address, verification results,
  timestamp, `secret_source_match: "PASS"`,
  and explicit `live_mutation: "NOT_PERFORMED"`,
  `compose_recreation: "NOT_PERFORMED"`, `volume_mutation: "NONE"`.
* `adopted-existing-success.json`: the same immutable identity, ordered file
  provenance, and verification contract, written only after all checks pass.

The certified Compose file order is exactly:

```text
docker-compose.stalwart-canonical.yml
docker-compose.stalwart-resend-secret.yml
docker-compose.stalwart-v0.16.15-security-upgrade.yml
```

The live `com.docker.compose.project.config_files` label is checked as this
ordered list, with exactly three unique approved repository paths. Per-file
SHA-256 hashes and the Compose config hash must also match during later
verification. The normal pre-secret migration lifecycle retains its existing
Compose ordering behavior; only `adopt-existing` uses this ordered stack.

Neither artifact contains the Resend key, a key fingerprint, credential values,
or environment values. Later verification automatically recognizes this
baseline and remains read-only:

```sh
stalwart_adopt_existing verify \
  --verification-secret-file /etc/aiat/resend-certification.env \
  --account-id w
```

The migration verifier discovers the authenticated JMAP endpoint from
`GET /jmap/session`, safely replaces an advertised `localhost` authority with
`http://127.0.0.1:18080`, and sends all management JMAP POSTs to the resolved
`/jmap/` URL. `POST /api` is never used for JMAP; `GET /api/account` remains
available only for permission introspection in the certification validator.

Until verification succeeds, restore the original Compose definition without
the secret with the following command. Successful verification closes this
rollback window. If the route has already been applied, roll it back first
with the route rollback command below:

```sh
stalwart_migrate rollback --approve-rollback
```

After migration verification, back up and apply the outbound route:

```sh
sudo sh scripts/configure-stalwart-resend-route.sh backup \
  profiles/oci-e2.1-micro-host.env.active \
  --secret-file /etc/aiat/resend-certification.env \
  --relay-secret-file /etc/aiat/stalwart-resend.env \
  --stalwart-container mas-stalwart-1 \
  --backup /secure/rollback/stalwart-remote-route.json

sudo sh scripts/configure-stalwart-resend-route.sh apply \
  profiles/oci-e2.1-micro-host.env.active \
  --secret-file /etc/aiat/resend-certification.env \
  --relay-secret-file /etc/aiat/stalwart-resend.env \
  --stalwart-container mas-stalwart-1 \
  --backup /secure/rollback/stalwart-remote-route.json

sudo sh scripts/configure-stalwart-resend-route.sh verify \
  profiles/oci-e2.1-micro-host.env.active \
  --secret-file /etc/aiat/resend-certification.env \
  --relay-secret-file /etc/aiat/stalwart-resend.env \
  --stalwart-container mas-stalwart-1 \
  --backup /secure/rollback/stalwart-remote-route.json
```

Then run the local certification preflight:

```sh
sudo sh scripts/preflight-resend-certification.sh \
  profiles/oci-e2.1-micro-host.env.active \
  --secret-file /etc/aiat/resend-certification.env \
  --relay-secret-file /etc/aiat/stalwart-resend.env \
  --stalwart-container mas-stalwart-1 \
  --account-id <existing-gateway-test-stalwart-account-id> \
  --sender gateway-test@agents.aiat.ca
```

Route rollback:

```sh
sudo sh scripts/configure-stalwart-resend-route.sh rollback \
  profiles/oci-e2.1-micro-host.env.active \
  --secret-file /etc/aiat/resend-certification.env \
  --relay-secret-file /etc/aiat/stalwart-resend.env \
  --stalwart-container mas-stalwart-1 \
  --backup /secure/rollback/stalwart-remote-route.json
```

Run only after an operator has approved the exact sender and external mailbox:

```sh
sudo sh scripts/certify-resend.sh \
  profiles/oci-e2.1-micro-host.env.active \
  --secret-file /etc/aiat/resend-certification.env \
  --relay-secret-file /etc/aiat/stalwart-resend.env \
  --stalwart-container mas-stalwart-1 \
  --account-id <existing-gateway-test-stalwart-account-id> \
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
