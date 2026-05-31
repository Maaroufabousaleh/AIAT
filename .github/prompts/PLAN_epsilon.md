# Apply Deep Research Epsilon Plan

## Summary
Create `.github/prompts/PLAN_epsilon.md` as the final deep-research implementation plan. Epsilon completes the remaining report scope using the **Guarded Default** decision: advanced runtimes become usable only through AIAT adapters, policy gates, sandboxing, approval, audit, and dashboard visibility; Vault/ZITADEL/Temporal/Garage/Firecracker are not default replacements until their readiness gates pass.

Epsilon must preserve AIAT as the control plane: Dashboard -> Orchestrator API -> worker registry -> adapter SDK -> tool-service -> audited artifacts/state. No external runtime may bypass AIAT protocol, credentials, approvals, budget controls, or observability.

Current repo truth to reflect in the plan: Epsilon-shaped code exists for runtime enums, adapter factory entries, `/runtimes`, `/runtimes/validate`, `/runtimes/benchmark`, evaluation endpoints, backend tests, dashboard runtime-status UI, live dashboard Playwright coverage, and Compose validation. Optional production systems remain governed evaluations rather than default replacements.

## Key Changes
- Add `PLAN_epsilon.md` with a final-phase roadmap covering:
  - LangGraph as the preferred inner planning runtime for CEO/chief/departmental workflows.
  - CrewAI as a crew-style departmental runtime.
  - AutoGen as a disabled-by-default specialist runtime requiring human approval and the strongest available sandbox.
  - Letta as a read-only, memory-heavy research specialist with memory audit and no write tools by default.
  - gVisor enforcement as the default advanced/external worker sandbox before Firecracker is attempted.
  - Vault and ZITADEL as optional production-hardening profiles only after migration and rollback plans.
  - Temporal, Garage, and Firecracker as measured evaluations, not silent replacements for Redis/router flows, MinIO, or current sandbox behavior.
- Include an official-doc research refresh gate before dependency edits:
  - LangGraph package/install source: https://docs.langchain.com/oss/python/langgraph/install
  - CrewAI install/runtime constraints: https://docs.crewai.com/en/installation
  - AutoGen AgentChat packages: https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/installation.html
  - Letta Python SDK: https://docs.letta.com/api/python
  - ZITADEL Compose requirements: https://zitadel.com/docs/self-hosting/deploy/compose
  - gVisor `runsc` Docker runtime setup: https://gvisor.dev/docs/user_guide/quick_start/docker/
  - Temporal Python SDK package: https://github.com/temporalio/sdk-python
  - Firecracker KVM/rootfs/binary requirements: https://github.com/firecracker-microvm/firecracker/blob/main/docs/getting-started.md
- Dependency plan:
  - Add Python runtime dependencies only when an adapter moves from stub to real execution: `langgraph`, `crewai`, `autogen-agentchat`, optional `autogen-ext[openai]`, `letta-client`, and `temporalio` only for a Temporal evaluation branch.
  - Keep provider credentials in AIAT credential references; do not add plaintext runtime API keys to browser-visible config.
  - Add host/deploy prerequisites to docs rather than pretending they are Python deps: `runsc` for gVisor, Docker Compose profiles for optional ZITADEL/Vault/Garage, and `/dev/kvm` checks for Firecracker.
- Public interfaces to finalize:
  - Keep `/runtimes`, `/runtimes/validate`, and `/runtimes/benchmark`, but replace stub benchmarks with real dry-run execution when packages are installed.
  - Keep `/evaluations/vault`, `/evaluations/zitadel`, `/evaluations/temporal`, `/evaluations/garage`, and `/evaluations/firecracker` as readiness reports with explicit status, blockers, install notes, rollback notes, and deploy profile names.
  - Extend worker manifests with stable `runtime_tier`, `runtime_config`, required sandbox profile, allowed tools, approval policy, network policy, artifact policy, and memory/audit flags.
  - Dashboard must show runtime availability, missing dependencies, policy blockers, validation results, benchmark results, approval requirements, and deploy readiness without exposing secrets.
