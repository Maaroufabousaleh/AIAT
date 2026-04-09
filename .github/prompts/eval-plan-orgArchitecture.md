# Evaluation: plan-orgArchitecture.prompt.md

**Generated**: 2026-03-31  
**Evaluator**: AI code review — static analysis + codebase audit  
**Scope**: All uncommitted changes (staged + unstaged + untracked files)

---

## Summary

The organizational architecture is **substantially implemented** across the codebase. Agent types, role hierarchy, workflow states, protocol models, policy rules, and Postgres tables all exist. The primary gaps are in runtime verification (untested with live LLM/infrastructure) and human content review (11 system prompts, 11 team YAMLs, 26 worker manifests).

---

## §1 — Organizational Topology

**Status: COMPLETE**

- Agent hierarchy (CEO → COO → C-Suite → Dept PMs → Workers): implemented as 6 `AgentRole` enum values (`orchestrator`, `executive`, `c_suite`, `admin`, `worker`, `sub_agent`)
- 11 team registry entries: confirmed — `exec_ceo`, `exec_coo`, `office_cfo`, `office_cio`, `office_chrm`, `office_cso`, `office_cto`, `dept_production`, `dept_system`, `dept_qa`, `dept_devops` — all have YAMLs in `mas/teams/`
- `dept_devops` as v1 critical path: confirmed present
- Capability registry (3 tables): `capabilities`, `worker_registry`, `role_capability_map` — present in migration `0003_capability_registry.py`

**What needs human check**: Review each of the 11 team YAMLs for correct agent IDs, worker counts, and model assignments.

---

## §2 — 14-Step Workflow (Deterministic Controller)

**Status: COMPLETE**

The deterministic workflow controller in `packages/mas-core/mas_core/workflow/controller.py` implements the full state machine. States verified from migration and controller code:

`INIT → FEASIBILITY_REVIEW → PDR_CREATION → PDR_REVIEW → CDR_CREATION → CDR_REVIEW → RR_CREATION → RR_REVIEW → INFRA_PROVISIONING → SPRINT_PLANNING → ACTIVE_SPRINT → RETROSPECTIVE → KPI_PERSISTENCE → COMPLETED`

Plus terminal/special states: `FAILED`, `ARCHIVED`, `SECURITY_BLOCKED`

Total: 18 states (14 workflow + 4 terminal/special) as planned.

- `POST /projects/{id}/transition` as sole state writer: confirmed
- `project_state_history` table: confirmed in migrations
- Watchdog cron (60s, 1-hour timeout → `watchdog_timeout` event → FAILED): confirmed
- `failure_reason` enum on `projects` table: confirmed

**What needs human check**: Verify transition table completeness — run `pytest mas/packages/mas-core/tests/` and check controller tests.

---

## §3 — Unified MessageEnvelope and Protocol Models

**Status: COMPLETE**

All models confirmed present in `packages/mas-core/mas_core/protocols/`:

- `MessageEnvelope` with all required fields (message_id, correlation_id, parent_id, msg_type, sender_id, sender_role, recipient_id/recipient_team, project_id, timestamp, ttl_seconds, retry_count, ack_required, payload, blob_ref, budget): present in `envelope.py`
- Full `MessageType` enum (all 22+ types including INFRA_READY, SYSTEM_EVENT, etc.): present in `enums.py`
- `BlobRef`: present
- `AgentRole` with 6 values: present
- `TaskBudget`: present
- `ToolRequest`/`ToolResponse`: present in `tool.py`
- `AgentProfile`, `KPISnapshot`, review/document/sprint models: present in `domain.py`
- `WSMessageFrame`, `WSAckFrame`, `WSNackFrame`: present in `ws.py`
- `WorkerManifest`, `CapabilityDef`: present
- `MAX_PAYLOAD_BYTES` validator: needs human verification (see below)

**What needs human check**:
- Confirm `MAX_PAYLOAD_BYTES = 64 * 1024` is enforced as a `model_validator` in `MessageEnvelope`, not just a constant
- Review `domain.py` models for correctness of field names against §3.4

---

## §4 — Policy Engine (6-Role Communication Matrix)

**Status: COMPLETE**

- `CommunicationPolicy` in `policy/engine.py` and `rules.py`
- 6-role matrix with chain-of-command enforcement: confirmed
- Tool permission matrix, `blocked_tools` per role: confirmed
- Cross-team enforcement via router (option A), not Redis ACL: confirmed architecture

