# AIAT Provider-Neutral PM Platform Integration Plan

## Status and decision record

**Status:** Approved implementation plan
**First target providers:** YouTrack and GitHub
**Authority model:** AIAT canonical; external platforms are governed projections and human command surfaces
**Setup model:** operator-confirmed, idempotent `plan` / `apply`; no automatic account creation, billing changes, global-admin grants, or destructive cleanup

**Implementation status:** The provider-neutral contracts, persistence
migrations, orchestrator APIs, gateway, fake/YouTrack/GitHub adapters, governed
tools, and dashboard operator view are implemented in this repository. The
live stack is at migration head `0026_pm_lifecycle_transition_plans`; the
current YouTrack connection and binding remain `SHADOW`. New provider changes
still require the persisted-plan and live-certification gates described below.

Migration `0026_pm_lifecycle_transition_plans` adds durable, operator-only,
digest-bound lifecycle plans and immutable transition audits. Connection and
binding state changes must use the versioned lifecycle API; legacy cutover and
rollback aliases fail closed with `lifecycle_plan_required`.

The per-canonical-project provisioning lifecycle is implemented behind the
project-scoped plan/apply endpoints. Migrations `0024`, `0025`, and `0026` are
applied in the deployed stack; no project-specific provider apply is implied
by the connection-level bootstrap digest.

**Local verification status (2026-07-28):** The focused PM/SCM suite and the
broader non-tool-service regression suite pass. Compile, diff, offline lockfile,
Compose configuration, and dashboard typecheck/lint checks pass (with one
pre-existing dashboard warning). The full collection still needs the optional
`mcp` and Playwright dependencies; a live Alembic upgrade/rollback and real
YouTrack/GitHub staging run are intentionally not claimed here.

This plan adds project-management integrations without making any external PM
provider part of AIAT's control-plane authority. It builds on the existing
Postgres project/sprint/issue model, the orchestrator API as workflow writer,
the governed tool service, the credential manager, and the worker/run audit
model.

## Decisions

### Keep

- Do not create a SaaS identity for every AIAT agent. AIAT agent identity,
  team, worker version, run, approval, and evidence remain AIAT records.
- Use a restricted YouTrack integration user per configured connection and a
  GitHub App, rather than shared human tokens or per-agent GitHub accounts.
- Keep credentials inside the AIAT credential boundary. A worker never receives
  a permanent token or GitHub App private key.
- Isolate concurrent coding runs by workspace/worktree, branch, repository
  scope, capability grant, run ID, and audit record.
- Receive provider changes through authenticated webhooks, persist them
  durably, and reconcile periodically.

### Correct the proposed external-provider architecture

- AIAT, not YouTrack, owns portable work-management fields: project/sprint
  membership, title, description, status, priority, estimate, assignment,
  dependencies, approval state, agent assignment, and KPI history. A provider
  edit is a proposed command that AIAT validates and commits before projecting
  the resulting canonical state back out.
- GitHub source-control facts (commits, pull requests, reviews, CI and checks)
  are authoritative only as evidence. They never bypass AIAT approvals or
  merge governance.
- Expose generic `project.*`, `sprint.*`, and `issue.*` tools to agents; do not
  expose `pm.youtrack.*` or provider-specific tools that make workers depend on
  an installed platform.
- Do not create an adapter service that writes project/workflow state beside the
  orchestrator. A dedicated gateway may own inbound/outbound delivery records,
  but all canonical mutations go through the orchestrator.
- Do not store transient event IDs, run IDs, approval IDs, GitHub references,
  and last execution state as permanent YouTrack fields. These belong in AIAT's
  durable audit/mapping records. Provider-visible comments carry concise,
  human-readable attribution instead.
- Treat a three-account YouTrack split (owner, daily operator, integration)
  as a recommended operating setup, not a code-enforced requirement.

## Target architecture

