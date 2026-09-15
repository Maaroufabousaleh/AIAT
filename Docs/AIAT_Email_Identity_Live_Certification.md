# AIAT email identity live certification

Certification scope: the provider-neutral AIAT email identity runtime using
Cloudflare inbound, D1-backed mail-edge storage, the signed identity-service
boundary, and the existing Resend outbound provider. This record contains
secret-safe evidence only. It is not a claim that AIAT is a product for sale or
managed hosting.

## Certification result

```text
LIVE_CLOUDFLARE_INBOUND = PASS
CLOUDFLARE_INBOUND_LIVE = PASS
IDENTITY_SERVICE_LIVE = PASS
REAL_IDENTITY_PROVISIONING = PASS
REAL_INBOUND_RECONCILIATION = PASS
RESEND_API_AUTH = PASS_SENDING_ACCESS
RESEND_OUTBOUND_LIVE = PASS_PROVIDER_LEVEL_AND_GOVERNED
RESEND_WEBHOOK_LIVE = PASS
GOVERNED_AIAT_OUTBOUND = PASS
GOVERNED_APPROVAL_USAGE_IDEMPOTENCY = PASS
CERTIFICATION_DATE = 2026-09-13
CERTIFICATION_RUN = LIVE-AIAT-EMAIL-IDENTITY-2026-09-13
FINAL_EMAIL_SUBSYSTEM_STATUS = CERTIFIED
```

The operator-provided live smoke run proved this path:

```text
external sender
  -> agents.aiat.ca MX
  -> Cloudflare Email Routing catch-all
  -> aiat-mail-edge
  -> recipient authorization
  -> D1 chunked raw MIME
  -> inbound event
  -> signed HMAC API
  -> AIAT CloudflareInboundAdapter
  -> MIME reconstruction and parsing
```

Secret-safe evidence recorded from that run:

- `agents.aiat.ca` MX routed through Cloudflare and the catch-all invoked
  `aiat-mail-edge`.
- Signed `/v1/health` passed.
- Signed recipient registration passed.
- A real external inbound message was accepted and produced an
  `inbound.message.received` event.
- The event correlated to the operator-provided smoke identity and worker; the
  envelope recipient is deliberately omitted from this record.
- D1 raw-message reconstruction and provider-neutral MIME parsing passed.
- The explicitly permitted smoke-test subject matched
  `AIAT live inbound smoke test`.

The external sender address, message body, verification code, raw MIME,
authentication secret, and provider credentials are deliberately absent from
this record.

## Production runtime hardening evidence

The production identity runtime also includes the following remediation,
validated before the inbound certification was treated as canonical:

- The official PostgreSQL image could not initialise its persistent data
  directory while `cap_drop: [ALL]` was applied. The permanent Compose fix
  retains `cap_drop: [ALL]` and restores only `CHOWN`, `DAC_OVERRIDE`,
  `FOWNER`, `SETGID`, and `SETUID`, which are required by the image entrypoint
  for first-time directory ownership and permission setup.
- The identity-service image previously copied Alembic assets as restrictive
  root-owned files. The permanent Dockerfile fix copies `alembic.ini` and the
  migrations with UID/GID `10001:10001` ownership and normal read/execute
  permissions, while retaining the non-root runtime user `10001:10001`.
- The remediation is committed in
  `528ad79199ea5fa2e808ca3f89012166cda57bf9`
  (`fix(identity): harden production database startup and migration assets`).
- The production database migrated through
  `0001_identity_control_plane`, `0002_mail_trace_correlation`,
  `0003_mail_edge_observations`, and `0004_provider_neutral_mail`; Alembic
  reports `0004_provider_neutral_mail (head)`. The identity-service
  `/healthz` and `/readyz` checks passed.

## Temporary certification identity

The temporary recipient was retired through the signed lifecycle API after the
certification run. Its provider reference is a non-secret opaque registration
value returned by the Worker; for the current deterministic recipient binding
it was:

```text
recipient:e2ad9f0588e174b1f82ebef7e389ad1b
```

The supported cleanup command is documented in the domain runbook. The live
call proved the `VERIFYING`/`ACTIVE` binding reaches `RETIRED`; it did not send
another external message. A separate operator-controlled rejection check may
be run after retirement if needed.

