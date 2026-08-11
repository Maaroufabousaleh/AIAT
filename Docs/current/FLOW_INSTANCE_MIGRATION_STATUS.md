# Flow Instance Migration Status

**Updated:** 2026-08-11
**Roadmap:** [AIAT Roadmap](../../ROADMAP.md)
**Owning feature:** [Projects, Flows, Knowledge, and Evidence](FEATURE_PROJECTS_FLOWS_AND_EVIDENCE.md)
**Scope:** personal/internal AIAT instance

## Reviewed implementation

Commit `67ed704` adds an evidence-preserving migration path for running flow
instances:

- `POST /flows/instances/{instance_id}/migrate` accepts a target immutable
  flow version and rejects terminal instances;
- compatible migration requires matching flow node-schema versions and keeps
  every active node ID and node type present in the target definition;
- graph rewrites are opt-in and require a complete, one-to-one mapping for
  the currently active nodes; incomplete, unknown, duplicate, removed, or
  type-changing mappings are rejected without storage mutation;
- `AgentStorage.migrate_flow_instance` updates the pinned flow/version and
  active-node projection while retaining all historical node executions;
- the migration record is bounded and includes source/target versions,
  active-node mapping, actor, and rewrite state in instance context and
  project history; and
- the dashboard proxy at
  `app/api/flows/instances/[id]/migrate` forwards the same operator action.

The route deliberately does not use the older unrestricted switch operation:
switching may be useful for recovery, while migration proves compatibility and
preserves evidence for a running instance.

Resource licence or restriction notices remain metadata only. They may be
displayed with the flow provenance, but they do not block migration, reject a
target definition, or change worker/execution authorization.

## Verification evidence

From `mas/`:

```bash
uv run --isolated pytest \
  apps/orchestrator-api/tests/test_flow_instance_migration_api.py -q
```

The focused API fixture passes under both configured async backends and covers
compatible migration, removed active nodes, explicit graph rewrite mapping,
and terminal-instance rejection. The implementation group was reviewed with
`git diff --cached --check` before commit.

## Remaining gates

- add a live Postgres test that proves the update is atomic and historical
  `flow_node_executions` rows remain unchanged;
- add recovery/watchdog coordination so an in-flight worker acknowledges the
  new active-node projection before dispatch continues;
- expose a dashboard confirmation/review surface with before/after graph
  diff and human actor identity; and
- add migration rollback/rehearsal evidence to the release ledger and the
  flow recovery runbook.
