# ADR: AIAT canonical authority and provider-neutral PM/SCM ports

**Status:** Accepted
**Date:** 2026-07-28

## Decision

AIAT owns canonical projects, iterations, work items, approvals, agent/run
identity, evidence, and KPI history. External PM systems are governed
projections and human command surfaces; source-control systems provide delivery
evidence. Agents call generic AIAT tools and never receive provider credentials.

Work-management and source-control are separate ports. A connection selects a
provider adapter, capability profile, credential references, and explicit
project/repository bindings. Durable mappings, inbox/outbox events, delivery
attempts, conflicts, reconciliation runs, cutovers, and evidence records make
the provider replaceable without changing canonical tables or worker tools.

The YouTrack `AIAT` project represents the AIAT software project itself. Each
new canonical project defaults to a separate provider project and its own
`pm_project_binding`; the shared connection, restricted integration account,
credential references, webhook gateway, and header are reused. A separate
digest-bound provisioning plan generates the provider selector and attaches
the stable AIAT fields. Issue/comment webhook authentication, a successful
projection, and a successful reconciliation are mandatory evidence before a
binding can become `ACTIVE`. An umbrella issue-only mapping is an explicit
operator-selected profile, never an implicit fallback.

## Consequences

- Provider changes are a binding/cutover operation, not a rewrite of agents.
- Provider outages produce retries/dead letters and do not block canonical work.
- Human edits become validated commands; unknown or out-of-scope objects become
  operator-visible conflicts.
- A real provider, database migration, and permissions still require staging
  certification; fake tests are not production evidence.

## Rejected alternatives

- One YouTrack/GitHub account per AIAT agent: poor lifecycle, credential, and
  seat posture.
- Direct two-way provider synchronization: echo loops and split authority.
- Making GitHub PR/check state canonical: it bypasses AIAT approvals and
  cannot represent non-code work.
