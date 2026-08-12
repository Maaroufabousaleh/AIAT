# Dashboard and Operator UX Feature Specification

**Baseline:** 2026-08-12
**Status:** broad dashboard implemented; API section ACL, distinct caller identities, generated flow-node forms, bounded CEO evidence deep links, executive reconciliation/action surfaces (`d1b8839`), company-timezone display fallback/schedule integration (`ee1361f`), the project-evidence error-state typecheck repair (`fc4f0fa`), dashboard operation selector hardening (`e378f40`), and the local Compose E2E matrix implemented; the ACL middleware and operator credential-proxy hardening are committed in `e9b4da4`; secret-safe deterministic and explicit-marker CEO evidence is committed in `f1801bb`; the mobile shell accessibility baseline is now covered by focused Playwright checks; the theme preference foundation (`5e3cc13`) now supports persisted system/light/dark selection, a light-palette migration layer, and reduced-motion defaults; the bounded evidence-detail read model (`8fefc8b`, trace extension `c8505eb`, model/integration expansion and scalar hardening `dc50719`, recovery regression `5357166`, tool catalogue extension `ec8cf67`, artifact/usage read authority `2ca5f3d`, stale-refresh retention `6c52552`) now loads safe scalar fields for fourteen canonical citation kinds and retains the last successful projection through a retryable refresh failure; governance's combined model-profile/catalogue/worker-run/steward read surface now retains its last successful state, exposes stale/retry recovery, and hides its action forms when access is denied (`52de581`, `888fde3`, source-built `governance-states.spec.ts` 3/3); System Control now retains the last successful runtime status through failed refreshes, keeps the status visible, and exposes stale/retry recovery (`f445c17`, source-built `system-status-states.spec.ts` 1/1); the Projects list now retains canonical project/flow data through failed refreshes and exposes stale/retry recovery (`d3482ab`, source-built `projects-states.spec.ts` 1/1); the project evidence package page now retains its last successful package through failed refreshes and exposes stale/retry recovery (`bc80ad5`, source-built `project-evidence-states.spec.ts` 1/1); the Tools catalogue now retains its last successful data through failed refreshes, keeps the catalogue visible, and exposes stale/retry recovery (`5f4b0eb`, source-built `tools-states.spec.ts` 1/1); the dead-letter queue now retains queued messages through failed refreshes, keeps them visible, and exposes stale/retry recovery (`823fa6d`, source-built `dlq-states.spec.ts` 1/1); the Metrics page now retains successful series through partial query failures and exposes stale/retry recovery (`85596b0`, source-built `metrics-states.spec.ts` 1/1); the Flows list now retains its last successful definitions through failed refreshes and exposes stale/retry recovery (`a0faf5b`, source-built `flows-states.spec.ts` 1/1); the flow editor now has an explicit first-load unavailable state, canonical Refresh, retained last-known flow state, and Retry recovery (`b5098e7`, source-built `flow-editor-states.spec.ts` 1/1); Container Logs now retain the last buffer through failed SSE reloads and expose stale/retry recovery (`280d363`, source-built `logs-states.spec.ts` 1/1); Agent Streams now retain history/messages across reconnect or history failures, label the view as stale, and expose Reconnect/Retry (`3e8a0ea`, source-built `streams-states.spec.ts` 1/1); the shared identity-resource surface now aborts obsolete refreshes, preserves last-known rows through failure, proves successful stale-to-recovered retry, and exposes semantic table/action controls with 44px targets plus named main/status/metadata/table regions, explicit busy state, and decorative-icon suppression (`46eccee`, `651ad11`, `a260e04`, source-built `identity-states.spec.ts` 1/1); the CEO Command Center chat now retains history through live-stream failures, keeps the retry action keyboard-visible, and recovers without dropping the transcript (`beabb95`, source-built `ceo-chat-states.spec.ts` 1/1); the Hiring Board now hides all worker mutations when access is denied while preserving last-known rows (`553f196`, source-built `workers-states.spec.ts` 3/3); the Credentials page now hides all read and mutation controls when access is denied while preserving only previously loaded redacted metadata (`982c9c0`, source-built `credentials-states.spec.ts` 3/3); the CEO Live Feed now hides read/send controls on 401/403 history, SSE, or composer responses while preserving last-known messages (`a3cbd99`, source-built `ceo-states.spec.ts` 3/3); broader page parity, native-Linux accessibility/mobile/visual certification remains
Agent Streams now also applies the access boundary to history and SSE reads:
401/403 responses expose a named access-denied region, preserve only previously
loaded messages, invalidate in-flight stream callbacks, and hide reconnect,
retry, filter, pause, clear, and copy controls (`118ff18`, source-built
`streams-states.spec.ts` 3/3).

The Projects list now applies the same access boundary to its paired project and
active-flow reads plus create/archive/delete mutations: 401/403 responses
expose a named access-denied region, retain only previously loaded definitions
as read-only text, clear selection, and hide refresh/retry, New Project,
filters, sorting, archive, and delete controls. Its focused source-built stale,
first-load-denial, retained-read-denial, and mutation-denial matrix passes 4/4
(`17d25b0`).

The dedicated evidence-detail page now also has a focused accessibility
baseline: a named page/canonical-citation region, a semantic bounded-detail
region with an explicit `aria-busy` refresh state, decorative citation icons
hidden from assistive technology, and 44px CEO chat/canonical-link/Refresh
targets (`32f3a76`). The existing source-built evidence-detail suite remains
9/9, including scalar redaction and stale-refresh retention; this is a
page-level baseline, not full WCAG/native-Linux certification.

The project detail page now distinguishes first-load API failure from a missing
project, preserves backend error detail, and exposes a keyboard-visible Retry
that recovers into the project workspace (`f364763`, source-built
`project-detail-states.spec.ts` 1/1).

Its focused accessibility baseline now adds named page/loading landmarks,
explicit project status, 44px refresh/retry/back and primary project-view tab
targets, and semantic project/workspace tab-panel relationships. The same
source-built recovery test covers these semantics and target sizes 1/1
(`40b87dd`). This is a page-level baseline; full project composition, WCAG,
native-Linux, and live provider/worker evidence remain separate.

The project workspace sub-surface now retains its last successful workspace
summary when the workspace or repository refresh fails, labels the retained
data, preserves the repository snapshot across a partial failure, and exposes
a keyboard-visible Retry for both stale and first-load failure states
(`cb1c665`, source-built `project-workspace-states.spec.ts` 1/1).