```text
AIAT worker/dashboard
  -> governed generic tool
  -> orchestrator: policy + canonical transaction + PM outbox
  -> integration gateway: provider adapter + retry/rate limit/reconciliation
  -> YouTrack / GitHub

YouTrack/GitHub webhook
  -> integration gateway: raw-body verification + durable inbox + dedupe
  -> provider-normalized command
  -> orchestrator: policy + optimistic-concurrency + canonical transaction
```

The integration gateway is internet-facing and must not write `projects`,
`sprints`, `issues`, approvals, workflow state, or worker lifecycle state
directly. The orchestrator remains the sole canonical writer.

## Canonical data model and API changes

### Canonical work-management data

Retain existing project, sprint, issue, approval, document, workflow, and KPI
tables. Add:

- `revision BIGINT NOT NULL` and `updated_at TIMESTAMPTZ NOT NULL` to projects,
  sprints, and issues. Every canonical mutation increments the revision.
- `work_item_comments`: AIAT work-item ID, author identity, body/blob reference,
  run/approval/evidence references, origin, created and edited timestamps.
- `work_item_links`: typed relationships between AIAT work items, projects,
  documents, external evidence, and repository delivery objects.
- `pm_connections`: provider kind, display name, validated base URL, encrypted
  secret references, capability profile, schema version, health, and activation
  status. No secret value is stored in configuration JSON.
- `pm_project_bindings`: canonical project, connection, provider project or
  repository selector, mapping profile, sync cursor, activation status, and
  direction. Enforce one active inbound work-management binding per project.
  Migration `0025_pm_project_provisioning_lifecycle` also records the
  provider short name, digest-bound provisioning state, activation blockers,
  issue/comment webhook evidence, projection evidence, and reconciliation
  evidence.
- `pm_object_mappings`: connection, canonical object type/ID, external ID/key,
  provider version token, content hash, last import/export revision and time.
  Enforce uniqueness for both canonical and external identities.
- `pm_inbox_events`: provider delivery ID, raw payload/blob reference, verified
  headers metadata, normalized type, received/processed times, result, and
  correlation/causation IDs. Uniqueness on `(connection_id, provider_delivery_id)`.
- `pm_outbox_events` and `pm_delivery_attempts`: canonical aggregate/revision,
  operation, idempotency key, serialized projection payload, retry schedule,
  provider response metadata, delivery lease, and final result.
- `pm_conflicts`, `pm_reconciliation_runs`, and `pm_cutovers`: durable operator
  decisions, drift evidence, repair actions, and rollback readiness.

Create `0024_pm_provider_control_plane` after
`0023_durable_browser_identity_and_tool_nonces`, then apply
`0025_pm_project_provisioning_lifecycle` before activating any project
binding. Both migrations must create indexes/uniqueness constraints before
enabling a provider, backfill revisions as `1`, and leave existing
sprint/issue behavior compatible.

### Public contracts

Preserve existing `project.*`, `sprint.*`, and `issue.*` contracts. Replace the
current sprint/issue CRUD path through `POST /tasks` with typed orchestrator use
cases; retain `/tasks` as a compatibility wrapper until all callers migrate.

Add provider-neutral operations:

- `issue.get`, `issue.update`, `issue.comment`, and `issue.link`.
- Read-only `pm.sync.status` for permitted operational roles.
- Operator-only connection, bootstrap, reconciliation, conflict, cutover, and
  rollback APIs.

Mutating calls return the committed canonical object plus each projection's
state (`pending`, `synced`, `conflicted`, or `failed`). They must never claim
that a provider operation succeeded merely because it was queued.

The legacy `POST /tasks` wrapper remains only for migration compatibility:
its deterministic sprint/issue/profile persistence actions use the same
operator authorization as the typed routes. Unknown actions may still be
routed as `ADMIN_TASK` envelopes until their callers migrate.

## Provider contracts

Implement two independent ports in a shared provider package.

### `WorkManagementProvider`

Required methods:

- `discover`, `health`, `capabilities`
- `plan_bootstrap`, `apply_bootstrap`, `verify_configuration`
- project/iteration/work-item create, read, update, archive, and list
- comments and typed links
- incremental change polling
- raw webhook verification and normalization

