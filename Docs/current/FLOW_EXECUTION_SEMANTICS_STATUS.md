# Flow Execution Semantics Status

**Updated:** 2026-08-18
**Roadmap:** [AIAT Roadmap](../../ROADMAP.md)
**Owning feature:** [Projects, Flows, Knowledge, and Evidence](FEATURE_PROJECTS_FLOWS_AND_EVIDENCE.md)
**Scope:** personal/internal AIAT instance

## Reviewed implementation

Commit `70fadf9` adds the deterministic `aiat.flow-execution-semantics.v1`
fixture and the corresponding traversal fixes in the canonical flow engine.
Commit `27247f4` adds the retained `aiat.flow-runtime-live.v1` certificate and
fixes two live defects: predecessor traversal now activates a switch node
before selecting its case, and cancellation clears stale active-node authority.
The bounded slice verifies:

- condition edges select only the matching true/false branch;
- parallel fan-out exposes both declared branches;
- a join waits until all incoming branches complete;
- a completed join is scheduled once and is never reactivated;
- switch routing selects only the case represented in context; and
- an unknown switch case blocks instead of selecting an arbitrary edge.

The deterministic checker runs only the in-memory flow engine. The live checker
uses the real local Compose API and Postgres with two disposable flows and
three disposable projects; it covers parallel fan-out, join waiting and
scheduling, switch activation/selection, terminal end handling, cancellation,
timeout/escalation, and safe retry. It retains scalar, payload-free,
secret-free evidence and verifies zero fixture residue. Resource licence or
restriction notices remain metadata only and are not part of traversal,
validation, activation, or execution decisions.

## Verification evidence

From `mas/`:

```bash
uv run --isolated pytest \
  packages/mas-core/tests/test_flow_execution_semantics.py -q
uv run --isolated python scripts/check_flow_execution_semantics.py --json
uv run --isolated python scripts/check_flow_runtime_live.py --live --confirm --json
```

The clean-checkout fixture reports `status: pass`,
`mutation_performed: false`, `worker_dispatch_performed: false`, and
`licence_metadata_is_gate: false`. The retained live certificate reports
`12/12` cases passed, `cleanup_verified: true`, and zero remaining flows or
projects; native watchdog and cold-crash status remain explicitly
`not_checked`. The scalar record is
[`flow_runtime_live_evidence.json`](../../mas/docs/provenance/flow_runtime_live_evidence.json).
The focused flow API, worker-binding, and watchdog regression suite also
passes after this engine change.

## Remaining gates

- prove duplicate scheduling and join recovery after a worker or process
  crash;
- exercise native watchdog, cold-crash, and worker-canary recovery; and
- complete native-Linux and provider-owned flow certification before release.
