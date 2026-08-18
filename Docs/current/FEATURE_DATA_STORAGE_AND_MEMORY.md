# Data, Storage, Memory, and Retention Feature Specification

**Baseline:** 2026-08-10
**Status:** Postgres/pgvector/Redis/MinIO implemented; the S3-compatible contract, checksum copy, deterministic backup/restore fixture, AIAT-owned AES-256-GCM backup envelope, local fresh-process encrypted-restore certificate, governed migration workflow fixture and guarded provider-diverse live rehearsal, bounded object-store benchmark contract, deployed local MinIO conformance, same-provider backup/restore rehearsal, bounded same-provider and provider-diverse dual-endpoint provider-pair recovery, and a disposable MinIO/SeaweedFS comparison pass; provider-managed encryption, provider durability/custody, production routing/retention cutover, clean-host/disaster-recovery restore, and optional memory services remain target work
**Authority:** [AIAT Target Programme](../../AIAT_TARGET_PROGRAMME.md)

## Purpose

AIAT stores durable truth in explicit canonical systems and exposes storage through governed service boundaries. No runtime-specific memory, cache, vector service, or object store may silently become the authority for projects or worker history.

## Implemented now

- Postgres/SQLAlchemy Core storage for the company, projects, workflow, documents, reviews, approvals, issues, KPI, context, capabilities, workers, stewards, models, runs, flows, integrations, evidence, and budgets.
- PgBouncer transaction-pooling boundary.
- pgvector migration and hybrid project-context retrieval.
- Redis Streams for message delivery plus bounded cache and queue/lease coordination under separate ACL users.
- MinIO S3-compatible artifact/document backend and blob tools.
- `BlobClient.download` and the deterministic object-store adapter now verify
  both SHA-256 and declared byte count on every checksum-bearing read-back;
  tampered digest or size references fail closed.
- Provider-neutral `aiat.object-store-conformance.v1` checks for scoped keys,
  checksum references, integrity rejection, empty objects, listing isolation,
  delete/absence agreement, path validation, and cleanup.
- The same contract is now executable through the real `BlobClient` adapter
  with `scripts/check_object_store_conformance.py --live`; missing endpoint or
  credentials and unavailable providers fail closed as a blocked result rather
  than being reported as a pass. The running local MinIO deployment passed all
  8/8 cases (evidence refreshed 2026-08-11 in `22c736d`); the secret-safe report is retained in
  [`mas/docs/provenance/object_store_live_conformance.json`](../../mas/docs/provenance/object_store_live_conformance.json),
  and [`mas/infra/compose/scripts/reconcile-minio-agent-user.sh`](../../mas/infra/compose/scripts/reconcile-minio-agent-user.sh)
  reconciles a persisted IAM secret after local rotation without touching
  object data.
- The aggregate release child accepts `--compose-local` and executes the
  checked-in MinIO probe inside the orchestrator container. This preserves the
  private `minio:9000` endpoint and credential boundary while making the
  host-side live ledger bounded; the refreshed child records all 8/8 cases as
  pass instead of timing out on an unresolvable host alias.
- The checked-in [`mas/infra/compose/scripts/check-minio-backup-restore.sh`](../../mas/infra/compose/scripts/check-minio-backup-restore.sh)
  seeds two disposable objects, verifies the checksum manifest through local
  backup and restore buckets, and confirms scoped cleanup. Its secret-safe
  result is retained at
  [`mas/docs/provenance/object_store_backup_restore_live.json`](../../mas/docs/provenance/object_store_backup_restore_live.json).
