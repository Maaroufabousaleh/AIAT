# GitHub setup

Create one GitHub App for the AIAT deployment and install it only on explicitly
selected repositories. Do not create a GitHub user per agent. Use separate
permission profiles: `pm` (metadata/issues), `delivery` (contents/pull
requests), and `checks` (checks write). Keep workflows and administration off
unless a separately approved adapter requires them.

Store the App private key in the AIAT credential manager. A connection using
the built-in broker must configure non-secret `github_app_id`,
`github_installation_id`, `github_app_private_key_ref`, and `repository`.
The orchestrator signs a short-lived App JWT, exchanges it for a
repository/permission-scoped installation token, and returns it only to the
governed run. The private key and token are never persisted in evidence.

Configure `webhook_secret_ref`/`webhook_secret_refs`, point events at
`pm-gateway`, and verify `X-Hub-Signature-256` over the unmodified body. Branch
names, repository paths, and event repository names are scope-checked.

Use AIAT branch names, commit trailers, PR bodies, and checks for attribution;
do not impersonate agents with `Co-authored-by`. PRs, reviews, checks, and
commit responses are persisted as source-control evidence. Run installation
discovery, least-privilege denial, token expiry, webhook replay, outage, and
rollback tests in a disposable staging repository before activation.
