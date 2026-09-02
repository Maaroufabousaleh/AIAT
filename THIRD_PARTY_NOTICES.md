# AIAT third-party notices

AIAT is a personal, single-operator, internal-use programme. The repository's
original source and visual assets are covered by [`LICENSE`](LICENSE). This
document does not relicense any third-party component. External software,
services, model references or weights, datasets, provider data, and other
resources retain their own licences, terms, notices, and restrictions.

> [!IMPORTANT]
> Licence information is metadata for this personal instance. It helps the
> operator understand provenance and obligations; it is not a substitute for a
> separate distribution review.

## At a glance

| Surface | What it means |
| --- | --- |
| AIAT source and docs | Repository-owned material identified by [`LICENSE`](LICENSE) |
| AIAT visual assets | Original SVG geometry under [`docs/assets/`](docs/assets/) |
| External runtimes and tools | Reached through dependencies, external processes, services, CLIs, or AIAT adapters |
| Technical identity | Exact version, release, commit, image digest, or explicit unavailability |
| Licence identity | Detected or declared licence/source evidence when known |
| Runtime decisions | Controlled by security, authenticity, sandbox, compatibility, privacy, budget, recovery, and human-approval evidence |

## Machine-readable catalogue

The authoritative component inventory is
[`mas/docs/provenance/third_party_components.yaml`](mas/docs/provenance/third_party_components.yaml).
Technical runtime and CLI pins are tracked separately in
[`mas/docs/provenance/operator_pins.yaml`](mas/docs/provenance/operator_pins.yaml).
Release, image, security, and runtime evidence remain in the surrounding
[`mas/docs/provenance/`](mas/docs/provenance/) directory.

Each catalogue entry records what is known about:

- component identity, role, integration mode, and active status;
- exact version, release, commit, image reference, or digest;
- canonical source and licence evidence links;
- stated notices, restrictions, and unresolved operator questions; and
- compatibility, security, sandbox, and certification evidence where applicable.

Package-managed dependencies are enumerated by [`mas/uv.lock`](mas/uv.lock) and
the dashboard lockfile. A future wheel, container, executable, model bundle,
or cached-data publication must build its own complete licence/notice bundle
from the exact dependency closure; this catalogue is not that bundle.

## Metadata-only policy

For this internal programme:

- AIAT records known licence data and stated restrictions rather than hiding them.
- AIAT has no licence allowlist or prohibited-licence list.
- Missing or unusual metadata creates an operator notice, not a default denial.
- Licence metadata is not an automated hiring, activation, installation, update, execution, or release gate.
- Technical suitability, authenticity, security, privacy, sandbox, compatibility,
  budget, data-loss, recovery, and human approval remain enforceable controls.
- A resource is not treated as AIAT authority code merely because an adapter can invoke it.

This policy does not waive, reinterpret, or change a third party's terms. The
personal operator owns the decision to use a resource and any obligations that
may apply.

## Repository-owned visual assets

The current GitHub presentation uses original, repository-owned SVG geometry:

- [`docs/assets/branding/aiat-header.svg`](docs/assets/branding/aiat-header.svg)
- [`docs/assets/architecture/aiat-system-map.svg`](docs/assets/architecture/aiat-system-map.svg)

They contain no remote images, tracking pixels, copied logos, external fonts,
or model-weight content. Their provenance and maintenance rules are documented
in [`docs/assets/README.md`](docs/assets/README.md).

## What is not bundled

The source repository does not intentionally vendor third-party source trees,
model-weight files, provider payloads, or training datasets. A dependency or
provider named in an architecture document is not by itself evidence that its
source or data is copied into AIAT.

The exact active integration boundary is the source of truth. If a component is
installed, vendored, modified, redistributed, or shipped inside a new artefact,
update the catalogue and preserve the applicable notices before distribution.

## Scope change

If AIAT is later sold, distributed, hosted for others, or used commercially,
create a separate distribution-specific review. Do not reuse this
internal-only metadata policy as that review, and do not assume that recording a
licence link changes any third-party obligation.