- Dedicated identity Postgres and LiteLLM database boundaries.
- Checksums, document lineage, artifact/run linkage, context chunking, tags, relations, checkpoints, and usage history.
- Trace-bearing task logs, project-usage events, and worker-run transition
  correlations have bounded storage read methods. Model usage and worker
  artifact rows now persist direct `trace_id`/`span_id` fields (with a legacy
  run-correlation fallback), while integration evidence records carry the same
  safe context. The `native_trace_spans` table and `aiat.native-trace-span.v1`
  normalizer persist payload-free transport, model, tool, audit, worker, and
  integration spans from the corresponding writers; sensitive attribute names
  are dropped before storage. The operator-only `aiat.trace-evidence.v1`
  projection joins these rows without returning raw payloads and reports the
  remaining configured mail-edge/live-retention gap. The identity-service
  Resend/Svix verifier and raw webhook ingress are implemented in `2d21a2f`,
  but live callback and durable bounce read-back evidence remain separate.
- Company retention manifests now expose `trace_days` and
  `trace_sample_rate`; these are operational metadata for the bounded trace
  surface and do not alter project authority or completion predicates.
- `list_project_usage_aggregates` provides a bounded, project-scoped read model
  for SLO/capacity calculations (event/call/failure counts, tokens, cost,
  duration summaries, and first/last timestamps) without exposing raw usage
  payloads. See the [SLO, Capacity, and Operational Forecast feature](FEATURE_SLO_CAPACITY_AND_OPERATIONS.md).
- `api_request_observations` stores the versioned, payload-free
  `aiat.api-observation.v1` scalar ledger written by orchestrator middleware;
  `record_api_request_observation` normalizes route/status/duration/trace
  values, while `list_api_request_observations` supports trace and SLO reads.
  Request/response bodies, headers, query strings, credentials, and exception
  text are intentionally absent from the schema.
- Deterministic `aiat.object-store-backup.v1` manifests record only scoped
  logical keys, checksums, sizes, and content types. The fixture runner copies
  source objects to a backup adapter, restores them to a clean target, and
  requires exact key-set and read-back checksum parity.
- The bounded `aiat.object-store-provider-pair.v1` checker (`351444a`) now
  exercises three checksum-bearing objects across two configured S3-compatible
  endpoints: clean-target dual-write, a rejected primary adapter after the
  copy, secondary-only recovery into a clean bucket, and zero-row cleanup.
  The retained live certificate uses two local MinIO endpoints and is
  explicitly same-provider evidence; it does not claim provider diversity,
  an actual provider process/network outage, KMS, clean-host recovery, or
  disaster recovery.
- The AIAT-owned `aiat.object-store-encrypted-backup.v1` envelope encrypts
  source bytes with AES-256-GCM before they reach a backup adapter. Its
  manifest retains only plaintext/ciphertext checksums and sizes, nonces, an
  opaque key ID, and content type. Wrong keys and tampered ciphertext fail
  closed, encrypted replication requires a clean target when requested, and
  plaintext logical keys are absent from the backup prefix. The deterministic
  certificate is retained at
  [`mas/docs/provenance/object_store_encryption_evidence.json`](../../mas/docs/provenance/object_store_encryption_evidence.json).
- `EncryptedBackupManifest.from_dict` verifies the scalar object count and
  manifest digest when a persisted encrypted bundle is reopened. The
  [`check_object_store_clean_environment_restore.py`](../../mas/scripts/check_object_store_clean_environment_restore.py)
  certificate writes only ciphertext plus scalar manifest metadata to a
  temporary bundle, restores it through a distinct Python process with fresh
  adapters and the production encrypted-restore helper, and removes the
  bundle and fixture objects. Its payload-free evidence is retained at
  [`object_store_clean_environment_restore_evidence.json`](../../mas/docs/provenance/object_store_clean_environment_restore_evidence.json).
- Deterministic `aiat.object-store-benchmark.v1` measurements exercise bounded
  upload/download checksum read-back and scoped cleanup. Fixture mode is
  repeatable without a provider; `scripts/check_object_store_benchmarks.py
  --live` requires both named MinIO and SeaweedFS endpoint/credential sets,
  returns `blocked` when either side is unavailable, and never chooses a
  primary provider or reads licence metadata as a gate. The retained live
  comparison covers payload sizes 1/32/4096 bytes on disposable Compose MinIO
  and SeaweedFS endpoints; it is timing/comparison evidence only.
