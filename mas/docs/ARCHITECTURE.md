# AIAT Database Architecture

> **Postgres-first, project-scoped knowledge and workflow model**

---

## Design Principles

1. **Postgres is the canonical source of truth** for all structured state
2. **MinIO stores heavy file bodies** (artifacts, diagrams, large documents)
3. **Hybrid retrieval** provides semantic access to unstructured context
4. **WorkflowController** is the application/business authority for project
   lifecycle transitions; host placement, run claiming, and host-loss recovery
   retain their own narrowly scoped persistence primitives

## Verified Runtime Planes and Ownership

The current AIAT runtime is intentionally split into two legitimate planes.
This is a working-tree architecture fact based on `HEAD`
`0f068476db908ee43bbc52900bf421394a121e6c`, not a proposal to force every
agent through one interface.

### Governance/company-agent plane

```text
TeamRunner
    -> AgentBase-derived Admin/Executive/CSuite/Worker/SubAgent instances
    -> router, direct LLM gateway, checkpoints, tools, and shared storage
```

`TeamRunner` constructs and starts `AgentBase` descendants directly. This plane
handles governance/company-agent messages and does not currently create every
operation as a `WorkerRunRequest` routed through `WorkerRunController` and
`WorkerAdapter`. It must remain a distinct plane unless a later benchmark and
architecture decision proves that a narrower governance shell is better.

### Specialist-worker plane

```text
HostScheduler
    -> host reservations / run-host binding
    -> HostExecutor + storage claim primitive
    -> WorkerRunController
    -> WorkerAdapter
    -> runtime or sandbox
```

Ownership is deliberately split:

| Boundary | Current authority |
| --- | --- |
| Placement decision | `HostScheduler` |
| Capacity reservation and host/run binding | `HostReservations` and `RunHostBinding` |
| `QUEUED -> CLAIMED` acquisition | `HostExecutor` plus the storage claim primitive |
| Expired host fencing and reservation expiry | `HostLeaseRecovery` |
| Expired worker-run requeue and host-binding reassignment | worker-run recovery in `AgentStorage` and the run-host binding recovery path |
| Post-claim validation/readiness/dispatch/running/pause/resume/tool mediation/result settlement | `WorkerRunController` |
| Runtime execution facts and normalized events | `WorkerAdapter` and its subordinate runtime |

`WorkerAdapter` is the canonical specialist-runtime SPI, but it is not the
only AIAT runtime plane. Adapters do not write canonical project or worker-run
business state. `WorkflowController` remains the business authority for the
project FSM, while `WorkerRunController` owns the specialist post-claim
execution lifecycle.

The governance plane has an explicit model-provenance bridge. A message may
carry an optional `model_resolution_snapshot_id`; `TeamRunner` reads that
immutable decision through its scoped control-plane storage boundary and passes
a per-dispatch `GovernanceModelBinding` to `AgentBase`. When present, the bound
call uses the exact model, does not invoke unrecorded automatic fallback,
records safe provenance, and rejects a response-model mismatch. When the
control-plane storage boundary is active, TeamRunner rejects a model-bearing
message that lacks a snapshot rather than falling back to the process/configured
model. Fresh orchestrator `TASK`/`ADMIN_TASK`/`DIRECTIVE`/`QUERY` producer paths
create and attach a persisted snapshot before publication. Explicit direct
AgentBase compatibility fixtures remain unbound and are recorded as unresolved;
provider-backed evidence and restart characterization remain open. Bound
project-scoped outbound messages carry the same snapshot and reject conflicting
or cross-project bindings. This does not merge the governance plane into
`WorkerAdapter`.

The adapter contract also has two deliberately non-authoritative recovery
hooks. `reconcile()` reports subordinate runtime facts and may return
`UNKNOWN`; it does not mutate the canonical worker run. `cancel()` may return a
`WorkerCancellationReceipt`, where request acceptance is distinct from
confirmed runtime termination. Adapters that still return `None` remain
compatible during the transition. The base adapter's acceptance, task, and
terminal maps are process-local, so a fresh process cannot infer the old
runtime's status without an adapter-specific durable lookup.

### Shared governed services

Both planes use AIAT-owned Postgres state, Redis Streams, model governance and
the LLM gateway, tool-service and privileged-operation policy, identity and
credential boundaries, project-scoped memory/artifacts, evidence/audit, and
the operator dashboard. External runtimes may report facts or request tools;
they do not become authorities for company state, project state, approvals,
budgets, model policy, credentials, or canonical evidence.

### ProcessAdapter environment boundary

`ProcessAdapter` now uses a closed-world child environment. An omitted or
empty `environment` mapping produces an explicitly empty environment; it is
never replaced with `os.environ`. Callers must provide required non-secret
values explicitly, and the adapter copies and validates that mapping before
launch. `AdapterContext.secrets` is a compatibility channel for
runtime-specific bootstrap credentials and is never copied into a process
child; its values are also hidden from the context representation. New
integrations should prefer opaque `secret_refs` resolved through an AIAT
governed service boundary.

