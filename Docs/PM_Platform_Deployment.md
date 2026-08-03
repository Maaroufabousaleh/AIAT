# PM platform deployment and configuration reference

## Services

- `orchestrator-api` is the policy and canonical database writer.
- `pm-gateway` is the bounded webhook ingress and delivery wake-up surface; it
  has no provider credentials.
- Postgres stores revisions, mappings, inbox/outbox, retries, conflicts,
  cutovers, comments, and SCM evidence.
- The credential manager resolves named provider secrets server-side.

Apply Alembic head (including `0025_pm_project_provisioning_lifecycle` and
`0026_pm_lifecycle_transition_plans`) before
creating connections or project bindings:

```powershell
uv run alembic current
uv run alembic upgrade head
```

Set `MAS_API_KEY`, `AIAT_OPERATOR_API_KEY`, and `PM_GATEWAY_API_KEY` to three
different high-entropy values. `MAS_API_KEY` authenticates ordinary internal
services, `AIAT_OPERATOR_API_KEY` is required for dashboard/operator and
canonical mutation routes, and `PM_GATEWAY_API_KEY` is limited to webhook
ingress and outbox draining. Actor-role headers are attribution only and never
grant privileges. Use HTTPS at the edge, and set
`PM_GATEWAY_ENVIRONMENT=production` in production. Configure
`PM_GATEWAY_PORT` and `PM_GATEWAY_OUTBOX_INTERVAL_SECONDS`; do not put provider tokens,
private keys, webhook secrets, or installation tokens in environment values
that are passed to workers or in connection JSON.

## Activation gates

`DISABLED -> SHADOW -> READ_ONLY/ACTIVE -> DRAINING` is operator controlled.
Require migration success, provider health/capabilities, least-privilege
evidence (including Create Project and automatic Project Admin ownership, with
unrelated global administration and Delete Project denied), webhook
verification for both issue and comment events, successful projection,
successful reconciliation, mapping/reconciliation results, and a rollback
record before `ACTIVE`. The default is one provider project and one binding per
canonical project; issue-only umbrella mapping is explicit. Use
archive/deactivation for normal cleanup; permanent provider deletion requires
an explicit operator approval path. Use backups and drain providers before any
database downgrade.

The lockfile/Compose and local fake suites are reproducible development gates;
they do not replace live YouTrack/GitHub staging certification.

All connection and binding transitions are persisted in
`pm_lifecycle_plans` before an operator sees a plan. The digest is computed
from canonical JSON containing immutable inputs and operations. Approval and
apply require the distinct operator credential, exact digest, and explicit
confirmation; actor-role headers cannot elevate service or worker callers.
`pm_lifecycle_audits` records the atomic compare-and-swap transition. A
`READ_ONLY` binding with a `SHADOW` connection is supported and means outbound
projection plus authenticated evidence only; it does not permit inbound
canonical mutation. The dashboard is a review/confirmation client and never
computes or writes lifecycle policy.

Provider TLS verification is fail-closed. When a local development host
performs HTTPS inspection, mount its public CA certificate into the
development-only orchestrator profile and set `AIAT_PROVIDER_CA_BUNDLE` to the
mounted PEM path. Do not set `verify=False`, use a wildcard trust override, or
copy the certificate into a production image; production should install the
approved enterprise CA through its normal image/host trust process.

## ACTIVE inbound policy and rollback

The orchestrator enforces a provider-neutral ACTIVE command allowlist. Direct
commands are limited to approved priority values and non-destructive statuses
(`backlog`, `in_progress`, `review`, `blocked`). Title/description and
assignee/reassignment are approval-required proposals; closing, deletion,
escalation, `done`, and `cancelled` are approval-required/destructive;
ordinary and edited/deleted comments are evidence-only; structured
`AIAT-COMMAND` comments require approval. AIAT identity, managed/revision,
project, ownership, credentials, governance, binding, and lifecycle fields are
reserved and rejected.

ACTIVE requires `config.external_actor_mappings` entries that identify an
authorized human/operator AIAT identity. The provider integration account,
AIAT_Agents, synthetic actors, unknown actors, and projection echoes cannot
authorize a command. The expected canonical revision is explicit in a
structured command or resolved from durable mapping metadata; missing/stale
values fail closed. Canonical update plus PM outbox enqueue uses the issue row
lock/CAS transaction. Actor evidence, provider version, mapping, origin,
payload hash, and idempotency records make duplicate/reordered events and
projection echoes auditable and loop-safe.

The kill switch is a governed lifecycle plan to `READ_ONLY`; direct status
writes are not supported. It prevents inbound canonical mutation while
retaining evidence and permitting outbound behavior according to the effective
connection/binding policy. The current deployment remains `READ_ONLY`/`SHADOW`;
no ACTIVE lifecycle plan is generated or applied by this certification.
