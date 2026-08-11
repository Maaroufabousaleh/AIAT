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
catalogue is schema version `1.0` with nine runtime node types. The API
publication and dashboard form-consumer wiring are a separate review group;
this commit establishes their deterministic shared input only.

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
```

The reproducibility test reads both checked-in artefacts and invokes the
generator in check mode. The broader flow/schema regression group and
`scripts/check_flow_topology.py --json` also pass; the topology fixture reports
no mutation, worker dispatch, or licence gate.

## Remaining gates

- review and commit the API schema route and dashboard form consumers as a
  separate bounded group;
- run the dashboard typecheck and focused editor/browser evidence against the
  generated catalogue;
- validate persisted definition/version migration against live storage; and
- complete live fan-out/join/switch recovery plus native-Linux and
  provider-owned certification.
