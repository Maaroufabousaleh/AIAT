# YouTrack setup

Create a restricted integration account for each AIAT connection. Do not create
accounts for AIAT agents. This account is intentionally allowed to own AIAT's
YouTrack projects: assign the global `Observer` and `Project Creator` roles,
and `Project Admin` on existing AIAT-managed projects. YouTrack automatically
grants the creating user `Project Admin` on a newly created project; keep that
ownership invariant in the certification evidence.

Do not assign `System Admin`, `User Manager`, `Low-level Admin Read/Write`,
organization administration, authentication administration, global app
administration, or `Delete Project`. Normal automation archives/deactivates a
project. Permanent deletion is an explicit operator-approval operation and is
not part of the provider adapter.

Create a permanent-token credential in the AIAT credential manager and put its
reference in `pm_connections.credential_ref`. Put only non-secret selectors in
`config`, for example the connection-level `project_id`, `webhook_header`, and
`webhook_secret_ref`/`webhook_secret_refs`.
Attach a redacted `permission_evidence` object containing the live role/deny
probe results; the doctor will not treat an absent snapshot as certified.
Configure the Webhook Triggers app to call `pm-gateway` over HTTPS and rotate
current/previous secret references during overlap.

Run the connection-level `plan`, inspect blockers and the digest, then `apply`
the exact plan with `confirm=true`. The `AIAT` provider project represents the
AIAT software project itself; it is not an umbrella for future canonical
projects. For each new canonical project, run its own
`/projects/{id}/pm-provisioning/plan` with the default `dedicated_project`
profile. That plan generates a unique valid short name, adopts/creates the
provider project, attaches the four stable fields, and creates one binding
using the same connection and credential references. `umbrella_issues` is an
explicit operator opt-in only.

If Webhook Triggers cannot be attached automatically, the project plan records
a blocking manual action and leaves its binding `SHADOW` or `DISABLED`. Run
authenticated issue/comment webhook tests, drain projections, and reconcile
before attempting cutover; storage refuses `ACTIVE` until all three evidence
classes are present. Keep the account separate from a daily operator and
break-glass administrator where the deployment's seat policy permits.

The adapter writes concise AIAT attribution in comments; complete actor/run/
approval/evidence data remains in AIAT. Live permission-denial, project-create/
ownership, project-archive, and webhook-rotation checks are required before
`ACTIVE`.
