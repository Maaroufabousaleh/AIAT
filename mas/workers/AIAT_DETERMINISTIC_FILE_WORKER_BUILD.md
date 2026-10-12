# AIAT deterministic file worker process image

This is a development candidate image, not an admission or activation record.
The process uses only Python's standard library. It reads one bounded
`WorkerRunRequest`, emits exactly one project-scoped `file_write` request over
the opt-in ProcessAdapter JSONL channel, waits for the correlated
`WorkerToolResponse`, and exits with a structured result. It has no workspace
mount, DB client, network client, provider key, or direct file-write code.

The Dockerfile pins its Python base by digest. Build from the repository root:

```bash
SOURCE_SHA="$(sha256sum mas/workers/aiat_deterministic_file_worker_process.py | cut -d ' ' -f 1)"
docker build --pull \
  --file mas/workers/Dockerfile.aiat_deterministic_file_worker \
  --build-arg "AIAT_WORKER_SOURCE_SHA256=${SOURCE_SHA}" \
  --tag aiat-deterministic-file-worker:local \
  mas/workers
```

The local tag is only a build convenience. Do not put it in a worker manifest.
Capture the image's content-addressed repository reference:

```bash
docker image inspect \
  --format '{{range .RepoDigests}}{{println .}}{{end}}' \
  aiat-deterministic-file-worker:local
```

The result is specific to the source, build recipe, and Docker daemon. Record
the exact `repository@sha256:<manifest-digest>` result in the matching
immutable worker candidate and evidence report. If `RepoDigests` is empty or
the image must be used by another host, tag and push to the selected registry,
then run the same inspect command against the pushed reference. The governed
candidate must also retain the source SHA label recorded in the image. Never
infer a manifest digest from a mutable tag or substitute the local image-config
ID for a manifest digest. A local digest is development evidence, not registry
promotion or production provenance.

At runtime OCIAdapter supplies `--network none`, drops all capabilities,
enables no-new-privileges, mounts no workspace or host paths, and applies a
read-only root filesystem and bounded resources. Any change to those settings
or to the worker source requires a new image digest and normal candidate
governance; this document does not assert security approval, certification, or
eligibility.
