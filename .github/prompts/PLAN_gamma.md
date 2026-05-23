# Apply Deep Research Gamma Plan

## Summary
Implement the next safe slice after Alpha+Beta: expand the operator dashboard and
project workspace so AIAT feels like a usable company operating system, while
keeping AIAT as the control plane. Gamma should improve visibility, navigation,
approval ergonomics, graph views, and exportable diagrams. Do not add Docling,
advanced runtimes, Vault/ZITADEL, Temporal, alternate object storage, or a real
Firecracker runtime in this pass.

## Preconditions
- Alpha+Beta is complete: protocol v1, first-run seed path, guarded worker
  evaluation, sandbox profile policy, and Hiring Board are implemented and
  tested.
- The existing `mas/` workspace remains canonical.
- Existing Compose startup remains the supported local launch path.
- Dashboard work must build on the existing Next.js app and current React Flow
  flow-builder/runtime surfaces.

## Key Changes

### Operator Workspace
- Create a first-class project workspace view that groups project state,
  active flow instance, pending approvals, recent decisions, worker activity,
  artifacts, logs, and cost/usage signals.
- Add a compact "next operator action" panel for pending approvals, failed
  nodes, retryable flow errors, blocked worker activation, and missing config.
- Preserve existing project routes and API contracts; this is an additive UX
  pass, not a workflow runtime rewrite.

### Company And Department Views
- Add dashboard pages or panels for the seeded AIAT company, CEO identity,
  departments, teams, chiefs, and assigned workers.
- Show department health using current system data: worker count, active
  projects, pending approvals, recent failures, and evaluation warnings.
- Keep company/department data in Postgres through the orchestrator API; do not
  introduce a separate graph database.

### Graph UX
- Keep React Flow as the editable workflow builder.
- Add a read-only org/capability graph view for company, departments, workers,
  capabilities, tools, projects, and approval links.
- Use a small adapter layer that converts existing orchestrator payloads into a
  graph model with stable node/edge IDs.
- Add Mermaid export for architecture, department maps, and flow summaries.
- Do not replace React Flow with Cytoscape; use graph views only where graph
  analysis/navigation is useful.

### Approval And Audit Surfaces
- Make approvals visible from the project workspace, company overview, and flow
  runtime views.
- Add audit-friendly timelines for decisions, worker evaluations, status
  transitions, privileged actions, and retries.
- Ensure dashboard proxy routes never expose plaintext secrets or hidden
  credential values.

### Artifacts, Logs, And Cost Surfaces
- Add project-scoped artifact listing using existing object/artifact metadata.
- Add filtered logs for project, flow instance, worker, and approval context.
- Add cost/usage cards from existing LLM/tool telemetry when available; show a
  clear unavailable state otherwise.
- Do not change the object store or observability backend in Gamma.

### Gamma Hardening Gates
- Audit dashboard server-side proxy routes for credential leakage.
- Audit Docker socket exposure and nested-container paths used by runner/worker
  containers; document risks and add policy tests where code can enforce them.
- Extend protocol fixture checks so TypeScript samples are compile-validated and
  at least one Node-side round-trip check compares fixture field names against
  the checked-in AIAT v1 schema.

## Public Interfaces
- Add read-only endpoints under system/company/project scope as needed for:
  company overview, department summary, org graph, capability graph, project
  workspace summary, project artifacts, and project audit timeline.
- Existing worker, flow, project, approval, credential, and system routes must
  remain backward compatible.
- Mermaid export may be API-generated text or client-generated text, but the
  source data must come from current AIAT state.

## Test Plan
- Python/API: company overview, department summary, graph payload shape,
  project workspace summary, audit timeline, artifact listing, and no-secret
  proxy regression tests.
- Dashboard unit/type: graph model conversion, Mermaid export generation,
  TypeScript protocol fixture checks, and empty/unavailable states.
- Dashboard Playwright: project workspace golden path, approval action from
  workspace, org/capability graph navigation, Mermaid export, artifact/log
  inspection, and regression coverage for existing flow builder/runtime/operator
  smoke tests.
- Security regression: credential responses remain masked through every
  dashboard proxy; worker/container socket exposure rules are enforced or
  explicitly reported as blocked configuration.
- Manual acceptance: from the Docker Compose stack, the operator can seed AIAT,
  open the dashboard, inspect the company/departments, open a project workspace,
  handle an approval, inspect related artifacts/logs/costs, and export a Mermaid
  diagram without leaving the AIAT dashboard.

## Explicitly Deferred
- Docling ingestion UI or worker implementation.
- GitHub task execution worker beyond already guarded metadata/evaluation use.
- LangGraph, CrewAI, AutoGen, Letta, browser-use, OpenCode default-worker
  adoption, or any advanced runtime.
- Vault, ZITADEL, Temporal, Neo4j, Qdrant, SeaweedFS, Garage, or Firecracker
  runtime implementation.
- Replacing the dashboard shell, storage model, router, workflow runtime, or
  AIAT CEO/control-plane authority.

## Assumptions
- Gamma is UX and operator-trust focused, not a new integration phase.
- AIAT remains the authority for routing, state, credentials, approvals, tools,
  and observability.
- Graph and diagram surfaces are read models over existing state.
- Any new package must be justified by a concrete dashboard capability and must
  not become a new control plane.
