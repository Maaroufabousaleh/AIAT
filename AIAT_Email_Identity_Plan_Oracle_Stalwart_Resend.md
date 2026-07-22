# AIAT Email Identity and External-Account Plan

## Chosen deployment architecture

This plan uses the following production topology:

```text
Internet senders and online services
        │ inbound SMTP on TCP 25
        ▼
Oracle Cloud Infrastructure Free Tier / Always Free VM
├── Stalwart Community Edition
│   ├── inbound SMTP
│   ├── real agent mailboxes
│   ├── JMAP/HTTPS
│   ├── outbound queue
│   └── authenticated relay to Resend
├── AIAT identity-service
├── dedicated identity Postgres database
├── backup and restore jobs
└── private administration network
        │
        │ authenticated SMTP submission on TCP 465 or 587
        ▼
Resend hosted SMTP relay
└── final outbound delivery to Gmail, Outlook, and other domains

Operator laptop
├── main AIAT application
├── dashboard
├── tool-service and agent runtimes
└── isolated browser-session workers
        │
        └── signed HTTPS/JMAP calls to the Oracle VPS
```

The Oracle VPS is the always-on mail edge. Stalwart receives and stores inbound
mail and relays approved outbound messages through Resend. It does not deliver
directly to recipient MX servers over outbound TCP 25.

The identity-service and its dedicated database run on the Oracle VPS so mailbox
ownership, provisioning, approvals, audit records, and queued identity events
remain available even when the operator laptop is off. The main AIAT application
stays on the laptop initially and accesses the identity-service only through a
signed HTTPS API. It must not connect directly to the VPS identity database.

Persistent browser automation remains on the laptop by default because browser
sessions are comparatively resource intensive. The identity-service issues only
opaque, short-lived leases to those local browser workers.

Outbound email remains disabled by default. When approved for a worker or
operation, Stalwart submits the message to Resend over authenticated TLS on port
465 or 587. If Oracle Always Free capacity is unavailable, the same design can
be deployed on a small paid VPS without changing the service boundaries.

---

# 1. Tasks you must do manually

Codex cannot legally or reliably perform these actions for you. Complete them yourself or explicitly authorize them through tightly scoped provider access.

## A. Buy and secure `aiat.ca`

1. Purchase `aiat.ca` through Spaceship.
2. Register it using:

   * Your legal name.
   * Canadian citizen eligibility.
   * Accurate Canadian contact information.
3. Enable:

   * Auto-renew.
   * Registrar lock.
   * MFA.
   * Login notifications.
4. Save the following outside AIAT:

   * Purchase receipt.
   * CIRA registrant information.
   * Registrar recovery codes.
   * Spaceship recovery information.
5. Use an independent personal email as the registrar recovery address.

Do not use an `@aiat.ca` address as the only recovery method for `aiat.ca`.

---

## B. Create and secure the DNS account

Recommended: Cloudflare Free DNS.

You must:

1. Create or use your Cloudflare account.
2. Add `aiat.ca`.
3. Copy the nameservers Cloudflare assigns.
4. Replace the Spaceship nameservers with the Cloudflare nameservers.
5. Wait until Cloudflare reports the zone as active.
6. Enable Cloudflare-account MFA.
7. Create a restricted API token for automation.

The token should only permit:

```text
Zone: aiat.ca
Permissions:
- Zone DNS: Edit
- Zone: Read
```

Do not create or give Codex a global Cloudflare API key.

Store the token outside Git, ideally in your production secret manager.

---

## C. Create the Oracle mail-edge VPS and Resend relay

### C1. Create the Oracle Cloud account

Create one legitimate Oracle Cloud account using accurate identity and billing
information. Do not create multiple free accounts. Oracle Free Tier capacity can
be unavailable in a selected region, so choose a nearby region with available
Always Free compute.

Keep the Oracle account recovery email, MFA, and recovery codes outside
`aiat.ca`.

### C2. Provision the mail-edge VM

Provision an Oracle Free Tier / Always Free compute VM with:

* A reserved public IPv4 address.
* At least 1 OCPU and 2 GB RAM; 4 GB RAM is preferred for Stalwart,
  identity-service, identity Postgres, and backup jobs.
* Persistent block storage.
* A supported Linux distribution.
* Automatic security updates.
* A separate encrypted backup destination.

Create Oracle network-security and host-firewall rules for:

```text
Inbound:
- TCP 25   from the Internet for SMTP reception
- TCP 443  from the Internet for Stalwart JMAP/HTTPS and the identity API
- TCP 80   only when needed for certificate issuance or redirect
- TCP 22   only from your current administration IP, or use a VPN/bastion

Outbound:
- TCP 443  for APIs, updates, certificate services, and backups
- TCP 465 or 587 to smtp.resend.com for authenticated outbound relay
- DNS and NTP as required
```

