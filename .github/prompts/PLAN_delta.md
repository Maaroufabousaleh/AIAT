# Apply Deep Research Delta Plan

## Executive Summary

Delta starts the low-risk integration phase after Gamma. The goal is to make
Docling, GitHub API access, defensive security tools, and optional n8n edge
automation usable through AIAT governance instead of adding unmanaged direct
execution paths.

Delta keeps the Gamma control-plane boundary:

**Human operator -> Dashboard -> Orchestrator API -> worker/tool registry ->
approved adapters -> audited tools and artifacts**

The first implementation slice is an operator-visible integration readiness
catalog. It does not activate Docling, GitHub task execution, or n8n by default.
It shows the required gates, current readiness state, worker references, and
blocking reasons so later integration work has a single governed path.

## Delta Scope

| Integration | Delta stance | First gate |
|---|---|---|
| Docling | Preferred document-ingestion worker | certify manifest, adapter contract, sandbox, artifact output |
| GitHub REST API | Low-risk code-system metadata and task API | credential reference, rate limit, audit, read/write split |
| TruffleHog and Semgrep | Defensive worker intake and CI scans | keep optional executable checks visible and test parser behavior |
| n8n | Optional edge automation only | no control-plane replacement, explicit allowlist and webhook audit |

## Non-Goals

- Do not replace AIAT orchestration, workflow runtime, or CEO authority.
- Do not execute arbitrary GitHub-cloned worker code outside the worker
  registry, adapter, sandbox, approval, and observability gates.
- Do not expose plaintext GitHub tokens or scanner secrets to browser code.
- Do not make n8n the core workflow engine.
- Do not replace MinIO with SeaweedFS or Garage in Delta's first slice.

## Acceptance Criteria

Delta is complete when:

1. Operators can inspect the Delta integration readiness catalog from the
   dashboard.
2. Docling ingestion is available as an approved, sandboxed worker or remains
   explicitly blocked with the missing gate visible.
3. GitHub REST access is routed through AIAT-owned credentials, rate limits,
   audit logging, and read/write policy.
4. TruffleHog and Semgrep checks remain visible in worker evaluations, including
   skipped-tool states when binaries are unavailable.
5. n8n, if added, is limited to audited edge automation and cannot replace the
   AIAT control plane.
6. Dashboard proxy routes continue to mask secrets.
7. Protocol and dashboard contract checks pass.

## Implementation Slices

### Slice 1: Integration Readiness Catalog

- Add a read-only orchestrator endpoint for Delta integration readiness.
- Add a dashboard proxy route for that endpoint.
- Surface readiness cards on the Hiring Board.
- Test that Docling, GitHub, defensive scanners, and n8n are represented with
  explicit gates and status.

### Slice 2: Docling Adapter Certification

- Replace the placeholder-only Docling manifest with a disabled-by-default
  certified manifest once the adapter contract is implemented.
- Store large extraction results as artifact references, not protocol blobs.
- Keep network egress denied unless a specific source requires an approved
  allowlist.

### Slice 3: GitHub Metadata And Task Boundary

- Add read-only GitHub metadata access through AIAT credentials.
- Separate metadata reads from write actions such as branches, issues, PRs, and
  workflow dispatch.
- Require approval and audit logging for write actions.

### Slice 4: Defensive Scanner Hardening

- Keep TruffleHog and Semgrep as optional executable checks.
- Add parser tests for scanner output and skipped-tool behavior.
- Show scanner availability and last evaluation state in the dashboard.

### Slice 5: Optional n8n Edge Automation

- Treat n8n as an external edge bridge only.
- Require allowlisted webhooks, named credentials, and audit events.
- Do not route core AIAT project workflows through n8n.

## Verification Strategy

Run these gates before calling a Delta slice complete:

```bash
cd mas
UV_PROJECT_ENVIRONMENT=.venv-wsl uv run pytest apps/orchestrator-api/tests/test_delta_integrations.py -q

cd apps/mas-dashboard
npm run build
npm run test:protocol-fixtures
```

For operator-facing claims, run the relevant Playwright spec against the Compose
dashboard before reporting completion.
