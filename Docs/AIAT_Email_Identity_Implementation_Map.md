# AIAT Email Identity: Repository Implementation Map

> Current topology notice (2026-09-12): the default production topology is
> Cloudflare Email Routing -> Email Worker/D1-only -> signed AIAT sync for
> inbound mail, with direct Resend API calls for outbound mail. R2 is an
> explicitly selected optional raw-message backend, and Stalwart remains an
> explicitly selected optional full-mailbox provider. Use
> `Docs/AIAT_Email_Identity_Provider_Architecture.md` and
> `Docs/AIAT_Email_Identity_Domain_Migration.md` for the active boundary and
> operator runbook.

## Scope and boundary

AIAT owns the control plane, identity API, lifecycle state, policy decisions,
audits, client signing, laptop reconciliation, local browser isolation, and
mail-edge deployment definitions. Cloudflare and Resend are untrusted
provider boundaries called through AIAT adapters. Stalwart remains an
external pinned optional full-mailbox container; it is never vendored. Neither
workers nor the laptop call a provider directly, and the laptop never connects
to the identity Postgres database.

Live Oracle, DNS, PTR, TLS, Stalwart, and Resend certification are explicitly
outside the repository boundary until operator-owned infrastructure and secrets
are available.

## Current architecture findings

| Requirement area | Existing anchor | Repository change |
| --- | --- | --- |
| Worker lifecycle | `apps/orchestrator-api/orchestrator_api/main.py` registers workers and gates `ACTIVE` status through `_worker_activation_blockers` | Add identity lifecycle state and make activation additionally require a verified, active required mailbox. Suspension/retirement revoke identity and browser access. |
| Governance and audit | `mas_core.policy.privileged_ops`, `mas_core.memory.storage` | Fail closed for unknown privileged actions; add durable identity audit, approval, state-transition, and outbox records in the dedicated identity DB. |
| Credentials | `mas_core.credentials.manager` | Preserve encrypted core credential storage; prohibit identity APIs and tools from returning encrypted values, credentials, cookies, TOTP material, or recovery material. |
| Tool boundary | `apps/tool-service` and `mas-tools-sdk` | Add governed identity/mail/external-account tools backed only by a signed identity-service client. Enforce caller identity and worker ownership at both gateway and identity-service layers. |
| Browser runtime | `apps/tool-service/tool_service/tools/browser.py` | Replace reusable anonymous contexts for governed external accounts with persistent local profiles keyed by `(worker_id, service)`, opaque session handles, and explicit revocation. |
| Credits and usage | `mas_core.memory` project usage events | Implement a dedicated reserve/commit/release identity ledger and usage events, so external identity operations are durable when the laptop is offline. |
| Dashboard | `apps/mas-dashboard` Next.js app and orchestrator proxy routes | Add identity pages and secure server-side proxy routes. All views are metadata-only and redact secret-like fields. |
| Deployment | `mas/infra/cloudflare`, `mas/infra/mail-edge`, `mas/infra/compose` | Make the Cloudflare Worker/D1-only + identity-service Compose bundle the default; retain the optional Worker R2 profile and Stalwart mail-edge/loopback profiles as explicit alternatives. |

## Delivery sequence

1. Harden fail-closed production settings and default-deny privileged handling.
2. Add `apps/identity-service`: signed API, dedicated schema/migration,
   provider-neutral inbound/outbound contracts, Cloudflare edge adapter, direct
   Resend API adapter, optional Stalwart adapter, outbox, usage, approvals,
   external accounts, and leases.
3. Add the signed laptop client and tool-service tools; integrate lifecycle
   provisioning and revocation into worker hiring/status transitions.
4. Add the dashboard metadata surfaces and mail-edge Compose bundle, scripts,
   backup/restore procedures, and environment templates.
5. Add policy, adapter, lifecycle, isolation, leak, outbox/reconciliation,
   migration, backup/restore, and Compose validation tests. Live-network tests
   remain marked and reported as unexecuted until operator infrastructure exists.

## Security invariants carried into implementation

