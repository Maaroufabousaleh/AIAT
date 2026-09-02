# Flow-Instance Recovery Probe Status

**Updated:** 2026-08-18
**Roadmap:** [AIAT Roadmap](../../ROADMAP.md)
**Owning feature:** [Projects, Flows, Knowledge, and Evidence](FEATURE_PROJECTS_FLOWS_AND_EVIDENCE.md)
**Scope:** personal/internal AIAT instance

## Reviewed implementation

Commit `8bc7863` adds the guarded `aiat.flow-instance-recovery-readiness.v1`
probe and focused tests. Commit `1f20132` adds the local Postgres
`aiat.flow-instance-recovery-postgres-certification.v1` certificate, which
drives the real storage methods through retry, switch, escalation, cancellation
and connection-reopen read-back in a reserved namespace.

The default mode is declaration-only. Live status inspection requires an
explicit instance ID; `start`, `pause`, `resume`, `cancel`, and `retry` also
require `--confirm`. Post-action status and execution history are read back,
and an unexpected state is reported as `fail` rather than being upgraded to a
pass. The probes do not claim worker canary, UI, provider, native watchdog, or
cold-crash certification. The Postgres certificate records only bounded status
and counts and removes all fixture rows before returning.

Resource licence or restriction notices remain metadata only and do not affect
the probe, action authorization, activation, or normal internal execution.

## Verification evidence

From `mas/`:

```bash
uv run --isolated pytest \
  packages/mas-core/tests/test_flow_instance_recovery.py -q
uv run --isolated python scripts/check_flow_instance_recovery.py --json
uv run --isolated pytest scripts/tests/test_check_flow_instance_recovery_postgres.py -q
uv run --isolated python scripts/check_flow_instance_recovery_postgres.py --json
```

The clean-checkout fixture passes. The local Postgres certificate passes at
migration `0042_worker_run_host_binding` and is retained at
[`mas/docs/provenance/flow_instance_recovery_postgres_evidence.json`](../../mas/docs/provenance/flow_instance_recovery_postgres_evidence.json).
The API `--live` path remains an explicit operator boundary and returns
`blocked` until an authenticated instance and recovery window are selected.

## Remaining gates

- repeat the Postgres certificate on the frozen release environment and retain
  durable audit/history evidence;
- exercise confirmed pause/resume/retry/cancel actions with persisted audit
  history and idempotent retries;
- combine these probes with worker canary, watchdog, cold-crash, provider, and
  UI evidence;
  and
- publish the resulting live report in the release ledger before marking
  recovery release-ready.
