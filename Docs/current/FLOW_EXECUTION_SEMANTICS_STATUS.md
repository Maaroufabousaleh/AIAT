# Flow Execution Semantics Status

**Updated:** 2026-08-11
**Roadmap:** [AIAT Roadmap](../../ROADMAP.md)
**Owning feature:** [Projects, Flows, Knowledge, and Evidence](FEATURE_PROJECTS_FLOWS_AND_EVIDENCE.md)
**Scope:** personal/internal AIAT instance

## Reviewed implementation

Commit `70fadf9` adds the deterministic `aiat.flow-execution-semantics.v1`
fixture and the corresponding traversal fixes in the canonical flow engine.
The bounded slice verifies:

- condition edges select only the matching true/false branch;
- parallel fan-out exposes both declared branches;
- a join waits until all incoming branches complete;
- a completed join is scheduled once and is never reactivated;
- switch routing selects only the case represented in context; and
- an unknown switch case blocks instead of selecting an arbitrary edge.

The checker runs only the in-memory flow engine. It does not create a project,
start a flow instance, dispatch a worker, call a provider, or mutate storage.
Resource licence or restriction notices remain metadata only and are not part
of traversal, validation, activation, or execution decisions.

## Verification evidence

From `mas/`:

```bash
uv run --isolated pytest \
  packages/mas-core/tests/test_flow_execution_semantics.py -q
uv run --isolated python scripts/check_flow_execution_semantics.py --json
```

The clean-checkout fixture reports `status: pass`,
`mutation_performed: false`, `worker_dispatch_performed: false`, and
`licence_metadata_is_gate: false`. The focused flow API, worker-binding, and
watchdog regression suite also passes after this engine change.

## Remaining gates

- exercise parallel/join and switch synchronization against persisted live
  flow instances;
- prove duplicate scheduling and join recovery after a worker or process
  crash;
- retain live fan-out/recovery evidence in the release ledger; and
- complete native-Linux and provider-owned flow certification before release.