### `SourceControlProvider`

Required methods:

- `discover_installation`, `capabilities`, `mint_run_credential`
- repository and issue projection when enabled
- branch, pull request, review-comment, check, and commit-evidence handling
- raw webhook verification and normalization

All contract inputs and outputs carry the canonical object ID/revision,
connection/binding IDs, actor/team/run/approval/evidence context,
correlation/causation IDs, idempotency key, origin, and provider version token.
Unsupported features are advertised through capability flags rather than
silently emulated.

The YouTrack adapter implements `WorkManagementProvider`. The GitHub adapter
implements source-control capabilities and may implement the work-management
port for repository delivery issues. This separation allows a future Jira,
Linear, GitHub Issues-only, or other provider integration without changing
agent tools, project state, policy, or workflow code.

## Synchronization, idempotency, and conflicts

### Outbound path

The orchestrator commits canonical state and its corresponding `pm_outbox_event`
in one database transaction. Gateway delivery workers claim events with database
locking, call the selected adapter with an idempotency key, persist mappings and
provider version tokens, then mark the delivery complete. They use bounded
exponential backoff with jitter, respect provider rate-limit responses, and
dead-letter permanent failures.

### Inbound path

The gateway verifies the raw request body before JSON parsing. It records the
event before returning a success response, deduplicates it, normalizes it to a
provider-neutral command, then calls the orchestrator. The orchestrator validates
scope, mapping, actor, supported field, workflow transition, and expected
revision before changing canonical state.

Origin/correlation metadata, provider delivery IDs, canonical revisions, and
content hashes prevent webhook echo loops. Providers never synchronize directly
with each other.

Unknown mappings, stale revisions, deletes, unsupported transitions, incompatible
field values, unauthorized external users, and out-of-scope project/repository
events become durable conflicts. They require an operator decision; automated
resolution is limited to idempotent replays and unambiguous no-op echoes.

### Authority matrix

| Data | Authority |
| --- | --- |
| Project, sprint, issue, priority, assignment, estimate, dependencies | AIAT |
| Agent identity, run, worker version, approval, budget, evidence, KPI | AIAT |
| Provider issue/project representation | AIAT projection; external changes are proposed commands |
| Commit, pull request, review, CI/check status | GitHub evidence source |
| Human-visible portfolio/sprint interface | YouTrack projection and approved inbound commands |

## Per-canonical-project provisioning lifecycle

The YouTrack project `AIAT` is the external representation of the AIAT
software project itself. It is not an umbrella for future canonical projects.
When a new canonical project is created, AIAT records an explicit
`dedicated_project` provisioning intent. The operator then runs the
project-scoped, read-only plan endpoint and approves its exact digest.

The default plan adopts or creates one provider project with a deterministic
short name (`AIAT-<name>-<canonical-id-suffix>`), attaches the four stable
fields, and creates one `pm_project_binding` for that canonical project using
the existing restricted connection and credential references. A project can
instead use `umbrella_issues` only when an operator explicitly selects that
mapping profile and supplies the external project selector.

Webhook Triggers attachment is represented as a manual action when the
approved integration permissions cannot perform app attachment. Applying the
safe project/field portion therefore leaves the binding `SHADOW` (or
`DISABLED`) with a persisted blocker. The binding cannot become `ACTIVE` until
authenticated issue and comment webhook evidence, a successful projection,
and a successful reconciliation are all recorded. Provider archive/deactivate
is the rollback action; permanent deletion remains an explicit operator
decision.

## YouTrack implementation

- Support YouTrack Cloud and Server through a validated HTTPS base URL,
  capability discovery, and a restricted integration account per connection.
- Store a permanent-token reference in the credential manager. The runtime
  identity intentionally has global `Observer` and `Project Creator` (therefore
  `Create Project`) plus `Project Admin` on AIAT-managed projects. A project it
  creates automatically makes that integration identity its Project Admin.
  Never grant System Admin, User Manager, Low-level Admin Read/Write,
  organization/authentication administration, global app administration, or
  Delete Project.
