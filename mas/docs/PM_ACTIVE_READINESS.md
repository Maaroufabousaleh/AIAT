# PM binding-wide ACTIVE readiness

This document describes the provider-to-canonical policy enforced by the PM
control plane. It applies to a binding only after both the connection and the
binding are `ACTIVE` (or both are `DRAINING` for controlled shutdown). A
`READ_ONLY` binding or a `SHADOW` connection retains provider changes as
authenticated evidence and does not mutate canonical issues.

## Enabled command policy

The normalized inbound command vocabulary is explicit and default-deny:

| Provider event or field | ACTIVE behavior |
| --- | --- |
| `issue.priority` values `low`, `medium`, `high`, `urgent`, `critical`, `normal` | Direct canonical CAS, subject to trusted actor scope and expected revision |
| `issue.status` values `backlog`, `in_progress`, `review`, `blocked` | Direct canonical CAS when authorized; `done` and `cancelled` require approval |
| `title`, `description`, `assigned_team`, `assigned_agent`, `assignee` | Approval-required proposal; no direct canonical mutation |
| Ordinary provider comments | Evidence-only; no canonical comment mutation |
| Structured `AIAT-COMMAND` comments | Approval-required and never implicit authority |
| Reserved identity, lifecycle, ownership, credential, project, or binding fields | Rejected as reserved mutations |
| Any other field, object type, deletion, or unsupported transition | Rejected as unsupported or requiring an operator decision |

The current certified mapping is narrower than the global policy: provider
actor `2-1` is trusted only for `issue.priority`. No actor or scope expansion
is implied by this readiness plan.

## Safety gates

An inbound command must pass authenticated webhook verification, connection
and binding scope, a durable mapping, immutable trusted actor resolution,
provider-version monotonicity, canonical revision CAS, field allowlisting, and
the applicable approval policy. Provider echo markers are acknowledged without
reprojection. The originating connection is excluded from its own outbound
projection to prevent loops.

Canonical mutation, command evidence, projection enqueue, accepted-command
count, and canary completion are committed in one database transaction. The
idempotency key and inbox delivery identity prevent duplicate replay.

## Kill switch and rollback

The kill switch is a governed lifecycle plan to move the binding to `DRAINING`
or `DISABLED`; for an emergency connection stop, a governed connection plan
may move the connection to `DISABLED`. Direct status patches and legacy
cutover/rollback endpoints cannot bypass a persisted approved lifecycle plan.

Rollback is another digest-bound lifecycle plan with a CAS against the current
connection/binding revision and an immutable lifecycle audit record. A binding
`ACTIVE` plan is invalid unless the connection is already `ACTIVE` and the
binding has authenticated issue/comment webhook, projection, reconciliation,
scope, and least-privilege evidence.

## Monitoring and alerts

Alert on any open PM conflict, active dead letter, pending/processing/failed
projection, reconciliation drift or scope conflict, stale provider version,
TLS verification failure, doctor blocker, repeated idempotency collision, or
unexpected lifecycle revision change. Review the immutable inbox, conflict,
outbox disposition, lifecycle audit, and integration-evidence records together.

## Latest governed rollout checkpoint

The latest bounded rollout was rolled back before any provider action because a
headed human browser session was unavailable or timed out. The connection
remains `ACTIVE` revision 2, while the binding is safely `READ_ONLY` revision 8.
The immutable
checkpoint and rollback evidence are recorded in
[PM_ACTIVE_CERTIFICATION_LEDGER.md](PM_ACTIVE_CERTIFICATION_LEDGER.md).
