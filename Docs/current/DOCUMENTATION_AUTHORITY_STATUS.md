# Documentation Authority Status

**Updated:** 2026-08-12
**Roadmap:** [AIAT Roadmap](../../ROADMAP.md)
**Authority:** [AIAT Target Programme](../../AIAT_TARGET_PROGRAMME.md)
**Scope:** personal/internal AIAT instance

## Audit result

The repository documentation audit read 85 Markdown documents plus the
available PDF/DOCX architecture and research sources. The maintained authority
set is intentionally smaller:

- one normative target programme (`AIAT_TARGET_PROGRAMME.md`);
- one root navigation/delivery roadmap (`ROADMAP.md`);
- eleven current feature specifications;
- three ordered plans; and
- focused implementation/review status notes linked from the roadmap.

Historical research, live-test ledgers, deployment runbooks, prompts, and
provider setup guides remain useful evidence or operating references. They do
not override the target programme. Where a historical document mentions a
licence allowlist, commercial-use restriction, prohibited component, or
licence-based activation decision, the current policy overlay marks that text
as superseded: licence and stated-use information is metadata only for this
personal/internal instance. Technical source integrity, version, security,
sandbox, privacy, compatibility, budget, approval, and recovery evidence
remain independent controls.

## Machine-checked status

The current workspace reports:

```text
canonical features: 11
canonical plans: 3
maintained documents: 20
licence metadata is a gate: false
```

Run from `mas/`:

```bash
uv run --isolated python scripts/check_docs_index.py --json
```

The checker validates maintained links, roadmap references, and the
metadata-only markers without evaluating or blocking any resource by licence.

The latest bounded implementation groups are reflected in the maintained
authority set: team-runner declaration reconciliation (`d9b1262`), production
startup reconciliation and `AgentConfig`/health propagation (`569231f`),
persisted model-profile bootstrap (`09bdd19`), flow schema/retry hardening
(`234adfb`), company-timezone propagation (`ee1361f`), project-evidence
typecheck repair (`fc4f0fa`), team-runner boundary hardening (`22fc21a`),
dashboard operation-selector hardening (`e378f40`), metric reconciliation
compatibility (`541d6e0`), and the isolated project-evidence router boundary
(`33e0384`), bounded artifact/usage evidence reads (`2ca5f3d`), stale
evidence-detail refresh retention (`6c52552`), and governance read-surface
stale/retry recovery (`52de581`), and System Control stale/retry recovery
(`f445c17`), Projects list stale/retry recovery (`d3482ab`), Tools catalogue
stale/retry recovery (`5f4b0eb`), and dead-letter queue stale/retry recovery
(`823fa6d`), credentials metadata stale/retry recovery (`970f09c`), shared
worker registry grant/update-policy hardening (`d8cafbb`), identity-resource
stale/retry recovery (`46eccee`) and table accessibility
(`651ad11`), Metrics
partial/stale/retry recovery (`85596b0`), and Flows list stale/retry recovery
(`a0faf5b`), the credentials render-state lint repair (`e6e6980`), Container
Logs stale/retry recovery (`280d363`), Agent Streams reconnect/history
recovery (`3e8a0ea`), Hiring Board stale/retry recovery (`7541b84`), CEO
Live Feed reconnect/history recovery (`1761429`), and CEO Command Center chat
stream/history recovery (`beabb95`), and the secret-safe system diagnostics
route/API contract group (`2860838`) and the API-facing operator CLI group
(`380daf5`, executable-mode follow-up `f8df50e`), and the message-router
sender role/team coherence group (`fb39128`), the hierarchy communication-policy
overlay group (`8b7d9f1`), its evidence-test cleanup (`3dc61ad`), focused live
E2E selector hardening (`d5f596e`), generalized Docker temp-context exclusions
(`b3a2e8e`), and fail-closed staged-context handling (`45ee42c`), each with
separate documentation updates. The storage
safety group `93bf755` now rejects
non-empty restore prefixes before copy and records clean-target verification;
the static contract currently passes 11 team files and
39 exact agent-to-manifest bindings. The Credentials denial-state recovery
group `982c9c0` now preserves only previously loaded redacted metadata and
hides read/mutation controls on 401/403 access loss; its source-built fixture
matrix passes 3/3. The CEO Live Feed denial-state recovery group `a3cbd99`
now applies the same boundary to history, SSE, and composer responses; its
source-built fixture matrix passes 3/3. The Agent Streams denial-state recovery
group `118ff18` now applies the same boundary to history and SSE responses; its
source-built fixture matrix passes 3/3. These checks establish technical
identity only; registration, activation, certification, and licence metadata
remain separate.
The Container Logs denial-state recovery group `156597c` now applies the same
boundary to SSE responses; its source-built fixture matrix passes 3/3.
The Metrics denial-state recovery group `b64b15e` now applies the same boundary
across six Prometheus query families; its source-built fixture matrix passes
3/3.
The dead-letter queue denial-state recovery group `e6ab3a1` now applies the
same boundary to reads and replay responses; its source-built fixture matrix
passes 3/3.
The Tools catalogue denial-state recovery group `b418f8a` now applies the same
boundary to catalogue reads; its source-built fixture matrix passes 3/3.
The Flows list denial-state recovery group `3108b02` now applies the same
boundary to list reads and deletes; its source-built fixture matrix passes 4/4,
covering stale retention, initial denial, retained-read denial, and mutation
denial while keeping retained definitions read-only.
The flow-editor denial-state recovery group `392d264` now applies the same
boundary to canonical flow reads and saves; its source-built fixture matrix
passes 4/4, covering stale recovery, initial denial, retained-read denial, and
mutation denial while keeping the retained canvas read-only.
The Projects list denial-state recovery group `17d25b0` now applies the same
boundary to paired project/active-flow reads and create/archive/delete
mutations; its source-built fixture matrix passes 4/4, covering stale
recovery, initial denial, retained-read denial, and deletion denial while
keeping retained definitions read-only.
The Project Detail denial-state recovery group `0671eaa` now applies the same
boundary to canonical project reads and workflow/mutation responses; its
source-built fixture matrix passes 4/4, covering transient recovery, initial
denial, retained-header denial, and workflow-mutation denial while retaining
only the last-known project header and hiding all tabs and mutation surfaces.
The Project Evidence package denial-state recovery group `00f81b5` now applies
the same boundary to canonical evidence-package reads; its source-built
fixture matrix passes 3/3, covering stale recovery, initial denial, and
retained-package denial while keeping the retained package read-only and
preserving safe Back to project navigation.
The Evidence Detail denial-state recovery group `23e2db9` now applies the same
boundary to bounded scalar reads; its source-built fixture matrix passes 11/11,
covering scalar redaction, stale recovery, initial denial, and retained-read
denial while keeping citation identity and safe navigation available.

## Clean-checkout verification

The focused clean-checkout flow verification at commit `2a41b7b` passed the
template, node-schema, portability, and migration tests, the generated-schema
check, and the topology check. The current workspace and a clean Git archive
both pass `check_docs_index.py`; the workspace lock is now tracked at
`mas/uv.lock`, so the default runtime contract is reproducible from source.