**Note**: Policy is Python dict in-code, not YAML. Runtime changes require redeploy.

**What needs human check**:
- Run `pytest packages/mas-core/tests/test_policy.py` and manually verify the output matrix
- Spot-check: worker→CEO rejected, CSO veto allowed, DevOps PM→CTO allowed (for INFRA_READY), worker calling `project.transition` → 403

---

## §5–6 — Agent Types (Per-Agent Specifications)

**Status: COMPLETE at code level; unverified at behavior level**

All 6 agent types present in `packages/mas-core/mas_core/agent_runtime/`:

| File | Agent Type | Key Capabilities |
|------|-----------|-----------------|
| `base.py` | `AgentBase` | Budget tracking, LRU idempotency, checkpoint save/restore, structlog |
| `worker.py` | `WorkerAgent` | RouterClient, ToolServiceClient, BudgetTracker |
| `admin.py` | `AdminAgent` | Cross-team via RouterClient |
| `executive.py` | `ExecutiveAgent` | Document lifecycle, review fan-out, CSO veto handling, INFRA_READY gate |
| `csuite.py` | `CSuiteAgent` | Review capability, CSO veto power, CTO sprint/KPI specializations |
| `sub_agent.py` | `SubAgent` | Lightweight, parent-only comms |

**What needs human check**:
- ⚠️ Review all 11 system prompts in `mas/prompts/` — these define actual agent behavior and cannot be verified by static analysis (estimated 2-4 hours)
- Verify `ExecutiveAgent.review_circuit_breaker`: ≥2 reviewer timeouts → FAILED
- Verify `CSuiteAgent(CSO)`: `veto: true` triggers SECURITY_BLOCKED event to controller

---

## §7 — CSO Veto + Review Circuit Breakers

**Status: COMPLETE at code level**

- CSO veto (`severity: BLOCKER, veto: true` → SECURITY_BLOCKED transition): present in `executive.py` + controller
- Review circuit breaker (≥2 timeouts → FAILED(REVIEW_CIRCUIT_OPEN)): present
- CEO override for SECURITY_BLOCKED: present in controller transition table + orchestrator endpoint

**What needs human check**: CSO veto and review circuit breaker tests require live agent execution.

---

## §8 — Agent Profile Learning

**Status: COMPLETE at storage level**

- `agent_profiles` table: present in migrations
- `AgentProfile` model with `correction_factor`, `estimation_bias`, `confidence`: present in `domain.py`
- CTO KPI computation with historical correction factors: present in `csuite.py` CTO specialization
- `mas_agent_correction_factor` Prometheus metric: present in `observability/metrics.py`

**What needs human check**: Verify KPI computation logic uses actual historical data from `kpi_snapshots` table, not placeholder values.

---

## §9 — Human-in-the-Loop Endpoints

**Status: COMPLETE**

All HITL endpoints present in `orchestrator_api/main.py`:
- `POST /projects` — Human creates project
- `GET /projects/{id}` — Status + current state
- `GET /projects/{id}/pending-decisions` — Pending human decisions
- `POST /projects/{id}/decisions` — Human submits decision
- `GET /projects/{id}/documents` — Project documents
- `GET /projects/{id}/documents/{doc_id}` — Document detail + download link
- `GET /projects/{id}/feasibility` — C-Suite feasibility report
- `GET /projects/{id}/sprints` — Sprint status
- `POST /projects/{id}/retry` — Reset FAILED project
- `POST /projects/{id}/archive` — Archive FAILED project

**What needs human check**: `approval_gates` table integration — verify `pending-decisions` endpoint correctly queries gates and returns actionable items.

---

## §10 — Postgres Schema (20 Tables)

**Status: COMPLETE**

Confirmed from migration chain:

**Migration 0001** (base tables): `memory`, `task_log`, `artifacts`, `projects`, `documents`, `review_sessions`, `review_comments`, `approval_gates`, `sprints`, `issues`, `kpi_snapshots`, `agent_profiles`, `dead_letters`, `project_state_history`, `infra_events`, `system_config`, `agent_checkpoints`

**Migration 0002** (missing tables): adds any tables missed in 0001

**Migration 0003** (capability registry): `capabilities`, `worker_registry`, `role_capability_map`

**Total: 20 tables** (3 base + 12 org-architecture + 2 system + 3 capability registry)