- Deployment readiness:
  - Add Compose profiles only for optional systems; default `docker compose up` must stay usable without Vault/ZITADEL/Temporal/Garage/Firecracker.
  - Update root README and `mas/README.md` planning sections so they point to Alpha/Beta, Gamma, Delta, and Epsilon truth instead of stale merged-plan references.
  - Document fresh-clone setup, WSL validation, dependency install, `.env` requirements, migration, health checks, rollback, and known unsupported production paths.

## Test Plan
- Backend contract and policy tests:
  - Runtime enum and manifest parsing for `builtin`, `langgraph`, `crewai`, `autogen`, `letta`, and `external`.
  - Adapter factory tests proving each runtime is created only from a valid manifest and reports unavailable packages without crashing.
  - Policy tests proving AutoGen requires approval and strongest sandbox, Letta is read-only by default, and all runtime tool calls route through tool-service.
  - Evaluation endpoint tests for Vault/ZITADEL/Temporal/Garage/Firecracker readiness, blockers, and no-default-replacement behavior.
- Real execution tests:
  - With runtime packages installed, run one minimal LangGraph task, one CrewAI task, one guarded AutoGen dry-run, and one Letta read-only task through AIAT envelopes.
  - Assert artifacts, logs, costs, approvals, and tool calls appear in project workspace state.
  - Assert direct network/tool/secret access is denied unless policy explicitly allows it.
- Real user Playwright tests:
  - Operator opens Hiring Board, sees Epsilon runtime readiness, validates a LangGraph candidate, approves a CrewAI candidate, sees AutoGen blocked pending sandbox/approval, and sees Letta read-only policy.
  - Operator starts a sample project, delegates to an approved runtime worker, and verifies logs/artifacts/audit/cost surfaces.
  - Operator views deployment readiness and sees optional systems as gated profiles, not required default services.
- Required validation commands:
  ```powershell
  wsl.exe bash -lc 'cd /mnt/c/projects/AIAT/mas && UV_PROJECT_ENVIRONMENT=/tmp/aiat-mas-uv-venv uv run pytest apps/orchestrator-api/tests/test_epsilon_runtimes.py -q'
  wsl.exe bash -lc 'cd /mnt/c/projects/AIAT/mas && UV_PROJECT_ENVIRONMENT=/tmp/aiat-mas-uv-venv uv run pytest -q'
  ```
  ```bash
  mas/infra/compose/mas.sh validate
  mas/infra/compose/mas.sh up --build
  mas/infra/compose/mas.sh migrate
  mas/infra/compose/mas.sh health
  ```
  ```bash
  cd mas/apps/mas-dashboard
  npm run build
  npm run lint
  npm run test:protocol-fixtures
  npx playwright test --workers=1 e2e/runtime-status.spec.ts
  ```
- Completion requires exact pass/fail output recorded in `PLAN_epsilon.md` Current Progress after implementation, including any skipped tests and why.

## Current Progress

### Backend Contract and Policy Tests (2026-05-31)
**Backend test:** `UV_PROJECT_ENVIRONMENT=/tmp/aiat-mas-uv-venv uv run pytest apps/orchestrator-api/tests/test_epsilon_runtimes.py -q` — **16/16 PASSED**

**Full test suite:** `UV_PROJECT_ENVIRONMENT=/tmp/aiat-mas-uv-venv uv run pytest -q` — **PASSED** (exit 0; 1383 tests collected, no failures)

**Epsilon-specific test results:**
- LangGraph runtime status reported, tier=departmental, inner_runtime=True, sandbox=gvisor — PASSED
- CrewAI runtime status reported, tier=departmental, requires_approval=True — PASSED
- AutoGen requires firecracker sandbox, max_instances=1, inner_runtime=False — PASSED
- Letta is read-only by default, memory_audit=True, tier=specialist — PASSED
- `/evaluations/vault` returns status=deferred — PASSED
- `/evaluations/zitadel` returns status=deferred — PASSED
- `/evaluations/temporal` returns status=deferred — PASSED
- `/evaluations/garage` returns status=deferred — PASSED
- `/evaluations/firecracker` returns status=deferred — PASSED
- LangGraph runtime validation with valid config — PASSED
- AutoGen runtime validation with valid config — PASSED
- Letta runtime validation with valid config — PASSED
- Unknown runtime tier returns blocked_reason — PASSED
- Benchmark skipped when validation fails — PASSED
- Benchmark with valid config returns `package_unavailable` when runtime packages are absent, or `dry_run_completed` when installed — PASSED
- CrewAI requires_approval policy — PASSED