## Certification follow-through

```text
TEMPORARY_RECIPIENT_RETIREMENT = PASS
```

## Resend provider transport certification

The existing operator-owned Resend credential was exercised through the
bounded provider certificate after the Cloudflare public webhook boundary was
verified. The key was classified as intentional least-privilege
`sending_access`; domain-management reads were not used as an authentication
gate. Exactly one harmless provider-level certification send was submitted,
with no automatic retry. The provider then delivered a signed webhook to the
exact public callback, and the identity-service accepted and normalized it
without retaining the webhook body.

```text
RESEND_API_AUTH = PASS_SENDING_ACCESS
RESEND_DOMAIN_STATUS = NOT_AVAILABLE_RESTRICTED_API_KEY
RESEND_TRANSPORT_CERTIFICATION = PASS_PROVIDER_LEVEL
RESEND_SEND_COUNT = 1
RESEND_AUTOMATIC_RETRY = false
RESEND_PROVIDER_MESSAGE_ID_PRESENT = PASS
RESEND_PROVIDER_MESSAGE_ID = 96638bf4-9e1e-4b57-b492-65034fc78de7
RESEND_WEBHOOK_URL = https://identity.aiat.ca/v1/mail-edge/provider-webhook/resend
RESEND_WEBHOOK_SIGNATURE = PASS
RESEND_WEBHOOK_EVENT_ID = msg_3JFWEiXYCndSuAKMUzOzyJe27x9
RESEND_DELIVERY_EVENT = delivered
RESEND_PROVIDER_OBSERVATION = PASS
```

The matching normalized observation recorded `source=provider_webhook`,
`outcome=success`, and `signature_verified=true`; its provider message
reference exactly matched the accepted provider message ID. A corresponding
`mail.provider_event` audit record was created. These identifiers are opaque
provider references; the recipient, sender, message body, credentials, and
webhook secret are deliberately absent.

## Certification matrix

```text
IDENTITY_SERVICE_LIVE = PASS
REAL_IDENTITY_PROVISIONING = PASS
REAL_INBOUND_RECONCILIATION = PASS
REAL_INBOUND_DELIVERY_VERIFICATION = PASS
NEW_CERTIFICATION_WORKER_ID = b9461896-ede2-404e-8647-6aef16066275
NEW_CERTIFICATION_IDENTITY_ID = 23b86a80-9427-4fee-874b-de5dd7a3ba3c
NEW_CERTIFICATION_IDENTITY_STATE = ARCHIVED_AFTER_CERTIFICATION
NEW_CERTIFICATION_CLOUDFLARE_BINDING = RETIRED
RESEND_API_AUTH = PASS_SENDING_ACCESS
RESEND_DOMAIN_STATUS = NOT_AVAILABLE_RESTRICTED_API_KEY
RESEND_OUTBOUND_LIVE = PASS_PROVIDER_LEVEL_AND_GOVERNED
RESEND_WEBHOOK_LIVE = PASS
GOVERNED_AIAT_OUTBOUND = PASS
GOVERNED_APPROVAL_USAGE_IDEMPOTENCY = PASS
REAL_AIAT_HIRING_LIFECYCLE_INTEGRATION = NOT_EXERCISED_IN_THIS_CERTIFICATION
FULL_PRODUCTION_IDENTITY_SERVICE = PASS_FOR_EMAIL_IDENTITY_RUNTIME
OUTBOUND_RELAY_CERTIFIED = true
DEFAULT_OUTBOUND_ENABLED = false
DIRECT_MX_OUTBOUND_ENABLED = false
FINAL_EMAIL_SUBSYSTEM_STATUS = CERTIFIED
```

The canonical signed identity-service lifecycle was exercised directly for
this bounded certification. A separate upstream hiring-orchestrator run was
not used as evidence and remains outside this record.

The repeatable operator command is
[`mas/scripts/certify_cloudflare_mail_edge_live.py`](../mas/scripts/certify_cloudflare_mail_edge_live.py).
It reads only `CLOUDFLARE_MAIL_EDGE_URL` and
`CLOUDFLARE_MAIL_EDGE_AUTH_SECRET` from the environment, uses the production
adapter/HMAC implementation, and emits only bounded metadata and pass/fail
status.