Its nested Activity/Resources/Cost controls now use semantic tab/tabpanel
relationships, roving keyboard focus with Arrow/Home/End navigation, and 44px
targets (`fcb0f4b`, source-built `project-workspace-states.spec.ts` 1/1).

The project evidence package page now has a focused accessibility baseline:
named main/package sections, labeled back/refresh actions with 44px targets,
and a captioned evidence table with scoped column headers. The same source-built
recovery test covers these semantics 1/1 (`89091c1`).

The Tools catalogue now has named main/search/group regions, captioned/scoped
group tables, keyboard-visible tool expansion, and 44px targets for refresh,
grouping, search, copy, retry, and empty-state actions. Its source-built stale/
retry test covers the focused baseline 1/1 (`83e39e6`).
Its 401/403 responses now expose a named access-denied region, retain only
previously loaded tool metadata, and hide refresh, retry, search, grouping,
expansion, and copy controls while leaving retained tables/details read-only;
the focused denial matrix passes 3/3 (`b418f8a`).

The dead-letter queue now has a named main/entry-list structure, semantic
envelope disclosure regions, `aria-pressed` severity filters, and 44px targets
for refresh, retry, selection, replay, filtering, and envelope inspection. Its
source-built stale/retry test covers the focused baseline 1/1 (`99a19a2`).
Its 401/403 read or replay responses now expose a named access-denied region,
retain only previously loaded messages, clear selection/replay state, and hide
refresh, retry, filters, selection, and replay controls while leaving retained
envelope inspection read-only; the focused denial matrix passes 3/3
(`e6ab3a1`).

The Credentials page now has a named main/security/data structure, a
captioned/scoped credentials table, explicit associations for creation dialog
fields, and 44px refresh, audit, selection, copy, delete, and dialog controls.
Its source-built stale/retry test covers the focused baseline 1/1 (`93fdfbc`).

The Metrics page now has named main/summary/chart regions, a semantic
time-range control, and 44px range, refresh, retry, and empty-state controls.
Its source-built partial/stale/retry test covers the focused baseline 1/1
(`da113af`). A 401/403 response from any query family now exposes a named
access-denied region, preserves only previously loaded series, and hides
refresh/retry, time-range, and reconnect controls; the focused denial matrix
passes 3/3 (`b64b15e`).

The Container Logs page now has named main/filter/legend/output/status regions,
44px stream/filter/recovery targets, and an `aria-busy` log output. Its
source-built stale/retry test covers the focused baseline 1/1 (`993b1cb`). A
401/403 SSE response now exposes a named access-denied region, preserves only
previously loaded lines, invalidates obsolete stream generations, and hides
load/retry, filter, clear, copy, and download controls; its focused denial
matrix passes 3/3 (`156597c`).

The Agent Streams page now has a named main/filter/feed/status structure, a
captioned message table, keyboard-accessible expandable rows, 44px
stream/filter/action targets, and an `aria-busy` feed state. Its source-built
reconnect/recovery test covers the focused baseline 1/1 (`d320383`). A 401/403
history or SSE response now exposes a named access-denied region, preserves
only previously loaded messages, invalidates in-flight stream callbacks, and
hides reconnect, retry, filter, pause, clear, and copy controls; the focused
denial matrix passes 3/3 (`118ff18`).

The Hiring Board now has a named main/policy/summary/filter/table structure,
integration and runtime status regions, a captioned/scoped worker table,
keyboard-expandable rows, associated registration-dialog fields, and 44px
refresh/register/filter/selection/row-action/dialog targets. Its source-built
stale/retry test covers the focused baseline 1/1 (`826b4c5`).

The CEO Live Feed now has named main/composer/summary/filter/feed/status
regions, 44px stream/composer/filter/recovery targets, and a busy feed state
while preserving keyboard-expandable messages. Its source-built reconnect/
recovery test covers the focused baseline 1/1 (`1f947a9`).

The CEO Command Center chat now has a named main/workspace/transcript/composer
structure, a live transcript log with busy state, 44px navigation/composer/
quick-command/recovery targets, and explicit chat guidance regions. Its
source-built stream-recovery test covers the focused baseline 1/1 (`8ffb5df`).

The Governance page now has a named main/read-surface structure, explicit
executive/model-profile/WorkerRun/steward/catalogue regions, a captioned/scoped
WorkerRun table, accessible catalogue status, and 44px refresh, retry,
executive-form, and confirmation controls. A 401/403 from any combined read
now exposes a named access-denied region, hides Refresh/Retry and all executive
action forms, and preserves only last-known read context after authorization is
lost. Its source-built recovery test covers stale recovery, first-load denial,
and post-read denial 3/3 (`f4ae7eb`, `888fde3`).

System Control now has a named main/loading state, explicit runtime-status/
schedule/control/dialog regions, scheduled-event semantics, and 44px refresh,
retry, shutdown/resume, schedule-input/save, and confirmation controls. Its
source-built stale/retry test covers the focused baseline 1/1 (`543f392`).

System Overview now has a focused home accessibility baseline: a named main and
hero/status surface, explicit health, overview-metrics, first-run,
company/project-state, and Quick Links regions, decorative icons hidden from
assistive technology, and 44px graph, Quick Links, and seed controls. The
source-built first-run test covers the landmark and target assertions 1/1 for
both `seeded` and `not_seeded` local deterministic orchestrator fixtures
(`c07b4a6`). The live orchestrator was unavailable during this run, so
control-plane availability and full WCAG/native-Linux visual certification
remain separate gates.

The unauthenticated operator sign-in route now exposes a named main and
operator-sign-in region, explicit busy/status announcements, labeled username
and password fields, password-visibility state, and 44px password/sign-in
targets. The source-built login accessibility test covers the landmark, status,
autocomplete, toggle, and target assertions 1/1 (`d928834`).

**Authority:** [AIAT Target Programme](../../AIAT_TARGET_PROGRAMME.md)

## Purpose

The dashboard is the human control and evidence surface for AIAT. It must make company state, projects, work, risk, approvals, cost, integrations, identities, and recovery understandable without exposing raw infrastructure or concealing uncertainty.

## Implemented now

