# Local Stalwart + AIAT identity service

This is a development-only Compose profile. Stalwart publishes only:

```text
127.0.0.1:2525  -> SMTP/25 (loopback delivery tests)
127.0.0.1:18080 -> HTTP/JMAP/admin/8080 (loopback bootstrap and management)
```

The profile has no public SMTP, public administration, DNS automation, MX,
Resend relay, public TLS, or Internet-delivery claim. The identity service is
reachable from the host only at `http://127.0.0.1:8011` for local tests and
from the orchestrator/tool-service over the private Compose network. Its
database is the separate `identity_postgres_local_data` volume.

The local identity configuration explicitly sets `OUTBOUND_RELAY_PROVIDER=disabled`,
`DIRECT_MX_OUTBOUND_ENABLED=false`, and `DEFAULT_OUTBOUND_ENABLED=false`; it
does not contact Resend or attempt Internet delivery.

## One-time local setup

Run these commands from `mas/infra/compose` in PowerShell. The root `.env`
must contain `STALWART_RECOVERY_ADMIN` for first boot. Keep that value in the
ignored root env file; do not paste it into a command line.

```powershell
if (-not (Test-Path .\.env.stalwart-local)) {
  Copy-Item .\stalwart-local.env.example .\.env.stalwart-local
}

$compose = @(
  '--env-file', '../../../.env',
  '--env-file', '.env.stalwart-local',
  '-f', 'docker-compose.yml',
  '-f', 'docker-compose.stalwart-local.yml',
  '--profile', 'mail-local'
)

docker compose @compose up -d stalwart
python .\scripts\bootstrap-stalwart-local.py
```

On a new Stalwart volume the bootstrap command prints only a restart
instruction. Apply it, then run the bootstrap command again:

```powershell
docker compose @compose restart stalwart
python .\scripts\bootstrap-stalwart-local.py
```

The script is idempotent. It creates or adopts `agents.aiat.local`, creates a
dedicated `aiat-service@agents.aiat.local` local JMAP account, and stores only
the generated development values in the ignored `.env.stalwart-local` file.
The management key is restricted to domain/account query/create/update plus
the explicit worker mail/JMAP permissions needed for passwordless mailbox
creation; worker accounts never inherit Stalwart's broad default User role.
The mail credential is a loopback-only Basic credential for the dedicated
service account. Production must use a managed OAuth bearer credential
instead.

Its JSON result marks each required reference as `configured` and includes
`"secrets_printed": false`; it never echoes a credential value.

## Start the complete local identity stack

The migration is run as a Compose dependency and must report revision
`0001_identity_control_plane`:

```powershell
docker compose @compose up -d identity-service orchestrator-api tool-service
docker compose @compose ps identity-postgres identity-migrate identity-service stalwart orchestrator-api tool-service
docker compose @compose logs --no-color identity-migrate
Invoke-WebRequest http://127.0.0.1:8011/healthz | Select-Object -Expand Content
Invoke-WebRequest http://127.0.0.1:8011/readyz | Select-Object -Expand Content
```

The expected migration evidence is an Alembic `Running upgrade ... ->
0001_identity_control_plane` (or an already-at-head/no-op result on a repeat
run), followed by a healthy `identity-service` `/readyz` check. No identity
credential is printed by these commands.

## Complete local acceptance matrix

Run the real provider test from the same directory:

```powershell
python .\scripts\test-stalwart-local-identity.py
```

The test creates the domain, provisions two deterministic worker mailboxes,
provisions one of them twice to prove idempotency, sends a message through the
loopback SMTP listener, lists and reads it through identity-service JMAP, and
extracts a six-digit verification code. It then verifies cross-worker denial,
activates the mailbox from persisted JMAP evidence, suspends one worker,
archives the other (revocation/retention path), and restarts both Stalwart and
identity-service before checking the durable state and mail evidence again.
It prints only resource IDs, states, counts, and pass/fail evidence—not
passwords, API keys, private keys, database contents, or message bodies.

The test is deliberately limited to local loopback behavior. It does **not**
test or imply Internet delivery, Resend, DNS, MX, public TLS, or public SMTP.

## Stop without deleting state

```powershell
docker compose @compose stop identity-service identity-migrate identity-postgres stalwart
```

The named volumes preserve the identity database and Stalwart mail state for
the restart/persistence test. Do not run `down -v` unless intentionally
discarding local state. Generated `.env.stalwart-local`, credentials, mail
state, and databases are ignored and must never be committed.
