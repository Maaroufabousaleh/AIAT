# AIAT PM integration operator runbook

The canonical architecture and scope are defined in
[`PM_Platform_Integration_Plan.md`](PM_Platform_Integration_Plan.md). This
runbook is the repeatable setup path for a new connection; it is intentionally
safe to run again after a partial failure.

Reference documents: [ADR](PM_Platform_Integration_ADR.md), [adapter
authoring](PM_Platform_Adapter_Authoring.md), [YouTrack setup](PM_Platform_YouTrack_Setup.md),
[GitHub setup](PM_Platform_GitHub_Setup.md), [deployment reference](PM_Platform_Deployment.md),
[dashboard guide](PM_Platform_Dashboard_Guide.md), and [certification
ledger](PM_Platform_Certification_Ledger.md).

## Deployment preflight

Run the migrations from `mas/` with the production database URL before
creating any integration connection or project binding. Verify the revision
first, then apply the head:

```powershell
uv run alembic current
uv run alembic upgrade head
```

Capture the migration output in the deployment evidence bundle. Do not use a
downgrade as routine cleanup: revisions `0024_pm_provider_control_plane`,
`0025_pm_project_provisioning_lifecycle`, and
`0026_pm_lifecycle_transition_plans` own the integration tables, activation
evidence, and transition audit history, so rollback requires an approved
database backup and a planned provider drain.

## One-shot connection flow

1. Create the provider credential in the AIAT credential manager. Store only a
   credential reference in the PM connection; never put a token, private key,
   webhook secret, or installation token in `config`.
2. Create a connection with a provider-specific selector. The initial state is
   `DISABLED` and the API rejects URLs containing credentials, query strings, or
   fragments.
3. Run `plan`, inspect the returned digest and blockers, then apply the exact
   digest. `apply` never creates users, changes billing, widens permissions, or
   deletes provider data.
4. Run `doctor`. Fix every connection blocker before provisioning a canonical
   project.
5. For each canonical project, call
   `POST /projects/{project_id}/pm-provisioning/plan`. The default
   `dedicated_project` profile adopts/creates a separate provider project and
   generates its unique short name. Approve that exact project-plan digest.
6. Apply only that exact project plan with `confirm=true`. The safe portion
   attaches the four stable fields and creates one `SHADOW`/`DISABLED` binding.
   Manual Webhook Triggers attachment remains a persisted blocker when the
   restricted account cannot perform it.
   Use the binding phase endpoint to move a safe `DISABLED` binding to
   `SHADOW`; it cannot move to `ACTIVE` by API metadata alone.
7. Drain the outbox and run `reconcile`. Verify authenticated issue and
   comment webhooks, successful projection, and successful reconciliation.
   Unknown external objects remain conflicts until an operator maps or ignores
   them.
8. Generate a persisted lifecycle transition plan for every connection or
   binding state change. Review its exact digest and blockers, approve it as an
   authenticated operator, then apply the exact digest with explicit
   confirmation. The storage gate refuses `ACTIVE` until all three evidence
   classes are present. Do not use the `umbrella_issues` profile unless an
   operator explicitly selected it.

## Governed lifecycle transition flow

The old cutover/rollback aliases intentionally fail closed; they cannot write
status directly. Use the versioned operator API instead:

```powershell
$plan = Invoke-RestMethod http://localhost:8000/api/v1/integrations/lifecycle-plans `
  -Method Post -Headers $headers -ContentType application/json -Body (@{
    target_type = "pm_binding"
    connection_id = $connection.id
    binding_id = $binding.id
    desired_binding_status = "READ_ONLY"
    ttl_seconds = 3600
  } | ConvertTo-Json)

# Inspect $plan.plan, $plan.plan_digest, $plan.blockers and $plan.plan.rollback_operations.
Invoke-RestMethod "http://localhost:8000/api/v1/integrations/lifecycle-plans/$($plan.plan.plan_id)/approve" `
  -Method Post -Headers $headers -ContentType application/json -Body (@{
    plan_digest = $plan.plan_digest
    reason = "operator reviewed exact lifecycle digest"
  } | ConvertTo-Json)

Invoke-RestMethod "http://localhost:8000/api/v1/integrations/lifecycle-plans/$($plan.plan.plan_id)/apply" `
  -Method Post -Headers $headers -ContentType application/json -Body (@{
    plan_digest = $plan.plan_digest
    confirm = $true
  } | ConvertTo-Json)
