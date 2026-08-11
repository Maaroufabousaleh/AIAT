# Identity, Mail, Credentials, and External Accounts Feature Specification

**Baseline:** 2026-08-10
**Status:** governed identity/mail lifecycle group `f577675` implemented; safe delivery-attempt trace correlation is implemented; the dashboard credentials list now retains redacted metadata through refresh failures with explicit stale/retry recovery (`970f09c`, source-built `credentials-states.spec.ts` 1/1); shared identity-resource refreshes are abort/generation-safe and prove stale-to-recovered retry (`46eccee`, source-built `identity-states.spec.ts` 1/1); production domain and transport certification pending
**Authority:** [AIAT Target Programme](../../AIAT_TARGET_PROGRAMME.md)

## Purpose

This boundary gives workers stable identities and tightly controlled access to mailboxes, external accounts, browser sessions, and secrets. It prevents credentials from leaking into prompts, manifests, worker containers, logs, or model requests.

## Implemented now

- Dedicated identity-service application and independent Postgres migration.
- Signed client requests, replay storage, identity lifecycle, domains, mailbox addressing, Stalwart/JMAP adapter, and verification helpers.
- Mail list/search/read/process/delete, verification-code/link extraction, and bounded wait.
- Outbound request, approval, approved send, delivery status, cancellation, and Resend relay boundary.
- External-account signup/status/login/rotation/suspend/close lifecycle.
- Versioned external-account action policy (`aiat.external-account-action-policy.v1`) exposes category-sensitive signup, always-approved credential rotation, human-approved closure, immediate safety suspension, and governed local browser-session rules. Close requests now pause at a durable approval before state change and session revocation.
- The deterministic `aiat.external-account-action-policy-check.v1` fixture reconciles the real five-action catalogue, development/organization/provider category dispositions, and fail-closed unknown action/category behavior without creating identity, account, session, credential, or provider state. Licence/restriction metadata is explicitly outside this policy.
- The deterministic `aiat.external-account-lifecycle.v1` fixture drives the
  real `IdentityService` over `InMemoryIdentityStore`: category-sensitive
  approval and signup idempotency, one-use browser leases, credential-rotation
  session revocation, immediate suspension, closure approval/revocation, and
  unknown-category denial all pass without an external account or provider
  call. Its report is secret-safe and keeps licence metadata non-gating.
- The deterministic `aiat.outbound-mail-lifecycle.v1` fixture drives the real
  `IdentityService` outbound path through approval pause, request/submission
  idempotency, definitive provider-failure retry, ambiguous-outage
  reconciliation hold, and secret-safe output without an external relay call.
- Isolated browser-session create/use/lease/revoke.
- Sync events, acknowledgements, outbox, usage holds, reconciliation, and dashboard resources.
- The signed orchestrator client can project safe outbound delivery-attempt
  outcomes/timestamps into the platform `mail_delivery` SLO without exposing
  recipients, subjects, provider IDs, relay reasons, or message content. When
  a trace is bound, the identity service persists only bounded `trace_id` and
  `span_id` metadata on the delivery attempt and the signed projection can
  filter by that trace.
- Orchestrator credential manager, approval requests, resolution audit, browser identity, and durable worker tool grants/nonces.
- The dashboard credentials list reads with `cache: "no-store"`, retains the last successful redacted metadata set after a failed refresh, keeps placeholders/policy/usage rows visible while retrying, labels the list as stale, and exposes header Refresh plus banner Retry controls. [`credentials/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/credentials/page.tsx>) and [`credentials-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/credentials-states.spec.ts) cover this path 1/1 without putting secret values in the fixture.
- All identity-resource tables share an abortable, generation-guarded loader: an obsolete refresh cannot overwrite newer data, retained rows remain visible while retrying, and a successful retry clears the stale warning. [`IdentityResourcePage.tsx`](../../mas/apps/mas-dashboard/components/identity/IdentityResourcePage.tsx) and [`identity-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/identity-states.spec.ts) prove the failure → retained-data → recovery path 1/1 without rendering sensitive fields (`46eccee`).
- Local Stalwart, direct mail-edge, and SMTP gateway deployment/runbook assets.

## Code anchors

- Identity service: [`mas/apps/identity-service/identity_service/`](../../mas/apps/identity-service/identity_service/)
- Signed orchestrator client and safe mail SLO projection: [`mas/apps/orchestrator-api/orchestrator_api/identity_client.py`](../../mas/apps/orchestrator-api/orchestrator_api/identity_client.py)
- Identity migrations: [`mas/apps/identity-service/migrations/versions/0001_identity_control_plane.py`](../../mas/apps/identity-service/migrations/versions/0001_identity_control_plane.py) and [`0002_mail_trace_correlation.py`](../../mas/apps/identity-service/migrations/versions/0002_mail_trace_correlation.py)
- Credential manager: [`mas/packages/mas-core/mas_core/credentials/`](../../mas/packages/mas-core/mas_core/credentials/)
- Dashboard credentials list: [`mas/apps/mas-dashboard/app/(dashboard)/credentials/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/credentials/page.tsx>) and [`credentials-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/credentials-states.spec.ts)
- Shared identity-resource dashboard surface: [`IdentityResourcePage.tsx`](../../mas/apps/mas-dashboard/components/identity/IdentityResourcePage.tsx) and [`identity-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/identity-states.spec.ts)
- Tool identity client/grants: [`mas/apps/tool-service/tool_service/identity_client.py`](../../mas/apps/tool-service/tool_service/identity_client.py) and [`tool_grants.py`](../../mas/apps/tool-service/tool_service/tool_grants.py)
- External-account policy and human gate: [`mas/apps/identity-service/identity_service/external_accounts/service.py`](../../mas/apps/identity-service/identity_service/external_accounts/service.py) and [`mas/apps/identity-service/identity_service/routes.py`](../../mas/apps/identity-service/identity_service/routes.py)
- External-account policy fixture: [`mas/scripts/check_external_account_action_policy.py`](../../mas/scripts/check_external_account_action_policy.py) and [`mas/packages/mas-core/tests/test_external_account_action_policy.py`](../../mas/packages/mas-core/tests/test_external_account_action_policy.py)
- External-account lifecycle fixture: [`mas/scripts/check_external_account_lifecycle.py`](../../mas/scripts/check_external_account_lifecycle.py) and [`mas/packages/mas-core/tests/test_external_account_lifecycle.py`](../../mas/packages/mas-core/tests/test_external_account_lifecycle.py)
- Outbound-mail lifecycle fixture: [`mas/scripts/check_outbound_mail_lifecycle.py`](../../mas/scripts/check_outbound_mail_lifecycle.py) and [`mas/packages/mas-core/tests/test_outbound_mail_lifecycle.py`](../../mas/packages/mas-core/tests/test_outbound_mail_lifecycle.py)
- Local mail: [`mas/infra/compose/README.stalwart-local.md`](../../mas/infra/compose/README.stalwart-local.md)
- Direct edge: [`mas/infra/mail-edge/README.md`](../../mas/infra/mail-edge/README.md)
- SMTP gateway: [`mas/infra/smtp-gateway/README.md`](../../mas/infra/smtp-gateway/README.md)

