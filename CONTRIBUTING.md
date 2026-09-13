# Contributing to AIAT

AIAT is maintained as a personal, single-operator, internal-use programme.
This guide describes the repository contract for changes made by the operator
or by supervised development workers; it is not a public contribution promise.

## Before changing code

Read the [documentation hub](Docs/README.md), then the closest current feature
specification, implementation reference, and tests. Keep authority and project
state inside AIAT. Treat every external runtime as untrusted execution behind a
versioned adapter or bounded tool boundary.

## Change boundaries

- AIAT-owned orchestrator, router, tool service, registries, credentials,
  approvals, dashboard, company control plane, managers, and evidence remain
  authoritative.
- Worker/runtime changes belong in shells, adapters, manifests, or explicit
  compatibility profiles; do not hardcode external worker behaviour into the
  control plane without a boundary decision.
- Security, authenticity, sandbox, network, filesystem, privacy, data-loss,
  budget, compatibility, recovery, and human-approval gates are independent of
  licence metadata.
- Do not commit secrets, local databases, provider payloads, model weights,
  generated runtime evidence, or files from ignored temporary directories.

## Documentation and visual assets

Implementation truth wins over plans and historical research. When a boundary
or interface changes, update the nearest maintained document and the relevant
release/evidence reference.

Repository-owned visual assets live under [`docs/assets/`](docs/assets/). Keep
them original, accessible, and maintainable. Prefer SVG geometry or Mermaid for
architecture views; do not add stock imagery, remote tracking pixels, copied
logos, or unverified screenshots. Label synthetic or fixture-based visuals
clearly.

External component metadata belongs in
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) and
[`mas/docs/provenance/third_party_components.yaml`](mas/docs/provenance/third_party_components.yaml).
Record the known source, exact version or digest, licence, notices, and stated
restrictions. The catalogue is metadata, not a licence allowlist or activation
gate for this internal instance.

## Local checks

From `mas/`:

```bash
uv sync
uv run python -m compileall -q packages apps
uv run python scripts/check_provenance.py
uv run python scripts/check_docs_index.py --json
uv run python scripts/check_release_ledger.py --json
uv run pytest
uv run ruff check .
uv run mypy .
```

For dashboard changes:

```bash
cd mas/apps/mas-dashboard
npm install
npm run typecheck
npm run build
npm run test:e2e
```

Live/provider, Docker, native-sandbox, and external-service checks remain
explicit operator workflows. A fixture pass does not establish live readiness.

## Commit and review notes

Use a concise imperative subject and explain the boundary affected. Before
integrating a change, record:

- what changed and why;
- which authority, state, or evidence boundary is affected;
- checks run and checks intentionally not run;
- any new external source, version, image digest, licence, notice, or restriction;
- any remaining operator or human-approval gate.

Keep unrelated refactors separate. Preserve original commit metadata; do not
rewrite dates or authorship to make activity appear to have happened at a
different time.

## Licence and contribution terms

The original AIAT Work is covered by [`LICENSE`](LICENSE). Third-party material
retains its own terms. This repository does not make a blanket inbound
contribution or CLA promise; the maintainer must approve any contribution terms
before accepting material intended for redistribution.