- Next.js 16.2.10, React 19.2.0, TypeScript, Tailwind, React Flow, Recharts, authenticated API proxy routes, and Playwright tests.
- Home, projects, detailed project workspace, flow list/create/editor, workers, governance, integrations, tools, credentials, identity/mail/external accounts, CEO cockpit/chat, LiteLLM/OmniRoute analytics, metrics, logs, streams, DLQ, system, and system-visualisation pages.
- Visual flow authoring, organisation/permission/orchestration visualisations, project graph/workspace views, charts, responsive shell, and destructive-action dialogs.
- The dashboard shell provides a semantic header/navigation/main landmark structure, a keyboard-visible skip link, a focusable 44px mobile menu control, focus transfer into the opened navigation, Escape-to-close, focus restoration to the trigger, and an exposed interactive backdrop close control. The focused regression is [`dashboard-shell-accessibility.spec.ts`](../../mas/apps/mas-dashboard/e2e/dashboard-shell-accessibility.spec.ts); this is a shell baseline, not a complete WCAG audit.
- The dashboard theme foundation provides a persisted operator preference for `system`, `light`, or `dark`, an inline no-flash bootstrap, system color-scheme change handling, a sidebar selector, and a compact mobile control. Theme tokens and the legacy slate migration layer preserve the existing information architecture while page-by-page visual parity is completed. Reduced-motion preferences disable authored transitions/animations and smooth scrolling. The source-built focused regression is [`dashboard-theme.spec.ts`](../../mas/apps/mas-dashboard/e2e/dashboard-theme.spec.ts); it passes 2/2 against the built dashboard.
- The evidence record page now fetches a read-only `aiat.evidence-detail.v1` projection for project, flow, flow-instance, worker, worker-run, credential, dead-letter, runtime, trace, model, integration, tool, artifact, and usage references. The dashboard proxy selects matching records from the model catalogue/integration list/tool-service manifest or operator-only artifact/usage read endpoints, allow-lists backend paths and scalar fields, bounds strings/numbers, drops nested payloads/secrets/metadata/pricing/resource/configuration/profile bindings/tool schemas/credential requirements/trace items, labels temporary unavailability without invalidating the citation, retains the last successful scalar projection through a failed refresh, exposes an explicit Refresh control, aborts obsolete requests on navigation, and keeps unsupported kinds identity-only. Focused source-built coverage passes 9/9 in [`dashboard-evidence-detail.spec.ts`](../../mas/apps/mas-dashboard/e2e/dashboard-evidence-detail.spec.ts), including artifact/usage scalar projections and stale-refresh recovery that preserves the identity, safe detail, and canonical link.
- The evidence-detail page accessibility baseline is committed in `32f3a76`: its named page/canonical-citation region, semantic bounded-detail region with `aria-busy`, decorative-icon suppression, and 44px CEO-chat/canonical-link/Refresh targets are asserted in the existing 9/9 source-built suite. Full WCAG/native-Linux visual certification remains open.
- Identity resource tables retain the last successful records when a refresh fails, label the view as stale, and expose an explicit retry action. The shared loader aborts obsolete requests and ignores late responses so a newer refresh cannot be overwritten; table captions/column scopes, explicit row-action names, and 44px action targets provide the shared keyboard/screen-reader baseline. The page now also exposes named main/status/metadata/table regions, an explicit busy state, decorative-icon suppression, and a distinct 401/403 access-denied region that removes misleading Refresh/Retry actions while preserving already loaded metadata-only rows. [`IdentityResourcePage.tsx`](../../mas/apps/mas-dashboard/components/identity/IdentityResourcePage.tsx) and [`identity-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/identity-states.spec.ts) cover failure, retained rows, successful recovery, denied access, and these semantics 2/2 without exposing sensitive values (`46eccee`, `651ad11`, `a260e04`, `0974434`).
- The same shared identity-resource surface is now fixture-covered across identities, approvals, audit, browser sessions, external accounts, domains, relay, mailboxes, and outbound mail; the route matrix passes 9/9 with safe metadata-only records (`485dfd2`). Provider, relay, and live identity certification remain separate.
- The Credentials table preserves redacted metadata during stale refreshes without reading refs during render; the render-safe loading/error branch is committed in `e6e6980`, and full dashboard lint now passes with only two unrelated hook warnings.
- The Hiring Board retains the last successful worker catalogue through a failed refresh, labels it as showing last-known workers, keeps rows visible while retrying, and exposes Retry; first-load failures show an explicit unavailable state. Its accessibility baseline adds named main/policy/summary/filter/table regions, integration/runtime status landmarks, a captioned/scoped worker table, keyboard-expandable rows, associated registration-dialog fields, and 44px refresh/register/filter/selection/row-action/dialog targets. A 401/403 worker read now renders a named access-denied region, hides Refresh/Retry, registration, evaluation, activation/deactivation, drain, and deletion controls, and preserves only last-known rows after authorization is lost. [`workers/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/workers/page.tsx>) and [`workers-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/workers-states.spec.ts) cover failure retention, successful recovery, first-load denial, post-read denial, and these semantics 3/3 (`7541b84`, `826b4c5`, `553f196`).
- The CEO Live Feed fetches bounded history with `cache: "no-store"`, guards reconnect generations, retains messages through failed history/SSE refreshes, labels the feed as last-known data, and exposes Reconnect plus Retry without changing the governed CEO message composer. A 401/403 history, SSE, or composer response now exposes a named access-denied region, preserves only previously loaded messages, invalidates in-flight stream callbacks, and hides reconnect/retry, copy/clear/filter, and composer controls. [`ceo/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/ceo/page.tsx>), [`app/api/streams/[team_id]/route.ts`](<../../mas/apps/mas-dashboard/app/api/streams/[team_id]/route.ts>), and [`ceo-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/ceo-states.spec.ts) cover failure retention, successful recovery, denial, and these semantics 3/3 against the source-built dashboard (`1761429`, `a3cbd99`).
- The CEO Command Center chat fetches bounded history with `cache: "no-store"`, guards obsolete history/live callbacks, keeps history visible after a stream failure, labels the transcript as last known, and exposes a keyboard-visible Retry action. History and live errors remain independent so a late history response cannot hide a stream outage. Its accessibility baseline adds a named main/workspace/transcript/composer structure, a live transcript log with busy state, 44px navigation/composer/quick-command/recovery targets, explicit chat guidance regions, and a mobile-safe accessible activity link; [`ceo/chat/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/ceo/chat/page.tsx>), [`use-ceo-stream.ts`](../../mas/apps/mas-dashboard/lib/ceo-feed/use-ceo-stream.ts), and [`ceo-chat-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/ceo-chat-states.spec.ts) cover failure retention, recovery, and these semantics 1/1 against the source-built dashboard (`beabb95`, `8ffb5df`). Native/live Redis/router evidence remains separate.
- Governance's combined model-profile, runtime-catalogue, WorkerRun, and external-steward read surface retains the last successful state when any source fails, labels the page as stale, and exposes both header Refresh and banner Retry controls. Its accessibility baseline adds a named main/read-surface structure, explicit executive/model-profile/WorkerRun/steward/catalogue regions, a captioned/scoped WorkerRun table, accessible catalogue status, and 44px refresh, retry, executive-form, and confirmation controls. A 401/403 from any combined read exposes a named access-denied region, hides Refresh/Retry and all executive action forms, and preserves only last-known read context after authorization is lost. [`governance/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/governance/page.tsx>), [`ExecutiveActionPanel.tsx`](../../mas/apps/mas-dashboard/components/governance/ExecutiveActionPanel.tsx), and [`governance-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/governance-states.spec.ts) cover failure retention, successful recovery, first-load denial, post-read denial, and these semantics 3/3 against the source-built dashboard (`52de581`, `f4ae7eb`, `888fde3`).
- System Control polls the canonical runtime status with `cache: "no-store"`, keeps the last successful status visible through a failed refresh, labels it as stale, and exposes a retry action. Its accessibility baseline adds a named main/loading state, explicit runtime-status/schedule/control/dialog regions, scheduled-event semantics, and 44px refresh, retry, shutdown/resume, schedule-input/save, and confirmation controls. A 401/403 status response now renders a named access-denied region, hides Refresh/Retry and all shutdown/resume/schedule mutations, and preserves only last-known read context after access is lost. [`system/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/system/page.tsx>) and [`system-status-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/system-status-states.spec.ts) cover stale recovery plus first-load and post-read denial 3/3 (`f445c17`, `543f392`, `14968d4`).
- The Projects list reads projects and active flows with `cache: "no-store"`, treats the paired read as one canonical surface, retains the last successful data after either source fails, separates load errors from create/action errors, and exposes stale/retry recovery. Its table now has an accessible caption and column scopes, explicit description disclosure relationships, responsive overflow, and 44px targets for selection, filter, sort, link, and row-action controls. A 401/403 read or create/archive/delete response now exposes a named access-denied region, retains only previously loaded definitions as read-only text, clears selection, and hides refresh/retry, New Project, filters, sorting, archive, and delete controls. [`projects/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/projects/page.tsx>) and [`projects-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/projects-states.spec.ts) cover failure retention, the focused table/accessibility baseline, initial denial, retained-read denial, and mutation denial 4/4 (`7828b48`, `17d25b0`).
- The Project evidence page reads the canonical package with `cache: "no-store"`, retains the last successful package when a refresh fails, labels the page as showing last-known evidence, and exposes a keyboard-visible Retry action; the first-load failure remains explicit. Its named main/package sections, labeled back/refresh actions with 44px targets, and captioned/scoped evidence table form a focused accessibility baseline. [`projects/[id]/evidence/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/projects/[id]/evidence/page.tsx>) and [`project-evidence-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/project-evidence-states.spec.ts) cover failed refresh retention, successful recovery, and these semantics 1/1 against the source-built dashboard (`bc80ad5`, `89091c1`). Full project-page composition and live provider/worker evidence remain open.
- The Tools catalogue reads with `cache: "no-store"`, retains the last successful tool definitions and circuit-breaker summaries after a failed refresh, keeps them visible while retrying, labels the view as stale, and exposes both header Refresh and banner Retry controls. Its named main/search/group regions, captioned/scoped group tables, keyboard-visible tool expansion, and 44px refresh/group/search/copy/retry/empty-state targets form a focused accessibility baseline. [`tools/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/tools/page.tsx>) and [`tools-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/tools-states.spec.ts) cover failure retention, successful recovery, and these semantics 1/1 against the source-built dashboard (`5f4b0eb`, `83e39e6`).
- The dead-letter queue reads with `cache: "no-store"`, retains the last successful message set through a failed refresh, keeps queue cards and replay context visible, labels the view as stale, and exposes both header Refresh and banner Retry controls. Its named main/summary/filter/list/disclosure regions, `aria-pressed` severity filters, keyboard-visible envelope inspection, and 44px recovery/selection/replay/inspection targets form a focused accessibility baseline. [`dlq/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/dlq/page.tsx>), [`FilterChips.tsx`](../../mas/apps/mas-dashboard/components/ui/FilterChips.tsx), and [`dlq-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/dlq-states.spec.ts) cover failure retention, successful recovery, and these semantics 1/1 against the source-built dashboard (`823fa6d`, `99a19a2`).
- The Credentials page reads with `cache: "no-store"`, retains redacted metadata through failed refreshes, keeps rows visible while retrying, labels the list as stale, and exposes both header Refresh and banner Retry controls without rendering secret values. Its named main/security/data regions, captioned/scoped table, explicit creation-dialog field associations, and 44px refresh/audit/selection/copy/delete/dialog targets form a focused accessibility baseline. A 401/403 read now exposes a named access-denied region, preserves only previously loaded redacted metadata, and hides Refresh/Retry, creation, deletion, placeholder-copy, selection, and audit navigation controls; creation and bulk mutations also fail closed on authorization loss. [`credentials/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/credentials/page.tsx>), [`BulkActionBar.tsx`](../../mas/apps/mas-dashboard/components/ui/BulkActionBar.tsx), and [`credentials-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/credentials-states.spec.ts) cover failure retention, successful recovery, denial, and these semantics 3/3 against the source-built dashboard (`970f09c`, `e6e6980`, `93fdfbc`, `982c9c0`).
- The Metrics page reads six Prometheus query families with `cache: "no-store"`, retains successful series when another query fails, keeps charts visible while retrying, labels partial data as stale, preserves the last-successful refresh timestamp, and exposes both header Refresh and banner Retry controls. Its named main/summary/chart regions, semantic time-range control, and 44px range/refresh/retry/empty-state targets form a focused accessibility baseline. [`metrics/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/metrics/page.tsx>) and [`metrics-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/metrics-states.spec.ts) cover partial failure, successful recovery, and these semantics 1/1 against the source-built dashboard (`85596b0`, `da113af`).
- The Flows list reads `/api/flows` with `cache: "no-store"`, retains the last successful definitions after a failed refresh, keeps rows visible while retrying, labels the view as showing last-known flows, and exposes header Refresh plus banner Retry controls. Its table now has an accessible name/caption, scoped headers, responsive overflow, and 44px targets for refresh/create/search/filter/selection/link/delete controls. A 401/403 read or delete response now exposes a named access-denied region, preserves only previously loaded definitions as read-only text, and hides refresh/retry, New Flow, search/status filters, selection, editing, and deletion controls. [`flows/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/flows/page.tsx>) and [`flows-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/flows-states.spec.ts) cover stale retention, the focused table/accessibility baseline, initial denial, retained read denial, and mutation denial 4/4 against the source-built dashboard (`6b0413b`, `3108b02`).
- The flow editor reads a route-selected flow through the canonical store, shows an explicit first-load unavailable state with Retry instead of exposing a blank editable canvas, and provides Refresh plus last-known flow/Retry recovery after a failed refresh. Its editor now exposes semantic header/main/palette/canvas/config landmarks, 44px toolbar/palette/config targets, and 44px generated schema-form controls. A 401/403 flow read or save now exposes a named denial region, preserves only the last successfully loaded canvas as read-only, and hides refresh/retry, palette, editing, undo/redo, and save controls. Backend error details remain visible, and the source-built [`flow-editor-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/flow-editor-states.spec.ts) covers first-load failure, retained nodes, stale labeling, recovery, initial denial, retained read denial, mutation denial, and the focused accessibility baseline 4/4 (`b5098e7`, `140af1c`, `392d264`).
- The project detail page distinguishes first-load API failure from a missing project, preserves backend error detail, shows an explicit Project unavailable state, and exposes a keyboard-visible Retry that recovers into the project workspace. [`projects/[id]/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/projects/[id]/page.tsx>) and [`project-detail-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/project-detail-states.spec.ts) cover failure → Retry → recovered detail 1/1 (`f364763`).
- The Project Detail page now also exposes a named page/loading state, explicit project status, 44px refresh/retry/back and primary project-view tab targets, and semantic project/workspace tab-panel relationships. [`projects/[id]/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/projects/[id]/page.tsx>) and [`project-detail-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/project-detail-states.spec.ts) cover the focused accessibility baseline 1/1 (`40b87dd`). Full project composition, WCAG, native-Linux, and live provider/worker evidence remain separate.
- The project workspace sub-surface retains its last successful workspace summary when `/workspace` or `/repository` refreshes fail, keeps canonical activity/resources/cost data visible, preserves the last repository snapshot across a partial failure, and exposes a keyboard-visible Retry. Its nested Activity/Resources/Cost tabs now expose semantic `aria-controls`/`aria-labelledby` relationships, roving `tabIndex`, Arrow/Home/End navigation, and 44px targets. [`projects/[id]/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/projects/[id]/page.tsx>) and [`project-workspace-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/project-workspace-states.spec.ts) cover failure → retained workspace → recovered workspace plus keyboard tab recovery 1/1 (`cb1c665`, `fcb0f4b`).
- Container Logs streams `/api/logs/[container]` over SSE, retains the last buffer when a reload emits an error, labels it as last-known data, and exposes Retry; a successful first event replaces the previous buffer so container switches do not mix lines. Its named main/filter/legend/output/status regions, 44px stream/filter/recovery targets, and `aria-busy` log output form a focused accessibility baseline. [`logs/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/logs/page.tsx>) and [`logs-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/logs-states.spec.ts) cover failure retention, successful recovery, and these semantics 1/1 against the source-built dashboard (`280d363`, `993b1cb`).
- Agent Streams fetches bounded history with `cache: "no-store"`, guards reconnect generations so obsolete events cannot overwrite the active team, retains the last messages when history or SSE refresh fails, labels the view as last-known data, and exposes both Reconnect and Retry. Its named main/filter/feed/status structure, captioned message table, keyboard-accessible expandable rows, 44px stream/filter/action targets, and `aria-busy` feed state form a focused accessibility baseline. [`streams/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/streams/page.tsx>) and [`streams-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/streams-states.spec.ts) cover failure retention, successful recovery, and these semantics 1/1 against the source-built dashboard (`3e8a0ea`, `d320383`).
- The Hiring Board worker catalogue retains rows through failed refreshes and exposes last-known/Retry recovery. Its named main/policy/summary/filter/table structure, integration/runtime status landmarks, captioned/scoped table, keyboard-expandable rows, associated registration-dialog fields, and 44px refresh/register/filter/selection/row-action/dialog targets form a focused accessibility baseline. [`workers/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/workers/page.tsx>) and [`workers-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/workers-states.spec.ts) cover failure retention, successful recovery, and these semantics 1/1 (`7541b84`, `826b4c5`).
- The CEO Live Feed fetches bounded history with `cache: "no-store"`, guards reconnect generations, retains messages through failed refreshes, labels last-known data, and exposes Reconnect plus Retry without changing the governed composer. Its named main/composer/summary/filter/feed/status regions, 44px stream/composer/filter/recovery targets, busy feed state, and keyboard-expandable messages form a focused accessibility baseline. [`ceo/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/ceo/page.tsx>) and [`ceo-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/ceo-states.spec.ts) cover failure retention, successful recovery, and these semantics 1/1 against the source-built dashboard (`1761429`, `1f947a9`).
- System visualisation fetches each source independently, retains responding sections when another source is unavailable, labels partial data as stale, and offers a retry action; PM integrations use the same last-known/conflict-preserving pattern for refresh failures. The targeted resilience checks live in [`app-operations.spec.ts`](../../mas/apps/mas-dashboard/e2e/app-operations.spec.ts).
- System visualisation now also has a focused accessibility baseline: named loading/error/ready page landmarks, horizontal visualization tabs with semantic tab/tabpanel links, and 44px breadcrumb, refresh, Mermaid-copy, path-trace, graph/detail, policy, retry, and back-link targets. The deterministic fixture-backed hierarchy/path-tracing test covers these semantics 1/1 (`ed5e551`). Full WCAG/native-Linux visual certification remains open.
- System visualisation now distinguishes a denied hierarchy read from a transient outage: a 401/403 hierarchy response renders an explicit access-denied region with no misleading Retry action, while independent partial failures retain available data and identify each failed source. The focused source-built resilience matrix now passes 4/4 for healthy, partial, offline, and denied hierarchy states (`db898e7`).
- PM integrations now also have a focused accessibility baseline: a named busy main landmark, explicit summary/connections/reconciliation/lifecycle regions, labeled lifecycle inputs, and 44px refresh/retry/generation/approval/apply controls. The existing fixture-backed conflict/stale-retry test covers these semantics 1/1 (`bbd6ba3`). Full WCAG/native-Linux visual certification and provider-owned evidence remain open.
- PM integrations now distinguish a denied read from a transient outage: a 401/403 response renders a named access-denied region, hides Refresh/Retry and lifecycle-plan generation, review, approval, and apply controls, and preserves only last-known reconciliation context after authorization is lost. The focused source-built matrix covers stale recovery, first-load denial, and post-read denial 3/3 (`7373360`).
- System Overview now also has a focused accessibility baseline: a named main and hero/status surface, explicit health/overview-metrics/first-run/company-project-state/Quick Links regions, decorative-icon suppression, and 44px graph/Quick Links/seed controls. The source-built first-run test covers these semantics and target sizes 1/1 for both `seeded` and `not_seeded` local deterministic orchestrator fixtures (`c07b4a6`). Full WCAG/native-Linux visual certification and live control-plane availability remain open.
- System Overview now classifies its seven independent control-plane/metrics reads as healthy, partial, or offline, names failed sources without inferring unavailable state, and exposes a bounded GET retry. The source-built recovery fixture passes 1/1 for both explicit offline and partial-source runs (`50cee61`). The shared `EmptyState` primitive now hides decorative status icons from assistive technology, covered by the offline overview assertion (`24be4ba`).
- The shared `ErrorBanner` primitive now marks its severity icon as decorative for assistive technology; the System Visualization partial-state fixture asserts the rendered status icon is hidden (`29b700c`).
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
- The authenticated local Compose dashboard run passes 58/59 Playwright tests
  (one explicit safe DLQ-fixture skip), covering credential masking, worker
  lifecycle, project workspace creation/audit/cost views, CEO directives and
  hiring context, schema-driven flow editing, all eight branching/recovery
  scenarios, hiring board evaluation details, identity stale-record/retry state, PM integration
  conflict/stale retry state, project-detail stale/retry state, runtime-status
  panels, mobile shell keyboard
  focus recovery, hierarchy communication-policy/path tracing, and
  system-visualization partial/offline retry states.
  Secret-safe
  evidence is retained at
  [`mas/docs/provenance/dashboard_e2e_live.json`](../../mas/docs/provenance/dashboard_e2e_live.json).

