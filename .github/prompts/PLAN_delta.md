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
catalog plus governed integration policy endpoints. It does not activate
Docling, GitHub write actions, or n8n as workflow authority by default. It shows
the required gates, current readiness state, worker references, blocking
reasons, scanner visibility, credential policy, rate limits, and artifact
contracts so later integration work has a single governed path.

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

## Current Progress

- Added `/integrations/delta-readiness` with governed readiness state for
  Docling, GitHub REST, TruffleHog/Semgrep, and n8n.
- Added scanner visibility for TruffleHog and Semgrep, including
  `SKIPPED_TOOL_UNAVAILABLE` when binaries are not installed.
- Added `/integrations/docling/certification-check`; Docling remains blocked
  until an approved active worker is registered, and the endpoint exposes the
  required gVisor, egress, and artifact-reference contract.
- Added `/integrations/github/repository-metadata`; GitHub metadata reads are
  routed through server-side named credential resolution when configured,
  include rate-limit/read/write policy, and never return plaintext tokens.
- Added `/integrations/n8n/edge-policy`; n8n can be validated only as an
  audited HTTPS edge webhook and is rejected if it asks for control-plane
  ownership.
- Added dashboard proxy routes for the Delta integration endpoints.
- Expanded the Hiring Board Delta panel with scanner status and policy text.
- Added backend tests for readiness, Docling certification blocking, GitHub
  credential routing/masking, and n8n edge-policy rejection.
- Verified on 2026-05-31 with
  `UV_PROJECT_ENVIRONMENT=/tmp/aiat-mas-uv-venv uv run pytest apps/orchestrator-api/tests/test_delta_integrations.py -q`
  as part of the passing plan-specific backend suite, plus dashboard
  `npm run build`, `npm run test:protocol-fixtures`, Compose health, and the
  live Hiring Board/operations Playwright coverage in the 23-test E2E suite.

Known boundary:
- Delta completes the governed adoption surface and policy gates. It does not
  make Docling, GitHub write operations, or n8n execute by default; those remain
  blocked unless the required worker/credential/approval gates are satisfied.
