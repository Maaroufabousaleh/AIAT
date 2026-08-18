# Object-Store Migration Review Status

**Updated:** 2026-08-18
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
- `73fcdd9` — bounded object-store benchmark contract
  (`aiat.object-store-benchmark.v1`) and fail-closed two-provider runner;
  fixture timings are evidence only and never select a provider.
- `93bf755` — restore-copy safety hardening: the fixture/live runner and
  governed migration workflow perform a non-mutating empty-target preflight,
  and `clean_target_verified` is retained in restore evidence.
- `351444a` — bounded `aiat.object-store-provider-pair.v1` dual-endpoint
  recovery checker and focused tests. It performs checksum-bearing dual-write,
  rejects a simulated unavailable primary at the adapter boundary, restores
  from the secondary into a clean recovery bucket, and cleans all reserved
  prefixes.
- `2026-08-18` live follow-up — the same checker was exercised between the
  Compose MinIO endpoint and a disposable SeaweedFS 4.42 endpoint, and the
  bounded benchmark runner completed three checksum cases on each endpoint.
  These are provider-diverse adapter/comparison observations, not a provider
  durability, outage, KMS, or migration-cutover certificate.
- `2026-08-18` copy/parity follow-up — the live verified-copy helper inventoried
  three reserved MinIO objects, copied them to SeaweedFS, read back matching
  checksums/sizes, and cleaned both prefixes to zero. Source preservation and
  no-cutover behavior remain properties of the copy helper, not migration
  approval.
- `ecbef00` — the migration checker now has a guarded live-rehearsal path.
  It requires two explicit S3-compatible endpoints, a project ID in the
  reserved `aiat-migration-live-` namespace, `--seed-fixture`, and separate
  human confirmations for cutover and rollback. The run inventories three
  objects, performs checksum/read-back copy and one dual write, records the
  AIAT-owned `CUTOVER` → `ROLLED_BACK` transition, and removes the four
  reserved objects from each endpoint before returning.
- `6794b9f` — the benchmark contract now bounds concurrent waves and larger
  payloads. Each named provider runs four concurrent checksum upload/read-back
  cases at 1 MiB and 8 MiB, verifies post-delete prefix emptiness, and retains
  only scalar results; configuration rejects unsafe concurrency or payload
  budgets before provider mutation.
- `a2f35de` — the S3-compatible adapter now exposes explicit multipart
  create/part/complete/abort operations. The bounded checker runs 8 MiB and
  16 MiB payloads with 5 MiB parts, verifies checksum read-back and an aborted
  upload leaves no object, and keeps the provider-neutral helper separate from
  routing authority.

The governed workflow never deletes source objects or silently changes
deployment routing. The bounded live checker deletes only its reserved
fixture objects after the rehearsal and never copies credentials or payloads
into reports. Licence/restriction metadata is not a technical predicate.
Provider durability, encryption, retention enforcement, clean-environment
disaster recovery, actual outage, and production routing evidence remain
separate gates.

## Verification evidence

From `mas/`, the reviewed commits pass these clean-checkout checks:

```bash
uv run --isolated pytest \
  packages/mas-core/tests/test_object_store_conformance.py \
  packages/mas-core/tests/test_object_store_backup.py \
  packages/mas-core/tests/test_object_store_migration.py \
  packages/mas-core/tests/test_object_store_migration_runner.py \
  packages/mas-core/tests/test_object_store_rollout.py \
  packages/mas-core/tests/test_object_store_benchmarks.py -q
uv run --isolated python scripts/check_object_store_conformance.py --json
uv run --isolated python scripts/check_object_store_copy.py --json
uv run --isolated python scripts/check_object_store_backup_restore.py --json
uv run --isolated python scripts/check_object_store_migration.py --json
PYTHONPATH=scripts uv run --isolated pytest -q \
  scripts/tests/test_check_object_store_migration.py
uv run --isolated python scripts/check_object_store_benchmarks.py --json
uv run --isolated pytest packages/mas-core/tests/test_object_store_multipart.py -q
uv run --isolated python scripts/check_object_store_multipart.py --json
```

The backup/restore fixture now includes a regression proving that a stale
object in the restore prefix is rejected before any manifest object is copied;
the passing report records `clean_target_verified: true` for guarded restores.