## Evidence commands

From `mas/`:

```bash
PYTHONPATH=apps/identity-service uv run --isolated pytest \
  apps/identity-service/tests/test_identity_service.py -q
uv run --isolated pytest \
  apps/orchestrator-api/tests/test_identity_reconciliation.py \
  apps/orchestrator-api/tests/test_trace_evidence.py -q
```

These tests exercise signed delivery, safe trace/span persistence and
projection, while provider delivery/bounce and live mail topology evidence
remain separate operator-owned checks.

## Identity model

- AIAT identity is stable even when a worker's runtime changes.
- A mailbox, external account, browser profile, API secret, and short-lived run credential are separate resources attached under policy.
- List and audit APIs return metadata, never secret material.
- Resolution is purpose-bound, time-bound, caller-bound, and recorded.
- Identity/mail tools require explicit per-worker grants and are revoked on suspension/retirement.
- Provider service identities do not impersonate human actors.

## Deployment profiles

| Profile | Status | Intended use |
| --- | --- | --- |
| `agents.aiat.local` Stalwart | Implemented local profile | Loopback development and deterministic tests; no public-mail claim. |
| Direct public Stalwart + Resend | Staged, live certification pending | Public host with DNS, TLS, inbound TCP/25, DKIM/SPF/DMARC, backup, and abuse controls. |
| Public SMTP gateway + WireGuard + private Stalwart + Resend | Staged, live certification pending | Environments where the private/home ISP cannot accept reliable inbound TCP/25. |

Oracle is one possible VPS provider, not an architectural dependency.

## Human-only boundaries

CAPTCHA, MFA enrolment/recovery, payment, legal acceptance, destructive account operations, sensitive provider changes, and other policy-classified actions pause for a human. An automation success response cannot substitute for a missing human event.

## Remaining gaps

- Add provider/webhook-level delivery, bounce, relay, and inbound mail-edge
  spans. The durable outbound-attempt correlation and safe orchestrator
  projection are complete, but they do not claim provider delivery or bounce
  truth.
- Select and certify the production mail topology with real DNS, TLS, send, receive, bounce, spam, outage, and restore evidence.
- Complete key rotation and domain migration rehearsal.
- [x] Add CEO service identity and persisted section-level dashboard ACLs; native deployment and UI evidence remains.
- Prove credential expiration/revocation during active worker/browser sessions.
- Extend the action taxonomy with provider-specific live conformance and outage/restore evidence; the generic high-risk taxonomy and human pause boundary are implemented.
- [x] Keep the external-account action taxonomy and lifecycle independently release-checkable: five actions, category-sensitive signup, human approval for rotation/closure, immediate suspension, governed browser sessions, fail-closed unknown inputs, one-use lease consumption, rotation revocation, and closure/suspension revocation are covered by deterministic fixtures; provider-specific live conformance remains.
- [x] Keep outbound mail independently release-checkable: the deterministic
  fixture covers approval pause, request/submission idempotency, definitive
  provider-failure retry, ambiguous-outage reconciliation hold, and
  secret-safe output without an external relay call; live send/receive/bounce
  and outage/restore evidence remains separate.
- Validate queue recovery across gateway/tunnel/private-mail outages.
- Ensure all dashboard proxy routes redact secrets and preserve operator/service identity.

## Acceptance criteria

- Unsigned, replayed, expired, wrong-caller, wrong-purpose, and wrong-project requests are denied.
- A suspended/retired worker loses identity, browser, credential, and mail tool access without erasing historical audit.
- No secret appears in API listings, logs, artifacts, model context, browser screenshots, or error messages.
- Outbound mail requiring approval cannot send before the exact approval and cannot send twice on retry.
- Definitive outbound-provider failures remain retryable, while ambiguous
  provider outages remain `SUBMISSION_UNKNOWN` until reconciliation; fixture
  reports never contain recipients or message content.
- Production mail can receive, send, bounce, queue through outage, and restore with verified evidence.
- External-account actions preserve actor, provider, identity, browser profile, policy, approval, and result evidence.
- Trace correlation on delivery attempts is limited to safe opaque IDs and
  never authorizes, gates, or exposes mail content or provider metadata.
