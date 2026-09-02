# AIAT visual and documentation system

This document is the maintenance guide for AIAT's repository presentation. It
keeps the GitHub landing page, diagrams, and documentation hub aligned with the
programme's actual architecture and internal-use scope.

## Design thesis

AIAT should read like a calm control room: authority is legible, execution
boundaries are visible, and evidence is easy to find. The visual language is a
graphite instrument field with cyan routing signals, amber approval and
untrusted-execution states, and green durable-evidence paths.

The presentation should make three things clear in the first few seconds:

1. AIAT owns the control plane, project state, approvals, and evidence.
2. External runtimes are replaceable workers reached through bounded adapters.
3. This is a personal, single-operator, internal-use programme.

## Visual language

| Token | Value | Use |
| --- | --- | --- |
| Graphite | `#070c12` | Canvas and page-level background |
| Control cyan | `#61d5e8` | Routing, authority, active system paths |
| Approval amber | `#f2bd67` | Approval gates and untrusted hand-off |
| Evidence green | `#72d3a0` | Durable state, audit, recovery, and reviewed evidence |
| Signal white | `#edf6fa` | Primary labels and key statements |
| Slate copy | `#9db0ba` | Supporting descriptions and secondary labels |

Colour is a signpost, not the only meaning-bearing channel. Diagrams also use
explicit labels such as `FAIL-CLOSED BOUNDARY`, `UNTRUSTED PROCESS`, and
`DURABLE STATE / EVIDENCE / RECOVERY`.

## Typography

Repository-owned SVG assets use a local system-font stack for readable display
copy and a monospace fallback for telemetry-style labels. They do not fetch,
embed, or depend on remote fonts. Markdown pages use GitHub's native rendering
and should rely on hierarchy, short paragraphs, tables, and deliberate callouts
rather than decorative formatting.

## Layout rules

- Keep the README's first viewport focused: banner, one-line promise, scope
  callout, status strip, and authority boundary before long implementation detail.
- Prefer one strong diagram over a gallery of unverified screenshots.
- Give every image useful alt text; give SVGs a `<title>` and `<desc>`.
- Keep diagrams wide enough to show the boundary at a glance and simple enough
  to remain readable when GitHub scales them down.
- Use tables for exact mappings and short code blocks for runnable commands.
- Link to maintained source-of-truth documents instead of copying their claims.

## Asset rules

Repository-owned presentation assets live under [`docs/assets/`](docs/assets/).
They must:

- depict the current AIAT boundary and terminology;
- be original geometry or clearly provenance-recorded material;
- avoid stock imagery, copied logos, tracking pixels, remote image calls, and
  unverified screenshots;
- remain accessible when viewed without colour perception; and
- be described in [`docs/assets/README.md`](docs/assets/README.md).

The current header and system map are intentionally self-contained SVGs. If a
future screenshot is added, record the capture scope, date, environment, and
whether it represents a fixture, operator-observed run, or live/provider state.

## Documentation rules

The documentation hub defines the reading order. The root README is the concise
operator brief; `Docs/current/` is the maintained feature set; `mas/docs/` holds
implementation and evidence references; historical research remains labelled as
historical.

Licence and source facts belong in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)
and [`mas/docs/provenance/third_party_components.yaml`](mas/docs/provenance/third_party_components.yaml).
They are provenance metadata for this internal instance, not visual decoration
and not an automated activation gate.

## Finish review

Before changing the presentation, confirm that:

- the first README viewport still states the product boundary accurately;
- all local documentation links and image paths resolve;
- the diagrams match `mas/docs/ARCHITECTURE.md` and the maintained feature set;
- the assets contain no accidental remote calls, credentials, or copied material;
- documentation and provenance checks pass; and
- commit authorship and dates remain truthful.