```

`GET /api/v1/integrations/lifecycle-plans/{plan_id}` must report
`digest_valid=true` before approval or application. Plans are durable rows,
not dashboard previews; statuses include `PLANNED`, `APPROVED`, `APPLIED`,
`REJECTED`, `EXPIRED`, `STALE`, `SUPERSEDED`, and `FAILED`. Application audit
records include the authenticated actor, approval reference, before/after
states, revisions, evidence references, transaction ID, and rollback
operation. A stale or newly blocked plan is rejected without changing state.

Example (replace IDs and values; the API key is never a provider credential):

```powershell
$headers = @{ "X-API-Key" = $env:AIAT_OPERATOR_API_KEY; "X-AIAT-Actor-Role" = "operator" }
$connection = Invoke-RestMethod http://localhost:8000/integrations/connections `
  -Method Post -Headers $headers -ContentType application/json -Body (@{
    provider_kind = "youtrack"
    display_name = "YouTrack primary"
    base_url = "https://youtrack.example.com"
    credential_ref = "youtrack-aiat-token"
    capability_profile = "pm"
    config = @{
      project_id = "0-0"
      permission_evidence = @{
        global_roles = @("Observer", "Project Creator")
        global_permissions = @("Create Project")
        project_roles = @{ "0-0" = @("Project Admin") }
        created_project_ownership = $true
      }
      webhook_secret_ref = "youtrack-webhook-current"
    }
  } | ConvertTo-Json -Depth 8)

$plan = Invoke-RestMethod "http://localhost:8000/integrations/connections/$($connection.id)/plan" `
  -Method Post -Headers $headers -ContentType application/json -Body (@{
    desired = @{ project_id = "0-0"; webhook_header = "X-YouTrack-Token" }
  } | ConvertTo-Json -Depth 8)

Invoke-RestMethod "http://localhost:8000/integrations/connections/$($connection.id)/apply" `
  -Method Post -Headers $headers -ContentType application/json -Body (@{
    plan = $plan.plan
    plan_digest = $plan.plan_digest
    confirm = $true
  } | ConvertTo-Json -Depth 20)

Invoke-RestMethod "http://localhost:8000/integrations/connections/$($connection.id)/doctor" `
  -Headers $headers
```

For a new canonical project, keep the connection-level bootstrap digest
separate from the project digest:

```powershell
$projectPlan = Invoke-RestMethod "http://localhost:8000/projects/$projectId/pm-provisioning/plan" `
  -Method Post -Headers $headers -ContentType application/json -Body (@{
    connection_id = $connection.id
    mapping_profile = "dedicated_project"
  } | ConvertTo-Json)

# Inspect and approve $projectPlan.plan_digest out of band.
Invoke-RestMethod "http://localhost:8000/projects/$projectId/pm-provisioning/apply" `
  -Method Post -Headers $headers -ContentType application/json -Body (@{
    plan = $projectPlan.plan
    plan_digest = $projectPlan.plan_digest
    confirm = $true
  } | ConvertTo-Json -Depth 30)
```

If the provider cannot attach Webhook Triggers with the approved permissions,
the apply response contains a manual-action blocker and leaves the binding
`SHADOW`/`DISABLED`. Never substitute the original connection bootstrap digest
for this project-specific digest.

For GitHub, use `provider_kind = "github"`, a `credential_ref` that resolves to
the installation token/JWT broker secret, and `config.repository = "owner/name"`.
Install one GitHub App only on the selected repositories. Use `pm`, `delivery`,
or `checks` profiles; the adapter rejects unknown profiles.

## Webhooks and gateway

Point provider webhooks at `pm-gateway` (`/webhooks/{connection_id}`), not at a
worker or provider adapter. The gateway forwards the raw bytes and an allowlist
of delivery/signature headers to the orchestrator. It does not parse provider
JSON, store provider secrets, or write canonical state. The orchestrator verifies
the signature/token before parsing, persists the inbox event, deduplicates the
delivery ID, and applies only a mapped, scope-checked command.

