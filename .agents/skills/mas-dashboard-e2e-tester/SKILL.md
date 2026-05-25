---
name: mas-dashboard-e2e-tester
description: Validate AIAT MAS dashboard operator workflows with build checks, protocol fixture checks, and Playwright tests. Use for tasks involving `mas/apps/mas-dashboard`, dashboard routes, Next.js API proxies, project workspace UI, Hiring Board, credentials, tools, system visualization, flow builder/runtime UI, authentication, or claims that a dashboard action works for an operator.
---

# MAS Dashboard E2E Tester

## Overview

Use this skill to test dashboard behavior the way an operator will experience it, not only through backend unit tests.

## Dashboard Workflow

1. Inspect the page and proxy route together.
   - Pages live under `mas/apps/mas-dashboard/app/(dashboard)`.
   - API proxy routes live under `mas/apps/mas-dashboard/app/api`.
   - Shared client helpers live under `mas/apps/mas-dashboard/lib`.
   - E2E tests live under `mas/apps/mas-dashboard/e2e`.

2. Keep dashboard state actionable.
   - Show pending approvals, blocked states, evaluation warnings, unavailable data, and backend errors explicitly.
   - For worker changes, verify the Hiring Board workflow, not only the raw API.
   - For project changes, verify Workspace, audit timeline, artifacts, approvals, and flow controls where relevant.
   - For system visualization, verify org graph data and Mermaid export when touched.

3. Use resilient Playwright locators.
   - Prefer roles, headings, labels, and stable `data-testid` values.
   - Avoid strict-mode ambiguity with repeated text like `Artifacts`, `Worker Activity`, or section names.
   - If a new workflow lacks stable selectors, add specific `data-testid` attributes close to the interaction.

4. Preserve dashboard command hygiene.
   - `npx tsc --noEmit` or protocol checks can modify `tsconfig.tsbuildinfo`; do not treat generated build-info churn as a source change.
   - Keep authentication helpers centralized in E2E tests when possible.

## Validation Ladder

Start with static validation:

```bash
cd mas/apps/mas-dashboard
npm run lint
npm run build
npm run test:protocol-fixtures
```

Then run the closest E2E spec:

```bash
cd mas/apps/mas-dashboard
npx playwright test --workers=1 e2e/app-operations.spec.ts
npx playwright test --workers=1 e2e/hiring-board.spec.ts
npx playwright test --workers=1 e2e/flow-builder.spec.ts e2e/flow-runtime-test2.spec.ts
```

Use the live Compose stack for operator-facing claims when the task asks whether the dashboard action works now:

```bash
cd mas
docker compose -f infra/compose/docker-compose.yml --env-file infra/compose/.env up -d --build
curl http://localhost:4000/api/health
```

Report dashboard results as:

- What operator workflow was tested.
- Which backend/proxy/page files were involved.
- Exact command results and pass counts.
- Any unavailable live dependency or environment blocker.
