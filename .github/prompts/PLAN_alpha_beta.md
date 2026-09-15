# Apply Deep Research Alpha+Beta Plan

## Summary
Implement the safe first slice from `Docs/archive/deep-research-report.md`: keep AIAT as the control plane, harden its contracts, add a first-run/default-company path, and turn worker hiring into a guarded adapter/evaluation workflow. Do not integrate advanced runtimes, replace storage, or replace the dashboard in this pass.

## Key Changes

### Contract Hardening
- Add non-breaking versioning to existing protocol models: `protocol_version="aiat.v1"` on `MessageEnvelope`, `ToolRequest`, `ToolResponse`, and `WorkerManifest`.
- Export JSON schemas for those contracts from `mas-core`, and add checked-in Python/TypeScript fixture samples for cross-runtime validation.
- Keep existing field names and routes; do not rename `message_id`, `sender_id`, `recipient_id`, `recipient_team`, `blob_ref`, or current worker registry fields.
- Add adapter conformance tests for existing transports: `process`, `http`, `mcp`, `oci`, and `human`.

### Worker Adapter + Hiring Board
- Formalize the existing worker registry as `aiat-worker-sdk v1` behavior using current `mas-core` protocol types.
- Extend worker evaluation to run guarded checks in this order: provenance/version pin, manifest validation, TruffleHog scan, Semgrep scan, compatibility tests, sandbox profile validation, budget/latency scoring, then approval.
- Add evaluator result fields to stored evaluation reports: `risk_tier`, `checks`, `blocked_reasons`, `recommended_status`, and `requires_human_approval`.
- Dashboard workers page becomes the first “Hiring Board”: candidate list, evaluation status, scan results, sandbox profile, activate/deactivate/drain actions, and “blocked until approval” state.
- Default adoption posture: TruffleHog and Semgrep are optional executable checks; if missing, evaluation records `SKIPPED_TOOL_UNAVAILABLE` instead of failing the whole request.

### Startup + Default Company
- Add an idempotent seed command/API path that creates the default AIAT company, permanent CEO identity, default departments, baseline worker manifests, and a sample project template.
- Wire the dashboard home page to show first-run state: “not seeded”, “seeded”, or “needs migration/config”.
- Keep the existing Compose flow; do not replace startup with a new installer.

### Sandbox Defaults
- Treat `standard`, `restricted`, `gvisor`, and `firecracker` as the only valid sandbox profiles.
- Current default for new external workers: `restricted`.
- Medium/dual-use workers require `gvisor` or higher and human approval before activation.
- Firecracker remains a declared profile only; no Firecracker runtime implementation in this pass.
- Add validation tests for filesystem, network-mode metadata, and invalid sandbox profile rejection.

### Guarded External Tool Adoption
- Adopt now as guarded integrations: TruffleHog, Semgrep, GitHub repository metadata, and existing React Flow dashboard flows.
- Add placeholders/manifests only for Docling and MCP worker mode; no full Docling ingestion UI unless the SDK and evaluator work is complete.
- Defer Cytoscape, Mermaid, LangGraph, CrewAI, AutoGen, Letta, browser-use, OpenCode default-worker status, SeaweedFS, Garage, Vault, ZITADEL, Temporal, and Firecracker runtime implementation.

## Public Interfaces
- `GET /capabilities/workers/{worker_id}/evaluations` should return the new evaluator fields while preserving existing fields.
- `POST /capabilities/workers/{worker_id}/evaluate` should accept an optional `checks` array; default is all guarded checks.
- Add a first-run endpoint under system scope, for example `POST /system/seed-default-company`, implemented as idempotent.
- Add schema export files or endpoints for `MessageEnvelope`, `ToolRequest`, `ToolResponse`, and `WorkerManifest`; generated schemas must match runtime models.

## Test Plan
- Python: protocol schema generation, JSON fixture round-trips, worker manifest validation, evaluator skipped-tool behavior, TruffleHog/Semgrep parser behavior, sandbox profile validation, and first-run seed idempotency.
- Orchestrator API: worker evaluate endpoint, evaluation listing, activation blocked by failed/pending checks, status transitions, and seed endpoint.
- Dashboard Playwright: first-run state, hiring board list, register candidate from GitHub URL, evaluate candidate, inspect scan results, blocked activation, approved activation, deactivate/drain.
- Regression: existing flow builder/runtime/operator smoke tests must still pass.
- Manual acceptance: from a fresh local stack, the operator can open the dashboard, seed default AIAT, register a low-risk worker candidate, run evaluation, see results, and activate only when policy allows it.

## Current Progress
- Verified on 2026-05-31 that protocol v1 schemas and Python/TypeScript fixtures are checked by `npm run test:protocol-fixtures`.
- Verified worker registry evaluation, skipped TruffleHog/Semgrep behavior, sandbox profile validation, medium/dual-use hardened sandbox enforcement, status transitions, and seed idempotency through the full backend pytest suite (`UV_PROJECT_ENVIRONMENT=/tmp/aiat-mas-uv-venv uv run pytest -q`, exit 0, 1383 tests collected, no failures).
- Verified the Hiring Board golden path live against the Compose dashboard in the 23-test Playwright suite, including register, evaluate, blocked activation, approved activation, deactivate, and drain behavior.
- Verified fresh-clone setup path corrections: root `.env.example` is populated, `mas.sh` reads the repository-root `.env`, Compose validation passes, migrations pass, and `mas.sh health` reports core services healthy.

## Assumptions
- Scope is Alpha+Beta only.
- New external tools use guarded adoption: optional at runtime, no control-plane replacement.
- AIAT remains the authority for routing, state, credentials, approvals, tools, and observability.
- Existing `mas/` workspace is canonical; root-level generated artifacts are ignored.
- Missing local TruffleHog/Semgrep binaries must not block development tests unless the test explicitly opts into live tool execution.
