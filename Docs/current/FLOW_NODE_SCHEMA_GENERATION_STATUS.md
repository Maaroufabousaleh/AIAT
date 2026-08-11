# Flow Node Schema Generation Status

**Updated:** 2026-08-11
**Roadmap:** [AIAT Roadmap](../../ROADMAP.md)
**Related review:** [Flow Node Schema and Topology Status](FLOW_NODE_SCHEMA_TOPOLOGY_STATUS.md)
**Owning feature:** [Projects, Flows, Knowledge, and Evidence](FEATURE_PROJECTS_FLOWS_AND_EVIDENCE.md)
**Scope:** personal/internal AIAT instance

## Reviewed implementation

Commit `4151e36` adds the reproducible generated-artifact group for the
versioned `aiat.flow-node-schemas` catalogue introduced by `9adcedf`:

- `mas/scripts/generate_flow_node_schemas.py` is the source-driven generator;
- `mas/schemas/workflow/flow_nodes.v1.json` is the language-neutral checked-in
  contract; and
- `mas/apps/mas-dashboard/lib/generated/flow-node-schemas.ts` is the typed,
  dashboard-consumer catalogue generated from the same core source.

The generator supports `--write` for intentional refreshes and `--check` for
stale-artifact detection. The generated files are not hand-edited. The current
catalogue is schema version `1.0` with nine runtime node types. Commit
`4777ee3` publishes the catalogue through `/flows/node-schemas` and connects
both dashboard flow editors to the generated contract, including typed fields,
governed worker/Model Profile selectors, and a collapsed legacy compatibility
surface.

Resource licence or restriction notices remain metadata only. They do not
prevent generation, schema publication, dashboard loading, migration,
activation, or execution.

## Verification evidence

From `mas/`:

```bash
uv run --isolated python scripts/generate_flow_node_schemas.py --check
uv run --isolated pytest \
  packages/mas-core/tests/test_flow_node_schema_generation.py \
  packages/mas-core/tests/test_flow_node_schema.py -q
uv run --isolated pytest \
  apps/orchestrator-api/tests/test_flow_node_schema_api.py -q
```

The reproducibility test reads both checked-in artefacts and invokes the
generator in check mode. The broader flow/schema regression group and
API route test pass. Focused dashboard ESLint also passes. The full dashboard
typecheck passes after the project-evidence error-state repair (`fc4f0fa`); it
is not part of this schema group. The topology fixture reports no mutation, worker
dispatch, or licence gate.

## Remaining gates

- run the dashboard typecheck and focused editor/browser evidence against the
  generated catalogue;
- validate persisted definition/version migration against live storage; and
- complete live fan-out/join/switch recovery plus native-Linux and
  provider-owned certification.
