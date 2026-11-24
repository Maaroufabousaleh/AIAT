# PM integration dashboard guide

Open **Integrations** in the AIAT dashboard. The page is an operator view over
the generic orchestrator APIs, not a second provider authority.

- Connections show provider kind, lifecycle status, and revision; secrets are never rendered.
- Connection health, capabilities, and doctor checks run on demand from the diagnostics panel for the selected connection. Results are shown as returned, or as an honest error when a check fails.
- Connection bootstrap uses a digest-review plan/apply card mirroring the provisioning review. Applying never moves connection status; status changes require a governed lifecycle plan.
- Plan/apply shows the digest and requires an explicit confirmation.
- Bindings show project scope, direction, and shadow/read-only/active/draining state. There are no provider-switching controls in this view.
- Conflicts show reason, canonical/external snapshots, and resolution state.
- The outbox section lists due PENDING rows only; it has no cursor and no retry control. Reconciliation runs show attempt counts including drift and conflict totals. Shared dead-letter review is linked without claiming its entries are connection-attributed. SCM evidence is read-only and links delivery facts back to AIAT runs/work items where supplied.
- Provider spend is out of scope here; the reconciliation panel links to company budgets in Companies for cost review.

Use the runbook for migration, webhook, outage, cutover, and rollback actions.
Dashboard visibility is not proof that a live provider call succeeded; inspect
the delivery attempt and certification evidence.

## Lifecycle plan review

The **Governed lifecycle plans** panel reads persisted plans from
`integrations/lifecycle-plans` (via the dashboard `/api/integrations/pm/...` proxies). It displays the target, exact digest,
immutable operations, gates, expiry, and rollback operations. An operator must
check the exact-digest confirmation before approving; applying requires a
second explicit browser confirmation and the control plane rechecks all gates
and revisions. The page cannot alter a plan payload or write a binding or
connection status directly. A plan that is stale, expired, superseded, or
blocked remains unapplied and must be regenerated.

## ACTIVE command-boundary review

Before any ACTIVE plan is considered, show the persisted policy report:
direct priority/non-destructive status fields; approval-required
title/description/assignee and destructive transitions; evidence-only
comments; structured-comment approval requirements; and reserved
AIAT/governance fields. Display external actor mappings as identity metadata
only. The dashboard must never add an actor, calculate policy, or resolve a
provider actor from a free-form header.

For each inbound event, expose the authenticated inbox ID, provider actor,
resolved AIAT identity (when authorized), expected/current canonical revision,
provider version, mapping, content hash, origin/echo decision, conflict or
approval reference, and resulting outbox ID. Replays and reordered events link
to existing evidence rather than create a second canonical mutation.

The kill switch is a governed lifecycle plan back to `READ_ONLY`; it requires
the persisted plan digest and explicit operator confirmation. The dashboard
must not write binding/connection status directly. This certification leaves
the binding READ_ONLY and creates no ACTIVE transition.