- The provider-pair checker was also exercised across the operator-observed
  MinIO/SeaweedFS topology. Its retained scalar evidence confirms three-object
  checksum dual-write, adapter-boundary primary-loss rejection, secondary-only
  clean recovery, and zero cleanup. This confirms endpoint diversity at the
  local adapter boundary, not provider durability, KMS, outage recovery,
  clean-host recovery, or a migration decision.
- The live `aiat.object-store-copy.v1` path now inventories three reserved
  MinIO objects, copies them to the disposable SeaweedFS endpoint, verifies
  matching checksums and sizes, preserves the source until explicit cleanup,
  and leaves both prefixes empty. Retained evidence is
  [`object_store_copy_provider_diverse_evidence.json`](../../mas/docs/provenance/object_store_copy_provider_diverse_evidence.json);
  this is parity evidence only and does not authorize cutover.
- The guarded live `aiat.object-store-migration.v1` rehearsal (`ecbef00`)
  requires explicit source/target endpoints, a reserved
  `aiat-migration-live-*` project, fixture seeding, and separate human
  confirmations. It passes three-object inventory, checksum/read-back copy,
  one dual write, AIAT-owned cutover/rollback transitions, and scoped cleanup
  against the disposable MinIO/SeaweedFS topology. Evidence is retained at
  [`object_store_migration_provider_diverse_evidence.json`](../../mas/docs/provenance/object_store_migration_provider_diverse_evidence.json).
  It never changes deployment routing or retention authority; provider outage,
  KMS, clean-host, disaster-recovery, and production cutover evidence remain
  separate.

## Code anchors

