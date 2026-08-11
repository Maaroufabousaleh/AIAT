# Documentation Authority Status

**Updated:** 2026-08-11
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
(`823fa6d`), each with separate
documentation updates. The static contract currently passes 11 team files and
39 exact agent-to-manifest bindings. These checks establish technical identity
only; registration, activation, certification, and licence metadata remain
separate.

## Clean-checkout verification

The focused clean-checkout flow verification at commit `2a41b7b` passed the
template, node-schema, portability, and migration tests, the generated-schema
check, and the topology check. The current workspace and a clean Git archive
both pass `check_docs_index.py`; the workspace lock is now tracked at
`mas/uv.lock`, so the default runtime contract is reproducible from source.