In production set all three API keys to high-entropy, distinct values, use
HTTPS at the edge, and configure `PM_GATEWAY_ENVIRONMENT=production`. Rotate
provider webhook secrets by accepting the current and previous reference
during the overlap window; rotate AIAT credentials through the credential
manager.

## Outage, conflicts, and dead letters

- `GET /integrations/outbox` shows pending events. The bounded gateway drain
  claims rows with a database lock and records every attempt. Failures use
  exponential backoff and move to `DEAD_LETTER` after five attempts.
- `GET /integrations/conflicts` shows unknown mappings, stale revisions,
  unsupported objects, and scope violations. Resolve with the conflict endpoint;
  forensic snapshots are retained.
- `POST /integrations/connections/{id}/reconcile` is read/compare first. It
  records a reconciliation run and proposes conflicts; it never guesses a
  canonical project or deletes external data.
- Rollback is another lifecycle plan with the previous state as its explicit
  desired state. Retain mappings and provider resources; use archive/deactivate
  for normal provider cleanup. Permanent deletion needs a separate explicit
  operator approval path.

## Adapter authoring checklist

Implement the shared `WorkManagementProvider` or `SourceControlProvider` port,
return explicit capability flags, use `ProviderHTTP` with the injected
credential resolver, validate provider URLs and selectors, preserve raw webhook
bytes, and add fake-provider contract tests for idempotency, pagination,
signature failure, retries, and unsupported capabilities. Register a new
adapter with `ProviderRegistry.register`; no agent tool or canonical schema
change should be required.

Before production activation, attach evidence for least privilege, scope,
secret rotation, webhook authentication, reconciliation, outage/dead-letter,
cutover, rollback, and live create/update/comment/link/iteration checks. Fixture
or fake-provider success is not live certification.

## ACTIVE inbound-command readiness (no activation)

Before generating an ACTIVE lifecycle plan, review the persisted command policy
in the control-plane source and the `active_inbound_command_policy` doctor
check.  The allowlist is deliberately narrow: priority values and non-
destructive status values (`backlog`, `in_progress`, `review`, `blocked`) may
be direct commands; title, description, assignee/reassignment, closing,
deletion, escalation, and destructive status values require an operator
approval proposal.  Ordinary provider comments, comment edits, and comment
deletes are evidence-only.  A structured `AIAT-COMMAND: {...}` comment is
approval-required, never self-authorizing.  Identity, managed/revision,
ownership, project, governance, credential, binding, and lifecycle fields are
reserved and rejected.

For every direct command, verify all of the following before accepting it:

1. The webhook was authenticated and the inbox row is durable.
2. The external object resolves to the binding's project/repository selector.
3. The provider actor is mapped in `external_actor_mappings` to an authorized
   human/operator AIAT identity.  `AIAT_Agents`, integration users, synthetic
   certification actors, and unknown actors fail closed.
4. The expected canonical revision is present or resolved from durable mapping
   state and equals the current issue revision.
5. The field/value is in the allowlist.  Compare-and-swap updates the issue and
   its projection outbox atomically; a stale or concurrent event becomes a
   retained conflict.

The actor evidence stores both provider and AIAT identities.  Provider echo
suppression uses origin, mapping, provider version, revision marker, content,
and idempotency data; do not add actor-name exceptions.  Replaying a delivery
must return the existing inbox/evidence result without another canonical
revision, mapping, or outbox event.

To stop inbound mutation, use the governed lifecycle-plan API to return the
binding to `READ_ONLY`; do not call a direct status update.  This retains the
inbox and forensic snapshots, stops inbound canonical commands, and leaves
outbound behavior governed by the connection/binding effective policy.  After
rollback, drain only approved outbound work and run full reconciliation.

The bounded canary is intentionally not run here.  If later approved, use
only mapped AIAT-3 (or a newly mapped certification issue), one authorized
human actor, one allowed priority/status change carrying the expected revision,
and one replay.  Success requires exactly one canonical revision advance, one
idempotent outbox event, one provider echo, zero loop/conflict/drift, and a
documented rollback trigger.  No title/description/assignee/delete/comment
mutation is in the canary.