Do not require outbound TCP 25. Oracle blocks direct outbound SMTP on TCP 25 by
default for newer tenancies, and this architecture intentionally routes external
mail through Resend instead.

Before continuing, test from an external network that the VM can accept a TCP
connection on port 25. Also test from the VM that `smtp.resend.com` is reachable
on the selected authenticated relay port.

### C3. Create and secure the Resend account

Create a Resend account and:

1. Enable MFA if offered.
2. Add and verify `agents.aiat.ca` as the sending domain.
3. Create a dedicated API key for the AIAT production relay.
4. Restrict or rotate the key according to the provider capabilities.
5. Save the key outside Git.
6. Add the exact SPF and DKIM DNS records supplied by Resend.
7. Keep the Resend account recovery path independent from `aiat.ca`.

Resend is used only for outbound relay. It does not replace Stalwart mailboxes,
inbound SMTP, JMAP, retention, mailbox ownership, or incoming replies.

### C4. Keep a paid-VPS fallback

If Oracle Free Tier capacity, account approval, inbound TCP 25, or reliable
operation is unavailable, deploy the same `mail-edge` Compose profile on a small
paid mail-capable VPS. No AIAT application redesign should be required.

---

## D. Configure forward DNS and Oracle reverse DNS

After reserving the Oracle public IPv4 address, first create:

```text
mail.aiat.ca.       A       <ORACLE_PUBLIC_IPV4>
agents.aiat.ca.     MX 10   mail.aiat.ca.
identity.aiat.ca.   A       <ORACLE_PUBLIC_IPV4>
```

Then open an Oracle support request for the public IP PTR record:

```text
<ORACLE_PUBLIC_IPV4> → mail.aiat.ca
```

Oracle requires the forward `A` record to exist before the PTR request. PTR is
still required for a correctly identified Internet mail host even though final
outbound delivery is performed by Resend.

Add the exact Resend SPF and DKIM records for `agents.aiat.ca`, then add a DMARC
record. Begin DMARC in monitoring mode until both inbound and outbound tests
pass, then strengthen the policy deliberately.

Codex can generate and verify the expected records, but you must perform
provider-owned support and account actions unless you grant a tightly scoped
deployment credential.

---

## E. Create production secrets

Generate strong independent secrets for:

```text
AIAT credentials encryption
Stalwart administrator
Stalwart automation API
Identity Postgres database
Identity-service authentication
Tool-service authentication
Service-to-service JWT signing
Laptop-to-VPS client authentication
Browser-session encryption
Backup encryption
Cloudflare restricted DNS token
Resend SMTP/API relay
```

Generate them with a cryptographically secure mechanism, for example:

```bash
openssl rand -base64 48
```

Store them in one of:

* Docker secrets.
* An encrypted `.env` managed outside Git.
* 1Password.
* Bitwarden.
* HashiCorp Vault.
* A cloud secret manager.

Never paste production secrets into a Codex prompt.

---

## F. Decide external-account policies

You must decide which services AIAT agents are permitted to join.

Create an initial policy such as:

| Service category                | Default          |
| ------------------------------- | ---------------- |
| Development test services       | Allowed          |
| GitHub organization accounts    | Human approval   |
| Google accounts                 | Human approval   |
| Microsoft accounts              | Human approval   |
| Social-media accounts           | Denied initially |
| Financial services              | Denied           |
| Payment services                | Denied           |
| Cloud-provider root accounts    | Denied           |
| Services prohibiting automation | Denied           |

Codex can implement the policy engine, but it cannot make the legal and organizational decision for you.

---

## G. Decide hiring and mailbox rules

You must approve the initial business rules:

```yaml
mailbox_policy:
  create_during_hiring: true
  mailbox_domain: agents.aiat.ca
  mailbox_quota_mb: 100
  inbound_enabled: true
  outbound_enabled: false
  outbound_relay_provider: resend
  direct_mx_outbound_enabled: false
  direct_imap_access: false
  direct_smtp_access: false
  human_approval_for_outbound: true
  archive_after_retirement_days: 180
```

Also decide:

* Whether every worker requires a mailbox.
* Whether temporary workers get mailboxes.
* Mail retention period.
* Maximum mailbox storage.
* Whether deleted agents keep forwarding or recovery access.
* Which executives may send email.
* Who approves new external-service accounts.

---

## H. Provide deployment access

Codex needs access to the development environment, but you should control production access.

You may provide:

* Repository write access.
* WSL and Docker access.
* Local database access.
* Permission to create migrations.
* Permission to rebuild local images.
* Permission to run all tests.
* Permission to edit Compose and infrastructure files.
* SSH access to a dedicated staging host.

