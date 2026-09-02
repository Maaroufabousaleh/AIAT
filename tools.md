# AIAT Tool Catalogue and Integration Boundary

> **Current policy (2026-08-10):** licence, notice, and stated-use data are
> informational metadata only for this personal/internal programme. They are
> recorded when known, but never form an AIAT allowlist, prohibited-component
> list, hiring gate, activation gate, or execution gate. Technical safety,
> privacy, compatibility, provenance, resource, and approval controls remain
> authoritative. See [`AIAT_TARGET_PROGRAMME.md`](AIAT_TARGET_PROGRAMME.md) and
> [`ROADMAP.md`](ROADMAP.md).

AIAT owns the authority boundary. An OSS or external capability is a replaceable
implementation behind an AIAT tool adapter; it does not become a second
control plane. The tool service owns typed input/output, permission checks,
rate limits, budgets, audit records, caching, circuit breaking, and sandbox or
network policy.

## Authority tools and capability adapters

| Tool area | AIAT-owned contract | Current implementation profile |
| --- | --- | --- |
| Tool-service shell | `POST /tools/{tool_name}/run`, catalog, health, audit | AIAT FastAPI service and SDK |
| Workflow/project authority | project state, transitions, flows, approvals, evidence | AIAT orchestrator and Postgres |
| Documents and reviews | draft/submit/revise, review sessions, comments, evidence | AIAT records with Docling/GitHub Spec Kit adapters; `document.ingest` falls back to explicit degraded plain-text output when Docling is absent |
| Planning and issues | sprints, issues, KPIs, canonical mappings | AIAT records with ccpm/GitHub Issues/YouTrack adapters |
| Blob and artifact boundary | upload/download/list/delete, hashes, retention | AIAT object-storage wrapper |
| Safe commands and repositories | `command.run_safe`, file/repo search and patch | AIAT permission, workspace, sandbox, and egress wrappers |
| Coding and testing | governed worker/tool requests and normalized results | OpenCode/OpenHands core, pytest, Playwright adapters |
| Research and web fetch | bounded search, fetch, scrape, browser sessions | Scrapling and browser adapters with identity/network/action limits |
| Code review | normalized review findings and status | AIAT deterministic diff reviewer by default; external pr-agent/open-code-review/stage-cli candidates use the exact-pin catalogue |
| Security evaluation | scan results and security evidence | `security.scan` with Semgrep/SkillSpector/TruffleHog aliases and sandbox tests; additional scanners use the same adapter boundary |
| DevOps and IaC | plan/apply requests, CI/CD evidence, health checks | OpenTofu, GitHub Actions, monitoring adapters |
| Diagrams and specifications | Mermaid and document/spec exports | Mermaid, Docling, GitHub Spec Kit |
| Protocol bridge | typed external tool/worker calls | MCP SDKs and certified server adapters |
| Isolation | worker/tool execution boundary | gVisor baseline; Firecracker optional for high-risk work |

## Default personal-instance profile

The default profile is chosen for technical fit and operational simplicity, not
licence classification. The current baseline is LangGraph/CrewAI/Microsoft
Agent Framework for worker runtimes; OpenCode/OpenHands core, Playwright, and
pytest for coding/testing; Docling, GitHub Spec Kit, and Mermaid for documents;
Semgrep and SkillSpector for security; OpenTofu and GitHub Actions for DevOps;
ccpm/GitHub Issues for planning; LiteLLM and OmniRoute for model/routing
analytics; and Letta, Qdrant, Temporal, and MCP where their integration
contracts are enabled.

Heavier dependencies (browser, Docling, Semgrep, and Mermaid/Node) are built in
the separately budgeted tool-service `extensions` profile. The general
`core` image stays small enough for ordinary orchestration and delegates
extension work through explicit adapter or sidecar policy. The document path
remains usable in the core profile: when Docling is missing, `document.ingest`
returns source text with `available: true`, `configured: true`,
`degraded: true`, and `backend: plain_text_fallback` rather than a false
unavailable result.

## Optional or replaceable integrations

TruffleHog, Plane, OpenProject, ZITADEL, Vault, Ansible, Neo4j, Grafana,
AutoGen, OpenClaw, browser-use, Firecracker, n8n, Garage, SeaweedFS, and other
tools may be used normally in this personal instance when their technical
boundary is useful. They are optional because of architecture, operations,
resource cost, maturity, or deployment choice—not because AIAT applies a
licence prohibition. They must still respect the AIAT control plane, identity,
network, workspace, sandbox, approval, and audit contracts.

## Tool-service groups

| Group | Representative tools | Typical authority |
| --- | --- | --- |
| Workflow | `project.*`, `flow.*`, `approval.*`, `privileged_ops.request` | CEO/orchestrator; request is audited by `/ceo/privileged-action` and may require human approval |
| Document | `document.*`, context, artifact references | chiefs, PMs, and workers according to project grants |
| Review | `review.*` | review leads and designated C-suite roles |
| Planning/issue | `sprint.*`, `issue.*`, `kpi.*` | CTO/PM roles with project-scoped grants |
| DevOps/security | `infra.*`, `cicd.*`, `monitoring.*`, scan adapters | DevOps/security workers through sandboxed adapters |
| Utility | bounded web, repository, blob, search, and reporting tools | explicit per-worker capability grants |

Every invocation records the caller, capability, project/run reference when
available, adapter/version, normalized result, resource usage, and audit
outcome. A tool may be added or replaced without changing AIAT's authority
model; the adapter manifest and evidence ledger are the compatibility record.

## Metadata record

The provenance catalogue may retain source URL, exact version or digest,
repository revision, dependency/image lock, SBOM and scan references, licence
identifier, notices, and any stated use/modification/redistribution language.
Those fields help the operator understand what is running. They do not change
the technical activation predicate for this personal/internal programme.