- Production uses no generated/default encryption, API, or client-signing key.
- Unknown privileged operations, external-service categories, and outbound
  recipient classes deny by default.
- Worker ownership is immutable per mailbox, external account, browser session,
  credential lease, and audit/outbox event.
- A stable `mailbox:<company_id>:<worker_id>` idempotency key prevents duplicate
  provisioning.
- Outbound starts disabled and can submit only after a durable human approval,
  credit hold, quota/rate checks, and sender ownership verification.
- Direct IMAP/SMTP client access and credential/cookie export are prohibited.
- Direct MX delivery is disabled in deployment configuration and host validation;
  the default inbound path has no public SMTP listener.
- Cloudflare edge access uses a signed, replay-protected HMAC protocol. The
  default D1 path stores registry/metadata plus ordered, checksummed raw-MIME
  chunks; raw MIME is absent from ordinary event rows. The optional R2 path
  remains available, and local identity-service copies are encrypted.

## Implemented repository architecture

- `apps/identity-service` is the AIAT-side authority. It has a dedicated
  Postgres migration, fail-closed production settings, signed Ed25519 requests,
  durable client registration/revocation, replay protection, worker ownership
  grants, lifecycle transitions, audit,
  approvals, encrypted outbound content, provider-rate counters, usage holds,
  provisioning jobs, mail/verification events, delivery attempts, and a
  sequence outbox with durable per-client acknowledgements.
- External-account approvals are created before their account rows, the
  approval target is reused as the account primary key, and
  `external_accounts.approval_id` is database-enforced `NOT NULL`. This removes
  the crash window in which an account could exist without an approval record;
  idempotent retries preserve the original account/approval pair.
- Durable signed-client registrations are checked at startup for the exact
  configured public key, active state, and scope set. Orchestrator and
  tool-service identity clients reject credential-bearing or non-origin URLs
  and require HTTPS in staging and production.
- The default inbound adapter uses the signed Cloudflare mail-edge API:
  envelope-recipient registration, lifecycle, event pull, raw-message fetch,
  acknowledgement, processed/delete mutation, and temporary retention
  protection. Synchronization commits local encrypted state before edge ack and
  advances a durable provider cursor only after that commit.
- The default outbound adapter calls Resend's HTTPS `/emails` API directly
  after the existing AIAT ownership, approval, usage, rate, and audit gates.
  Its idempotency key and opaque provider ID remain inside the identity-service
  delivery state. Workers never receive the Resend API key.
- Stalwart management calls use the private `/api` JMAP endpoint and a
  management-only API key. Mail calls use `/jmap`, the JMAP mail/submission
  capabilities, and a separate service bearer token. Outbound composition
  resolves the real Drafts/Sent mailboxes and sender Identity before creating
  and submitting an RFC-shaped message.
- Mailbox provisioning reconciles an exact existing provider account before
  creation, refuses to adopt credential-bearing or ambiguous accounts, and
  resumes stale durable provisioning jobs. This closes duplicate-mailbox
  windows after either a local-process crash or a provider-commit/local-commit
  split.
- The orchestrator provisions required identities during hiring, blocks worker
  activation until `IDENTITY_ACTIVE`, reconciles signed outbox events after
  laptop downtime, and archives identities during retirement. Temporary
  mailboxes require a durable human approval before provider mutation.
- Suspension and archival commit local revocation first. Browser sessions and
  linked external accounts are revoked even when a provider is unavailable, and
  the provider failure is audited for orchestrator retry.
- The tool service authenticates callers, persists tool grants, forwards only
  signed governed operations, and keeps persistent browser profiles local and
  isolated by `(worker_id, service)`. The Oracle service stores opaque session
  metadata and hashes of short-lived credential leases, never cookies or raw
  credentials.
- Browser requests and redirects fail closed on unsupported schemes, embedded
  credentials, internal hostnames, DNS failures, and every non-global IPv4 or
  IPv6 result. The production image runs as UID/GID 10001 with Chromium's
  sandbox enabled; Compose drops all capabilities, sets
  `no-new-privileges`, and mounts a dedicated persistent profile volume.