- Bootstrap only four stable custom fields: `AIAT Object ID`, `AIAT Object
  Type`, `AIAT Revision`, and `AIAT Managed`.
- For agent-authored comments, show `AIAT actor`, `run`, and `evidence` links.
  AIAT retains the complete signed audit data.
- Map human assignees only from explicit external-user mappings. Agent-owned
  work may project to the integration user where the operator enables it.
- Verify and use the Webhook Triggers app. A missing app install or project
  permission is a manual bootstrap prerequisite, not a reason to widen runtime
  privileges. The doctor requires a redacted least-privilege snapshot and
  checks the positive Create Project/project-owner path as well as negative
  global-administration and deletion probes.
- Validate the configured shared webhook-token header in constant time, use
  HTTPS, permit zero-downtime rotation with current/previous secret references,
  and compensate for provider delivery limitations with inbox hashes and
  reconciliation.

## GitHub implementation

Use one GitHub App per AIAT deployment/connection. Install it only on explicitly
selected repositories. Keep its private key in the credential manager and mint
repository- and permission-scoped installation tokens for governed runs.

Permission profiles:

| Profile | Required access |
| --- | --- |
| PM | Metadata read; Issues read/write |
| Delivery | PM profile plus Contents and Pull requests read/write |
| Checks | Delivery profile plus Checks read/write |

Actions read is optional. Workflows and Administration are disabled. A run gets
the token only through an ephemeral credential helper in its isolated workspace;
it never receives the private key.

Use sanitized, length-bounded branch names such as
`aiat/<agent>/<run>/<work-item>`. Put structured AIAT references in commit
trailers and PR bodies, but do not use `Co-authored-by` to impersonate an agent
as a GitHub user. Publish checks only when the Checks profile is enabled.

Verify `X-Hub-Signature-256` against unmodified raw request bytes, retain the
GitHub delivery ID for dedupe, and subscribe only to events required by the
enabled profile.

## Governed bootstrap and provider switching

Define a versioned connection manifest containing provider kind, base URL,
credential references, project/repository selectors, capability profile, mapping
profile, webhook endpoint, and desired activation mode.

Operator commands:

1. `plan` performs read-only discovery and writes a digest-bound diff of resources to
   create/adopt, fields, webhooks, permissions, mappings, secret references,
   validation gates, rollback actions, and blockers.
2. `apply --plan-sha --confirm` requires explicit confirmation, rechecks drift, and makes
   only idempotent non-destructive changes. Compatible resources are adopted by
   immutable IDs; incompatible resources or ambiguous name matches block.
3. `doctor` verifies credentials, least privilege, project/repository scope,
   webhook authentication, mapping completeness, and provider health.
4. `reconcile` compares mappings, counts, revisions, hashes, links, and
   provider state. It proposes repairs without deleting external data.

Connection bootstrap and canonical-project provisioning are separate approval
artifacts. The connection-level plan for the `AIAT` software project does not
authorize creating provider projects for later canonical projects. Each later
project receives its own plan ID and digest, and a changed plan must stop at
the approval boundary rather than applying the earlier digest.

Use connection states `disabled`, `shadow`, `read_only`, `active`, and
`draining`.

A binding may be `READ_ONLY` while its connection remains `SHADOW`: the
connection being non-disabled permits outbound projections and authenticated
inbound evidence, while the binding blocks inbound canonical mutation. Only an
`ACTIVE`/`DRAINING` binding paired with an `ACTIVE`/`DRAINING` connection permits
inbound canonical mutation. A disabled connection blocks both directions.

### Durable lifecycle plans

`POST /api/v1/integrations/lifecycle-plans` persists a complete transition plan
before returning it. The canonical digest covers the immutable target,
expected states/revisions, operations, gates, evidence, expiry, and rollback;
approval/application timestamps and results are excluded. Operators review the
exact digest, approve it with `POST .../{id}/approve`, and apply only that
approved digest with `POST .../{id}/apply`. Apply rechecks doctor,
reconciliation, mappings, conflicts, outbox/dead letters, provider health, TLS
verification, and compare-and-swap revisions in one transaction with the state
update and audit record. Replays of an applied plan return the original result;
a changed state marks the plan `STALE`.

