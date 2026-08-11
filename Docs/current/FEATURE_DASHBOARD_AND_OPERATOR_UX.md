# Dashboard and Operator UX Feature Specification

**Baseline:** 2026-08-10
**Status:** broad dashboard implemented; API section ACL, distinct caller identities, generated flow-node forms, bounded CEO evidence deep links, executive reconciliation/action surfaces (`d1b8839`), company-timezone display fallback/schedule integration (`ee1361f`), the project-evidence error-state typecheck repair (`fc4f0fa`), and the local Compose E2E matrix implemented; the ACL middleware and operator credential-proxy hardening are committed in `e9b4da4`; secret-safe deterministic and explicit-marker CEO evidence is committed in `f1801bb`; the mobile shell accessibility baseline is now covered by focused Playwright checks; native-Linux accessibility/mobile/visual certification remains
**Authority:** [AIAT Target Programme](../../AIAT_TARGET_PROGRAMME.md)

## Purpose

The dashboard is the human control and evidence surface for AIAT. It must make company state, projects, work, risk, approvals, cost, integrations, identities, and recovery understandable without exposing raw infrastructure or concealing uncertainty.

## Implemented now

- Next.js 16.2.10, React 19.2.0, TypeScript, Tailwind, React Flow, Recharts, authenticated API proxy routes, and Playwright tests.
- Home, projects, detailed project workspace, flow list/create/editor, workers, governance, integrations, tools, credentials, identity/mail/external accounts, CEO cockpit/chat, LiteLLM/OmniRoute analytics, metrics, logs, streams, DLQ, system, and system-visualisation pages.
- Visual flow authoring, organisation/permission/orchestration visualisations, project graph/workspace views, charts, responsive shell, and destructive-action dialogs.
- The dashboard shell provides a semantic header/navigation/main landmark structure, a keyboard-visible skip link, a focusable 44px mobile menu control, focus transfer into the opened navigation, Escape-to-close, focus restoration to the trigger, and an exposed interactive backdrop close control. The focused regression is [`dashboard-shell-accessibility.spec.ts`](../../mas/apps/mas-dashboard/e2e/dashboard-shell-accessibility.spec.ts); this is a shell baseline, not a complete WCAG audit.
- Identity resource tables retain the last successful records when a refresh fails, label the view as stale, and expose an explicit retry action; [`identity-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/identity-states.spec.ts) covers the failure/retry path without exposing sensitive values.
- System visualisation fetches each source independently, retains responding sections when another source is unavailable, labels partial data as stale, and offers a retry action; PM integrations use the same last-known/conflict-preserving pattern for refresh failures. The targeted resilience checks live in [`app-operations.spec.ts`](../../mas/apps/mas-dashboard/e2e/app-operations.spec.ts).
- Project creation reads the active flow catalogue with `cache: "no-store"`, so a newly saved/versioned flow is selectable immediately after navigation instead of waiting for a stale browser response; the full flow-builder golden path now passes again.
- Flow editors render the generated node-schema contract and editable form (version, descriptions, required-any rules, field types, defaults, enums, CSV/JSON fields, governed workers, and approved Model Profiles). Deprecated `team_id`/`action` assignments are identified in the contract and remain editable only in the collapsed compatibility controls, which also preserve adapter extension keys.
- New-flow starter cards consume the canonical `/flow-templates` catalogue through the dashboard proxy, preserve template configs/evidence metadata, remap branch references, and retain a blank-canvas fallback for catalogue outages.
- System Overview now consumes the read-only `aiat.executive-reconciliation.v1`
  report and shows durable spend, active/total projects, terminal-run success,
  budget availability, model-profile findings, and reconciliation status. The
  proxy preserves the governance section context for this read. The same
  surface renders bounded `aiat.executive-views.v1` CFO, CTO, and CEO role cards
  without creating a second authority.
- Deterministic API-owned CEO actions and read responses publish the secret-safe
  `aiat.ceo-evidence.v1` envelope with bounded scalar record references and a trace;
  the CEO chat renders those citations and the synchronous `/ceo/message` response
  returns the same envelope for API clients. Credential values, arbitrary action
  payloads, and list contents are never copied into the citation surface. Known
  reference kinds render encoded internal links to project, flow, governance,
  worker, credential, integration, tool, project-evidence, and log sections plus
  a dedicated `/evidence/{kind}/{id}` record page. The bounded kinds include
  artifact, integration, model, runtime, tool, usage, worker-run, and trace
  records without exposing their payloads. The legacy model fallback accepts only
  explicit `AIAT_EVIDENCE: kind=id` markers, strips them from prose, and labels
  the resulting references `unverified`.
- Mermaid organisation definition can be copied as text; Mermaid rendering is not a bundled dashboard capability.
- Dashboard API proxies attach a bounded `X-AIAT-Dashboard-Section` context. The
  finite section policy and deterministic persisted ACL normalizer are committed
  as `d405ccb`; `e9b4da4` adds fail-closed middleware enforcement, persisted
  operator-only ACL updates, and strict operator-key credential proxies. The
  orchestrator applies them for human operator, CEO, service,
  worker, gateway, and PM-gateway principals, with the operator as the only
  full-surface recovery principal.
- `GET /dashboard/access`, `GET /dashboard/sections/{section}`, and operator-only `PUT /dashboard/sections/{section}/acl` expose the auditable section capability and persistence boundary.
- Credential and identity proxy routes use the operator principal rather than
  silently falling back to the shared service key; missing operator
  configuration fails closed.
- Executive action proxies forward CFO model-override, CTO worker-run, and CEO
  privileged-action requests with the operator key and governance section
  context, preserving the backend's `201`/`202` outcomes and secret-safe
  `aiat.executive-action.v1` response envelope. The proxies do not create a
  second authority or expose worker output/payload secrets.
- Governance now includes a typed operator action panel for those three
  routes. It requires the canonical identifiers/reason, validates dispatch and
  payload JSON, requires explicit CEO privileged-action confirmation, and
  renders only the returned envelope.
- The authenticated local Compose dashboard run passes 34/35 Playwright tests
  (one explicit safe DLQ-fixture skip), covering credential masking, worker
  lifecycle, project workspace creation/audit/cost views, CEO directives and
  hiring context, schema-driven flow editing, all eight branching/recovery
  scenarios, hiring board, identity stale-record/retry state, PM integration
  conflict/stale retry state, project-detail stale/retry state, runtime-status
  panels, mobile shell keyboard
  focus recovery, and system-visualization partial/offline retry states.
  Secret-safe
  evidence is retained at
  [`mas/docs/provenance/dashboard_e2e_live.json`](../../mas/docs/provenance/dashboard_e2e_live.json).

## Code anchors

- Dashboard application: [`mas/apps/mas-dashboard/app/`](../../mas/apps/mas-dashboard/app/)
- Components: [`mas/apps/mas-dashboard/components/`](../../mas/apps/mas-dashboard/components/)
- Flow editor: [`mas/apps/mas-dashboard/app/(dashboard)/flows/`](<../../mas/apps/mas-dashboard/app/(dashboard)/flows/>)
- Generated flow contract/form: [`mas/apps/mas-dashboard/components/flows/NodeSchemaContractSummary.tsx`](../../mas/apps/mas-dashboard/components/flows/NodeSchemaContractSummary.tsx), [`mas/apps/mas-dashboard/components/flows/NodeSchemaForm.tsx`](../../mas/apps/mas-dashboard/components/flows/NodeSchemaForm.tsx)
- Project workspace: [`mas/apps/mas-dashboard/app/(dashboard)/projects/[id]/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/projects/[id]/page.tsx>)
- System visualisation: [`mas/apps/mas-dashboard/components/system-viz/`](../../mas/apps/mas-dashboard/components/system-viz/)
- Dashboard shell accessibility: [`DashboardShell.tsx`](../../mas/apps/mas-dashboard/components/DashboardShell.tsx), [`Sidebar.tsx`](../../mas/apps/mas-dashboard/components/Sidebar.tsx), [`dashboard-shell-accessibility.spec.ts`](../../mas/apps/mas-dashboard/e2e/dashboard-shell-accessibility.spec.ts)
- Identity resource state handling: [`IdentityResourcePage.tsx`](../../mas/apps/mas-dashboard/components/identity/IdentityResourcePage.tsx), [`identity-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/identity-states.spec.ts)
- PM integration conflict/stale state: [`integrations/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/integrations/page.tsx>), [`app-operations.spec.ts`](../../mas/apps/mas-dashboard/e2e/app-operations.spec.ts)
- Project-detail stale/retry state: [`projects/[id]/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/projects/[id]/page.tsx>), [`app-operations.spec.ts`](../../mas/apps/mas-dashboard/e2e/app-operations.spec.ts)
- Executive reconciliation proxy, role-view proxy, and role cards: [`mas/apps/mas-dashboard/app/api/executive/reconciliation/route.ts`](../../mas/apps/mas-dashboard/app/api/executive/reconciliation/route.ts), [`mas/apps/mas-dashboard/app/api/executive/views/[role]/route.ts`](<../../mas/apps/mas-dashboard/app/api/executive/views/[role]/route.ts>), [`mas/apps/mas-dashboard/app/(dashboard)/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/page.tsx>)
- Executive action proxies: [`mas/apps/mas-dashboard/app/api/executive/actions/`](../../mas/apps/mas-dashboard/app/api/executive/actions/)
- Executive action panel: [`mas/apps/mas-dashboard/components/governance/ExecutiveActionPanel.tsx`](../../mas/apps/mas-dashboard/components/governance/ExecutiveActionPanel.tsx)
- CEO evidence citation/deep-link surfaces: [`mas/apps/mas-dashboard/app/(dashboard)/ceo/chat/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/ceo/chat/page.tsx>), [`mas/apps/mas-dashboard/app/(dashboard)/evidence/[kind]/[id]/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/evidence/[kind]/[id]/page.tsx>)
- E2E tests: [`mas/apps/mas-dashboard/e2e/`](../../mas/apps/mas-dashboard/e2e/)
- Package versions: [`mas/apps/mas-dashboard/package.json`](../../mas/apps/mas-dashboard/package.json)