- The dashboard exposes metadata-only identity, domain, mailbox, outbound,
  relay, external-account, session, approval, and audit views. Its mutation
  surface is an explicit allowlist for approval decisions, lifecycle
  suspension/archive, credential-rotation requests, external-account state,
  and session revocation; arbitrary identity-service paths cannot be proxied.
- `infra/cloudflare` pins the default identity Postgres/Caddy runtime and
  contains the source-controlled Worker/D1-only deployment boundary plus an
  explicit optional Worker R2 profile. It does not
  pass a Cloudflare account token to AIAT. `infra/mail-edge` pins Stalwart,
  Postgres, and Caddy for the optional full-mailbox profile; it keeps Postgres and
  administration private; creates an environment-backed Resend relay; removes
  every pre-existing remote delivery route; validates that the sole saved
  remote strategy is authenticated Resend; checks Stalwart's real
  `/healthz/ready` endpoint; and provides encrypted logical backup/offline
  restoration workflows. Backup quiesces Stalwart and identity-service and
  restores precisely their prior running states. Restoration removes hidden
  stale files as well as ordinary files. The identity image runs non-root and
  installs against constraints exported from the repository lock.

## Repository certification evidence (2026-09-11)

- The provider-neutral identity suite passes (**47 passed, 2 skipped**),
  including Cloudflare envelope authorization, recipient-scoped deduplication,
  signed sync retry/cursor semantics, lifecycle isolation, D1 fixture retention,
  and direct Resend API approval/idempotency fixtures. The skips are live or
  opt-in database cases.
- The Cloudflare Worker package passes TypeScript typecheck and its local
  Vitest suite (**29 passed**); Wrangler applies the checked-in D1 migrations
  in the local emulator. The suite covers D1 chunking, binary reconstruction,
  checksum/retry/retention behavior, signed API security, and an explicit
  optional-R2 regression. No Cloudflare or Resend network/account mutation was
  performed.
- The payload-free provider conformance checker passes all mocked cases for
  Cloudflare, Resend, and the retained Stalwart adapter. Its `--live` mode
  remains explicitly blocked until operator-selected endpoints and evidence
  exist.

The older repository-wide counts below are historical evidence from the
preceding identity implementation and do not certify the current providers.

- The non-live identity-service suite passes (**29 passed, 1 skipped**), with the explicit
  Oracle live-certification test skipped until enabled, and
  `TEST_IDENTITY_DATABASE_DSN` pointed at a disposable, freshly migrated
  PostgreSQL 16 container. This includes provider adapters, signed request/body
  reuse, lifecycle policy, provider-crash reconciliation, local revocation
  during provider outages, isolation, outbox reconciliation, credits, and the
  real Postgres store integration.
- The complete tool-service suite passes (**185 passed**) with
  `TEST_CORE_DATABASE_DSN` enabled. It includes a real Postgres test for durable
  browser namespaces and signed-request nonce replay protection, production
  signed ASGI request validation, explicit worker grants, browser profile
  isolation, redirect/subresource filtering, and IPv4/IPv6 SSRF denial.
- The complete orchestrator suite passes (**1,148 passed, 15 skipped**). The
  complete mas-core suite passes (**715 passed, 6 skipped**); its optional
  credential-policy integration was also executed separately against the
  disposable core Postgres database and passed. The skips are existing
  opt-in/live-runtime cases, not identity failures.
- The mail-edge operational suite passes (**10 passed**) across backup
  stop/start permutations, relay strategy rejection, and secret-evidence
  fail-closed/redaction behavior.
- The remaining configured workspace suites (tools SDK, message router, and
  team runner) pass (**72 passed**), so every configured AIAT package suite was
  rerun after the implementation.
- An opt-in staging acceptance test now contains the exact executable path for
  two real mailboxes, SMTP/25 delivery, JMAP reads, unknown-recipient rejection,
  idempotency, signed cross-worker denial, human-approved Resend submission,
  reply routing, and suspension. It is intentionally unexecuted without the
  operator-owned host and keys.
