# Flow-Instance Recovery Probe Status

**Updated:** 2026-08-11
**Roadmap:** [AIAT Roadmap](../../ROADMAP.md)
**Owning feature:** [Projects, Flows, Knowledge, and Evidence](FEATURE_PROJECTS_FLOWS_AND_EVIDENCE.md)
**Scope:** personal/internal AIAT instance

## Reviewed implementation

Commit `8bc7863` adds the guarded `aiat.flow-instance-recovery-readiness.v1`
probe and focused tests. The probe reads one flow instance and its execution
history through the orchestrator API, summarizes status/active nodes/retry
count/execution-state counts, and keeps credentials out of reports.

The default mode is declaration-only. Live status inspection requires an
explicit instance ID; `start`, `pause`, `resume`, `cancel`, and `retry` also
require `--confirm`. Post-action status and execution history are read back,
and an unexpected state is reported as `fail` rather than being upgraded to a
pass. The probe does not claim worker canary, UI, project, provider, or native
crash-recovery certification.

Resource licence or restriction notices remain metadata only and do not affect
the probe, action authorization, activation, or normal internal execution.

## Verification evidence

From `mas/`:

```bash
uv run --isolated pytest \
  packages/mas-core/tests/test_flow_instance_recovery.py -q
uv run --isolated python scripts/check_flow_instance_recovery.py --json
```

The clean-checkout fixture passes. The `--live` path remains an explicit
operator boundary and returns `blocked` until an authenticated instance and
recovery window are selected.

## Remaining gates

- select a disposable flow instance and retain read-only status/history
  evidence;
- exercise confirmed pause/resume/retry/cancel actions with persisted audit
  history and idempotent retries;
- combine this probe with worker canary, watchdog, cold-crash, and UI evidence;
  and
- publish the resulting live report in the release ledger before marking
  recovery release-ready.