- Tables: [`mas/packages/mas-core/mas_core/memory/models.py`](../../mas/packages/mas-core/mas_core/memory/models.py)
- Storage API: [`mas/packages/mas-core/mas_core/memory/storage.py`](../../mas/packages/mas-core/mas_core/memory/storage.py)
- Blob adapter: [`mas/packages/mas-core/mas_core/memory/blob.py`](../../mas/packages/mas-core/mas_core/memory/blob.py)
- Object-store contract/report: [`mas/packages/mas-core/mas_core/memory/object_store_conformance.py`](../../mas/packages/mas-core/mas_core/memory/object_store_conformance.py)
- Verified copy/parity helper: [`mas/packages/mas-core/mas_core/memory/object_store_migration.py`](../../mas/packages/mas-core/mas_core/memory/object_store_migration.py)
- Governed migration workflow: [`mas/packages/mas-core/mas_core/memory/object_store_rollout.py`](../../mas/packages/mas-core/mas_core/memory/object_store_rollout.py)
- Offline fixture command: [`mas/scripts/check_object_store_conformance.py`](../../mas/scripts/check_object_store_conformance.py)
- Live/fixture conformance command: [`mas/scripts/check_object_store_conformance.py`](../../mas/scripts/check_object_store_conformance.py) (`--live` with `AIAT_OBJECT_STORE_*` or `MINIO_*` configuration)
- Running local MinIO probe: [`mas/infra/compose/scripts/check-minio-conformance.sh`](../../mas/infra/compose/scripts/check-minio-conformance.sh) (executes the same contract inside the private Compose network)
- Local MinIO IAM reconciliation (`5558f3c`): [`mas/infra/compose/scripts/reconcile-minio-agent-user.sh`](../../mas/infra/compose/scripts/reconcile-minio-agent-user.sh). The helper uses a pinned `mc` image, keeps credentials in a mode-600 temporary file, updates only the agent user/policy, and performs no object-data mutation.
- Offline copy/parity command: [`mas/scripts/check_object_store_copy.py`](../../mas/scripts/check_object_store_copy.py)
- Live copy/parity runner: [`mas/scripts/check_object_store_copy.py`](../../mas/scripts/check_object_store_copy.py) (`--live` with explicit source/target provider configuration)
- Backup manifest/restore boundary: [`mas/packages/mas-core/mas_core/memory/object_store_backup.py`](../../mas/packages/mas-core/mas_core/memory/object_store_backup.py)
- Backup/restore fixture and live runner: [`mas/scripts/check_object_store_backup_restore.py`](../../mas/scripts/check_object_store_backup_restore.py) (`--live` requires source, backup, and restore provider configuration)
- Encrypted backup envelope: [`mas/packages/mas-core/mas_core/memory/object_store_encryption.py`](../../mas/packages/mas-core/mas_core/memory/object_store_encryption.py)
- Encrypted backup certificate: [`mas/scripts/check_object_store_encryption.py`](../../mas/scripts/check_object_store_encryption.py) and [`mas/docs/provenance/object_store_encryption_evidence.json`](../../mas/docs/provenance/object_store_encryption_evidence.json)
- Fresh-process encrypted restore certificate: [`mas/scripts/check_object_store_clean_environment_restore.py`](../../mas/scripts/check_object_store_clean_environment_restore.py) and [`mas/docs/provenance/object_store_clean_environment_restore_evidence.json`](../../mas/docs/provenance/object_store_clean_environment_restore_evidence.json)
- Migration workflow fixture and guarded live rehearsal: [`mas/scripts/check_object_store_migration.py`](../../mas/scripts/check_object_store_migration.py)
- Migration checker tests: [`mas/scripts/tests/test_check_object_store_migration.py`](../../mas/scripts/tests/test_check_object_store_migration.py)
- Benchmark contract: [`mas/packages/mas-core/mas_core/memory/object_store_benchmark.py`](../../mas/packages/mas-core/mas_core/memory/object_store_benchmark.py)
- Benchmark fixture/live boundary: [`mas/scripts/check_object_store_benchmarks.py`](../../mas/scripts/check_object_store_benchmarks.py)
- Provider-pair dual-write/recovery checker: [`mas/scripts/check_object_store_provider_pair.py`](../../mas/scripts/check_object_store_provider_pair.py) and retained evidence [`mas/docs/provenance/object_store_provider_pair_evidence.json`](../../mas/docs/provenance/object_store_provider_pair_evidence.json)
- Context/checkpoints: [`mas/packages/mas-core/mas_core/memory/`](../../mas/packages/mas-core/mas_core/memory/)
- Database migrations: [`mas/migrations/versions/`](../../mas/migrations/versions/)
- Blob tools: [`mas/apps/tool-service/tool_service/tools/infra.py`](../../mas/apps/tool-service/tool_service/tools/infra.py)
- Trace evidence projection: [`mas/packages/mas-core/mas_core/observability/trace_evidence.py`](../../mas/packages/mas-core/mas_core/observability/trace_evidence.py), [`mas/packages/mas-core/mas_core/memory/storage.py`](../../mas/packages/mas-core/mas_core/memory/storage.py), [`mas/scripts/check_trace_evidence.py`](../../mas/scripts/check_trace_evidence.py)
- Native span contract/table: [`mas/packages/mas-core/mas_core/observability/native_spans.py`](../../mas/packages/mas-core/mas_core/observability/native_spans.py), [`mas/packages/mas-core/mas_core/memory/models.py`](../../mas/packages/mas-core/mas_core/memory/models.py), [`mas/migrations/versions/0036_native_trace_spans.py`](../../mas/migrations/versions/0036_native_trace_spans.py), [`mas/scripts/check_native_trace_spans.py`](../../mas/scripts/check_native_trace_spans.py)
- Local live transport read-back: [`mas/scripts/check_live_trace_observability.py`](../../mas/scripts/check_live_trace_observability.py), [`mas/docs/provenance/trace_observability_live.json`](../../mas/docs/provenance/trace_observability_live.json)
- SLO/capacity projections: [`mas/packages/mas-core/mas_core/observability/slo.py`](../../mas/packages/mas-core/mas_core/observability/slo.py), [`mas/scripts/check_slo_capacity.py`](../../mas/scripts/check_slo_capacity.py)
- API observation schema/table/migration: [`mas/packages/mas-core/mas_core/observability/api_observations.py`](../../mas/packages/mas-core/mas_core/observability/api_observations.py), [`mas/packages/mas-core/mas_core/memory/models.py`](../../mas/packages/mas-core/mas_core/memory/models.py), [`mas/migrations/versions/0034_api_request_observations.py`](../../mas/migrations/versions/0034_api_request_observations.py)
- Direct evidence trace columns/migration: [`mas/migrations/versions/0035_trace_correlation_evidence.py`](../../mas/migrations/versions/0035_trace_correlation_evidence.py)