## Canonical AIAT inbound reconciliation

The final live run used a fresh UUID-backed identity through the real signed
identity-service lifecycle rather than treating the temporary smoke mailbox as
the production test identity. The following evidence is intentionally limited
to opaque identifiers and state transitions:

```text
CANONICAL_RUN = LIVE-AIAT-EMAIL-IDENTITY-2026-09-13
worker_id = b9461896-ede2-404e-8647-6aef16066275
identity_id = 23b86a80-9427-4fee-874b-de5dd7a3ba3c
provider_event_id = evt-840898776cc6d0c7c31b495863ff38fce4def84e61f4893f
provider_message_id = m-2af1b427076840199f09b59f1c8d40f7
inbound_cursor = 3
identity_state_after_verification = IDENTITY_ACTIVE
identity_state_after_cleanup = ARCHIVED
cloudflare_binding_after_cleanup = RETIRED
```

Secret-safe live evidence for that run:

- The real identity-service/Postgres runtime reached `/healthz` and `/readyz`;
  the production database was at Alembic head `0004_provider_neutral_mail`.
- Governed allocation created the identity, registered its Cloudflare binding,
  and left the identity in `IDENTITY_VERIFYING` until delivery evidence was
  available.
- A real external inbound message reached the canonical envelope recipient;
  the Worker stored it in D1 chunks, the identity-service retrieved and
  reconstructed the raw message, and ownership/correlation matched the worker
  and identity above.
- The normalized `inbound.message.received` event was persisted before the
  signed ACK, and the identity-service reconciliation cursor advanced to `3`.
- Delivery verification persisted `INBOUND_RECEIVED` and
  `DELIVERY_VERIFIED`, then the identity reached `IDENTITY_ACTIVE`.
- A post-ACK Cloudflare read with `after=3` returned no pending events. The
  certification identity was subsequently archived through the signed
  lifecycle API and its Cloudflare binding reached `RETIRED`; no registry or
  D1 row was deleted directly.

An earlier historical smoke event was sequence `1` from a standalone smoke
certificate. It used non-UUID temporary identity and worker identifiers that
are incompatible with canonical identity-service synchronization. Its exact
safe envelope metadata was verified, it was ACKed through the signed
Cloudflare provider adapter, and `PostgresIdentityStore.advance_inbound_sync_cursor()`
advanced the durable cursor from `0` to `1`. It was not processed by
canonical identity-service synchronization. The earlier canonical run was the
sequence-2 proof. The final run above is the sequence-3 proof, and the final
cursor is `3`.

The external sender, message body, verification code, raw MIME, authentication
secrets, webhook secrets, and provider credentials are deliberately absent
from this record.

## Governed outbound certification

The final run used the same newly activated identity as the inbound
certification. It exercised the signed identity-service request, explicit
approval, approved submission, provider webhook correlation, and signed
archive lifecycle. No second provider-level certification send was made.

```text
GOVERNED_OUTBOUND_REQUEST = PASS
GOVERNED_REQUEST_ID = 483df3e6-0189-42b8-96cc-30a11adcca51
GOVERNED_REQUEST_INITIAL_STATE = PENDING_APPROVAL
GOVERNED_APPROVAL = PASS
GOVERNED_APPROVAL_ID = d454fcfc-f395-4965-9dfb-6eb11989164f
GOVERNED_APPROVAL_STATE = APPROVED
GOVERNED_SEND = PASS
GOVERNED_SEND_COUNT = 1
GOVERNED_PROVIDER_MESSAGE_ID = be920549-3c62-4cec-9af8-ee910d5ad457
GOVERNED_PROVIDER_CORRELATION_ID = 85085e8f-db7c-4b9e-a4df-4de395f0bd84
GOVERNED_PRE_APPROVAL_SEND = DENIED_403
IDEMPOTENCY = PASS
IDEMPOTENCY_DUPLICATE_PROVIDER_SUBMISSIONS = 0
DELIVERY_ATTEMPT_COUNT = 1
USAGE_ACCOUNTING = PASS
USAGE_HOLDS_COMMITTED = outbound_message:1,provider_api_call:1
AUDIT_EVIDENCE = PASS
DELIVERY_EVENT = delivered
RESEND_WEBHOOK_EVENT_SENT = msg_3JFbRUCmwQO7YBmc0B4TqytwhuZ
RESEND_WEBHOOK_EVENT_DELIVERED = msg_3JFbRcGvZue1jBgHGbQaKPu95ih
RESEND_WEBHOOK_SIGNATURE = PASS
RESEND_PROVIDER_CORRELATION = PASS
```

