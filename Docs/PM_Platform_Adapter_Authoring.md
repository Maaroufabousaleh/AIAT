# PM/SCM adapter authoring guide

Implement one of the ports in `mas_core.integrations.ports` and keep provider
logic inside `mas_core.integrations.providers`. Do not add provider names to
worker tools, canonical models, or workflow code.

## Required rules

1. Use `ProviderConnection` and `ProviderHTTP`; resolve only named credentials.
2. Reject URLs with credentials/query/fragment and validate repository/project
   selectors before constructing request paths.
3. Declare capabilities explicitly. Raise an honest unsupported-capability
   error instead of emulating a missing feature.
4. Preserve provider delivery IDs, raw-body signature inputs, provider version
   tokens, stable content hashes, actor metadata, correlation IDs, and
   idempotency keys.
5. Make create/update/comment/link operations idempotent and safe to retry.
6. Return normalized commands only for objects the port owns. PR/check/commit
   facts belong in the evidence ledger, not the PM mapping table.
7. Add tests for authentication, scope, idempotency, pagination, retries,
   malformed payloads, signature failure, unsupported capabilities, and schema
   drift. Add a staging certification record before enabling `ACTIVE`.

8. Work-management adapters must implement read-only planning and
   digest-bound application for one canonical project. Plans distinguish the
   default dedicated provider project from an explicitly selected
   umbrella/issue-only profile, generate a unique valid provider selector,
   adopt/create the four stable AIAT fields, and report issue/comment webhook
   attachment as a manual blocker when the approved role cannot attach the
   provider app. The adapter never claims `ACTIVE`; orchestration storage owns
   the webhook, projection, and reconciliation activation gates.

## Registration

Register reviewed external adapters with `ProviderRegistry.register`. Built-in
`fake`, `youtrack`, and `github` kinds cannot be replaced at runtime. The
registry injects the credential resolver and, for GitHub, the server-side
installation-token broker.

## Certification evidence

Attach exact adapter version/commit, dependency lock hash, API permission
matrix, test output, webhook replay output, outage/dead-letter result, and
rollback result to the certification ledger. A passing fake adapter is a local
contract gate only.