## Store ownership

| Store | Allowed purpose | Prohibited purpose |
| --- | --- | --- |
| Postgres | Canonical structured state, audit, revisions, usage, relations | Ephemeral high-volume message transport. |
| pgvector | Project-scoped semantic/hybrid retrieval | Unscoped global memory or canonical source replacement. |
| Redis | Streams, pending work, cache, leases, rate/coordination state | Long-term truth, artifacts, or direct worker database. |
| S3-compatible object store | Content-addressed artifacts, documents, reports, logs, checkpoints | Authority decisions without matching Postgres metadata. |
| Optional Letta/Qdrant | Certified memory/retrieval enrichment | Bypassing project scope, retention, audit, or deletion. |
| Optional Temporal | Certified durable workflow execution | Replacing AIAT's state/authority model. |

## Object-storage target

MinIO remains the current backend. Business logic targets an S3-compatible abstraction. SeaweedFS is the preferred benchmark candidate for future hot storage, with Garage/R2/B2 or another approved target for encrypted backup/replication.

Migration requires dual-read/write or verified-copy tooling, checksum parity, multipart/large-file and concurrency tests, outage recovery, URI compatibility, retention parity, measured resource performance, backup restore, a reversible cutover, and operator approval. Until that gate passes, documentation and code must continue to state that MinIO is current.

The checked-in fixture report is a static/unit contract result. In addition,
the retained local MinIO run is deployment evidence for the scoped contract;
it is not a claim about provider diversity or disaster recovery. The new
provider-pair certificate is a bounded two-endpoint MinIO exercise with
secondary-only recovery after adapter-boundary primary failure; it is not an
actual provider process/network outage certificate. The same command
can be run without Docker using the deterministic fixture or with `--live`
against a disposable provider adapter. The 2026-08-18 live MinIO/SeaweedFS
comparison and provider-pair rehearsal are retained at
[`object_store_provider_benchmark_evidence.json`](../../mas/docs/provenance/object_store_provider_benchmark_evidence.json)
and
[`object_store_provider_pair_provider_diverse_evidence.json`](../../mas/docs/provenance/object_store_provider_pair_provider_diverse_evidence.json);
they do not authorize routing or cutover.
The live path reports unavailable configuration/service state as `blocked` and
does not replace large-object, multipart, outage, benchmark comparison, backup,
or restore evidence.

The verified-copy helper (`aiat.object-store-copy.v1`) accepts explicit
`BlobRef` inputs, preserves the project prefix, verifies source and target
checksums/sizes, reads each target back, and removes a partially copied target
on failed verification. It never deletes source objects or performs a cutover.
`check_object_store_copy.py --live` builds those checksum-bearing refs from an
explicit source inventory and runs the same helper against a second
S3-compatible provider. It blocks on missing configuration or an empty source
inventory so a no-op cannot be mistaken for migration parity.

The backup/restore boundary adds a deterministic `aiat.object-store-backup.v1`
manifest and `aiat.object-store-restore.v1` verification result. The fixture
runner proves source → backup → clean restore with exact logical key sets and
read-back checksums, including an empty object. Its `--live` mode requires
three explicitly configured S3-compatible endpoints and blocks on missing
configuration, empty inventory, or provider failure; it does not claim
encryption, retention, disaster recovery, or a cutover.
The local MinIO rehearsal uses one provider with separate source, backup, and
restore buckets and is intentionally limited to manifest/read-back parity and
disposable-prefix cleanup; it does not certify provider diversity, encryption,
retention, clean-environment recovery, or regional outage handling.