The deterministic reports pass with no live provider mutation. The local
MinIO conformance and same-provider backup/restore rehearsals are retained in
[`object_store_live_conformance.json`](../../mas/docs/provenance/object_store_live_conformance.json)
and
[`object_store_backup_restore_live.json`](../../mas/docs/provenance/object_store_backup_restore_live.json).
Those reports document local deployment evidence only; they do not certify a
provider pair or disaster recovery.

The live same-provider certificate at
[`object_store_provider_pair_evidence.json`](../../mas/docs/provenance/object_store_provider_pair_evidence.json)
(`f385bd7`) records three objects dual-written between the existing Compose
MinIO endpoint and a disposable local MinIO endpoint, secondary-only clean
restore after the primary adapter failure probe, payload-free output, and zero
remaining objects. Both endpoints are MinIO; provider-diverse durability,
actual provider process/network outage, provider-managed encryption/KMS,
clean-host recovery, and disaster recovery remain separate gates.

The provider-diverse follow-up uses the same checker between Compose MinIO and
disposable SeaweedFS. It retains scalar-only evidence at
[`object_store_provider_pair_provider_diverse_evidence.json`](../../mas/docs/provenance/object_store_provider_pair_provider_diverse_evidence.json)
and a three-size benchmark comparison at
[`object_store_provider_benchmark_evidence.json`](../../mas/docs/provenance/object_store_provider_benchmark_evidence.json).
All six benchmark cases and all three pair objects pass checksum read-back and
scoped cleanup. The follow-on bounded wave retains
[`object_store_benchmark_advanced_provider_diverse_evidence.json`](../../mas/docs/provenance/object_store_benchmark_advanced_provider_diverse_evidence.json)
and passes sixteen 1 MiB/8 MiB concurrent cases across the same two endpoints,
with four concurrent cases per size and zero remaining fixture objects. The
topology is operator-observed and local; provider-managed durability/custody,
resource profiling, actual process/network outage, clean-host/disaster
recovery, and migration cutover/rollback remain open.

The multipart follow-up retains scalar evidence at
[`object_store_multipart_provider_diverse_evidence.json`](../../mas/docs/provenance/object_store_multipart_provider_diverse_evidence.json).
Compose MinIO and disposable SeaweedFS each pass 8 MiB and 16 MiB uploads with
5 MiB parts, checksum read-back, explicit abort-without-object, and zero
remaining fixture objects. This closes the bounded multipart adapter contract
only; resource profiling, provider outage, provider-managed encryption/KMS,
clean-host/disaster recovery, and production migration remain open.

The verified-copy follow-up retains scalar evidence at
[`object_store_copy_provider_diverse_evidence.json`](../../mas/docs/provenance/object_store_copy_provider_diverse_evidence.json).
Three source-inventory cases copy from MinIO to SeaweedFS with matching
checksums/sizes, preserve source data until explicit cleanup, and leave zero
reserved objects on both sides. Retention parity, cutover/rollback, outage,
and clean-host/disaster recovery remain open.

The guarded live migration rehearsal retains scalar evidence at
[`object_store_migration_provider_diverse_evidence.json`](../../mas/docs/provenance/object_store_migration_provider_diverse_evidence.json).
It ran against the same disposable MinIO/SeaweedFS topology, passed inventory,
copy/read-back, dual write, human-confirmed workflow cutover and rollback, and
scoped cleanup, with zero reserved objects remaining. It did not change
deployment routing or retention authority; actual production cutover,
retention parity, provider outage, KMS, clean-host, and disaster-recovery
evidence remain open.

## Remaining gates

- Extend the bounded migration rehearsal into a separately approved production
  change only after retention parity, deployment-routing ownership, rollback
  authority, and operator recovery evidence are defined. The provider-diverse
  rehearsal is complete, but it does not authorize a production cutover.
- Extend the retained serial, bounded concurrency, and multipart waves with
  reliability, resource, outage, and recovery comparison evidence. The current
  timings and multipart observations are local disposable evidence and do not
  justify a provider decision.
- Add encrypted secondary backup and clean-environment disaster-recovery
  verification. The current empty-target preflight is a bounded safety check,
  not proof of a clean host, provider durability, or regional recovery.
- Measure resource/outage behavior and compare any optional backend before
  changing the MinIO default; large-object, bounded concurrency, and multipart
  behavior are now checked but do not close those remaining gates.
- Prove Postgres/object-store consistency, lifecycle cleanup, orphan handling,
  and legal-hold behavior in a live recovery exercise.

Licence, redistribution, and restriction values remain provenance/notice
metadata only. They never decide conformance, backup parity, migration state,
normal internal use, or activation.
