# Flow Legacy-Task Migration Status

**Updated:** 2026-08-11
**Roadmap:** [AIAT Roadmap](../../ROADMAP.md)
**Owning feature:** [Projects, Flows, Knowledge, and Evidence](FEATURE_PROJECTS_FLOWS_AND_EVIDENCE.md)
**Scope:** personal/internal AIAT instance

## Reviewed implementation

Commit `54ad710` adds the bounded migration surface for saved flow definitions
that still contain compatibility task aliases:

- `POST /flows/{flow_id}/migrate-legacy-tasks` supports a non-mutating dry run
  and an operator-approved immutable version migration;
- every task that needs execution must receive an explicit UUID `worker_id`;
  the API never infers a worker from `team_id`;
- `action` is normalized to `task_type` only when needed, deprecated aliases
  are removed, and an omitted model declaration becomes node-level
  `model_mode: none` so worker policy remains authoritative;
- the source flow remains unchanged; the created version records
  `aiat.flow-legacy-task-migration.v1` metadata with before/after findings,
  bindings, actor, and source version;
- the dashboard proxy and flow store expose the same operator boundary without
  adding a second migration authority; and
- flow dry-run responses expose deterministic compatibility findings and the
  concrete worker-binding recommendation before any saved definition changes.

Resource licence/restriction values remain provenance metadata only. They are
not used to reject a dry run, remove a binding, block an import, or authorize a
worker.

## Verification evidence

From `mas/`:

```bash
PYTHONPATH=packages/mas-api-sdk uv run --isolated pytest \
  apps/orchestrator-api/tests/test_flow_legacy_migration.py \
  apps/orchestrator-api/tests/test_flow_dry_run.py -k 'legacy or dry_run' \
  packages/mas-core/tests/test_flow_node_schema.py -q

PYTHONPATH=packages/mas-api-sdk uv run --isolated pytest \
  apps/orchestrator-api/tests/test_flows.py -k \
  'flow_node_schema or template or lifecycle or migration or graph_rewrite or create_flow_version' \
  apps/orchestrator-api/tests/test_flow_definition_lifecycle_api.py -q
```

The reviewed focused group passes 19 tests, the broader flow lifecycle subset
passes, and `scripts/check_api_contract.py --json` remains green. Dashboard
typecheck is currently stopped by the unrelated pre-existing `ErrorBanner`
prop error in the project-evidence page; no migration route error is reported.

## Remaining gates

- review and apply bindings to each existing legacy flow as an operator action;
- publish the resulting immutable versions only after the normal flow checks;
- prove live worker canary, rollback, and recovery behavior; and
- add a browser-facing migration form once the dashboard-wide typecheck gate is
  repaired.
