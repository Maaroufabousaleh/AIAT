# AIAT mail edge

This is the production mail and identity deployment bundle for Oracle or a
small paid VPS. It exposes only inbound SMTP (`25`) and Caddy HTTPS (`80/443`).
Postgres, the Stalwart management API, and identity-service do not publish
ports. They communicate on `mail_private`; `mail_egress` permits only outbound
ACME, Resend, and provider API traffic and does not publish an administration
surface.

## Preconditions

- Set `A`/`MX`/PTR records for `mail.aiat.ca`, `aiat.ca`, and `agents.aiat.ca`.
- Add the provider-issued Resend SPF/DKIM records and a DMARC record.
- Create an age recipient for encrypted backups and store its private identity
  outside the repository and VPS backup volume.
- Provide real values in a root-owned `.env.mail-edge` copied from the example.
  Do not commit this file. Enrol public Ed25519 client keys for the laptop and
  tool-service in `IDENTITY_CLIENT_PUBLIC_KEYS_JSON` with least-privilege
  scopes in `IDENTITY_CLIENT_SCOPES_JSON`. The matching laptop/tool-service
  environment names are documented in [`mas/.env.identity.example`](../../.env.identity.example).
- Supply two separate Stalwart credentials: `STALWART_API_KEY` is restricted
  to management JMAP operations, while `STALWART_JMAP_SERVICE_TOKEN` is an
  operator-created service OAuth/bearer credential for governed mail JMAP
  access. Stalwart API keys cannot authenticate to mail JMAP; neither value is
  ever exposed to workers or the laptop. Identity-service and Caddy use the
  unexposed `stalwart:8080` listener only on `mail_private`; public JMAP/HTTPS
  terminates with a trusted certificate at Caddy.

## First deployment

Run the same render/build preflight on the operator laptop before uploading a
reviewed commit. Placeholder values are sufficient for static validation; they
are not sufficient to start the production services.

```sh
cd /mnt/c/projects/aiat/mas/infra/mail-edge
cp .env.mail-edge.example .env.mail-edge.local
./scripts/validate-mail-edge.sh .env.mail-edge.local
MAIL_EDGE_ENV_FILE=.env.mail-edge.local docker compose --env-file .env.mail-edge.local \
  --profile backup --profile restore --profile configure config -q
MAIL_EDGE_ENV_FILE=.env.mail-edge.local docker compose --env-file .env.mail-edge.local \
  --profile backup build identity-service encrypted-backup
```

On Oracle staging, and then production only after staging evidence is signed
off, use a root-owned environment file and the exact reviewed commit:

```sh
git checkout --detach <reviewed-commit-sha>
cd mas/infra/mail-edge
install -m 0600 /secure/aiat/mail-edge.env .env.mail-edge
./scripts/validate-mail-edge.sh .env.mail-edge
docker compose --env-file .env.mail-edge --profile backup build identity-service encrypted-backup
docker compose --env-file .env.mail-edge up -d identity-postgres stalwart
docker compose --env-file .env.mail-edge run --rm identity-migrate
docker compose --env-file .env.mail-edge up -d identity-service ingress
docker compose --env-file .env.mail-edge ps
```

The detached source build above is the approved first-staging path (method A):
the host checks out the reviewed commit and builds the custom images locally.
For a complete detached source build, build the laptop/control-plane images
from the same checkout as well:

```sh
cd /mnt/c/projects/aiat/mas
OPENCODE_SERVER_PASSWORD=static-validation-placeholder \
LLM_API_KEY=static-validation-placeholder \
LLM_GATEWAY_URL=http://litellm:4000 \
docker compose --env-file ../.env.example \
  -f infra/compose/docker-compose.yml build orchestrator-api tool-service
```

Record the resulting image content digests in the staging change record. For
production promotion, use method B instead: pull the reviewed custom images
from the operator-approved registry by immutable digest, or use an equivalently
reproducible signed release process. Mutable tags such as `latest` are not
production release identifiers. When using registry artifacts, replace the
build step with the registry pulls and verify each image digest before starting
services, for example:

```sh
docker pull <registry>/aiat/identity-service@sha256:<identity-digest>
docker pull <registry>/aiat/mail-edge-backup@sha256:<backup-digest>
docker pull <registry>/mas/orchestrator-api@sha256:<orchestrator-digest>
docker pull <registry>/mas/tool-service@sha256:<tool-digest>
docker image inspect \
  <registry>/aiat/identity-service@sha256:<identity-digest> \
  <registry>/aiat/mail-edge-backup@sha256:<backup-digest> \
  <registry>/mas/orchestrator-api@sha256:<orchestrator-digest> \
  <registry>/mas/tool-service@sha256:<tool-digest>
```

Do not treat locally built digests as registry-published artifacts, and do not
publish or push them without explicit operator authorization.

After Stalwart bootstrap creates an administrator/API key, run the relay policy
from the private Docker network. The policy creates the authenticated implicit
TLS Resend route, makes it the only remote route, removes `Mx` routes, and
checks the saved configuration.

```sh
docker compose --env-file .env.mail-edge --profile configure run --rm stalwart-relay-configurator
```

Re-run `verify-stalwart-relay.sh` through the profile-gated configurator after
every Stalwart upgrade or route change. Stalwart's management API supports `x:MtaRoute` and
`x:MtaOutboundStrategy`; the latter must select `resend-relay` for every remote
recipient. See the [Stalwart routing documentation](https://stalw.art/docs/mta/outbound/routing/)
and [strategy reference](https://stalw.art/docs/ref/object/mta-outbound-strategy/).

For first bootstrap only, Stalwart's HTTP admin listener is bound to VPS
loopback as `127.0.0.1:18080`; it is not a public port. Reach it from the
operator laptop with `ssh -N -L 18080:127.0.0.1:18080 <vps>` and open
`http://127.0.0.1:18080/admin`. Set a one-time `STALWART_RECOVERY_ADMIN` in
the secret environment if DNS/TLS is not ready. Complete the wizard and save
the API key in the secret store. Keep Stalwart's internal HTTP listener enabled
because the identity service requires private `/api` and `/jmap` access;
remove the recovery administrator and do not publish the loopback bootstrap
port in a hardened production override. Caddy keeps
`/admin` and `/api` unavailable on the public mail hostname.

## Operations

```sh
./scripts/validate-firewall.sh
PUBLIC_MAIL_IP=203.0.113.10 ./scripts/validate-dns.sh
./scripts/validate-tls.sh
./scripts/validate-smtp-relay.sh
./scripts/run-backup.sh .env.mail-edge
```

Schedule `run-backup.sh` from host cron/systemd (for example daily at
02:15 UTC), then copy the encrypted `.age` artifact to a second provider or
object-storage account. Keep the age private key off the VPS. A scheduled job
must alert on a missing artifact and perform a disposable restore drill at
least quarterly; the archived PostgreSQL payload is a logical `pg_dump`, not
a copied live database directory. The wrapper briefly stops identity-service
and Stalwart so the database and mail-file boundary is quiescent. Its exit trap
restores only services that were running before the backup; it never starts a
service that the operator had intentionally stopped.

The firewall must accept inbound TCP `25`, `80`, and `443`; it must reject
outbound TCP `25`. Do not expose `8010`, Postgres, or Stalwart `/api`/`/admin`.
Caddy intentionally returns `404` for Stalwart management paths on the public
mail host.

To restore, stop ingress, identity-service, and Stalwart before setting
`STALWART_RESTORE_OFFLINE=true`; leaving any of them active can admit writes or
mutate files during restoration. The restore profile is deliberately absent
from normal `up` and requires `RESTORE_CONFIRM=AIAT_MAIL_EDGE_RESTORE`. It
restores the logical identity database first. Stalwart files are copied only in
the explicit offline mode. Restart the three services only after the restore
command succeeds.

```sh
docker compose --env-file .env.mail-edge stop ingress identity-service stalwart
BACKUP_FILE=/backups/aiat-mail-edge-YYYYMMDDTHHMMSSZ.tar.age \
BACKUP_DECRYPTION_IDENTITY_FILE=/secure/age-identity.txt \
RESTORE_CONFIRM=AIAT_MAIL_EDGE_RESTORE \
STALWART_RESTORE_OFFLINE=true \
docker compose --env-file .env.mail-edge --profile restore run --rm encrypted-restore
docker compose --env-file .env.mail-edge up -d stalwart identity-service ingress
```

## Release rollback

Take and copy an encrypted backup off-host before every promotion. For an
application rollback that does not require an older schema, detach at the last
known-good reviewed commit, rebuild, and recreate only application containers:

```sh
git checkout --detach <last-known-good-commit-sha>
cd mas/infra/mail-edge
./scripts/validate-mail-edge.sh .env.mail-edge
docker compose --env-file .env.mail-edge --profile backup build identity-service encrypted-backup
docker compose --env-file .env.mail-edge up -d --no-deps --force-recreate identity-service ingress
docker compose --env-file .env.mail-edge ps
```

Do not downgrade the database while a newer application is running. If the
old release cannot read the current schema, stop ingress, identity-service,
and Stalwart, then restore the pre-release encrypted artifact with the offline
restore command above. Treat an Alembic downgrade as a destructive maintenance
operation; use it only after a second encrypted backup and schema-specific
review:

```sh
docker compose --env-file .env.mail-edge stop ingress identity-service stalwart
docker compose --env-file .env.mail-edge run --rm --entrypoint alembic identity-migrate \
  -c /app/alembic.ini downgrade <reviewed-revision>
docker compose --env-file .env.mail-edge up -d stalwart identity-service ingress
```

## Promotion checklist

1. `validate-mail-edge.sh`, firewall, DNS, and relay-TLS checks pass.
2. `verify-stalwart-relay.sh` confirms no `Mx` route and the Resend relay route.
3. A fresh worker is provisioned; it remains inactive until a real JMAP read
   confirms inbound delivery, then becomes `IDENTITY_ACTIVE`.
4. A tool call cannot read another worker's mailbox or external-account state.
5. An outbound request is approved by a human, sent through Stalwart, and has a
   stored delivery correlation without exporting the Resend credential.
6. An encrypted backup is created and a restore is tested in a disposable
   staging environment before production promotion.
7. The JMAP service credential can read and submit only through the
   identity-service; verify it cannot be retrieved from any AIAT endpoint.

## Mandatory live certification

Do this only on an approved staging deployment. Create two new fixed worker
UUIDs and two Ed25519 keypairs in the operator secret store. Enrol their public
keys in `IDENTITY_CLIENT_PUBLIC_KEYS_JSON` under client IDs
`worker:<worker-uuid>` with empty scope lists. Enrol the operator client with
`identity:admin` and `identity:delegate`. Never place private keys in the
mail-edge environment file, command line, shell history, or repository.

Inject the following into the test process from the operator secret store:

```text
AIAT_RUN_LIVE_IDENTITY_TESTS=1
LIVE_IDENTITY_SERVICE_URL=https://identity.aiat.ca
LIVE_MAIL_HOST=mail.aiat.ca
LIVE_SMTP_PORT=25
LIVE_SMTP_ENVELOPE_FROM=<approved-external-sender>
LIVE_IDENTITY_COMPANY_ID=<staging-company-uuid>
LIVE_IDENTITY_OPERATOR_CLIENT_ID=<enrolled-operator-client-id>
LIVE_IDENTITY_OPERATOR_PRIVATE_KEY=<secret>
LIVE_IDENTITY_WORKER_A_ID=<new-fixed-worker-uuid>
LIVE_IDENTITY_WORKER_A_CLIENT_ID=worker:<new-fixed-worker-uuid>
LIVE_IDENTITY_WORKER_A_PRIVATE_KEY=<secret>
LIVE_IDENTITY_WORKER_B_ID=<new-fixed-worker-uuid>
LIVE_IDENTITY_WORKER_B_CLIENT_ID=worker:<new-fixed-worker-uuid>
LIVE_IDENTITY_WORKER_B_PRIVATE_KEY=<secret>
LIVE_IDENTITY_OUTBOUND_RECIPIENT=<operator-controlled-external-inbox>
LIVE_IDENTITY_REQUIRE_REPLY=1
LIVE_IDENTITY_REPLY_TIMEOUT_SECONDS=600
LIVE_IDENTITY_SUSPEND_WORKER_B=1
```

The external-inbox operator must reply to the certification message while the
test is waiting. Run:

```sh
cd /mnt/c/projects/aiat/mas/apps/identity-service
PYTHONPATH=. ../../.venv-wsl/bin/python -m pytest \
  tests/test_live_identity_acceptance.py -vv -m live
```

That test creates two real Stalwart mailboxes, repeats provisioning to prove
idempotency, injects distinct messages over public SMTP/25, reads them through
governed JMAP, extracts a real code, rejects an unknown recipient, proves both
normal and forged-actor cross-worker reads fail, obtains a human outbound
approval, submits through Stalwart, waits for the external reply, and suspends
Worker B to prove mailbox revocation. Fresh worker UUIDs are required for a
subsequent full rerun because the second worker is deliberately left suspended.

The remaining acceptance gates and their exact commands are:

| Gate | Operator action and certification command |
| --- | --- |
| Oracle ingress, listeners, Resend egress, outbound MX denial | Configure the OCI NSG and host firewall, then run `cd mas/infra/mail-edge && ./scripts/validate-firewall.sh`. |
| Saved Stalwart Resend-only route | Run `docker compose --env-file .env.mail-edge --profile configure run --rm stalwart-relay-configurator`; it fails if an `Mx` route exists or the strategy is not `resend-relay`. |
| Forward DNS, PTR, MX, SPF, DKIM, DMARC | Publish the records, then run `PUBLIC_MAIL_IP=<reserved-ip> ./scripts/validate-dns.sh`. |
| Public TLS and identity health | Run `./scripts/validate-tls.sh`. |
| Resend TLS reachability | Run `./scripts/validate-smtp-relay.sh`. |
| Provisioning failure blocks activation | Run `cd /mnt/c/projects/aiat/mas && .venv-wsl/bin/python -m pytest apps/orchestrator-api/tests/test_identity_reconciliation.py -q`, then `cd apps/identity-service && PYTHONPATH=. ../../.venv-wsl/bin/python -m pytest tests/test_identity_service.py -q`; repeat the scenario on staging with Stalwart temporarily rejecting the dedicated new test mailbox and confirm the worker remains inactive before restoring service. |
| Laptop-offline Oracle continuity | Stop the laptop orchestrator and tool service, send a new message from a separate external host, and on Oracle run `docker compose --env-file .env.mail-edge ps identity-postgres stalwart identity-service`; all three must remain healthy. |
| Reconnection idempotency | Restart the laptop orchestrator twice, then run `cd /mnt/c/projects/aiat/mas && .venv-wsl/bin/python -m pytest apps/orchestrator-api/tests/test_identity_reconciliation.py -q` and verify the worker lifecycle/audit dashboard contains each Oracle sequence once. |
| External-account/browser isolation | With two explicitly approved staging test accounts, run `cd /mnt/c/projects/aiat/mas && .venv-wsl/bin/python -m pytest apps/tool-service/tests/test_browser_identity_isolation.py apps/tool-service/tests/test_signed_caller_auth.py -q`, then verify the two live profile paths, credentials, MFA state, and cookies remain distinct without exporting their contents. |
| No credential leakage | Run `install -d -m 0700 /secure/evidence && docker compose --env-file .env.mail-edge logs --no-color identity-service stalwart > /secure/evidence/mail-edge.log`, then `python3 ./scripts/validate-secret-evidence.py .env.mail-edge /secure/evidence/mail-edge.log <other-evidence-files>`. The scanner reports variable names only and must return zero; securely remove plaintext evidence after signing the result. |
| Identity Postgres is unreachable from laptop | From the laptop run `if timeout 5 bash -c ':</dev/tcp/identity.aiat.ca/5432' 2>/dev/null; then exit 1; else echo blocked; fi`. |
| Backup and ownership-preserving restore | Run `./scripts/run-backup.sh .env.mail-edge`, copy the `.age` artifact off-host, and execute the offline restoration procedure below against disposable staging; then rerun the two-worker live test with fresh enrolled worker IDs. |

Store the command output, provider message IDs, reply evidence, dashboard audit
export, DNS output, and encrypted-backup checksum in the staging change record.
Only the operator may approve production promotion after every row passes.