Before approval, `send-approved` returned `403` and no provider submission
occurred. After the durable approval, the provider accepted exactly one send.
A duplicate signed `send-approved` call returned the existing submitted record
with the same provider message ID; the delivery-attempt count remained one.
The `outbound_message` and `provider_api_call` holds both settled as
`COMMITTED`, with one corresponding usage event each. Durable audit rows cover
the request, approval decision, submission, and provider events.

The signed Resend webhook produced both `sent` and terminal `delivered`
observations. Both were accepted with `signature_verified=true` and correlated
to the exact governed request and provider message ID. No webhook body,
message content, recipient value, sender value, credential, or secret was
retained in this evidence.

## Current production boundary

The production identity database and local runtime are healthy. The dedicated
Cloudflare Tunnel is connected and the public identity boundary has been
verified through the Cloudflare edge. The narrow gateway exposes only the
signed provider webhook; unrelated public paths remain hidden. Provider-level
and governed Resend transport, signed webhook, inbound reconciliation, and
identity cleanup have passed for this bounded internal-use certification.

```text
PUBLIC_IDENTITY_INGRESS = PASS
CLOUDFLARE_TUNNEL = PASS
CLOUDFLARE_TUNNEL_ID = 50b7a5c8-06ff-4d40-bd3c-34987b8c1c46
PUBLIC_WEBHOOK_REACHABILITY = PASS
PRIVATE_IDENTITY_API_EXPOSURE = PASS_FAIL_CLOSED
PUBLIC_WEBHOOK_URL = https://identity.aiat.ca/v1/mail-edge/provider-webhook/resend
```

The safe edge probe returned `404` for `/healthz` and an unrelated path,
`404` for an unsigned `GET` to the webhook path, and application-level `401`
for an unsigned `POST` to the exact webhook path. Internal identity-service
health and readiness both returned `200`.

A secret-safe Resend API probe using the existing injected credential returned
the structured provider classification `restricted_api_key` with HTTP `401`.
This confirms the intentional least-privilege `sending_access` mode; it is not
an invalid-key result, and no credential was rotated or replaced.
Domain-management reads are unavailable in this mode, so transport
certification used the bounded `/emails` send endpoint with the configured
production sending domain. One provider-level certification message and one
governed certification message were accepted; both received signed terminal
delivery evidence.

```text
IDENTITY_SERVICE_RUNTIME_LOCAL = PASS
PRIVATE_WEBHOOK_GATEWAY = PASS (unrelated paths 404; unsigned POST 401)
PUBLIC_IDENTITY_INGRESS = PASS
CLOUDFLARE_TUNNEL = PASS
PUBLIC_WEBHOOK_REACHABILITY = PASS
PRIVATE_IDENTITY_API_EXPOSURE = PASS_FAIL_CLOSED
RESEND_API_AUTH = PASS_SENDING_ACCESS
RESEND_DOMAIN_MANAGEMENT_READ = NOT_AVAILABLE_RESTRICTED_API_KEY
RESEND_OUTBOUND_LIVE = PASS_PROVIDER_LEVEL_AND_GOVERNED
RESEND_WEBHOOK_LIVE = PASS
REAL_AIAT_HIRING_LIFECYCLE_INTEGRATION = NOT_EXERCISED_IN_THIS_CERTIFICATION
GOVERNED_AIAT_OUTBOUND = PASS
FULL_PRODUCTION_IDENTITY_SERVICE = PASS_FOR_EMAIL_IDENTITY_RUNTIME
OUTBOUND_RELAY_CERTIFIED = true
DEFAULT_OUTBOUND_ENABLED = false
DIRECT_MX_OUTBOUND_ENABLED = false
CERTIFICATION_IDENTITY_FINAL_STATE = ARCHIVED
CLOUDFLARE_CERTIFICATION_BINDING = RETIRED
FINAL_EMAIL_SUBSYSTEM_STATUS = CERTIFIED
```
