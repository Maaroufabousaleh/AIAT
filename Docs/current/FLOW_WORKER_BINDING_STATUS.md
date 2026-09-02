# Governed Flow Worker-Binding Status

**Updated:** 2026-08-11
**Roadmap:** [AIAT Roadmap](../../ROADMAP.md)
**Owning feature:** [Projects, Flows, Knowledge, and Evidence](FEATURE_PROJECTS_FLOWS_AND_EVIDENCE.md)
**Scope:** personal/internal AIAT instance

## Reviewed implementation

This slice is split into two reviewed commits:

- `82e5fa3` adds the public `aiat.flow-worker-binding.v1` contract, the
  copy-on-write context helpers, terminal/non-terminal state classifier, and
  deterministic checker/test;
- `7318e75` wires that contract into the governed `flow_node_action` and retry
  routes.

The runtime behavior is now explicit:

- `QUEUED`, `CLAIMED`, `READY`, `DISPATCHING`, `RUNNING`, pause/resume states,
  and other declared non-terminal states keep the task active and persist its
  authoritative Worker Run ID in `context_json.active_worker_runs`;
- `SUCCEEDED`, `FAILED`, `CANCELLED`, and `TIMED_OUT` are terminal outcomes;
  terminal actions must reference the Worker Run bound to the active node
  execution;
- unknown states fail closed instead of being treated as success or failure;
- settling one task removes only that node's binding, preserving parallel
  branch bindings; and
- safe retry marks prior node executions `SUPERSEDED` rather than deleting
  evidence, then re-enters governed Worker Run dispatch for worker-backed
  nodes.

Worker Run state is authoritative for governed task settlement. Resource
licence or restriction notices remain metadata only and do not affect
discovery, installation, activation, dispatch, or execution.

## Verification evidence

From `mas/`:

```bash
uv run --isolated pytest \
  packages/mas-core/tests/test_flow_worker_binding.py \
  apps/orchestrator-api/tests/test_flows.py -q
uv run --isolated python scripts/check_flow_worker_binding.py --json
```

The deterministic checker reports schema `aiat.flow-worker-binding.v1`,
`storage: false`, `worker_dispatch: false`, and
`affects_discovery_install_activation_or_execution: false`. The clean-checkout
flow API suite and focused current-worktree queued/retry tests pass.

## Remaining gates

- run a selected live worker canary through queued, terminal, timeout, and
  retry transitions;
- prove cold-crash recovery, lease expiry, and idempotent callback handling
  with persistent database history;
- retain live worker/recovery evidence in the release ledger; and
- complete native-Linux and provider-owned recovery evidence before treating
  this path as release-certified.
