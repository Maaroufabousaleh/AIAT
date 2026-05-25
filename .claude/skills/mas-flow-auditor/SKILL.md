---
name: mas-flow-auditor
description: Audit AIAT MAS flow definitions, flow runtime behavior, approval gates, retry/escalation paths, persistence, dashboard flow controls, and operator integration tests. Use for changes under `mas/packages/mas-core/mas_core/workflow`, `mas/apps/orchestrator-api` flow endpoints, `mas/apps/mas-dashboard/app/(dashboard)/flows`, project flow runtime UI, approval decision handling, or tests named `test_flows*`, `flow-builder.spec.ts`, and `flow-runtime-test2.spec.ts`.
---

# MAS Flow Auditor

## Overview

Use this skill to verify that MAS flow changes preserve runtime semantics, operator approval behavior, and persisted project state.

## Audit Workflow

1. Map the flow boundary.
   - Flow engine and validation: `mas/packages/mas-core/mas_core/workflow`.
   - Backend endpoints and runtime orchestration: `mas/apps/orchestrator-api/orchestrator_api/main.py`.
   - Storage and approval gates: `mas/packages/mas-core/mas_core/memory/storage.py` and `models.py`.
   - Dashboard flow builder/runtime UI: `mas/apps/mas-dashboard/app/(dashboard)/flows` and project detail pages.
   - Tests: `mas/apps/orchestrator-api/tests/test_flows*.py` and dashboard `e2e/flow-*.spec.ts`.

2. Check core flow semantics.
   - Approval nodes require a role or user.
   - Approval decisions should route through `approved`, `edit_requested`, and `rejected` outcomes where supported.
   - Retry should resume from the intended safe node and preserve enough context for auditability.
   - Failed or terminal instances should remain visible when the dashboard needs refresh/retry behavior.
   - Flow validation should reject malformed nodes, edges, missing targets, and invalid approval/switch config.

3. Verify persistence, not just response status.
   - Check `approval_gates`, `flow_node_executions`, `project_state_history`, and instance context expectations in tests.
   - Ensure project workspace read models still surface pending approvals, next actions, artifacts, audit timeline, and worker activity.
   - Confirm dashboard decision payloads match backend expectations.

4. Test with the smallest meaningful ladder.
   - Unit-level flow engine checks for validation or routing rules.
   - Orchestrator integration tests for API/runtime/storage behavior.
   - Dashboard E2E for operator interactions and visual state.

## Validation Commands

Backend:

```bash
cd mas
uv run pytest apps/orchestrator-api/tests/test_flows.py apps/orchestrator-api/tests/test_flows_test2_runtime.py apps/orchestrator-api/tests/test_flows_operator_integration.py -q
```

WSL fallback:

```powershell
wsl.exe bash -lc 'cd /mnt/c/projects/AIAT/mas && .venv-wsl/bin/python -m pytest apps/orchestrator-api/tests/test_flows.py apps/orchestrator-api/tests/test_flows_test2_runtime.py apps/orchestrator-api/tests/test_flows_operator_integration.py -q'
```

Dashboard:

```bash
cd mas/apps/mas-dashboard
npm run build
npx playwright test --workers=1 e2e/flow-builder.spec.ts e2e/flow-runtime-test2.spec.ts
```

When reporting findings, separate these statuses:

- Flow definition validates.
- Runtime transition works.
- Persistence/audit rows are correct.
- Dashboard operator action works.
- Live stack was or was not available.