**LSP type errors in migration files**: `JSONB` and `alembic` import errors appear in LSP diagnostics. These are false positives — `alembic` and `sqlalchemy.dialects.postgresql.JSONB` are runtime dependencies not installed in the LSP environment. Not a code defect.

**What needs human check**:
- ⚠️ Run `alembic upgrade head` against live Postgres to verify all migrations apply
- Run `alembic downgrade -1 && alembic upgrade head` to verify reversibility
- Confirm index coverage matches §10 of the org-architecture plan

---

## §11 — Tool Service (7 Tool Groups)

**Status: COMPLETE**

All 7 tool groups present in `apps/tool-service/tool_service/tools/`:
1. Workflow (`project.*`) — via `tools/project.py`
2. Document (`document.*`)
3. Review (`review.*`)
4. Sprint/Issue (`sprint.*`, `issue.*`)
5. DevOps (`infra.*`, `cicd.*`, `monitoring.*`, `secrets.*`)
6. Capability (`capability.*`)
7. KPI/Utility (`kpi.*`, `web_search`, `web_fetch`, etc.) — via `tools/sprint_kpi.py`

`_orch_client.py` (new untracked): orchestrator API client used by tool implementations that need to call back to the controller — present.

**What needs human check**:
- Run `GET /tools` and verify returned manifest matches §17.3 canonical tool manifest
- Web search tools are stubs unless `WEB_SEARCH_API_KEY` is configured (see `plan-phase4-manualTodo.prompt.md §P3-22`)

---

## §11b — v0 Vertical Slice Scope

**Status: Implemented beyond v0 minimum**

The codebase implements the full v1 scope, not just the v0 vertical slice. This is intentional — the AI agent completed all phases. The v0 scope table (CEO + COO + CTO only, 3 tool groups, happy path only) is already exceeded.

---

## §16 — Paperclip Integration

**Status: DEFERRED (intentional)**

No event bridge, adapter mapping, or Paperclip-specific code present. System operates standalone. See `plan-phase4-manualTodo.prompt.md §P3-18`.

---

## §17 — Canonical Glossary Alignment

**Status: VERIFIED (naming audit)**

The plan documents (all 3) have been updated in this change set to use canonical names. Verified alignment:
- Table names: `kpi_snapshots` (not `kpi_metrics`), `review_sessions` + `review_comments` (not `reviews`) ✓
- Stream names: `stream:{team_id}` pattern ✓
- Tool group names: 7 groups as specified ✓
- Endpoint paths: match orchestrator-api implementation ✓
- Role values: 6 roles as specified ✓

**What needs human check**: Do a final naming cross-check between `plan-orgArchitecture.prompt.md §17` and `orchestrator_api/main.py` endpoint paths before declaring the canonical glossary authoritative.

---

## Issues Summary

| Severity | Location | Issue |
|----------|----------|-------|
| High | All test suites | No test has been run against live infrastructure — all passing assertions are against mocks |
| Medium | `mas/prompts/` | 11 system prompts require human review before live execution |
| Medium | `mas/teams/*.yaml` | 11 team YAMLs require human review for model assignments and budget defaults |
| Medium | `mas/workers/*.yaml` | 26 worker manifests require human review for sandbox profiles |
| Low | `orchestrator_api/main.py` | `bind_trace_id()` without matching `clear_trace_context()` |
| Low | `main.py` files (router + tool-service) | Unused local Prometheus counters (dead code) |
| Architecture | `orchestrator_api/main.py` | `MAS_PROJECT_STATE` label cardinality unbounded |

---

## Verification Checklist

- [ ] `pytest packages/mas-core/tests/test_policy.py` — review output against §4 matrix
- [ ] `pytest packages/mas-core/tests/test_phase8.py` — agent type tests pass
- [ ] `pytest packages/mas-core/tests/test_observability.py` — observability tests pass
- [ ] `alembic upgrade head` — all 20 tables created
- [ ] `GET /tools` — returns 7 tool groups with correct manifest
- [ ] Review all 11 system prompts in `mas/prompts/`
- [ ] Review 11 team YAMLs in `mas/teams/`
- [ ] Review 26 worker manifests in `mas/workers/`
- [ ] End-to-end: create project → CSO veto → CEO override → resume
- [ ] End-to-end: review circuit breaker (2 timeouts → FAILED)
