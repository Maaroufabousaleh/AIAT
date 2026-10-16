# AIAT deterministic file worker process image

This is a development candidate image, not an admission or activation record.
The process uses only Python's standard library. It reads one bounded
`WorkerRunRequest`, emits exactly one project-scoped `file_write` request over
the opt-in ProcessAdapter JSONL channel, waits for the correlated
`WorkerToolResponse`, and exits with a structured result. It has no workspace
mount, DB client, network client, provider key, or direct file-write code.

The Dockerfile pins its Python base by digest. Build only from the exact
candidate source commit using the bounded-context helper:

```bash
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="/tmp/aiat-deterministic-worker-candidate/${RUN_ID}"
mkdir -p "$RUN_DIR"
uv run python mas/scripts/build_deterministic_file_worker_image.py \
  --source-revision 1855a75fb17243d8298b84468372e7a6e496da46 \
  --builder aiat-wsl \
  --archive "$RUN_DIR/candidate.oci.tar" \
  --evidence "$RUN_DIR/candidate.provenance.json"
uv run python mas/scripts/build_deterministic_file_worker_image.py \
  --verify \
  --archive "$RUN_DIR/candidate.oci.tar" \
  --evidence "$RUN_DIR/candidate.provenance.json"
```

The helper creates a clean detached checkout, stages a context containing only
the pinned worker source file, pins the base image by digest, retains BuildKit
SLSA v1 provenance in the OCI output, imports the image, and verifies that the
full OCI index digest resolves in the configured local Docker daemon. The
report includes the OCI index and `linux/amd64` platform-manifest digests,
image labels, embedded-source hash, exact sanitized Buildx argv, and raw
index/platform/attestation/statement linkage. It refuses to overwrite an
existing archive or evidence file. The retained candidate record can be
verified with the archive path recorded in its `build.archive_path` field; do
not overwrite the checked-in evidence when building a new candidate.

BuildKit reports an empty `builder.id`; no signature or authenticated builder
identity is present. Treat the evidence only as an unsigned, unauthenticated
local source/image binding. It does not grant candidate security approval,
stewardship, or activation. A rebuilt digest is a new immutable candidate
identity/version pin; do not overwrite an existing pinned candidate in place.
Keep the OCI archive available for independent verification. It is a local
development artifact, not production registry promotion or production
provenance.

The OCI source revision label is the exact 40-character Git commit used for the
candidate; `com.aiat.worker.source-sha256` records the separately checked
entrypoint hash. The helper does not read `.env` files or include the operator
worktree in its build context. Before any commit, review the complete staged
diff and run the configured secret scanner; do not stage credentials or
secret-bearing logs/evidence.

At runtime OCIAdapter supplies `--network none`, drops all capabilities,
enables no-new-privileges, mounts no workspace or host paths, and applies a
read-only root filesystem and bounded resources. Any change to those settings
or to the worker source requires a new image digest and normal candidate
governance; this document does not assert security approval, certification, or
eligibility.