Provider cutover:

1. Bootstrap the new provider in `shadow` mode and backfill canonical objects.
2. Reconcile mappings, counts, hashes, relationships, and revisions.
3. Send outbound shadow projections while inbound changes remain disabled.
4. Freeze old-provider inbound processing, apply its final delta, and require
   no blocking conflicts.
5. Atomically switch the active binding, enable new inbound processing, and
   leave the old connection `read_only` then `draining`.
6. Roll back by switching the active binding and replaying retained outbox
   events; never delete mappings or old provider resources automatically.

## Delivery sequence

1. Add canonical revisions, integration persistence, typed work-management use
   cases, generic tools, and fake-provider conformance tests. Existing behavior
   remains available through compatibility wrappers.
2. Add the integration gateway, outbox/inbox processing, credential references,
   gateway health/metrics, dashboard operator pages, and the plan/apply/doctor
   workflow. Certify with fake adapters first.
3. Implement YouTrack bootstrap, mapping, projection, webhook normalization,
   reconciliation, and staging certification in `shadow` mode.
4. Implement GitHub App profiles, installation discovery, repository issues,
   PR/check evidence, webhook handling, and staging certification in `shadow`
   mode.
5. Exercise conflict resolution, provider outage, dead-letter replay, backup,
   cutover, and rollback. Enable active inbound synchronization only after all
   gates pass.

## Test and certification requirements

Automated coverage must include:

- Alembic upgrade/rollback, backfill, constraints, project isolation, revision
  increments, and atomic canonical/outbox writes.
- Shared provider-contract tests for fake, YouTrack, and GitHub adapters.
- Raw-body signature validation, invalid signatures, duplicate/reordered/replayed
  events, schema changes, echo loops, stale revisions, and unknown mappings.
- Crash windows around inbox persistence, canonical commit, provider call,
  mapping persistence, acknowledgement, and retry.
- Rate limiting, token expiry/rotation, permission loss, outage, dead-letter
  replay, concurrency, conflicts, and reconciliation repair proposals.
- URL allowlists, secret redaction, repository/project scope, least-privilege
  denials, and agent inability to select arbitrary connections.
- Existing `sprint.*` and `issue.*` compatibility, dashboard plan/conflict views,
  Compose configuration, protocol fixtures, and dashboard E2E coverage.

Live staging certification must prove:

- YouTrack Observer, Project Creator/Create Project, existing-project Project
  Admin, and automatic created-project ownership work while forbidden global
  administration and Delete Project fail. Archive/deactivation remains the
  normal cleanup path.
- GitHub tokens are installation-, repository-, and permission-scoped.
- Deployed webhook ingress validates YouTrack and GitHub authentication.
- Create/update/comment/link/iteration, GitHub issue/PR/check, pagination,
  rate-limit, rotation, reconciliation, outage, and rollback scenarios work.
- No blocking mapping drift remains after reconciliation.

Mock or fixture success alone is not production certification.

## ACTIVE inbound-command boundary (prepared, not activated)

ACTIVE is a governed command surface, not a provider mirror.  The immutable
policy is enforced after webhook authentication, mapping, project scope, and
echo detection:

| Provider input | ACTIVE behavior | Gate/rollback |
| --- | --- | --- |
| `priority` | Direct command only for `low`, `medium`, `high`, `urgent`, `critical`, `normal` | Mapped human actor + expected canonical revision + CAS; restore the prior value through the canonical update path |
| `status` | Direct command only for `backlog`, `in_progress`, `review`, `blocked` | `done`, `cancelled`, delete/archive, and escalation are approval-required; rollback is a governed canonical update |
| `title`, `description` | Approval-required proposal; never direct | Persist an `approval_required` conflict with both snapshots |
| `assignee`, `assigned_team`, `assigned_agent` | Approval-required proposal; never direct | Reassignment requires operator approval |
| ordinary comments and edits/deletes | Evidence-only | No canonical comment or outbox mutation |
| structured `AIAT-COMMAND: {…}` comment | Approval-required (invalid structure is rejected) | Provider receipt cannot bypass AIAT approval |
| AIAT identity/managed/revision fields, project/ownership, governance, credentials, lifecycle | Rejected/reserved | Persist `reserved_field_mutation`; no write |

