# Trace Retention Review Status

**Updated:** 2026-08-17
**Roadmap:** [AIAT Roadmap](../../ROADMAP.md)
**Owning feature:** [Trace Evidence and Retention](FEATURE_TRACE_EVIDENCE_AND_RETENTION.md)
**Scope:** personal/internal AIAT instance

## Reviewed implementation

Commit `af8abd8` adds the deterministic `aiat.trace-retention-plan.v1`
planner. It consumes only bounded native-span metadata, honors an explicit
`retention_until` when present, derives an expiry from the configured trace
retention period otherwise, and classifies each row as `retain`, `archive`,
`delete`, or `invalid`.

Commit `f8829d6` adds the operator-only `GET /observability/retention/plan`
read model and generated API contracts, plus `check_trace_retention.py --live`.
The route reads bounded native-span metadata, returns `mode: read-only-plan`,
and proves `mutation_performed: false`; the live checker validates that
invariant and emits only bounded counts/policy metadata. No archive/delete or
authoritative legal-hold, erasure, project-narrowing, audit, or restore action
is performed.
Commit `b3fca97` makes this response a typed Pydantic/OpenAPI model with
bounded count/candidate schemas and validation that rejects a true mutation
flag.
Commit `9a80c6c` adds an explicit-boolean `legal_hold` marker to each candidate
and a separate `counts.legal_hold` value. Held rows remain `retain` and never
enter `deletion_ids`; ambiguous string values such as `"true"` do not activate
the hold.

Commit `01996c9` adds the provider-neutral `aiat.trace-retention-execution.v1`
contract and deterministic `InMemoryRetentionStore` rehearsal. Preview mode is
non-mutating; apply mode sends one complete atomic action batch only after
project scope, a typed authoritative hold snapshot, typed backup-parity
evidence, and explicit human confirmation pass. Commit `57e13cb` validates
matching source, backup, and restored manifest digests, record counts, checked
read-back count, and clean-target verification; `15054ba` validates hold source,
observed time, active/released state, duplicate handling, and project scope.
Commit `5d71309` types the bounded audit envelope passed to the adapter,
normalizing only evidence references, scalar counts, and evaluation time.
`scripts/check_trace_retention_execution.py` proves the preview/apply and audit
invariants, while `--live` remains blocked until reviewed registry/storage
recovery adapters are configured.

Invalid rows are reported and excluded from deletion candidates. The planner
does not connect to storage, delete spans, archive bytes, or establish the
authoritative hold registry. Retention mode and hold markers are operational
metadata; licence and restriction notices remain metadata-only and never
affect the result.

## Verification evidence

From `mas/`:

```bash
uv run --isolated pytest \
  packages/mas-core/tests/test_trace_retention.py \
  packages/mas-core/tests/test_trace_evidence.py \
  packages/mas-core/tests/test_native_trace_spans.py -q
uv run --isolated python scripts/check_trace_retention.py --json
uv run --isolated python scripts/check_trace_retention.py --live --json \
  --trace-id aiat-live-trace-check
uv run --isolated pytest \
  packages/mas-core/tests/test_retention_execution.py \
  scripts/tests/test_check_trace_retention_execution.py -q
uv run --isolated python scripts/check_trace_retention_execution.py --json
uv run --isolated python scripts/check_trace_retention_execution.py --live --json
```

The clean-checkout review passes the focused tests and fixture. The fixture
reports `mutation_performed: false`, `live_enforcement_status: not_checked`,
and `licence_metadata_is_gate: false`. A configured live plan is accepted only
when it declares the retention-plan schema, `mode: read-only-plan`, and
`mutation_performed: false`; unavailable configuration or storage returns
`blocked` with exit code 2.

The operator incident API/dashboard groups (`b4b7cef`, `869202c`) and the
retention plan group (`f8829d6`, `b3fca97`, `9a80c6c`) and execution rehearsal
(`01996c9`, `57e13cb`, `15054ba`, `5d71309`) consume the same bounded trace evidence authority but do not claim
live retention enforcement. They expose only safe incident metadata, finding
references, counts, policy scalars, and an auditable fixture result; production
retention execution remains an independent storage/recovery action.

## Remaining gates

- Connect the guarded execution contract to an operator/recovery worker with
  project narrowing, an authoritative legal-hold/erasure registry, durable
  audit records, live backup/read-back parity, and restore-tested rollback. The
  current `legal_hold` metadata guard, typed hold/parity evidence, typed audit
  envelope, and `InMemoryRetentionStore` are fail-safe rehearsal boundaries,
  not live authority, durable audit, or database mutation.
- Prove retention behavior against restored storage and a representative live
  multi-service trace workload.