## Target information architecture

- Executive home and CEO copilot.
- Projects and evidence workspaces.
- Flows and live instances.
- Company, people, workers, stewards, hiring, and rollouts.
- Governance for budgets, models, grants, policies, source/version provenance, third-party metadata, and approvals.
- PM/SCM integrations and lifecycle evidence.
- Identity, mail, credentials, external accounts, and browser sessions.
- Operations for health, analytics, traces, logs, streams, DLQ, schedules, backup, shutdown, and recovery.

## Interaction principles

- Show canonical state and revision; label stale or partial data explicitly.
- Before risky actions, show scope, actor, expected revision, consequence, required evidence, and rollback.
- Never represent a submitted command as committed before canonical confirmation.
- Make every status badge traceable to evidence or an explanation of missing evidence.
- Design empty, loading, offline, stale, partial, denied, conflict, timeout, failure, rollback, and recovered states.
- Preserve essential evidence/actions on mobile rather than hiding them.
- Meet WCAG 2.2 AA with keyboard navigation, focus, semantic landmarks, contrast, labels, error announcements, and reduced motion.
- Use shared design tokens for light, dark, and system themes.

## CEO experience

The CEO cockpit summarises portfolio, company health, spend, risk, approvals, incidents, and next decisions. The CEO chat reads authorised AIAT evidence, distinguishes facts/inferences/proposals, cites records, and invokes only governed tools/flows. It never bypasses approval, budget, model, tool, credential, project, or CSO-veto policy.