The local encrypted envelope adds `aiat.object-store-encrypted-backup.v1` and
`aiat.object-store-encrypted-restore.v1`: AES-256-GCM is applied before
replication, manifests expose only opaque key IDs and scalar integrity
metadata, and authenticated read-back rejects wrong keys and tampering. The
fresh-process certificate persists only ciphertext and scalar metadata,
reopens it in a distinct process with fresh adapters, verifies/replicates it,
and cleans the temporary bundle. This closes the local clean-process
prerequisite only; clean-host/filesystem recovery, provider-managed SSE/KMS,
external Garage/R2/B2, key custody, and regional/provider outage evidence
remain open.

The `aiat.object-store-migration.v1` workflow composes checksum inventory,
verified provider copy, optional dual-write parity, and explicit
human-confirmed cutover/rollback. It records the active bucket, manifest
digest, copy and restore evidence, dual-write records, and transition history
without silently changing deployment routing. The deterministic
`scripts/check_object_store_migration.py` fixture completes the full sequence;
its guarded `--live` rehearsal requires explicit disposable endpoints,
reserved-prefix seeding, and both human confirmation flags, then cleans only
that reserved fixture scope. Retention authority and production routing remain
outside the checker.

## Retention target

Each data class declares retention, archive, legal hold, export, deletion, backup, and restore behaviour. The default company manifest includes bounded trace retention/sampling metadata (`trace_days`, `trace_sample_rate`) for the operator evidence projection. Worker/runtime retirement preserves historic evidence and provenance. Credential deletion destroys secret material while retaining non-secret audit. Project archive is preferred over deletion.

## Remaining gaps

- Extend the bounded migration rehearsal into a separately approved production
  change only after provider-certified retention parity, deployment-routing
  ownership, rollback authority, and outage/recovery evidence are available;
  the provider-diverse inventory/copy/dual-write rehearsal already passes.
- Extend the benchmark beyond the retained 1/32/4096-byte cases with
  large-object, multipart, concurrency, resource, outage, and recovery
  comparison evidence; current timings remain informational.
- Run the encrypted envelope against a configured Garage, R2, B2, or other
  approved backend with provider-managed key custody/rotation evidence; the
  provider-neutral local certificate is already retained.
- Run the backup/restore runner against a real provider pair and retain
  encrypted-at-rest, retention, clean-host/clean-environment, and cross-store
  evidence; the same-provider local rehearsal and fresh-process encrypted
  restore certificate are already retained separately.
- Certify Letta, Qdrant, and Temporal only if their benefit exceeds operational cost.
- Extend the current company trace retention metadata to project-level narrowing
  and explicit erasure/hold workflows once the live retention runner exists.
- The refreshed local orchestrator is at migration `0036_native_trace_spans`
  and the bounded API/transport writer has live read-back evidence; connect
  native model/tool/audit/worker/integration writers to a representative live
  run and read back provider/webhook-level identity-service mail-edge/bounce
  span events so those SLO targets can move from explicit `no_data` to
  deployment evidence. The identity persistence path is now migration-backed;
  safe outbound delivery-attempt trace/span correlation is already durable,
  while direct model/artifact/integration evidence and API, PM/SCM, and recovery
  projections already have bounded durable read paths.
- Prove Postgres point-in-time recovery and cross-store consistency after restore.
- Add object lifecycle, orphan detection, garbage collection, and legal-hold tests.

## Acceptance criteria

- Canonical state can be restored with matching counts, revisions, ownership, and checksums.
- Artifacts are never marked complete without a verified object and metadata transaction/recovery path.
- Redis loss causes recoverable degradation, not loss of canonical project state.
- Semantic search never returns cross-project content.
- A backend migration preserves every object checksum and can roll back without broken references.
- Retention/deletion removes only the exact authorised scope and leaves required audit evidence.
