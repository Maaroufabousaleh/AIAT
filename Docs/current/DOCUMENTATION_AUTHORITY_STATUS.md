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

## Clean-checkout limitation

The focused clean-checkout flow verification at commit `2a41b7b` passed the
template, node-schema, portability, and migration tests, the generated-schema
check, and the topology check. A detached clean checkout currently fails the
docs-index command because the dirty source workspace still contains many
implementation/provenance files referenced by the roadmap that have not yet
been committed in their own bounded groups. This is an explicit release
documentation gate, not a reason to weaken link checking or to treat missing
files as a licence decision.

The next documentation batches should commit those existing implementation and
evidence groups, rerun the clean checker, and update the release ledger only
when the clean result is reproducible.