This boundary does not make a process trusted: a process still has the host
user's filesystem and network reach unless it is launched through a certified
OCI/gVisor `sandboxed` or Kata `vm_isolated` profile. Direct Firecracker is
retained only as a compatibility/host-VMM path, not a fourth AIAT policy tier.
Known explicit secret values are redacted from normalized process output and
diagnostics.

The separate legacy/parallel adapter family under
`mas/packages/mas-core/mas_core/worker_registry/` is inventoried in the
[canonical AIAT OSS architecture and implementation plan](AIAT_OSS_ARCHITECTURE_AND_IMPLEMENTATION_PLAN.md).
It remains unchanged until caller, import, manifest, and dynamic-reference
analysis justifies a later consolidation.

---

## Core Tables

| Table | Purpose |
|-------|---------|
| `projects` | Workflow state machine records |
| `project_state_history` | Immutable audit trail of state transitions |
| `documents` | Versioned document metadata (body in MinIO) |
| `review_sessions` / `review_comments` | Parallel review fan-out tracking |
| `approval_gates` | Human decision gates + override audit |
| `sprints` / `issues` | Sprint planning and issue tracking |
| `kpi_snapshots` | Per-sprint KPI metrics |
| `agent_profiles` | Per-agent estimation correction factors |
| `agent_checkpoints` | Mid-task LLM conversation checkpoints (resumable) |
| `dead_letters` | DLQ: messages that exhausted delivery retries |
| `system_config` | System lifecycle state + working-hours schedule |
| `project_state_history` | State transition audit log |

---

## Project Context Layer

Each project has its own persistent knowledge space:

| Table | Purpose |
|-------|---------|
| `project_context_items` | Files, URLs, text content |
| `project_context_chunks` | Chunked content with embeddings |
| `project_context_tags` | Tagging taxonomy |
| `project_context_relations` | Item relationships |

---

## Storage Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     orchestrator-api                         │
│            (AIAT control plane + scoped state owners)         │
├─────────────────────────────────────────────────────────────┤
│                         AgentStorage                         │
│              (SQLAlchemy Core + asyncpg)                     │
├──────────────────────┬──────────────────────────────────────┤
│     PostgreSQL       │              MinIO                   │
│  (canonical state)  │         (blob storage)               │
│  - projects         │   - document bodies                  │
│  - documents        │   - generated artifacts              │
│  - workflows        │   - large content                    │
│  - context metadata │   - diagrams                         │
│  - vectors (pgvector)                                       │
└──────────────────────┴──────────────────────────────────────┘
```

---

## Hybrid Retrieval

Project context is queried using a **hybrid approach**:

1. **Filter by `project_id`** - always, for isolation
2. **Keyword search** - ILIKE on `content_text`
3. **Metadata filters** - tags, source_type, date_range
4. **Semantic search** - pgvector cosine similarity (when embeddings exist)
5. **Hybrid scoring** - combine keyword + semantic ranks

### Usage

```python
# Keyword only (fast, no embeddings needed)
results = await storage.search_context_chunks_keyword(
    project_id=project_id,
    query="architecture design",
)

# Semantic (requires embeddings)
results = await storage.search_context_chunks_semantic(
    project_id=project_id,
    query_vector=[0.1, 0.3, ...],
)

# Hybrid (combines both)
results = await storage.search_context_hybrid(
    project_id=project_id,
    query="architecture design",
    query_vector=[0.1, 0.3, ...],
    filters={"tag_ids": ["...", "..."]},
)
```

---

## Chunking Strategies

Three chunking strategies are supported:

| Strategy | Description | Use Case |
|----------|-------------|----------|
| `fixed_size` | Fixed character chunks (default 1000) | Simple text, logs |
| `sliding_window` | Overlapping chunks with step size | Preserving context |
| `semantic` | (Future) Sentence-aware chunking | Code, documents |

### API Usage

```python
req = CreateContextItemRequest(
    item_type="TEXT",
    name="Architecture Spec",
    content_text="...",
    chunking_strategy="sliding_window",
    chunk_size=2000,
    chunk_overlap=400,
)
```

---

## Migration Path

1. **Install pgvector** - Add extension to PostgreSQL
2. **Run migration** - `0008_pgvector_support.py`
3. **Rebuild indexes** - After populating embeddings

---

## Safety Properties

- **Project isolation**: All queries filtered by `project_id`
- **Controller authority**: `WorkflowController` authorizes project lifecycle
  transitions; placement, claim, and host-recovery components own their stated
  narrow persistence boundaries
- **Checkpoint resume**: Agent state persisted for shutdown-safe resume
- **DLQ handling**: Failed messages stored in `dead_letters` for inspection
- **PM ACTIVE readiness**: Binding-wide inbound mutation requires both sides of
  the connection/binding lifecycle to be ACTIVE and is governed by a persisted
  digest-bound lifecycle plan; see [PM_ACTIVE_READINESS.md](PM_ACTIVE_READINESS.md)