## Code anchors

- Dashboard application: [`mas/apps/mas-dashboard/app/`](../../mas/apps/mas-dashboard/app/)
- Operator sign-in accessibility: [`login/page.tsx`](<../../mas/apps/mas-dashboard/app/login/page.tsx>) and [`login-accessibility.spec.ts`](../../mas/apps/mas-dashboard/e2e/login-accessibility.spec.ts) (`d928834`)
- Components: [`mas/apps/mas-dashboard/components/`](../../mas/apps/mas-dashboard/components/)
- Shared empty-state accessibility: [`EmptyState.tsx`](../../mas/apps/mas-dashboard/components/ui/EmptyState.tsx) (`24be4ba`)
- Shared error-banner accessibility: [`ErrorBanner.tsx`](../../mas/apps/mas-dashboard/components/ui/ErrorBanner.tsx) and [`app-operations.spec.ts`](../../mas/apps/mas-dashboard/e2e/app-operations.spec.ts) (`29b700c`)
- Flow editor: [`mas/apps/mas-dashboard/app/(dashboard)/flows/`](<../../mas/apps/mas-dashboard/app/(dashboard)/flows/>)
- Generated flow contract/form: [`mas/apps/mas-dashboard/components/flows/NodeSchemaContractSummary.tsx`](../../mas/apps/mas-dashboard/components/flows/NodeSchemaContractSummary.tsx), [`mas/apps/mas-dashboard/components/flows/NodeSchemaForm.tsx`](../../mas/apps/mas-dashboard/components/flows/NodeSchemaForm.tsx)
- Project workspace: [`mas/apps/mas-dashboard/app/(dashboard)/projects/[id]/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/projects/[id]/page.tsx>)
- System visualisation: [`mas/apps/mas-dashboard/components/system-viz/`](../../mas/apps/mas-dashboard/components/system-viz/)
- System-visualisation access-denied and resilience state: [`system-viz/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/system-viz/page.tsx>) and [`app-operations.spec.ts`](../../mas/apps/mas-dashboard/e2e/app-operations.spec.ts) (`db898e7`)
- System-visualisation accessibility state: [`system-viz/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/system-viz/page.tsx>), [`HierarchyViz.tsx`](../../mas/apps/mas-dashboard/components/system-viz/HierarchyViz.tsx), [`PermissionsViz.tsx`](../../mas/apps/mas-dashboard/components/system-viz/PermissionsViz.tsx), [`OrchestrationViz.tsx`](../../mas/apps/mas-dashboard/components/system-viz/OrchestrationViz.tsx), and [`app-operations.spec.ts`](../../mas/apps/mas-dashboard/e2e/app-operations.spec.ts) (`ed5e551`)
- System Overview accessibility and source-status recovery: [`page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/page.tsx>), [`QuickLinkCard.tsx`](../../mas/apps/mas-dashboard/components/ui/QuickLinkCard.tsx), [`SeedDefaultCompanyButton.tsx`](../../mas/apps/mas-dashboard/components/SeedDefaultCompanyButton.tsx), [`first-run-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/first-run-states.spec.ts), and [`system-overview-recovery.spec.ts`](../../mas/apps/mas-dashboard/e2e/system-overview-recovery.spec.ts) (`c07b4a6`, `50cee61`)
- PM-integrations accessibility state: [`integrations/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/integrations/page.tsx>) and [`app-operations.spec.ts`](../../mas/apps/mas-dashboard/e2e/app-operations.spec.ts) (`bbd6ba3`)
- Dashboard shell accessibility: [`DashboardShell.tsx`](../../mas/apps/mas-dashboard/components/DashboardShell.tsx), [`Sidebar.tsx`](../../mas/apps/mas-dashboard/components/Sidebar.tsx), [`dashboard-shell-accessibility.spec.ts`](../../mas/apps/mas-dashboard/e2e/dashboard-shell-accessibility.spec.ts)
- Theme preference and palette foundation: [`ThemeProvider.tsx`](../../mas/apps/mas-dashboard/components/ThemeProvider.tsx), [`ThemeToggle.tsx`](../../mas/apps/mas-dashboard/components/ThemeToggle.tsx), [`globals.css`](../../mas/apps/mas-dashboard/app/globals.css), [`dashboard-theme.spec.ts`](../../mas/apps/mas-dashboard/e2e/dashboard-theme.spec.ts)
- Bounded evidence detail proxy/read model: [`mas/apps/mas-dashboard/app/api/evidence/[kind]/[id]/route.ts`](<../../mas/apps/mas-dashboard/app/api/evidence/[kind]/[id]/route.ts>), [`dashboard-evidence-detail.spec.ts`](../../mas/apps/mas-dashboard/e2e/dashboard-evidence-detail.spec.ts)
- Artifact/usage evidence authorities: [`get_artifact_evidence`](../../mas/apps/orchestrator-api/orchestrator_api/main.py), [`get_usage_event_evidence`](../../mas/apps/orchestrator-api/orchestrator_api/main.py), [`get_project_usage_event`](../../mas/packages/mas-core/mas_core/memory/storage.py), and [`test_evidence_detail_routes.py`](../../mas/apps/orchestrator-api/tests/test_evidence_detail_routes.py)
- Identity resource state, accessibility, and route matrix: [`IdentityResourcePage.tsx`](../../mas/apps/mas-dashboard/components/identity/IdentityResourcePage.tsx), [`identity-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/identity-states.spec.ts), and [`identity-resource-matrix.spec.ts`](../../mas/apps/mas-dashboard/e2e/identity-resource-matrix.spec.ts) (`a260e04`, `485dfd2`)
- PM integration conflict/stale and access-denied state: [`integrations/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/integrations/page.tsx>), [`app-operations.spec.ts`](../../mas/apps/mas-dashboard/e2e/app-operations.spec.ts) (`7373360`)
- Project-detail stale/retry state: [`projects/[id]/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/projects/[id]/page.tsx>), [`app-operations.spec.ts`](../../mas/apps/mas-dashboard/e2e/app-operations.spec.ts)
- Project-detail first-load/retry state: [`projects/[id]/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/projects/[id]/page.tsx>), [`project-detail-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/project-detail-states.spec.ts)
- Project-detail accessibility state: [`projects/[id]/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/projects/[id]/page.tsx>), [`project-detail-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/project-detail-states.spec.ts) (`40b87dd`)
- Evidence-detail accessibility state: [`evidence/[kind]/[id]/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/evidence/[kind]/[id]/page.tsx>), [`dashboard-evidence-detail.spec.ts`](../../mas/apps/mas-dashboard/e2e/dashboard-evidence-detail.spec.ts) (`32f3a76`)
- Project-workspace stale/retry state: [`projects/[id]/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/projects/[id]/page.tsx>), [`project-workspace-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/project-workspace-states.spec.ts)
- Project evidence package stale/retry and accessibility state: [`projects/[id]/evidence/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/projects/[id]/evidence/page.tsx>), [`project-evidence-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/project-evidence-states.spec.ts) (`bc80ad5`, `89091c1`)
- Governance stale/retry, accessibility, and access-denied state: [`governance/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/governance/page.tsx>), [`ExecutiveActionPanel.tsx`](../../mas/apps/mas-dashboard/components/governance/ExecutiveActionPanel.tsx), [`governance-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/governance-states.spec.ts) (`52de581`, `f4ae7eb`, `888fde3`)
- System status stale/retry and accessibility state: [`system/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/system/page.tsx>), [`system-status-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/system-status-states.spec.ts) (`f445c17`, `543f392`)
- Projects list stale/retry, accessibility, and access-denied state: [`projects/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/projects/page.tsx>), [`projects-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/projects-states.spec.ts) (`7828b48`, `17d25b0`)
- Tools catalogue stale/retry, accessibility, and access-denied state: [`tools/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/tools/page.tsx>), [`app/api/tools/route.ts`](../../mas/apps/mas-dashboard/app/api/tools/route.ts), [`tools-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/tools-states.spec.ts) (`5f4b0eb`, `83e39e6`, `b418f8a`)
- Dead-letter queue stale/retry, accessibility, and access-denied state: [`dlq/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/dlq/page.tsx>), [`app/api/dlq/route.ts`](../../mas/apps/mas-dashboard/app/api/dlq/route.ts), [`app/api/dlq/[id]/replay/route.ts`](<../../mas/apps/mas-dashboard/app/api/dlq/[id]/replay/route.ts>), [`FilterChips.tsx`](../../mas/apps/mas-dashboard/components/ui/FilterChips.tsx), [`dlq-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/dlq-states.spec.ts) (`823fa6d`, `99a19a2`, `e6ab3a1`)
- Credentials stale/retry, accessibility, and access-denied state: [`credentials/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/credentials/page.tsx>), [`BulkActionBar.tsx`](../../mas/apps/mas-dashboard/components/ui/BulkActionBar.tsx), [`credentials-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/credentials-states.spec.ts) (`970f09c`, `e6e6980`, `93fdfbc`, `982c9c0`)
- Metrics partial/stale/retry, accessibility, and access-denied state: [`metrics/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/metrics/page.tsx>), [`app/api/metrics/route.ts`](<../../mas/apps/mas-dashboard/app/api/metrics/route.ts>), [`lib/prometheus.ts`](../../mas/apps/mas-dashboard/lib/prometheus.ts), [`metrics-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/metrics-states.spec.ts) (`85596b0`, `da113af`, `b64b15e`)
- Flows list stale/retry, accessibility, and access-denied state: [`flows/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/flows/page.tsx>), [`flows-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/flows-states.spec.ts) (`6b0413b`, `3108b02`)
- Flow editor load/stale/retry, accessibility, and access-denied state: [`flows/[id]/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/flows/[id]/page.tsx>), [`flow-store.ts`](../../mas/apps/mas-dashboard/lib/flow-store.ts), [`flow-editor-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/flow-editor-states.spec.ts) (`140af1c`, `392d264`)
- Container Logs stale/retry, accessibility, and access-denied state: [`logs/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/logs/page.tsx>), [`app/api/logs/[container]/route.ts`](<../../mas/apps/mas-dashboard/app/api/logs/[container]/route.ts>), [`logs-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/logs-states.spec.ts) (`280d363`, `993b1cb`, `156597c`)
- Agent Streams stale/retry, accessibility, and access-denied state: [`streams/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/streams/page.tsx>), [`app/api/streams/[team_id]/route.ts`](../../mas/apps/mas-dashboard/app/api/streams/[team_id]/route.ts), [`streams-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/streams-states.spec.ts) (`3e8a0ea`, `d320383`, `118ff18`)
- Hiring Board stale/retry, accessibility, and access-denied state: [`workers/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/workers/page.tsx>), [`workers-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/workers-states.spec.ts) (`7541b84`, `826b4c5`, `553f196`)
- CEO Live Feed stale/retry, accessibility, and access-denied state: [`ceo/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/ceo/page.tsx>), [`app/api/streams/[team_id]/route.ts`](<../../mas/apps/mas-dashboard/app/api/streams/[team_id]/route.ts>), [`ceo-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/ceo-states.spec.ts) (`1761429`, `1f947a9`, `a3cbd99`)
- CEO Command Center chat stale/retry and accessibility state: [`ceo/chat/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/ceo/chat/page.tsx>), [`use-ceo-stream.ts`](../../mas/apps/mas-dashboard/lib/ceo-feed/use-ceo-stream.ts), [`ceo-chat-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/ceo-chat-states.spec.ts) (`beabb95`, `8ffb5df`)
- Executive reconciliation proxy, role-view proxy, and role cards: [`mas/apps/mas-dashboard/app/api/executive/reconciliation/route.ts`](../../mas/apps/mas-dashboard/app/api/executive/reconciliation/route.ts), [`mas/apps/mas-dashboard/app/api/executive/views/[role]/route.ts`](<../../mas/apps/mas-dashboard/app/api/executive/views/[role]/route.ts>), [`mas/apps/mas-dashboard/app/(dashboard)/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/page.tsx>)
- Executive action proxies: [`mas/apps/mas-dashboard/app/api/executive/actions/`](../../mas/apps/mas-dashboard/app/api/executive/actions/)
- Executive action panel: [`mas/apps/mas-dashboard/components/governance/ExecutiveActionPanel.tsx`](../../mas/apps/mas-dashboard/components/governance/ExecutiveActionPanel.tsx)
- CEO evidence citation/deep-link surfaces: [`mas/apps/mas-dashboard/app/(dashboard)/ceo/chat/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/ceo/chat/page.tsx>), [`mas/apps/mas-dashboard/app/(dashboard)/evidence/[kind]/[id]/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/evidence/[kind]/[id]/page.tsx>)
- CEO Live Feed stale/retry state: [`mas/apps/mas-dashboard/app/(dashboard)/ceo/page.tsx`](<../../mas/apps/mas-dashboard/app/(dashboard)/ceo/page.tsx>), [`ceo-states.spec.ts`](../../mas/apps/mas-dashboard/e2e/ceo-states.spec.ts)
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

The CEO Command Center chat now exposes a semantic main/workspace/transcript/
composer structure, live transcript status, 44px operator controls, and named
guidance/quick-command regions. This is a focused page-level accessibility
baseline; native/live Redis/router and full WCAG evidence remain open
(`8ffb5df`).

## Remaining gaps

- The authenticated local API ACL matrix is retained at
  [`mas/docs/provenance/dashboard_acl_live.json`](../../mas/docs/provenance/dashboard_acl_live.json):
  operator/CEO/service/worker identities receive the expected 200/403 outcomes
  for CEO, credentials, projects, and workers sections. Run the persisted ACL
  through the native-Linux dashboard/network matrix and add UI-level denial
  states before release certification. The local Compose UI matrix is green at
  58/59 with the explicit DLQ fixture skip, shell keyboard/mobile focus,
  identity stale-record/retry, PM integration conflict/stale retry,
  project-detail stale/retry, and
  system-visualization partial/offline retry coverage; it is not native-Linux
  release evidence.
- Generate typed forms/clients from backend schemas for the remaining governance actions; the CFO/CTO/CEO action panel is implemented, while broader governance action forms remain.
- Consolidate legacy flow compatibility aliases into an explicit extension editor after migration coverage is complete.
- Complete page-by-page light/dark/system visual parity and responsive/mobile parity; the persisted preference, no-flash bootstrap, palette migration layer, and reduced-motion baseline are implemented in `5e3cc13`.
- Complete the WCAG 2.2 AA audit and broader automated checks; the shell landmark/skip-link/focus baseline is covered, while page-level semantics, contrast, reduced motion, and native-Linux evidence remain.
- Extend the focused System Overview source-status baseline into retained
  stale/offline/partial history and page-level visual/mobile certification; its
  current explicit source classification and GET retry are covered by
  `50cee61`, while the named landmarks, first-run state, and 44px operator
  targets remain covered by `c07b4a6`.
- Improve stale/conflict/rollback and partial-evidence experiences; system
  visualization, identity tables, PM integrations, project list/detail/workspace, the
  combined governance read surface, System Control, Tools catalogue,
  dead-letter queue, Metrics, credentials list, project evidence package page,
  flow editor, and project detail now have explicit
  stale/partial/conflict states; the Flows list, Container Logs, and Agent
  Streams now have explicit stale/retry and first-load/offline retry states,
  and System Visualization now has an explicit hierarchy access-denied state;
  broader page-specific denial and
  rollback coverage remains.
- [x] Add bounded CEO citation/deep-link coverage across tools, models, workers,
  integrations, artifacts, usage, runtimes, and trace records; the dedicated
  evidence route never renders payloads. The scalar detail projection is now
  implemented for fourteen kinds, including model/integration/tool selections,
  scalar-only trace summaries, operator-authenticated artifact/usage reads, and
  stale-refresh retention with an explicit retry control; broader stale/offline
  states and full golden-path coverage remain.
- The legacy CEO model fallback now supports explicit, stripped `AIAT_EVIDENCE`
  markers and labels them unverified (`f1801bb`); authoritative deterministic
  citations remain preferred. Broader evidence detail kinds, stale/offline
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