For production, prefer:

```text
Codex prepares deployment
You review the diff and migration
Codex deploys using a limited deployment account
You approve final activation
```

Do not give it:

* Your registrar master password.
* Your personal-email password.
* Your registrar MFA recovery codes.
* Your Cloudflare global API key.
* Unrestricted personal cloud credentials.
* Payment-card access.

---

## I. Perform final external verification

After Codex deploys the system, you should personally verify:

1. `aiat.ca` opens correctly.
2. `mail.aiat.ca` and `identity.aiat.ca` present valid TLS certificates.
3. An external host can reach Oracle inbound SMTP on TCP 25.
4. A message from Gmail reaches an agent mailbox.
5. A message from Outlook reaches an agent mailbox.
6. An approved agent message is queued by Stalwart and delivered through
   Resend over TCP 465 or 587.
7. Direct outbound MX delivery over TCP 25 is disabled.
8. Replies to a relayed agent message return to the Stalwart mailbox.
9. SPF passes for Resend-delivered messages.
10. DKIM passes for Resend-delivered messages.
11. DMARC alignment passes.
12. Unknown addresses are rejected.
13. Mail continues arriving while the operator laptop is shut down.
14. When the laptop reconnects, main AIAT can reconcile identity events and
    read only authorized mailbox data through the signed identity API.
15. The registrar recovery path works without `aiat.ca`.
16. You can recover the Cloudflare, Oracle, and Resend accounts independently.

---

# 2. Information to prepare for Codex

Give Codex configuration values through environment variables or a secrets file that is excluded from Git.

Prepare these non-secret values:

```text
DEPLOYMENT_TOPOLOGY=oracle_vps_stalwart_resend
PRIMARY_DOMAIN=aiat.ca
AGENT_MAIL_DOMAIN=agents.aiat.ca
MAIL_HOSTNAME=mail.aiat.ca
IDENTITY_HOSTNAME=identity.aiat.ca
PUBLIC_MAIL_IP=<oracle-reserved-public-ipv4>
PUBLIC_IDENTITY_URL=https://identity.aiat.ca
PUBLIC_APP_URL=https://app.aiat.ca
PUBLIC_API_URL=https://api.aiat.ca
STALWART_PROVIDER=stalwart
STALWART_PUBLIC_URL=https://mail.aiat.ca
IDENTITY_DATABASE_LOCATION=oracle_vps
MAIN_AIAT_LOCATION=operator_laptop
BROWSER_RUNTIME_LOCATION=operator_laptop
OUTBOUND_RELAY_PROVIDER=resend
OUTBOUND_RELAY_HOST=smtp.resend.com
OUTBOUND_RELAY_PORT=465
OUTBOUND_RELAY_TLS_MODE=implicit
DIRECT_MX_OUTBOUND_ENABLED=false
DEFAULT_MAILBOX_QUOTA_MB=100
DEFAULT_MAIL_RETENTION_DAYS=180
DEFAULT_OUTBOUND_ENABLED=false
```

Prepare these secrets separately:

```text
CREDENTIALS_ENCRYPTION_KEY
STALWART_ADMIN_SECRET
STALWART_API_KEY
IDENTITY_SERVICE_SECRET
IDENTITY_DATABASE_PASSWORD
SERVICE_JWT_PRIVATE_KEY
AIAT_IDENTITY_CLIENT_PRIVATE_KEY
CLOUDFLARE_DNS_TOKEN
RESEND_API_KEY
BACKUP_ENCRYPTION_KEY
```

Codex should see only the variable names in the repository. Real values should be injected at runtime.

---

# 3. Tasks to give Codex

Give Codex full permission to modify the AIAT repository, create migrations, build containers, run tests, and deploy to a dedicated staging environment.

“Full permission” should mean:

```text
Full repository and development-environment permission
Not unrestricted access to personal, registrar, billing, or recovery accounts
```

## Codex work phases

### Phase 1 — inspect and plan

Codex should:

1. Inspect the current hiring lifecycle.
2. Inspect worker activation and deactivation.
3. Inspect the credentials manager.
4. Inspect privileged-operation policy.
5. Inspect tool-service caller authentication.
6. Inspect worker grants and audit persistence.
7. Inspect the existing budget/credits system.
8. Inspect Docker Compose and deployment architecture.
9. Produce an implementation map before changing code.

---

### Phase 2 — harden AIAT foundations

Codex should fix:

* Production credential encryption must fail closed.
* Secret approval and rate limits must be enforced.
* Unknown privileged operations must deny.
* Caller identity must be cryptographically verified.
* Tool grants must persist in Postgres.
* Identity operations must have durable auditing.
* Raw credential export must be prohibited.
* Secrets must never enter model prompts or logs.

---

### Phase 3 — build the identity service

