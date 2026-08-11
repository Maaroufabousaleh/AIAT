# Flow Node Schema and Topology Status

**Updated:** 2026-08-11
**Roadmap:** [AIAT Roadmap](../../ROADMAP.md)
**Owning feature:** [Projects, Flows, Knowledge, and Evidence](FEATURE_PROJECTS_FLOWS_AND_EVIDENCE.md)
**Scope:** personal/internal AIAT instance

## Reviewed implementation

Commit `9adcedf` adds the versioned `aiat.flow-node-schemas` catalogue and
integrates it with the canonical flow engine. The bounded core group includes:

- typed, UI-friendly field schemas for every runtime node type;
- schema-version validation with a backwards-compatible `1.0` default;
- typed field checks that preserve adapter extension fields;
- deterministic legacy `team_id`/`action` audit and explicit worker/profile
  migration helpers that never guess a worker ID;
- parallel branch, join arity, and switch case-to-edge topology validation;
  and
- the `aiat.flow-topology-check.v1` fixture covering valid and invalid graphs.

The generated JSON/TypeScript artefacts are committed in `4151e36`, and
`4777ee3` publishes the catalogue through the API and dashboard editors; see
[Flow Node Schema Generation Status](FLOW_NODE_SCHEMA_GENERATION_STATUS.md) for
the grouped generator, route, and editor evidence. Resource licence or
restriction notices remain
metadata only and never affect schema validation, migration, activation, or
execution.

## Verification evidence

From `mas/`:

```bash
uv run --isolated pytest \
  packages/mas-core/tests/test_flow_node_schema.py -q
uv run --isolated python scripts/check_flow_topology.py --json
```

The clean-checkout schema/topology fixtures pass. The combined flow,
worker-binding, watchdog, and recovery regression suite also passes after the
engine integration. The topology report records
`mutation_performed: false`, `worker_dispatch_performed: false`, and
`licence_metadata_is_gate: false`.

## Remaining gates

- run dashboard editor/browser evidence against the published contract; the
  full dashboard typecheck now passes after `fc4f0fa`;
- validate persisted definitions and migration paths against live storage; and
- complete live fan-out/join/switch recovery, native-Linux, and provider-owned
  certification.