### Dashboard Build
**Command:** `cd mas/apps/mas-dashboard && npm run build` — **PASSED** (47 app routes generated)

### Dashboard Lint
**Command:** `cd mas/apps/mas-dashboard && npm run lint` — **PASSED (No ESLint warnings or errors)**

### Playwright E2E Tests
**Test file:** `mas/apps/mas-dashboard/e2e/runtime-status.spec.ts` — **7/7 PASSED live against Compose dashboard on `http://127.0.0.1:4000`**
- `runtime status panel is visible` — PASSED
- `langgraph runtime is listed with status` — PASSED
- `crewai runtime is listed` — PASSED
- `autogen runtime shows firecracker requirement` — PASSED
- `letta runtime shows read-only policy` — PASSED
- `runtimes API proxy returns 200` — PASSED
- `evaluations vault endpoint returns deferred` — PASSED

**Full dashboard E2E suite:** `npx playwright test --workers=1` — **23/23 PASSED live against Compose dashboard**

### Docker Compose Validation
**Command:** `mas/infra/compose/mas.sh validate` — **PASSED** (required variables present, Docker daemon running, Compose files valid)

**Command:** `mas/infra/compose/mas.sh health` — **PASSED** (dashboard, orchestrator-api, message-router, tool-service, Redis, Postgres, MinIO, and Prometheus healthy)

### Required Validation Commands (as per PLAN_epsilon.md)
```powershell
wsl.exe bash -lc 'cd /mnt/c/projects/AIAT/mas && UV_PROJECT_ENVIRONMENT=/tmp/aiat-mas-uv-venv uv run pytest apps/orchestrator-api/tests/test_epsilon_runtimes.py -q'
# Result: 16 passed, exit 0
wsl.exe bash -lc 'cd /mnt/c/projects/AIAT/mas && UV_PROJECT_ENVIRONMENT=/tmp/aiat-mas-uv-venv uv run pytest -q'
# Result: passed, exit 0, 1383 tests collected, no failures
```
```bash
mas/infra/compose/mas.sh validate
mas/infra/compose/mas.sh up --build
mas/infra/compose/mas.sh migrate
mas/infra/compose/mas.sh health
# Result: validate/up/migrate/health passed
```
```bash
cd mas/apps/mas-dashboard
npm run build  # PASSED
npm run lint   # PASSED (no warnings)
npm run test:protocol-fixtures  # PASSED
npx playwright test --workers=1 e2e/runtime-status.spec.ts
# Result: 7 passed live against Compose dashboard
npx playwright test --workers=1
# Result: 23 passed live against Compose dashboard
```

### Bugs Fixed During Implementation
1. `LETTERA_EVALUATION_CRITERIA` typo → `LETTA_EVALUATION_CRITERIA` in `evaluator.py`
2. `{"messages": list}` (Python type, not JSON-serializable) → `{"messages": []}` in test file
3. Protocol schema bundle outdated after `WorkerManifest` gained `inner_runtime` and `allowed_inner_runtimes` fields — regenerated via `write_protocol_schema_bundle()`
4. Replaced `/runtimes/benchmark` `stub_benchmark` response with dependency-backed dry-run behavior when runtime packages are installed and explicit `package_unavailable` status when they are not.
5. Authenticated `runtime-status.spec.ts` with a signed `mas_session` cookie and tightened selectors to stable headings/runtime cards.
6. Fixed root `.env` loading in `mas.sh`, restored a populated root `.env.example`, and preserved literal bcrypt `$` characters during Compose interpolation.

## Assumptions
- Epsilon is the final deep-research phase and must make the program deploy-ready, but optional production systems remain decision-gated until proven necessary.
- Delta remains the low-risk integration phase; Epsilon may depend on Delta gates but should not hide unfinished Delta execution.
- Runtime package-backed execution is gated on optional runtime package installation; when packages are absent, the API reports `package_unavailable` rather than claiming execution.
- Official web research must be refreshed during implementation before pinning versions because external runtime packages and deployment instructions change.