Codex should create:

```text
mas/apps/identity-service/
```

The service is deployed on the Oracle VPS and is the authority for mailbox
identity state. Responsibilities:

* Email-domain management.
* Mailbox provisioning.
* Mailbox suspension and retirement.
* Stalwart adapter.
* Resend relay adapter.
* Mail reading through JMAP.
* Verification-code extraction.
* Governed outbound-message approval and submission.
* External-account ownership.
* Credential leases.
* Remote browser-session brokering.
* Approval workflows.
* Usage and credits accounting.
* Audit persistence.
* Durable event outbox and laptop reconnection reconciliation.
* Signed HTTPS API for the main AIAT application.

The main AIAT laptop must never connect directly to the identity database.
Browser execution remains local by default; the VPS service stores only opaque
session metadata and issues short-lived, scoped leases.

---

### Phase 4 — database migrations

Codex should create durable tables for:

```text
email_domains
agent_email_identities
email_aliases
mailbox_provisioning_jobs
mail_events
mail_verification_transactions
outbound_mail_requests
outbound_delivery_attempts
identity_event_outbox
identity_client_registrations
external_accounts
credential_leases
browser_auth_sessions
identity_approval_requests
identity_usage_events
identity_audit_events
identity_provider_rates
identity_budget_holds
```

It must include:

* Unique constraints.
* Foreign keys.
* Idempotency keys.
* State transition history.
* Immutable worker-to-mailbox ownership.
* No plaintext secret columns.

---

### Phase 5 — Stalwart and Resend integration

Codex should implement Stalwart mailbox and queue operations:

```text
create_domain
verify_domain
create_mailbox
get_mailbox
disable_mailbox
enable_mailbox
rotate_mailbox_password
set_quota
add_alias
remove_alias
archive_mailbox
delete_mailbox
list_messages
read_message
search_messages
wait_for_message
extract_verification_code
extract_verification_link
submit_outbound_message
get_outbound_queue_status
cancel_queued_message
health_check
```

Codex should also implement a Resend relay adapter and deployment validation:

```text
validate_relay_credentials
validate_sending_domain
test_relay_connection
record_provider_message_id
record_delivery_event
classify_transient_or_permanent_failure
health_check
```

Stalwart remains the message queue and mailbox authority. Resend is only the
external outbound transport. The worker-facing system must never call Resend
directly.

All requests must have:

* Timeouts.
* Retries only where safe.
* Idempotency.
* Sanitized logs.
* Structured errors.
* Audit events.
* Provider request correlation IDs.

---

### Phase 6 — hiring integration

Codex should modify hiring so that:

```text
Worker approved
→ mailbox pending
→ mailbox created
→ delivery verified
→ tool grants created
→ browser identity created
→ worker activated
```

Failure must produce:

```text
IDENTITY_PROVISIONING_FAILED
```

and block activation.

Repeated hiring or event delivery must never create duplicate mailboxes.

---

### Phase 7 — mailbox tools

Codex should add governed tools:

```text
identity.email.get_address
mail.list
mail.search
mail.read
mail.wait_for_verification
mail.extract_code
mail.extract_link
mail.mark_processed
mail.delete
mail.send_request
mail.send_approved
mail.get_delivery_status
mail.cancel_queued
```

No tool may expose:

```text
mailbox password
Stalwart API key
TOTP seed
service password
session cookies
OAuth refresh token
recovery code
```

Worker A must never be able to request Worker B’s mailbox.

Every send operation must enforce sender ownership, allowed recipient class,
human approval when required, rate limits, credits, content-size limits, and a
durable idempotency key. Workers must never receive the Resend API key or raw
SMTP credentials.

---

### Phase 8 — external-account broker

Codex should add:

```text
identity.external.signup_request
identity.external.login
identity.external.get_status
identity.external.rotate_credentials
identity.external.suspend
identity.external.close
identity.session.create
identity.session.use
identity.session.revoke
```

Each external account must be bound to:

```text
worker
service
email identity
credential reference
browser profile
approval
audit trail
```

---

### Phase 9 — isolated browser sessions

Codex should create one browser context per:

```text
worker + external service
```

It must enforce:

* Separate cookies.
* Separate local storage.
* Separate cache.
* Separate OAuth state.
* Separate downloads.
* Separate credentials.
* No arbitrary session export.
* No cross-worker browser reuse.
* Cleanup and revocation.

---

### Phase 10 — credits integration

Codex should implement durable identity credits for:

```text
mailbox provisioning
storage
outbound messages
browser minutes
signup attempts
SMS MFA when introduced
provider API usage
```

Use:

```text
reserve
commit
release
```

A failed provisioning attempt must release its unused hold.

---

### Phase 11 — Oracle mail-edge deployment