All unsupported fields are default-deny.  A command requires a provider actor
that resolves through connection `config.external_actor_mappings` to an
authorized `identity_type=human|operator` AIAT identity.  Integration users,
`AIAT_Agents`, certification actors, unknown actors, and projection echoes are
never human commands.  Evidence records preserve provider actor, resolved
identity, inbox ID, payload hash, provider version, mapping revision, and
operation.

The command carries `expected_canonical_revision` when the adapter receives a
structured command envelope; otherwise it resolves the latest durable mapping
observation.  Missing or stale revisions are conflicts.  Canonical writes use
the existing row-lock/CAS update and atomic outbox transaction, so concurrent,
reordered, delayed, and retried events cannot overwrite newer state.  Origin,
mapping, provider-version, revision, marker, and idempotency evidence suppress
projection echoes; actor names are not loop-prevention policy.

Rollback and the kill switch are lifecycle operations: an operator generates,
approves, and applies a persisted plan to return the binding to `READ_ONLY`.
Queued inbound work remains evidence and is not drained into canonical state;
outbound projection and reconciliation continue according to the effective
binding/connection policy.  No ACTIVE plan is generated by this certification.

## Documentation deliverables

- [Architecture decision record](PM_Platform_Integration_ADR.md) for AIAT
  canonical authority and the PM/SCM port split.
- [Provider contract and adapter-authoring guide](PM_Platform_Adapter_Authoring.md)
  with mapping and conformance requirements.
- [YouTrack setup](PM_Platform_YouTrack_Setup.md) and [GitHub setup](PM_Platform_GitHub_Setup.md),
  including least privilege, webhooks, custom fields, token rotation,
  attribution, and manual prerequisites.
- [Operator runbook](PM_Platform_Integration_Runbook.md) for plan/apply,
  adoption, reconciliation, conflicts, outages, dead letters, cutover,
  rollback, backups, and incident response.
- [Deployment/configuration reference](PM_Platform_Deployment.md),
  [dashboard operator guide](PM_Platform_Dashboard_Guide.md), and [live
  certification ledger](PM_Platform_Certification_Ledger.md).

## External constraints verified during planning

- YouTrack's free plan currently supports up to ten users; this is a deployment
  constraint, not an AIAT identity model. See [YouTrack pricing](https://www.jetbrains.com/youtrack/buy/).
- YouTrack permanent tokens inherit their owning account's permissions and have
  no automatic expiry. See [Manage Permanent Tokens](https://www.jetbrains.com/help/youtrack/cloud/manage-permanent-token.html).
- YouTrack's Webhook Triggers app sends a shared token header, so the receiver
  must validate it, use HTTPS, and support rotation. See [Webhook Triggers](https://www.jetbrains.com/help/youtrack/cloud/webhook-triggers.html).
- GitHub App installation tokens expire after one hour and can be scoped to
  repositories and permissions. See [GitHub App installation authentication](https://docs.github.com/en/enterprise-cloud%40latest/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-as-a-github-app-installation).
- GitHub webhook verification uses `X-Hub-Signature-256` over the raw payload;
  writing check runs requires a GitHub App with Checks write permission. See
  [GitHub webhook troubleshooting](https://docs.github.com/en/enterprise-cloud%40latest/webhooks/testing-and-troubleshooting-webhooks/troubleshooting-webhooks)
  and [GitHub checks API guidance](https://docs.github.com/en/rest/guides/using-the-rest-api-to-interact-with-checks).
