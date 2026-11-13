# AIAT image candidate promotion

This workflow only promotes the exact image archive already scanned and attested by the pinned evidence runs. It never checks out the candidate source revision in a job with package-write permission and never rebuilds the candidate payload.

## Review and dispatch model

The pull-request event runs only the read-only unit tests, actionlint, and a checksum-pinned Gitleaks scan of the added promotion files. The promotion entry point is `workflow_dispatch` and is intentionally usable only from `main`. GitHub exposes manual dispatch only after the workflow file exists on the default branch, so review and merge of this small entry-point PR are prerequisites to dispatch; merging the separate feature branch is not required.

The manifest pins the candidate source SHA, repository CI run, worker evidence run, Redis evidence run, artifact IDs/names/digests, inner archive checksums, image identities, package names, and required order. The dispatch must select exactly one image, provide every matching run ID, and execute the workflow revision on `main`. A mismatch or missing API result fails closed. HTTP 404 is treated as unverifiable, never as proof that a package or setting is absent.

The read-only preflight checks completed successful evidence runs, their branch/SHA/workflow identity, artifact metadata, archive checksums, report contents, original archive attestations, source branch protection, the release environment's actual reviewer rule, and required reviewer access. Package metadata uses a separate read-only GitHub Packages credential with only `read:packages`; it is not available to the publisher job. The API must return the exact private package owner and repository link. The preflight also obtains a pull-only GHCR bearer token and confirms the exact full-SHA candidate tag does not already exist, so a rerun cannot silently retag an existing candidate. The registry permission mode is not exposed by the metadata response used here, so an administrator must inspect the package settings and record `AIAT_GHCR_PACKAGE_ACCESS_MODE=repository_inherited` and `AIAT_GHCR_PACKAGE_ACCESS_VERIFIED_SHA=<full candidate SHA>` only after verifying that the AIAT repository has the required Actions access. These values are an administrator's verification record, not a claim that the REST response exposed granular package ACLs.

Before any candidate publication can run, configure and verify:

- The `aiat-image-candidate-release` environment with at least one required reviewer, self-review prevention, and protected-branch restriction. The workflow reads and checks these actual settings and verifies reviewer repository access.
- Main-branch protection requiring a pull-request review and enforcing the rule for administrators.
- A read-only GitHub App credential for environment, branch-protection, and reviewer checks.
- A separate read-only GitHub Packages credential (`read:packages`) for private package metadata. Do not use a write-capable PAT here.
- Both exact, private, AIAT-linked GHCR packages, with Actions access verified in package settings. The workflow rejects missing or inaccessible package metadata; it does not infer safety from a 404 or create/change package visibility.

If any of these controls cannot be read or verified, the read-only preflight fails and the package-write job remains blocked. The `publish_candidate` input defaults to false. The publisher has `packages: write` only after all preflights pass and requires the protected release environment. It publishes only `candidate-<full source SHA>`; it does not publish `latest`, modify deployment pins, or deploy.

## Evidence and identity boundaries

The manifest distinguishes the outer Actions artifact ZIP checksum, inner OCI/Docker archive checksum, OCI index digest, platform manifest digest, and image configuration digest. A successful archive attestation proves the archived subject and its recorded workflow identity. It does not retroactively authenticate the BuildKit builder. The current worker evidence explicitly records `buildkit_builder_authenticated=false`; therefore a registry promotion of that archive would remain a candidate transfer and would not satisfy any governance policy requiring authenticated builder provenance. The promotion attestation binds the immutable registry digest to the verified source/archive evidence, but it is not a replacement for original builder provenance.

The Redis initializer must be promoted and freshly verified before the worker candidate. Each fresh-runner verification pulls the image by immutable digest, compares platform and payload identity with the scanned archive, and verifies registry provenance, SBOM, and the promotion record. A candidate image digest is not production approval.

## Current authorization boundary

This PR prepares code and tests only. It does not publish to GHCR, provision credentials, change package settings, modify live Redis, release the live startup hold, activate a worker, or change any deployment pin. Publication still requires separate explicit operator authorization for the exact destination and candidate. Redis live replacement requires a separate maintenance approval.
