# YouTrack bootstrap plan (approval artifact)

This is the server-generated, digest-bound plan for the live AIAT YouTrack
connection. It is intentionally a planning artifact: no YouTrack project,
custom field, webhook, or AIAT binding was created when this file was written.

## Approval identity

| Field | Value |
| --- | --- |
| Provider | `youtrack` |
| Connection ID | `1b699f09-06c7-4a16-a4f3-9c1aaf69d6e2` |
| Connection state | `DISABLED` |
| Base URL | `https://aiat.youtrack.cloud` |
| Credential reference | `youtrack-aiat-token` |
| Webhook secret reference | `youtrack-webhook-current` |
| Webhook header | `X-YouTrack-Token` |
| Public gateway | `https://pm-gateway.aiat.ca` |
| Plan ID | `8a8c7326-6dd6-40c7-8139-74b8fd437680` |
| Plan digest | `40712b0e566cb96929dd886e2de971a5b11b49693445f270fd35141351b43627` |
| Generated at (UTC) | `2026-07-29T01:53:12.743747Z` |
| Server `ready_to_apply` | `true` |

Approve exactly the digest above. The previously reported plan
`8ec4975c-11ff-4fa8-8a0f-79bcdcea69df` / digest
`3809319bd0d6cd41da6355cfa513f6304e788cc461ef0f0814cae45ca72a4ba0` was not
applied and is superseded. Do not regenerate this plan before applying;
regeneration creates a new plan ID and therefore a new digest.

## Desired provider resources

- Read-only discovery found zero YouTrack projects.
- Create project `AIAT` (`short_name=AIAT`); the provider-assigned numeric ID
  is intentionally unknown until an approved create/projection step runs.
- The integration identity is the creator and receives automatic `Project
  Admin` ownership.
- Adopt compatible fields by immutable name or create them:
  `AIAT Object ID` (string), `AIAT Object Type` (string), `AIAT Revision`
  (integer), and `AIAT Managed` (string).
- Configure a manual Webhook Triggers entry with events `issue` and `comment`
  and header `X-YouTrack-Token`.

## Webhook endpoint

`https://pm-gateway.aiat.ca/webhooks/1b699f09-06c7-4a16-a4f3-9c1aaf69d6e2`

The gateway forwards the raw request to the orchestrator. The managed
`youtrack-webhook-current` reference is resolved only at the orchestrator
verification boundary.

## Mapping and validation policy

- Mapping profile: `default`; no binding or mapping rows exist yet.
- AIAT remains canonical for projects, sprints, issues, comments, links,
  assignments, revisions, approvals, and agent/run/evidence metadata.
- Provider IDs, provider versions, content hashes, delivery IDs, and import /
  export revisions are persisted in the AIAT mapping and inbox ledgers after
  an approved binding is created.
- Validation gates: Global Observer; Global Project Creator; `Create Project`;
  Project Admin on existing AIAT-managed projects; automatic Project Admin
  ownership on created projects; forbidden global administration denied;
  project deletion denied (archive/deactivation only); restricted identity;
  REST read/write; webhook token; mapping uniqueness; provider health and
  capability discovery; scope; reconciliation; and conflict-free cutover.

## Rollback

1. Disable the binding.
2. Disable the webhook.
3. Archive/deactivate provider projects.
4. Retain external resources and mappings for operator review.

Permanent project deletion is not automated and requires an explicit operator
approval path.

## Exact server plan

```json
{
  "actions": [
    {
      "action": "create_project",
      "current": null,
      "desired": {
        "name": "AIAT",
        "owner": "integration_user",
        "project_admin": true,
        "short_name": "AIAT"
      },
      "destructive": false,
      "manual": false,
      "reason": "Create Project grants the integration user automatic Project Admin ownership",
      "resource": "youtrack:project:AIAT"
    },
    {
      "action": "adopt_or_create_field",
      "current": null,
      "desired": {"name": "AIAT Object ID", "type": "string"},
      "destructive": false,
      "manual": false,
      "reason": "stable AIAT mapping marker; compatible fields are adopted by name",
      "resource": "youtrack:field:AIAT Object ID"
    },
    {
      "action": "adopt_or_create_field",
      "current": null,
      "desired": {"name": "AIAT Object Type", "type": "string"},
      "destructive": false,
      "manual": false,
      "reason": "stable AIAT mapping marker; compatible fields are adopted by name",
      "resource": "youtrack:field:AIAT Object Type"
    },
    {
      "action": "adopt_or_create_field",
      "current": null,
      "desired": {"name": "AIAT Revision", "type": "integer"},
      "destructive": false,
      "manual": false,
      "reason": "stable AIAT mapping marker; compatible fields are adopted by name",
      "resource": "youtrack:field:AIAT Revision"
    },
    {
      "action": "adopt_or_create_field",
      "current": null,
      "desired": {"name": "AIAT Managed", "type": "string"},
      "destructive": false,
      "manual": false,
      "reason": "stable AIAT mapping marker; compatible fields are adopted by name",
      "resource": "youtrack:field:AIAT Managed"
    },
    {
      "action": "configure_webhook",
      "current": null,
      "desired": {"events": ["issue", "comment"], "header": "X-YouTrack-Token"},
      "destructive": false,
      "manual": true,
      "reason": "Operator configures Webhook Triggers; the runtime identity only validates its token",
      "resource": "youtrack:webhook:unselected"
    }
  ],
  "blockers": [],
  "checks": [
    "Global Observer",
    "Global Project Creator",
    "Create Project",
    "Project Admin on existing AIAT-managed projects",
    "automatic Project Admin ownership on integration-created projects",
    "forbidden global administration denied",
    "project deletion denied; archive/deactivation only",
    "restricted integration identity",
    "REST read/write",
    "webhook token",
    "mapping uniqueness"
  ],
  "connection_id": "1b699f09-06c7-4a16-a4f3-9c1aaf69d6e2",
  "generated_at": "2026-07-29T01:53:12.743747Z",
  "plan_id": "8a8c7326-6dd6-40c7-8139-74b8fd437680",
  "provider_kind": "youtrack",
  "rollback_actions": [
    "disable binding",
    "disable webhook",
    "archive/deactivate provider projects",
    "retain external resources"
  ]
}
```

