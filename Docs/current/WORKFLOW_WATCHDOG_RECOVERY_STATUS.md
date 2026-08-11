# Workflow Watchdog and Safe-Recovery Status

**Updated:** 2026-08-11
**Roadmap:** [AIAT Roadmap](../../ROADMAP.md)
**Owning feature:** [Projects, Flows, Knowledge, and Evidence](FEATURE_PROJECTS_FLOWS_AND_EVIDENCE.md)
**Scope:** personal/internal AIAT instance

## Reviewed implementation

Commit `9007236` adds the deterministic watchdog and safe-retry review
fixture. It exercises the real workflow helpers and pure controller without
storage, worker dispatch, network calls, or state mutation.

The fixture records schema `aiat.workflow-watchdog-recovery.v1` and verifies:

- post-boot grace suppresses a timeout;
- elapsed time is downtime-aware and fires at the configured boundary;
- a watchdog timeout uses the universal transition to `FAILED`;
- an explicit retry restores the recorded safe state instead of guessing a
  new workflow stage; and
- `FAILED`, `COMPLETED`, and `ARCHIVED` are excluded from automatic watchdog
  re-entry.

The report explicitly records `storage: false`, `worker_dispatch: false`, and
`mutation: false`. Resource licence or restriction notices remain metadata
only and are not predicates for discovery, installation, activation, or
execution.

## Verification evidence

From `mas/`:

```bash
uv run --isolated pytest \
  packages/mas-core/tests/test_workflow_watchdog_recovery.py -q
uv run --isolated python scripts/check_workflow_watchdog_recovery.py --json
```

The deterministic fixture passes in a clean checkout. The `--live` mode is an
explicit operator gate and returns `blocked` until native watchdog and
cold-recovery evidence is collected.

## Remaining gates

- exercise a native watchdog against a selected local runtime window;
- prove cold-start recovery after an interrupted worker/project transition;
- verify persistent transition history, retry idempotency, and audit evidence
  with the storage-backed controller; and
- retain the live report with the release ledger before treating the recovery
  path as release-ready.
