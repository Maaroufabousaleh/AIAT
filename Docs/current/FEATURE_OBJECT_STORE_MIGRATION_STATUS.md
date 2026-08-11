# Object-Store Migration Review Status

**Updated:** 2026-08-11
**Roadmap:** [AIAT Roadmap](../../ROADMAP.md)
**Owning feature:** [Data, Storage, Memory, and Retention](FEATURE_DATA_STORAGE_AND_MEMORY.md)
**Scope:** personal/internal AIAT instance

## Reviewed implementation

This bounded review batch turns the S3-compatible object-store boundary into
explicit, testable contracts while keeping provider routing and deployment
authority outside the helpers.

- `a5cb439` — checksum-verified `BlobClient` read-back, provider-neutral
  conformance (`aiat.object-store-conformance.v1`), verified copy
  (`aiat.object-store-copy.v1`), backup manifests, clean-target restore
  verification, fixture/live runners, and private-network MinIO probes.
- `3a45147` — governed migration record
  (`aiat.object-store-migration.v1`) covering checksum inventory, verified
  copy, optional dual-write parity, and explicit human-confirmed cutover and
  rollback.

The helpers never delete source objects, silently change deployment routing,
copy credentials into reports, or treat licence/restriction metadata as a
technical predicate. Provider diversity, encryption, retention enforcement,
clean-environment disaster recovery, and outage evidence remain separate
gates.

## Verification evidence

From `mas/`, the reviewed commits pass these clean-checkout checks:

```bash
uv run --isolated pytest \
  packages/mas-core/tests/test_object_store_conformance.py \
  packages/mas-core/tests/test_object_store_backup.py \
  packages/mas-core/tests/test_object_store_migration.py \
  packages/mas-core/tests/test_object_store_migration_runner.py \
  packages/mas-core/tests/test_object_store_rollout.py -q
uv run --isolated python scripts/check_object_store_conformance.py --json
uv run --isolated python scripts/check_object_store_copy.py --json
uv run --isolated python scripts/check_object_store_backup_restore.py --json
uv run --isolated python scripts/check_object_store_migration.py --json
```

The deterministic reports pass with no live provider mutation. The local
MinIO conformance and same-provider backup/restore rehearsals are retained in
[`object_store_live_conformance.json`](../../mas/docs/provenance/object_store_live_conformance.json)
and
[`object_store_backup_restore_live.json`](../../mas/docs/provenance/object_store_backup_restore_live.json).
Those reports document local deployment evidence only; they do not certify a
provider pair or disaster recovery.

## Remaining gates

- Run verified copy and the migration workflow against a provider-certified
  pair with retention, routing, and rollback evidence.
- Add encrypted secondary backup and clean-environment restore verification.
- Measure large-object/multipart/concurrency/outage behavior and compare any
  optional backend before changing the MinIO default.
- Prove Postgres/object-store consistency, lifecycle cleanup, orphan handling,
  and legal-hold behavior in a live recovery exercise.

Licence, redistribution, and restriction values remain provenance/notice
metadata only. They never decide conformance, backup parity, migration state,
normal internal use, or activation.
