# Trace Retention Review Status

**Updated:** 2026-08-11
**Roadmap:** [AIAT Roadmap](../../ROADMAP.md)
**Owning feature:** [Trace Evidence and Retention](FEATURE_TRACE_EVIDENCE_AND_RETENTION.md)
**Scope:** personal/internal AIAT instance

## Reviewed implementation

Commit `af8abd8` adds the deterministic `aiat.trace-retention-plan.v1`
planner. It consumes only bounded native-span metadata, honors an explicit
`retention_until` when present, derives an expiry from the configured trace
retention period otherwise, and classifies each row as `retain`, `archive`,
`delete`, or `invalid`.

Invalid rows are reported and excluded from deletion candidates. The planner
does not connect to storage, delete spans, archive bytes, enforce legal holds,
or change project authority. Retention mode is operational metadata; licence
and restriction notices remain metadata-only and never affect the result.

## Verification evidence

From `mas/`:

```bash
uv run --isolated pytest \
  packages/mas-core/tests/test_trace_retention.py \
  packages/mas-core/tests/test_trace_evidence.py \
  packages/mas-core/tests/test_native_trace_spans.py -q
uv run --isolated python scripts/check_trace_retention.py --json
```

The clean-checkout review passes the focused tests and fixture. The fixture
reports `mutation_performed: false`, `live_enforcement_status: not_checked`,
and `licence_metadata_is_gate: false`.

## Remaining gates

- Apply retention decisions through an operator/recovery worker with project
  narrowing, legal-hold/erasure handling, audit records, and backup parity.
- Prove retention behavior against restored storage and a representative live
  multi-service trace workload.
