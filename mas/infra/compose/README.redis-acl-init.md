# Redis ACL initializer

The initializer disables the default bootstrap identity, revokes its existing
connections, and saves the disabled ACL state before reporting success. It
checks the Redis ACL file through the read-only `redis_data` mount. Do not run
an initializer image against an existing data volume without a preflight and
an independently verified recovery path.

For a repeat initialization, restricted application users cannot inspect the
default user's ACL. Configure a separate verification identity with only the
Redis commands needed to authenticate and run `ACL GETUSER`, and provide its
name in `REDIS_ACL_VERIFY_USER` and a mounted secret-file path in
`REDIS_ACL_VERIFY_PASSWORD_FILE`. That permission can disclose password hashes
through `ACL GETUSER`; protect the account and its secret accordingly. The
credential file must be supplied by the deployment's secret mechanism, not
committed or printed. Merely providing environment variable names does not
provision the user, mount the secret, or certify a release.

The initializer fails closed if the verifier is unavailable, the effective
default user is enabled, the saved default-user state is missing or enabled, or ACL
persistence fails. Compose therefore cannot restart dependent services after a
repeat initialization until the verifier has been provisioned and configured.
The existing recovery administrator is not the verifier and must be preserved.

Production image refs remain release-ledger inputs. A local candidate image is
not a release pin: create the immutable image, provenance, SBOM and scan
evidence through the governed native-Linux release workflow before deployment.

## Temporary release hold

Compose currently replaces the initializer entrypoint with an exit-78 hold.
The checked-in lifecycle wrappers also block activation paths that could start
the old initializer. This does not change an existing Created container or
intercept direct Docker or Compose commands; direct start of an existing
container remains possible. Do not use direct commands to bypass the hold.
Once release evidence and verifier setup are approved, replace the existing
container during the approved window, set the certified immutable image ref,
and remove the hold in the same reviewed deployment change. Reapply the hold
if rollback returns to the uncertified configuration. Never roll back the
live ACL to the previously exposed bootstrap configuration as a routine step.

Verifier credential rotation requires a maintenance plan: provision a new
protected secret and an independently authenticated recovery path, update the
verifier identity and saved ACL under controlled authorization, update the
mounted secret, then test a new connection, disabled default state, saved ACL,
and initializer idempotence. Revoke the old verifier credential only after the
replacement works. Preserve the recovery administrator separately. A failure
must block dependent startup and be handled using the tested recovery plan.
No live verifier identity or secret is created by this documentation.
