# AIAT image candidate registry release

This procedure promotes already scanned and attested image archives to private
GHCR candidate packages. It does not change a governed worker pin, certify an
AIAT production release, modify live Redis, or authorize deployment.

## Current evidence

Both evidence workflows passed for source commit
`1d3e4fcb0f759eeb139ea167cdf7bfc5d104c575`:

| Candidate | CI run | Image identity in the evidence archive | Archive SHA-256 |
| --- | --- | --- | --- |
| Alpine deterministic worker | [43277606064](https://github.com/Maaroufabousaleh/AIAT/actions/runs/43277606064) | OCI index `sha256:c733919c487a5b95b2909e5129f97a0da4102393090f7747a0c2b85970c0fc88`; platform manifest `sha256:f05e87ffd034d58cc26b1932788e8f5e1d307cd22ec04cce62d0bbbe8df7e9d3`; image config `sha256:b93d6ca177cfa57d473eccf6c8e059c14582bb0fca7e02526d19adfbd21ab132` | `2c4a2f0abbba59b0b61297e52bae49bf33e0a701980d37e6bae52882ce822648` |
| Redis ACL initializer | [43277606886](https://github.com/Maaroufabousaleh/AIAT/actions/runs/43277606886) | Image config `sha256:fcf96f020bed4fad0466af6f38242cd4e56946dfac93d3852ce3e1597d2d214a` | `3d273f32052b0ff69d80c7be3f6c3b1d63eb2e273f85a633bfa20f780992992d` |

The worker evidence has zero high/critical image and source findings, a passing
repeat platform/config identity check, and a passing gVisor process-protocol
test. Its OCI index includes run-specific BuildKit provenance; its platform
manifest and image config were reproducible. The Redis evidence reports zero
high/critical findings and passed disposable restart, verifier, regression,
SBOM, and archive-attestation checks. The GitHub attestations currently bind
the candidate OCI archives, not registry images. The worker has not completed
governance approval or a genuine WorkerRun. Live Redis remains under its
release hold.

The ZIP artifacts were retained locally outside the repository at
`%LOCALAPPDATA%\Temp\aiat-release-evidence-20261112` and verified against the
GitHub artifact digests. New evidence workflow artifacts use a 90-day retention
setting; the earlier run artifacts still expire on November 26, 2026.

## Candidate publication controls

`.github/workflows/image-candidate-registry-release.yml` is manual-only. Its
default is evidence preflight; it has no registry write permission unless the
operator explicitly sets `publish_candidate=true`. The workflow accepts only
the exact head of `agent/fix-review-p1`, verifies that each supplied evidence
run is successful and belongs to that SHA, checks the retained report and
archive hashes, and verifies the archive attestations again.

The package destinations are:

- `ghcr.io/maaroufabousaleh/aiat-deterministic-file-worker`
- `ghcr.io/maaroufabousaleh/aiat-redis-acl-init`

The repository owner and linked GitHub login are both
`Maaroufabousaleh`. A first GHCR container publication defaults to private.
Publishing with this repository's `GITHUB_TOKEN` also links a newly created
package to the workflow repository. The publish job checks existing package
visibility/linkage before pushing and checks them again after the push. It
grants `packages: write` only to the protected publish job. It pushes a
source-SHA candidate tag for discovery, then records and verifies the returned
registry digest. Only `ghcr.io/...@sha256:...` references are deployment
identities; tags are not treated as immutable.

The first use of the workflow must not proceed until the operator has:

1. Added the workflow to the default branch through the normal reviewed
   process. GitHub only exposes `workflow_dispatch` after the workflow file is
   present on the default branch; select `agent/fix-review-p1` as the run ref.
2. Created the `aiat-image-candidate-release` environment and configured its
   required human reviewer(s).
3. Verified in the package settings that both target names are unused or are
   already private and linked to `Maaroufabousaleh/AIAT`.
4. For an authorized candidate only, set these repository variables:
   `AIAT_GHCR_CANDIDATE_PUBLICATION_ENABLED=true`,
   `AIAT_GHCR_CANDIDATE_APPROVED_SHA=<the exact reviewed 40-character SHA>`,
   `AIAT_GHCR_CANDIDATE_VISIBILITY=private`, and
   `AIAT_GHCR_CANDIDATE_ENVIRONMENT_PROTECTED=true`.

Those variables are deliberately unset by this change. A failed metadata
preflight, missing review, image-scan finding, artifact mismatch, or registry
attestation verification stops the candidate flow. Registry provenance and
SBOM attestations are attached to the actual returned image digest, and the
workflow pulls that digest back to verify its platform, config, and layer
payload against the scanned local candidate. It does not change Compose pins
or the governed worker image reference.