Codex should create a dedicated Compose profile or deployment bundle for the
Oracle VPS containing:

```text
mail-edge
├── Stalwart
├── identity-service
├── identity-postgres
├── ingress/TLS component, if not already provided
└── encrypted backup job
```

It must include:

* Pinned Stalwart image version and digest.
* Pinned identity-service and database images.
* Persistent volumes.
* Health and readiness checks.
* Resource limits suitable for a small Oracle VM.
* Read-only filesystems where possible.
* Non-root execution where supported.
* Private administration networking.
* Public inbound SMTP on TCP 25.
* Public HTTPS/JMAP and identity API on TCP 443.
* Outbound authenticated Resend relay on TCP 465 or 587.
* No direct outbound MX delivery on TCP 25.
* No public Stalwart administration endpoint.
* No public Postgres endpoint.
* Signed laptop-to-VPS API authentication.
* Durable event outbox for periods when the laptop is offline.
* Backup and restoration scripts.
* Secret injection.
* Oracle firewall and host-firewall verification scripts.
* Cloudflare, PTR, Resend SPF/DKIM, and DMARC verification scripts.
* A paid-VPS-compatible deployment path using the same profile.

---

### Phase 12 — dashboard

Codex should add:

```text
/identities
/mail-domains
/mailboxes
/outbound-mail
/mail-relay
/external-accounts
/auth-sessions
/identity-approvals
/identity-audit
```

The UI must support:

* Mailbox state.
* Agent ownership.
* Aliases.
* Quota.
* Outbound approval state.
* Stalwart queue state.
* Resend relay health and provider message IDs.
* Delivery attempts and sanitized failure reasons.
* Connected external accounts.
* Active browser sessions.
* Approvals.
* Suspension.
* Credential rotation.
* Audit review.
* No secret display.

---

### Phase 13 — testing

Codex should add:

* Unit tests.
* Integration tests.
* Migration tests.
* Policy tests.
* Stalwart adapter tests.
* Resend relay adapter and SMTP-route tests.
* Live Stalwart inbound tests.
* Live Resend outbound tests.
* Hiring tests.
* Idempotency tests.
* Cross-worker isolation tests.
* Browser isolation tests.
* Credential leak tests.
* Laptop-disconnected and reconnection tests.
* Event-outbox replay tests.
* Direct-MX-outbound-disabled tests.
* Backup/restore tests.
* Failure and rollback tests.
* Dashboard type checks.
* Local and mail-edge Compose validation.
* Security regression tests.

---

# 4. Exact prompt to give Codex

Copy this prompt after completing the manual infrastructure steps that can be done in advance.

