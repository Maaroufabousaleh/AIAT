---
name: mas-api-contract-verifier
description: Verify AIAT MAS API contracts across FastAPI services, shared protocol schemas, Next.js dashboard proxy routes, TypeScript declarations, and Playwright expectations. Use for changes under `mas/apps/orchestrator-api`, `mas/apps/tool-service`, `mas/apps/message-router`, `mas/apps/mas-dashboard/app/api`, `mas/packages/mas-core/mas_core/protocols`, protocol fixtures, worker endpoints, project workspace endpoints, org graph endpoints, or any dashboard/API payload mismatch.
---

# MAS API Contract Verifier

## Overview

Use this skill when a MAS change crosses a service boundary. The goal is to prove the backend shape, dashboard proxy shape, TypeScript assumptions, and operator UI assertions still agree.

## Contract Workflow

1. Identify every boundary touched.
   - Orchestrator API: `mas/apps/orchestrator-api/orchestrator_api/main.py`
   - Tool service and message router: `mas/apps/tool-service`, `mas/apps/message-router`
   - Dashboard proxies: `mas/apps/mas-dashboard/app/api/**/route.ts`
   - Dashboard pages/components: `mas/apps/mas-dashboard/app/(dashboard)/**`
   - Shared protocols: `mas/packages/mas-core/mas_core/protocols`, `mas/packages/mas-core/schemas/protocol`, and protocol fixtures.

2. Read tests before editing.
   - Worker registry: `mas/apps/orchestrator-api/tests/test_capabilities.py`, `test_workers_test4_config.py`, `test_workers_test5_lifecycle.py`
   - System/company/org graph: `mas/apps/orchestrator-api/tests/test_system.py`
   - Flow contracts: `mas/apps/orchestrator-api/tests/test_flows*.py`
   - Protocol contracts: `mas/packages/mas-core/tests/test_protocols.py`
   - Dashboard E2E: `mas/apps/mas-dashboard/e2e/*.spec.ts`

3. Check the actual payload shape.
   - Do not assume dashboard form field names match backend request fields.
   - `POST /capabilities/workers` expects backend fields such as `name`, `adapter_type`, `adapter_config`, and `sandbox_profile`.
   - Worker evaluation and activation must preserve evaluation status, risk tier, approval requirement, and sandbox rules.
   - Project approval decisions should use `decision`, `decided_by`, and `comments` where that backend path expects the current decision shape.

4. Keep proxy normalization explicit.
   - Dashboard API routes should translate backend shapes into page-friendly shapes without hiding missing data.
   - Preserve explicit unavailable states for logs, cost/usage, or live-only data.
   - Avoid browser-side service credentials; the dashboard proxy owns service credentials server-side.

## Validation

Run targeted backend tests for the changed endpoint first:

```bash
cd mas
uv run pytest apps/orchestrator-api/tests/test_capabilities.py apps/orchestrator-api/tests/test_workers_test5_lifecycle.py -q
```

For protocol changes, run both Python and dashboard contract checks:

```bash
cd mas
uv run pytest packages/mas-core/tests/test_protocols.py -q
cd apps/mas-dashboard
npm run test:protocol-fixtures
```

For dashboard proxy or page changes, run TypeScript/build plus the closest Playwright spec:

```bash
cd mas/apps/mas-dashboard
npm run build
npx playwright test --workers=1 e2e/app-operations.spec.ts
```

If Windows `uv` hits the known `mas/.venv` issue, rerun backend tests through WSL:

```powershell
wsl.exe bash -lc 'cd /mnt/c/projects/AIAT/mas && .venv-wsl/bin/python -m pytest <tests> -q'
```
