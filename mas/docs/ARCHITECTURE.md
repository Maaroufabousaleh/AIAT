# AIAT Database Architecture

> **Postgres-first, project-scoped knowledge and workflow model**

---

## Design Principles

1. **Postgres is the canonical source of truth** for all structured state
2. **MinIO stores heavy file bodies** (artifacts, diagrams, large documents)
3. **Hybrid retrieval** provides semantic access to unstructured context
4. **Workflow controller** is the sole writer of lifecycle state

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
│              (WorkflowController - sole writer)              │
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
- **Controller authority**: Only `WorkflowController` writes workflow state
- **Checkpoint resume**: Agent state persisted for shutdown-safe resume
- **DLQ handling**: Failed messages stored in `dead_letters` for inspection