```text
You are working in the AIAT repository at C:\projects\AIAT and
/mnt/c/projects/AIAT.

You have full permission to inspect and modify the repository, create
database migrations, add services, modify Docker Compose, run all tests,
build images, and deploy to the dedicated AIAT staging environment.

You do not have permission to purchase services, accept contracts, change
billing, access personal recovery accounts, weaken security controls, expose
secrets, or activate production without the final operator approval.

OBJECTIVE

Implement the complete AIAT email identity and external-account system using:

- Root domain: aiat.ca
- Agent email domain: agents.aiat.ca
- Mail hostname: mail.aiat.ca
- Identity hostname: identity.aiat.ca
- Mail-edge host: Oracle Cloud Infrastructure Free Tier / Always Free VM
- Mail server: Stalwart Community Edition on the Oracle VPS
- Outbound relay: Resend authenticated SMTP on port 465 or 587
- Direct outbound MX delivery: disabled
- Identity-service: deployed on the Oracle VPS
- Identity database: dedicated Postgres on the Oracle VPS
- Main AIAT application: remains on the operator laptop initially
- Browser-session runtime: remains on the operator laptop initially
- Identity architecture: one real mailbox per qualifying AIAT worker
- Access model: AIAT-governed signed HTTPS/JMAP and identity tools
- Default outbound email: disabled
- External account isolation: one browser profile per worker and service
- Secret model: opaque references and short-lived leases only

The result must automatically provision, verify, govern, suspend, archive,
and audit a real Stalwart mailbox as part of the worker hiring lifecycle.
Inbound mail and identity state must remain available while the operator laptop
is offline. Approved outbound messages must be queued by Stalwart and relayed
through Resend without using Oracle outbound TCP 25.

NON-NEGOTIABLE RULES

1. Never commit or print production secrets.
2. Never place plaintext credentials, cookies, TOTP seeds, recovery codes,
   API keys, or OAuth refresh tokens in normal database fields.
3. Never return raw secrets to a worker or language model.
4. Production startup must fail when required encryption or authentication
   keys are missing.
5. Unknown privileged actions must deny by default.
6. Caller identity must be cryptographically authenticated and cannot be
   accepted from an untrusted caller_id or role field.
7. Identity and mailbox grants must be durable in Postgres.
8. Worker A must never read, search, authenticate to, or manipulate Worker
   B's mailbox, external accounts, credentials, or browser sessions.
9. Worker activation must remain blocked until required mailbox provisioning
   and delivery verification succeed.
10. Provisioning must be idempotent.
11. Outbound email must remain disabled by default.
12. Direct outbound MX delivery over TCP 25 must remain disabled.
13. Approved outbound email must use the configured Resend relay over TLS.
14. Stalwart administration and identity Postgres must never be exposed publicly.
15. The main AIAT application must access identity state only through a signed
    API and must never connect directly to identity Postgres.
16. Browser execution remains local by default; the VPS may store only opaque
    browser-session metadata and scoped leases.
17. The implementation must not bypass the terms or anti-abuse controls of
    external services.
18. Production activation requires operator approval after live evidence.

PHASE 1: REPOSITORY INSPECTION

Inspect:

- Worker hiring, activation, suspension, retirement, and rollout flows.
- Credentials manager and models.
- Privileged-operation policy.
- Tool-service authentication and grant enforcement.
- Durable audit and usage infrastructure.
- Budget and credits logic.
- Browser and automation runtimes.
- API versioning.
- Dashboard conventions.
- Database migrations.
- Compose and deployment structure.

Produce an implementation map before making broad changes.

PHASE 2: FOUNDATION HARDENING

Implement:

- Production fail-closed credentials encryption.
- Real enforcement of approval and secret rate-limit policy.
- Default deny for unknown privileged actions.
- Signed service and worker identity.
- Durable tool and identity grants.
- Durable identity audit records.
- Secret-response redaction.
- Prohibition of raw secret and cookie exports.

Do not weaken existing worker and rollout governance.

PHASE 3: IDENTITY SERVICE

Create:

mas/apps/identity-service/
  identity_service/
    providers/stalwart.py
    providers/resend.py
    domains/service.py
    mailboxes/service.py
    messages/jmap_client.py
    messages/verification_parser.py
    outbound/service.py
    outbound/policy.py
    sync/outbox.py
    clients/auth.py
    external_accounts/service.py
    credentials/leases.py
    sessions/browser_sessions.py
    approvals/service.py
    usage/ledger.py
    routes.py
    main.py

Expose versioned signed HTTPS APIs and health endpoints. Deploy this service
and its dedicated Postgres database on the Oracle VPS. The main AIAT laptop
must never connect directly to the identity database. Implement durable outbox
and cursor-based reconciliation so the laptop can disconnect and later catch up
without duplicate state transitions.

PHASE 4: DATABASE

Create migrations and models for:

- email_domains
- agent_email_identities
- email_aliases
- mailbox_provisioning_jobs
- mail_events
- mail_verification_transactions
- outbound_mail_requests
- outbound_delivery_attempts
- identity_event_outbox
- identity_client_registrations
- external_accounts
- credential_leases
- browser_auth_sessions
- identity_approval_requests
- identity_usage_events
- identity_audit_events
- identity_provider_rates
- identity_budget_holds

Add all necessary unique constraints, foreign keys, indexes, idempotency keys,
ownership fields, state transition evidence, audit metadata, provider message
IDs, event sequence numbers, and per-client reconciliation cursors.

Do not store raw secrets in these tables. Identity Postgres must be private to
the Oracle mail-edge network.

PHASE 5: STALWART AND RESEND ADAPTERS

Implement and test Stalwart operations:

- health_check
- create_domain
- verify_domain
- create_mailbox
- get_mailbox
- enable_mailbox
- disable_mailbox
- rotate_mailbox_password
- set_mailbox_quota
- add_alias
- remove_alias
- archive_mailbox
- delete_mailbox
- list_messages
- search_messages
- read_message
- wait_for_message
- extract_verification_code
- extract_verification_link
- submit_outbound_message
- get_outbound_queue_status
- cancel_queued_message

Implement and test Resend relay operations:

- validate_relay_credentials
- validate_sending_domain
- test_relay_connection
- record_provider_message_id
- record_delivery_event
- classify_transient_or_permanent_failure
- health_check

Stalwart must queue all approved outbound messages and route external
recipients through Resend. Workers and the main AIAT application must never
call Resend directly. Direct MX delivery over outbound TCP 25 must be disabled.

Use timeouts, safe retries, idempotency, structured errors, correlation IDs,
provider message IDs, delivery-attempt persistence, and sanitized logs.

PHASE 6: HIRING LIFECYCLE

Integrate this state flow:

worker approved
→ HIRED_PENDING_IDENTITY
→ IDENTITY_PROVISIONING
→ IDENTITY_VERIFYING
→ IDENTITY_ACTIVE
→ worker ACTIVE

Any failed mandatory step must result in:

IDENTITY_PROVISIONING_FAILED

and keep the worker inactive.

Use a stable provisioning key:

mailbox:<company_id>:<worker_id>

A retry must return the existing mailbox rather than create another mailbox.

Primary address format:

w-<stable-worker-id>@agents.aiat.ca

Allow an optional friendly role alias.

PHASE 7: GOVERNED MAIL TOOLS

Add:

- identity.email.get_address
- mail.list
- mail.search
- mail.read
- mail.wait_for_verification
- mail.extract_code
- mail.extract_link
- mail.mark_processed
- mail.delete
- mail.send_request
- mail.send_approved
- mail.get_delivery_status
- mail.cancel_queued

Enforce worker ownership, project/run context, audit, quota, rate limit,
purpose restrictions, sender authorization, recipient policy, approval state,
credits, and durable idempotency.

`mail.send_request` creates an approval-governed request. `mail.send_approved`
may execute only after the required approval exists and must submit through
Stalwart's queue. Never expose mailbox passwords, Stalwart administrator
credentials, Resend API keys, or raw SMTP credentials.

PHASE 8: EXTERNAL ACCOUNTS

Add:

- identity.external.signup_request
- identity.external.login
- identity.external.get_status
- identity.external.rotate_credentials
- identity.external.suspend
- identity.external.close
- identity.session.create
- identity.session.use
- identity.session.revoke

Bind every external account to:

- worker
- email identity
- provider/service
- credential reference
- browser profile
- approval
- audit trail

PHASE 9: BROWSER ISOLATION

Create one persistent browser context per worker and external service.

Separate:

- cookies
- local storage
- cache
- OAuth state
- downloads
- credentials
- session lifecycle

Run persistent browser contexts on the operator laptop by default. The Oracle
VPS identity-service may store only opaque session identifiers, policy state,
and short-lived scoped leases.

Use opaque session handles. Do not expose or export cookies to workers.

PHASE 10: CREDITS

Implement durable reserve/commit/release accounting for:

- mailbox provisioning
- storage
- outbound email
- browser usage
- signup attempts
- optional MFA provider usage

Failed operations must release unused holds.

PHASE 11: DEPLOYMENT

Create a dedicated Oracle VPS `mail-edge` deployment profile containing:

- pinned Stalwart container version and image digest
- identity-service
- dedicated identity Postgres
- ingress/TLS component if required by existing AIAT conventions
- encrypted backup job

Configure:

- persistent storage
- health and readiness checks
- backup and restore scripts
- internal administration network
- secret injection
- public inbound SMTP port 25
- public HTTPS/JMAP and identity API port 443
- outbound Resend SMTP relay on port 465 or 587
- direct outbound MX delivery disabled
- no public Stalwart administration endpoint
- no public Postgres endpoint
- signed laptop-to-VPS API authentication
- event outbox and reconnection reconciliation
- resource limits for a small Oracle VM
- restart behavior
- startup validation
- Oracle network and host-firewall verification
- Cloudflare DNS, Oracle PTR, Resend SPF/DKIM, and DMARC verification tooling
- the same deployable profile for a paid-VPS fallback

Keep the main AIAT application and browser-session runtime on the operator
laptop. Do not install a second local Stalwart server.

Use environment variables:

DEPLOYMENT_TOPOLOGY=oracle_vps_stalwart_resend
PRIMARY_DOMAIN=aiat.ca
AGENT_MAIL_DOMAIN=agents.aiat.ca
MAIL_HOSTNAME=mail.aiat.ca
IDENTITY_HOSTNAME=identity.aiat.ca
PUBLIC_IDENTITY_URL=https://identity.aiat.ca
STALWART_PUBLIC_URL=https://mail.aiat.ca
OUTBOUND_RELAY_PROVIDER=resend
OUTBOUND_RELAY_HOST=smtp.resend.com
OUTBOUND_RELAY_PORT=465
OUTBOUND_RELAY_TLS_MODE=implicit
DIRECT_MX_OUTBOUND_ENABLED=false
DEFAULT_MAILBOX_QUOTA_MB=100
DEFAULT_MAIL_RETENTION_DAYS=180
DEFAULT_OUTBOUND_ENABLED=false

Required secret variables include:

RESEND_API_KEY
IDENTITY_DATABASE_PASSWORD
AIAT_IDENTITY_CLIENT_PRIVATE_KEY

Do not commit real values for secret variables.

PHASE 12: DASHBOARD

Add:

- /identities
- /mail-domains
- /mailboxes
- /outbound-mail
- /mail-relay
- /external-accounts
- /auth-sessions
- /identity-approvals
- /identity-audit

Support secure lifecycle actions, outbound approvals, Stalwart queue status,
Resend relay health, provider message IDs, and sanitized delivery failures
without displaying secret values.

PHASE 13: TESTING

Add and run:

- unit tests
- integration tests
- migration tests
- Stalwart adapter tests
- Resend relay adapter tests
- live Stalwart inbound tests
- live Resend outbound tests
- worker hiring tests
- idempotency tests
- policy tests
- cross-worker mailbox isolation tests
- browser isolation tests
- credential-leak tests
- laptop-offline availability tests
- event-outbox reconciliation tests
- direct-MX-outbound-disabled tests
- suspension and retirement tests
- backup and restoration tests
- local Compose and mail-edge Compose validation
- dashboard TypeScript checks
- Ruff and Python compilation
- the complete existing AIAT test suites

The following live acceptance tests are mandatory:

1. Create two workers and two independent Stalwart mailboxes.
2. Deliver different inbound messages to both mailboxes over Oracle TCP 25.
3. Prove each worker can access only its own mailbox.
4. Prove an unknown recipient is rejected.
5. Replay the hiring event and prove no duplicate mailbox is created.
6. Simulate provisioning failure and prove worker activation stays blocked.
7. Receive and parse a real external verification email.
8. Approve outbound for one worker, send through Stalwart, and prove Resend
   performs the external delivery over the configured relay path.
9. Prove direct outbound MX delivery on TCP 25 is disabled.
10. Reply to a Resend-delivered agent message and prove the reply reaches the
    corresponding Stalwart mailbox.
11. Shut down the operator laptop and prove inbound SMTP, mailbox storage,
    identity-service, and audit persistence remain available on the Oracle VPS.
12. Restart the laptop and prove signed event reconciliation is complete and
    idempotent.
13. Prove two permitted external accounts have separate credentials, MFA state,
    cookies, and local browser contexts.
14. Suspend one worker and prove mailbox and browser access are revoked.
15. Prove no secret appears in API responses, logs, events, traces, or model
    context.
16. Prove the laptop cannot connect directly to identity Postgres.
17. Restore mailbox and identity data from backup and prove mailbox ownership
    remains isolated.
18. Verify forward DNS, Oracle PTR, TLS, SPF, DKIM, and DMARC, and report any
    operator-owned step that remains.

OPERATOR BOUNDARY

Stop and report rather than faking completion when any of the following is
missing:

- Real production or staging DNS token
- Oracle account or available Free Tier capacity
- Reserved Oracle public IPv4
- Inbound TCP 25 connectivity to the Oracle VM
- Outbound TCP 465 or 587 connectivity to Resend
- PTR/reverse DNS support request
- Stalwart production secrets
- Resend account, verified domain, and API key
- Identity Postgres and service-authentication secrets
- Approved staging deployment access
- Required human approval for an external service
- Provider agreement or billing action

FINAL RESPONSE

Return:

1. FINAL STATUS: COMPLETE, STAGING_COMPLETE, or BLOCKED.
2. Architecture implemented.
3. All migrations and current migration head.
4. Every modified and added file.
5. Test totals.
6. Live inbound Stalwart and outbound Resend delivery evidence.
7. Evidence that direct outbound MX delivery is disabled.
8. Cross-worker isolation evidence.
9. Hiring idempotency evidence.
10. Credential and local-browser isolation evidence.
11. Laptop-offline availability and reconnection evidence.
12. Oracle network, PTR, DNS, SPF, DKIM, DMARC, TLS, SMTP, JMAP, and Resend
    relay status.
13. Remaining manual operator actions.
14. Every secret variable that must be supplied, without secret values.
15. Exact local, Oracle staging, and Oracle production deployment commands.
16. Rollback and restoration commands.
17. Any unresolved security or operational risk.

Do not describe the system as complete when any mandatory live test or
operator-owned infrastructure requirement remains unverified.
```

## Recommended execution boundary

Give Codex broad authority over:

```text
Git repository
WSL
Docker
Test databases
Migrations
Local containers
Mail-edge Compose profile
Limited SSH deployment account on the Oracle staging VM
Staging deployment
Test mailbox creation
Resend sandbox or test relay configuration after you inject the secret
Automated test accounts on explicitly approved services
```

Keep these under your direct control:

```text
Registrar ownership
CIRA identity
Billing
Recovery email
Registrar MFA
Cloudflare master account
Oracle master account, MFA, billing, and recovery
Oracle PTR support request approval
Resend master account, billing, MFA, and recovery
Production Resend API-key creation and injection
Production activation
External-service legal approvals
```

That boundary lets Codex complete virtually all technical work without placing ownership of AIAT’s domain and recovery chain inside an autonomous coding session.