## Live blockers and deployment follow-up

The server plan has no provider blockers. The doctor reports
`no project binding configured`, which is expected and intentional before
apply. Webhook configuration, a `SHADOW` binding, reconciliation, and cutover
remain gated operations.

The disabled connection contains only the supplied credential references and
webhook header. The stale gateway reference injection was corrected and both
services now receive the required YouTrack names; the orchestrator runs with
`MAS_ENVIRONMENT=production`. Existing credential rows were verified under the
development fallback and transactionally re-encrypted under the configured
persistent production key; no key material is recorded here. Migration
`0024_pm_provider_control_plane` was verified at the database head before this
future-project lifecycle revision; `0025_pm_project_provisioning_lifecycle`
must be applied before any new binding activation.

The local host performs TLS inspection, so the orchestrator was recreated with
an ignored, host-provided CA bundle mounted read-only. A normal production host
must provide its own trusted CA chain; disabling TLS verification is not an
acceptable fallback. The post-fix doctor passes provider health, credentials,
configuration, scope discovery, capabilities, least privilege, and webhook
reference checks. Its only blocker is the intentional absence of a project
binding before approval.

## Application record

The approved digest was applied idempotently on 2026-07-28. The first apply
created the project and all four approved project custom fields; subsequent
digest-bound retries adopted the same five resources and created no duplicates.
The connection remains `DISABLED`, no project binding was created, and no
YouTrack webhook was created.

| Resource | Provider ID | Result | Type |
| --- | --- | --- | --- |
| Project `AIAT` | `0-1` | created, then adopted on retry | project |
| `AIAT Object ID` | project `575-0`; global `161-11` | created, then adopted on retry | string |
| `AIAT Object Type` | project `575-1`; global `161-12` | created, then adopted on retry | string |
| `AIAT Revision` | project `575-2`; global `161-13` | created, then adopted on retry | integer |
| `AIAT Managed` | project `575-3`; global `161-14` | created, then adopted on retry | string |

The final doctor result is `ready=false` only because `no project binding
configured`; health and credentials, capabilities, configuration,
least-privilege, provider scope, and webhook secret reference checks pass.
The manual webhook remains gated: configure `issue` and `comment` events at
`https://pm-gateway.aiat.ca/webhooks/1b699f09-06c7-4a16-a4f3-9c1aaf69d6e2`,
using header `X-YouTrack-Token` and the managed
`youtrack-webhook-current` secret reference. Do not create a binding or change
the connection status until the next approved plan.

## Future canonical-project provisioning (plan revision)

Inspection of the implementation found that the connection bootstrap above
does not and must not provision future canonical projects. The `AIAT` project
(`0-1`) represents the AIAT software project itself. A new canonical project
now records `dedicated_project` intent and must use its own
`/projects/{project_id}/pm-provisioning/plan` and exact digest before provider
side effects are allowed.

The project-scoped plan adopts or creates a unique valid YouTrack short name,
attaches `AIAT Object ID`, `AIAT Object Type`, `AIAT Revision`, and `AIAT
Managed`, and creates one binding reusing this connection, credential
references, webhook header, secret reference, and gateway. `umbrella_issues`
is an explicit operator-selected exception only. Webhook Triggers attachment
is a blocking manual action when the restricted integration account cannot
perform it; the resulting binding stays `SHADOW` or `DISABLED`.

Migration `0025_pm_project_provisioning_lifecycle` adds durable provisioning
state and activation evidence. `ACTIVE` is refused until authenticated issue
and comment webhook evidence, a successful projection, and a successful
reconciliation are all present. This lifecycle revision changes the future
project plan surface; the previously approved connection digest
`40712b0e566cb96929dd886e2de971a5b11b49693445f270fd35141351b43627` is not a
project-provisioning approval and must not be reused for a new project.