- The real Postgres integrations exercised provisioning jobs, durable grants,
  hashed verification records, mail events, encrypted outbound content,
  atomic submission claims, outbox sequences, monotonic client cursors, and
  provider-rate enforcement, as well as one-use credential approvals, resolve
  rate limits, browser identity lifecycle, and nonce replay denial.
- The identity image and backup image build through Compose using narrow build
  contexts. The identity image imports as its non-root runtime package.
- The orchestrator and tool-service production Dockerfiles also build from
  explicit clean contexts and both application modules import successfully.
  The hardened tool-service image was additionally run with every capability
  dropped and `no-new-privileges`; Chromium launched without `--no-sandbox` as
  UID/GID 10001. The resulting tool-service image measured **7.26 GB** because
  Docling resolves a CUDA
  PyTorch stack; this is a deployment-size risk, not an identity correctness
  failure, and should be reduced with a separately certified CPU dependency
  profile.
- Dashboard `tsc --noEmit`, Python compilation, focused Ruff undefined-name
  checks, shell syntax, `git diff --check`, live Alembic upgrade/downgrade,
  base/all-profile Compose rendering, and mail-edge policy validation pass.
- Fresh core migrations exposed and corrected Alembic's default
  `alembic_version.version_num` width before the first revision identifier over
  32 characters. A clean pgvector-enabled database now upgrades through `0023`,
  downgrades to `0019`, and upgrades to head again.
- A disposable Docker drill created an age-encrypted archive, restored it into
  a fresh PostgreSQL container, and recovered the exact worker ID, mailbox
  address, and `ACTIVE` lifecycle state. The drill also exposed and corrected
  an Alpine `tar -C` portability defect in the original backup script.
- The optional Stalwart-profile relay validator proves the configured remote route is authenticated
  TLS to `smtp.resend.com:465`, its secret comes from `RESEND_API_KEY`, and the
  policy contains no unapproved remote route. The configured health check uses
  Stalwart's readiness endpoint. Live firewall and Stalwart saved-state checks
  still require the VPS.
- Suffix-style secret files (`.env.identity`, `.env.mail-edge`, and local
  variants) are ignored repository-wide under `mas/`; their corresponding
  `.env.*.example` templates remain trackable. The evidence scanner compares
  configured secret values without printing them and reports variable names
  only on a detected leak.

## Current migration heads

- Identity Postgres: `0004_provider_neutral_mail` (independent provider
  bindings, encrypted inbound sync state/content, retention metadata, and
  direct-Resend content type; provider payloads remain identity-owned).
- Laptop/control-plane Postgres:
  `0023_durable_browser_identity_and_tool_nonces` (including durable credential
  approvals/rates in `0022` and tool grants in `0021`).

## Operator-owned live blockers

Repository completion is not production acceptance. The status remains
**BLOCKED** for live certification until the operator supplies the selected
Cloudflare/Resend resources and real secrets and executes the mandatory live
tests. Outstanding evidence:

- Cloudflare D1 resource migration, Worker deployment, exact Email Routing
  route,
  public identity TLS, and inbound delivery/retry/restart evidence.
- Resend account/API key, verified sending domain, direct API acceptance,
  authenticated webhook evidence, external delivery/reply evidence, and
  confirmation that every send remains approval-gated.
- Production Postgres migration, encrypted backup/restore, worker ownership
  isolation, suspension/retirement, and provider-outage reconciliation.
- If the optional Stalwart profile is selected instead, its separate JMAP,
  SMTP, DNS, firewall, backup, and saved-route evidence remains required.

Exact default deployment and provider-boundary commands are in
[`mas/infra/cloudflare/README.md`](../mas/infra/cloudflare/README.md) and
[`AIAT_Email_Identity_Domain_Migration.md`](AIAT_Email_Identity_Domain_Migration.md).
Optional Stalwart deployment, validation, backup, restore, and promotion
commands are in [`mas/infra/mail-edge/README.md`](../mas/infra/mail-edge/README.md).
No live credential value belongs in this document or in the repository.

The exhaustive implementation file inventory is in
[`AIAT_Email_Identity_Changed_Files.md`](AIAT_Email_Identity_Changed_Files.md).