## Remaining gaps

- The authenticated local API ACL matrix is retained at
  [`mas/docs/provenance/dashboard_acl_live.json`](../../mas/docs/provenance/dashboard_acl_live.json):
  operator/CEO/service/worker identities receive the expected 200/403 outcomes
  for CEO, credentials, projects, and workers sections. Run the persisted ACL
  through the native-Linux dashboard/network matrix and add UI-level denial
  states before release certification. The local Compose UI matrix is green at
  34/35 with the explicit DLQ fixture skip, shell keyboard/mobile focus,
  identity stale-record/retry, PM integration conflict/stale retry,
  project-detail stale/retry, and
  system-visualization partial/offline retry coverage; it is not native-Linux
  release evidence.
- Generate typed forms/clients from backend schemas for the remaining governance actions; the CFO/CTO/CEO action panel is implemented, while broader governance action forms remain.
- Consolidate legacy flow compatibility aliases into an explicit extension editor after migration coverage is complete.
- Complete light/dark/system themes and responsive/mobile parity.
- Complete the WCAG 2.2 AA audit and broader automated checks; the shell landmark/skip-link/focus baseline is covered, while page-level semantics, contrast, reduced motion, and native-Linux evidence remain.
- Improve stale/conflict/rollback and partial-evidence experiences; system
  visualization, identity tables, PM integrations, and project detail now have
  explicit stale/partial/conflict and first-load/offline retry states, while broader
  page-specific denial and rollback coverage remains.
- [x] Add bounded CEO citation/deep-link coverage across tools, models, workers,
  integrations, artifacts, usage, runtimes, and trace records; the dedicated
  evidence route never renders payloads. Rich resource-specific detail loading,
  stale/offline states, and full golden-path coverage remain.
- The legacy CEO model fallback now supports explicit, stripped `AIAT_EVIDENCE`
  markers and labels them unverified (`f1801bb`); authoritative deterministic
  citations remain preferred. Resource-specific detail loading, stale/offline
  states, and full golden-path coverage remain.
- Run complete Playwright desktop/mobile golden paths on native Linux CI.
- Add visual regression within stable, intentional thresholds.

## Acceptance criteria

- A restricted section denies CEO identity while permitting the authorised human operator.
- All critical workflows are keyboard-operable and screen-reader-labelled.
- Mobile users can approve, veto, cancel, recover, and inspect evidence safely.
- Stale revisions produce a clear conflict and refresh path, never silent overwrite.
- Every consequential action has a durable result/evidence link.
- Playwright covers project golden path, worker rollout, flow failure/recovery, integration rollback, identity approval, DLQ replay, and shutdown.
