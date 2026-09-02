# AIAT security

AIAT is a personal, single-operator, internal-use programme. It is not a
publicly operated service and this repository does not promise a public bug
bounty, response SLA, or production security certification.

## Security posture

AIAT keeps authority, project state, credentials, approvals, and evidence in
AIAT-owned services. External runtimes and tools are untrusted and must remain
behind bounded adapter, network, filesystem, secret, and sandbox profiles.

The default development and test paths are local. Passing a unit test,
installing a dependency, or configuring an endpoint does not establish trust,
provider readiness, live-action authority, or a release approval.

## Report privately

Do not open a public issue containing credentials, private provider responses,
personal data, exploit details, or an actionable secret.

For this private programme, send a report through the maintainer's private
channel or the private disclosure mechanism provided by the hosting platform.
Include only the minimum detail needed to reproduce the issue:

- affected commit or version;
- impacted component and boundary;
- safe reproduction steps;
- observed and expected behaviour; and
- whether credentials, data, or external systems may have been exposed.

If no private channel is available, stop at a high-level description and ask the
operator to provide one before sending sensitive details.

## If a secret is exposed

1. Revoke or rotate it immediately.
2. Preserve only the minimum evidence needed for investigation.
3. Remove the secret from working files and logs without copying it into an issue or commit.
4. Check Git history and provider audit logs for use.
5. Record the incident and recovery decision in the appropriate internal evidence path.

The ignored `.env`, local state roots, temporary runtime directories, provider
payloads, and model weights are not documentation material and must not be
published as screenshots or artefacts.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) and the [documentation hub](Docs/README.md)
for the wider change and evidence boundaries.
