# MAS Dashboard — Implementation Plan

**Target**: Next.js 14 web dashboard for monitoring, controlling, and diagnosing the AIAT Multi-Agent System.
**Host port**: `4000`
**Auth**: Single-user username + bcrypt-hashed password, JWT session cookie
**Deployment**: Docker container on the internal MAS network

---

## 1. Architecture Overview

```
Browser
  │  HTTPS (port 4000)
  ▼
┌─────────────────────────────────────────────────┐
│  mas-dashboard  (Next.js 14, App Router)        │
│                                                 │
│  /app/api/*  ← secure proxy routes              │
│    - holds MAS_API_KEY, ROUTER_SECRET,          │
│      TOOL_SECRET, JWT_SECRET server-side        │
│    - never serializes secrets to JSON           │
│                                                 │
│  /app/(dashboard)/*  ← React UI pages           │
│    - fetches from /api/* only                   │
│    - SSE clients for real-time feeds            │
└─────────────┬───────────────────────────────────┘
              │  Docker internal network
    ┌─────────┼──────────────┐
    ▼         ▼              ▼
orchestrator  message-router  tool-service
:8000         :8001           :8002
              │
              ▼
           prometheus
           :9090
           │
           ▼
       /var/run/docker.sock (log streaming)
```

### Key principles

- **Secure proxy only**: the browser talks exclusively to Next.js API routes. All upstream credentials stay server-side.
- **SSE for real-time**: Next.js holds a persistent WebSocket to `message-router` per subscribed stream; browsers receive Server-Sent Events (text/event-stream). This avoids requiring WSS on the dashboard port.
- **Log streaming**: Docker socket mounted read-only. A `/api/logs/stream` endpoint spawns `docker logs --follow <container>` as a child process and pipes stdout/stderr to an SSE response.
- **Auth**: JWT stored in an `httpOnly`, `sameSite=strict` cookie. A middleware file (`middleware.ts`) verifies the JWT on every request except `/login`.

---

## 2. Docker Service Spec

### Addition to `mas/infra/compose/docker-compose.yml`

```yaml
  # ── 8. MAS Dashboard ─────────────────────────────────────────────────────────
  dashboard:
    image: mas/dashboard:latest
    build:
      context: ../..
      dockerfile: infra/docker/Dockerfile.dashboard
    restart: unless-stopped
    networks:
      - internal   # reach orchestrator-api, message-router, tool-service, prometheus
      - public     # reachable from browser
    ports:
      - "4000:3000"
    depends_on:
      orchestrator-api:
        condition: service_healthy
      message-router:
        condition: service_healthy
      tool-service:
        condition: service_healthy
    environment:
      DASHBOARD_USERNAME: "${DASHBOARD_USERNAME}"
      DASHBOARD_PASSWORD_HASH: "${DASHBOARD_PASSWORD_HASH}"
      JWT_SECRET: "${JWT_SECRET}"
      MAS_API_KEY: "${MAS_API_KEY}"
      ROUTER_SECRET: "${ROUTER_SECRET}"
      ORCHESTRATOR_URL: "http://orchestrator-api:8000"
      MESSAGE_ROUTER_URL: "http://message-router:8001"
      TOOL_SERVICE_URL: "http://tool-service:8002"
      PROMETHEUS_URL: "http://prometheus:9090"
      NODE_ENV: "production"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    mem_limit: 512m
    cpus: "0.5"
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:3000/api/health"]
      interval: 15s
      timeout: 5s
      start_period: 20s
      retries: 3
```

### Prometheus service (needed in base compose, currently only in dev overlay)

The dashboard queries Prometheus directly. Add it to the base `docker-compose.yml` or confirm `docker-compose.dev.yml` is always used in development:

```yaml
  prometheus:
    image: prom/prometheus:latest
    restart: unless-stopped
    networks:
      - internal
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
    mem_limit: 256m
    cpus: "0.25"
```

---

## 3. `.env` Additions

Append to `mas/.env` (and document in `mas/.env.example`):

```dotenv
# ── Dashboard ──────────────────────────────────────────────────────────────────
DASHBOARD_USERNAME=admin
# Generate with: node -e "const b=require('bcryptjs');console.log(b.hashSync('yourpassword',12))"
DASHBOARD_PASSWORD_HASH=$2b$12$...
# Generate with: openssl rand -base64 32
JWT_SECRET=<32-char-random>
# Must match the X-API-Key header accepted by orchestrator-api
MAS_API_KEY=tIGNTR7Z06s9XEQVpDCA1jp07wCiCUJl3m0TlCBWzUs
```

---

## 4. Dockerfile

**Path**: `mas/infra/docker/Dockerfile.dashboard`

```dockerfile
FROM node:20-alpine AS deps
WORKDIR /app
COPY apps/mas-dashboard/package.json apps/mas-dashboard/package-lock.json* ./
RUN npm ci --omit=dev

FROM node:20-alpine AS builder
WORKDIR /app
COPY apps/mas-dashboard/ .
COPY --from=deps /app/node_modules ./node_modules
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
RUN addgroup --system --gid 1001 nodejs && adduser --system --uid 1001 nextjs
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
```

---

## 5. Directory Structure

```
mas/apps/mas-dashboard/
├── package.json
├── next.config.ts
├── middleware.ts                  ← JWT auth guard on all routes except /login
├── app/
│   ├── layout.tsx
│   ├── login/
│   │   └── page.tsx
│   ├── (dashboard)/               ← route group (all protected)
│   │   ├── layout.tsx             ← sidebar + nav shell
│   │   ├── page.tsx               ← home / system overview
│   │   ├── projects/
│   │   │   ├── page.tsx           ← project list
│   │   │   └── [id]/
│   │   │       └── page.tsx       ← project detail
│   │   ├── streams/
│   │   │   └── page.tsx           ← agent stream monitor
│   │   ├── ceo/
│   │   │   └── page.tsx           ← CEO live feed
│   │   ├── metrics/
│   │   │   └── page.tsx           ← Prometheus charts
│   │   ├── dlq/
│   │   │   └── page.tsx           ← dead letter queue
│   │   ├── logs/
│   │   │   └── page.tsx           ← container log viewer
│   │   ├── system/
│   │   │   └── page.tsx           ← system control panel
│   │   └── tools/
│   │       └── page.tsx           ← tool manifest + circuit breakers
│   └── api/
│       ├── health/
│       │   └── route.ts
│       ├── auth/
│       │   ├── login/route.ts
│       │   └── logout/route.ts
│       ├── projects/
│       │   ├── route.ts           ← GET list, POST create
│       │   └── [id]/
│       │       ├── route.ts       ← GET detail
│       │       ├── transition/route.ts
│       │       ├── decisions/route.ts
│       │       ├── documents/route.ts
│       │       ├── state-history/route.ts
│       │       ├── retry/route.ts
│       │       └── archive/route.ts
│       ├── dlq/
│       │   ├── route.ts           ← GET dead letters
│       │   └── [id]/
│       │       └── replay/route.ts
│       ├── system/
│       │   ├── status/route.ts
│       │   ├── shutdown/route.ts
│       │   ├── resume/route.ts
│       │   └── schedule/route.ts
│       ├── tools/
│       │   └── route.ts           ← GET tool manifest + circuit states
│       ├── metrics/
│       │   └── route.ts           ← proxy Prometheus /api/v1/query_range
│       ├── streams/
│       │   └── [team_id]/
│       │       └── route.ts       ← SSE: bridges WS to message-router → browser
│       └── logs/
│           └── stream/route.ts    ← SSE: docker logs --follow <container>
├── components/
│   ├── ui/                        ← shadcn/ui primitives
│   ├── ProjectStateTimeline.tsx
│   ├── AgentStreamFeed.tsx
│   ├── MetricChart.tsx            ← wraps Recharts + Prometheus query
│   ├── LogViewer.tsx
│   ├── DLQTable.tsx
│   ├── ToolCircuitTable.tsx
│   └── SystemStatusCard.tsx
└── lib/
    ├── auth.ts                    ← bcrypt verify, JWT sign/verify
    ├── orchestrator.ts            ← typed fetch wrapper for orchestrator-api
    ├── messageRouter.ts           ← WebSocket client (server-side)
    ├── prometheus.ts              ← Prometheus HTTP API client
    └── constants.ts               ← stream IDs, workflow states, message types
```

---

## 6. Page-by-Page Feature Breakdown

### 6.1 Home (`/`) — System Overview

**Data sources**:
- `GET /system/status` → orchestrator-api
- `GET /health` → message-router (known_teams, background task state)
- `GET /health` → tool-service (circuit breaker summary)
- `GET /projects` → count by state
- Prometheus instant query: `mas_dlq_depth`, `mas_llm_calls_total`, `mas_tool_calls_total`

**Components**:
- `SystemStatusCard`: running / shutdown / degraded badge
- Active projects count (grouped by state as a mini bar)
- LLM calls/min (last 5 min rate from Prometheus)
- DLQ depth gauge
- Tool circuit breakers: open count vs total

---

### 6.2 Projects List (`/projects`)

**Data sources**: `GET /projects`

**Features**:
- Table: name, state badge (color-coded by workflow phase), created_at, last transition
- Filter by state (multi-select dropdown using the 18 workflow states)
- Create project modal (`POST /projects`)
- Row click → navigates to `/projects/[id]`

---

### 6.3 Project Detail (`/projects/[id]`)

**Data sources**:
- `GET /projects/{id}`
- `GET /projects/{id}/state-history`
- `GET /projects/{id}/documents`
- `GET /projects/{id}/feasibility`
- `GET /projects/{id}/sprints`
- `GET /projects/{id}/pending-decisions`
- `GET /projects/{id}/allowed-transitions`

**Features**:
- State history timeline (vertical stepper showing all 18 states, current highlighted)
- Documents panel: feasibility report, sprint list (expandable)
- Pending decisions: approve/reject buttons → `POST /projects/{id}/decisions`
- Transition controls: dropdown of allowed transitions → `POST /projects/{id}/transition`
- Recovery actions: Retry (`POST /projects/{id}/retry`) and Archive (`POST /projects/{id}/archive`) for FAILED state
- Auto-refreshes every 10s while project is in an active state

---

### 6.4 Agent Stream Monitor (`/streams`)

**Data source**: `GET /api/streams/[team_id]` (SSE → message-router WS)

**Features**:
- Dropdown: select any of the 11 team streams:
  ```
  stream:exec_ceo, stream:exec_coo,
  stream:office_cfo, stream:office_cio, stream:office_chrm,
  stream:office_cso, stream:office_cto,
  stream:dept_production, stream:dept_system, stream:dept_qa, stream:dept_devops
  ```
- Live feed table: timestamp | message_type | sender | recipient | payload preview
- Message type badges (color-coded: DIRECTIVE=blue, REPORT=green, TOOL_CALL=orange, TOOL_RESULT=yellow, VETO=red, SHUTDOWN=gray)
- Payload inspector: click any row to expand full JSON
- Pause/resume toggle
- Auto-scroll with scroll-lock when inspecting

---

### 6.5 CEO Live Feed (`/ceo`)

**Data source**: `GET /api/streams/exec_ceo` (SSE)

**Features**:
- Dedicated full-page view for `stream:exec_ceo`
- Think loop visualizer: groups consecutive messages by project_id into "think cycles"
- Highlights:
  - TOOL_CALL → tool name + args (collapsible)
  - TOOL_RESULT → result summary + duration
  - LLM call events → model, token counts (if present in payload)
  - DIRECTIVE received → trigger event
  - REPORT sent → output summary
- Timeline view with relative timestamps ("3s ago")
- Active project badge in header

---

### 6.6 Metrics Dashboard (`/metrics`)

**Data source**: Prometheus HTTP API at `PROMETHEUS_URL` proxied via `/api/metrics`

Prometheus queries (`/api/v1/query_range`, step=60s, range=last 1h by default):

| Chart | PromQL |
|-------|--------|
| LLM calls/min | `rate(mas_llm_calls_total[5m])` |
| Tool calls by name (top 10) | `topk(10, rate(mas_tool_calls_total[5m]))` |
| Tool calls by status | `sum by (status) (rate(mas_tool_calls_total[5m]))` |
| DLQ depth by stream | `mas_dlq_depth` |
| Budget exhaustions | `increase(mas_budget_exhausted_total[1h])` |
| Messages by direction | `sum by (direction) (rate(mas_messages_total[5m]))` |
| Messages by team | `sum by (team) (rate(mas_messages_total[5m]))` |
| Active project states | `mas_project_state` |
| Agent correction factor | `mas_agent_correction_factor` |
| Tool circuit breakers open | `mas_tool_circuit_state` |

**Features**:
- Time range selector: 15m / 1h / 6h / 24h
- Charts rendered with Recharts (LineChart / BarChart)
- Auto-refresh every 30s

---

### 6.7 Dead Letter Queue (`/dlq`)

**Data sources**:
- `GET /dead-letters` → orchestrator-api

**Features**:
- Table: id, stream, message_type, failure_reason, retry_count, created_at
- Row expand: full envelope JSON with syntax highlighting
- Replay button → `POST /dead-letters/{id}/replay`
- Bulk replay (checkbox select + replay all)
- Auto-refresh every 30s

---

### 6.8 Log Viewer (`/logs`)

**Data source**: `/api/logs/stream?container=<name>` (SSE → `docker logs --follow`)

**Container list** (hardcoded from compose service names):
```
mas-message-router, mas-tool-service, mas-orchestrator-api,
mas-team-exec-ceo, mas-team-exec-coo,
mas-team-office-cfo, mas-team-office-cio, mas-team-office-chrm,
mas-team-office-cso, mas-team-office-cto,
mas-team-dept-production, mas-team-dept-system,
mas-team-dept-qa, mas-team-dept-devops
```

**Features**:
- Container selector (multi-select: view up to 4 containers simultaneously in split panes)
- Structured JSON log parser: renders `event`, `level`, `logger`, `timestamp`, `project_id`, `agent_id` as colored columns; raw fallback for non-JSON lines
- Level filter: DEBUG / INFO / WARNING / ERROR
- Search bar (client-side substring filter on displayed buffer)
- Max 2000 lines in buffer, auto-evicts oldest
- Pause/resume + clear buffer button

### SSE implementation for log streaming

```typescript
// app/api/logs/stream/route.ts
import { spawn } from "child_process";

export async function GET(req: Request) {
  const container = new URL(req.url).searchParams.get("container");
  const encoder = new TextEncoder();

  const stream = new ReadableStream({
    start(controller) {
      const proc = spawn("docker", ["logs", "--follow", "--tail", "100", container!]);
      proc.stdout.on("data", (chunk) => {
        controller.enqueue(encoder.encode(`data: ${JSON.stringify({ line: chunk.toString() })}\n\n`));
      });
      proc.stderr.on("data", (chunk) => {
        controller.enqueue(encoder.encode(`data: ${JSON.stringify({ line: chunk.toString() })}\n\n`));
      });
      proc.on("close", () => controller.close());
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "Connection": "keep-alive",
    },
  });
}
```

---

### 6.9 System Control (`/system`)

**Data sources**:
- `GET /system/status`
- `PUT /system/schedule`
- `POST /system/shutdown`
- `POST /system/resume`

**Features**:
- System status banner: RUNNING / SHUTDOWN / DEGRADED (color coded)
- Shutdown button: confirmation dialog → `POST /system/shutdown`
- Resume button: `POST /system/resume`
- Schedule config form: cron expression input → `PUT /system/schedule`
- All buttons disabled with tooltip when system is already in target state

---

### 6.10 Tool Manifest (`/tools`)

**Data sources**:
- `GET /tools` → tool-service (48 tools, 7 groups)
- `GET /health` → tool-service (circuit breaker states)
- `GET /metrics` → tool-service (Prometheus — `mas_tool_circuit_state`, `mas_tool_calls_total`)

**Features**:
- Grouped table: 7 tool groups with expandable rows
- Per-tool: name, description, circuit breaker state badge (CLOSED=green / OPEN=red / HALF_OPEN=yellow)
- Call count (last 1h from Prometheus)
- Success rate (last 1h)
- Click tool name → inspect JSON schema of input/output

---

## 7. Real-Time Stream Architecture (Server Side)

### Problem
`message-router` uses WebSocket + Bearer token auth for stream subscriptions. Browsers cannot set `Authorization` headers on native WebSocket connections.

### Solution
Next.js API route acts as a server-side WS client and re-broadcasts via SSE:

```typescript
// app/api/streams/[team_id]/route.ts
import WebSocket from "ws";

export async function GET(req: Request, { params }: { params: { team_id: string } }) {
  const { team_id } = params;
  const token = `dashboard:${process.env.ROUTER_SECRET}`;
  const wsUrl = `${process.env.MESSAGE_ROUTER_URL!.replace("http", "ws")}/ws/subscribe/${team_id}`;

  const encoder = new TextEncoder();

  const stream = new ReadableStream({
    start(controller) {
      const ws = new WebSocket(wsUrl, {
        headers: { Authorization: `Bearer ${token}` },
      });

      ws.on("message", (data) => {
        controller.enqueue(
          encoder.encode(`data: ${data.toString()}\n\n`)
        );
      });

      ws.on("error", (err) => {
        controller.enqueue(
          encoder.encode(`event: error\ndata: ${JSON.stringify({ error: err.message })}\n\n`)
        );
        controller.close();
      });

      ws.on("close", () => controller.close());

      // Clean up on client disconnect
      req.signal.addEventListener("abort", () => ws.close());
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "Connection": "keep-alive",
    },
  });
}
```

The browser uses `EventSource`:
```typescript
const source = new EventSource(`/api/streams/${teamId}`);
source.onmessage = (e) => {
  const envelope = JSON.parse(e.data);
  // render in feed
};
```

---

## 8. Auth Model

### Login flow
1. `POST /api/auth/login` receives `{ username, password }` (JSON body)
2. Compares username to `DASHBOARD_USERNAME` env var
3. Uses `bcryptjs.compare(password, DASHBOARD_PASSWORD_HASH)` to verify
4. On success: signs a JWT with `{ sub: username, iat, exp: +8h }` using `JWT_SECRET`
5. Sets cookie: `session=<jwt>; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800`
6. Returns `{ ok: true }`

### Route protection
`middleware.ts` at project root:

```typescript
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { jwtVerify } from "jose";

export async function middleware(req: NextRequest) {
  if (req.nextUrl.pathname.startsWith("/login")) return NextResponse.next();
  if (req.nextUrl.pathname.startsWith("/api/auth")) return NextResponse.next();

  const token = req.cookies.get("session")?.value;
  if (!token) return NextResponse.redirect(new URL("/login", req.url));

  try {
    await jwtVerify(token, new TextEncoder().encode(process.env.JWT_SECRET!));
    return NextResponse.next();
  } catch {
    return NextResponse.redirect(new URL("/login", req.url));
  }
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
```

### Password hash generation
```bash
node -e "const b=require('bcryptjs'); console.log(b.hashSync('your-password', 12))"
```
Paste the output into `DASHBOARD_PASSWORD_HASH` in `mas/.env`.

---

## 9. Key Dependencies (`package.json`)

```json
{
  "dependencies": {
    "next": "14.x",
    "react": "18.x",
    "react-dom": "18.x",
    "jose": "^5.0",
    "bcryptjs": "^2.4",
    "ws": "^8.0",
    "recharts": "^2.0",
    "tailwindcss": "^3.0",
    "@radix-ui/react-dialog": "^1.0",
    "@radix-ui/react-dropdown-menu": "^2.0",
    "@radix-ui/react-badge": "*",
    "clsx": "^2.0",
    "date-fns": "^3.0"
  },
  "devDependencies": {
    "@types/bcryptjs": "^2.4",
    "@types/ws": "^8.0",
    "@types/node": "^20",
    "@types/react": "^18",
    "typescript": "^5.0"
  }
}
```

---

## 10. Orchestrator API Proxy Pattern

All orchestrator calls follow this pattern in Next.js API routes:

```typescript
// lib/orchestrator.ts
const BASE = process.env.ORCHESTRATOR_URL!;
const KEY  = process.env.MAS_API_KEY!;

export async function orchestratorFetch(path: string, init?: RequestInit) {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "X-API-Key": KEY,
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Orchestrator ${res.status}: ${text}`);
  }
  return res.json();
}
```

Usage in a route handler:
```typescript
// app/api/projects/route.ts
import { orchestratorFetch } from "@/lib/orchestrator";

export async function GET() {
  const data = await orchestratorFetch("/projects");
  return Response.json(data);
}
```

---

## 11. Build Phases

### Phase 1 — Auth + Project Management (~4h)
- [ ] Scaffold Next.js app with Tailwind + shadcn/ui
- [ ] `middleware.ts` JWT auth guard
- [ ] `/login` page
- [ ] `/api/auth/login` and `/api/auth/logout`
- [ ] `lib/orchestrator.ts` proxy client
- [ ] `/projects` list page
- [ ] `/projects/[id]` detail page with state history, documents, decision buttons
- [ ] System status card on home page

**Deliverable**: Can log in, browse projects, approve/reject decisions, trigger transitions.

### Phase 2 — Real-Time Agent Streams (~3h)
- [ ] `ws` package installed for server-side WebSocket
- [ ] `/api/streams/[team_id]` SSE bridge to message-router
- [ ] `/streams` page with team selector and live feed table
- [ ] `/ceo` page with think loop grouping

**Deliverable**: Can watch any agent team's message stream live in the browser.

### Phase 3 — Prometheus Metrics Charts (~3h)
- [ ] `lib/prometheus.ts` HTTP API client
- [ ] `/api/metrics` proxy with query/query_range support
- [ ] `/metrics` page with Recharts (10 charts per table above)
- [ ] Time range selector

**Deliverable**: Live LLM call rates, tool call breakdown, DLQ depth, budget exhaustions visible in charts.

### Phase 4 — Log Viewer (~2h)
- [ ] `/api/logs/stream` SSE endpoint (docker logs child process)
- [ ] `/logs` page with multi-container split panes
- [ ] JSON log parser, level filter, search bar

**Deliverable**: Streaming structured logs from any container in the browser.

### Phase 5 — System Controls + DLQ + Tools (~2h)
- [ ] `/api/dlq` routes + `/dlq` page with replay
- [ ] `/api/system/*` routes + `/system` page with shutdown/resume/schedule
- [ ] `/api/tools` route + `/tools` page with circuit breaker table

**Deliverable**: Full operational control surface — replay dead letters, shut down the system, inspect every tool.

---

## 12. Workflow State Reference

For state badge colors in the UI:

| State | Phase | Color |
|-------|-------|-------|
| INIT | Intake | gray |
| FEASIBILITY_CHECK | Analysis | blue |
| FEASIBILITY_REPORT | Analysis | blue |
| PDR_CREATION | Design | indigo |
| PDR_REVIEW | Design | indigo |
| SECURITY_BLOCKED | Blocked | red |
| CDR_CREATION | Architecture | violet |
| CDR_REVIEW | Architecture | violet |
| HUMAN_APPROVAL | Gate | amber |
| RR_CREATION | Release | cyan |
| SPRINT_PLANNING | Execution | teal |
| INFRA_PROVISIONING | Execution | teal |
| IN_PROGRESS | Execution | green |
| RETROSPECTIVE | Wrap-up | lime |
| KPI_PERSISTENCE | Wrap-up | lime |
| COMPLETED | Terminal | emerald |
| ARCHIVED | Terminal | stone |
| FAILED | Terminal | rose |

---

## 13. Team Stream IDs Reference

```typescript
// lib/constants.ts
export const TEAM_STREAMS = [
  { id: "exec_ceo",         label: "CEO",        role: "C-Suite" },
  { id: "exec_coo",         label: "COO",        role: "C-Suite" },
  { id: "office_cfo",       label: "CFO",        role: "C-Office" },
  { id: "office_cio",       label: "CIO",        role: "C-Office" },
  { id: "office_chrm",      label: "CHRM",       role: "C-Office" },
  { id: "office_cso",       label: "CSO",        role: "C-Office" },
  { id: "office_cto",       label: "CTO",        role: "C-Office" },
  { id: "dept_production",  label: "Production", role: "Department" },
  { id: "dept_system",      label: "System",     role: "Department" },
  { id: "dept_qa",          label: "QA",         role: "Department" },
  { id: "dept_devops",      label: "DevOps",     role: "Department" },
] as const;
```

---

## 14. Security Checklist

Before deploying to any non-local environment:

- [ ] `HTTPS` enforced (TLS termination at reverse proxy, e.g. Traefik or Nginx)
- [ ] `DASHBOARD_PASSWORD_HASH` uses bcrypt cost factor ≥ 12
- [ ] `JWT_SECRET` is at least 32 bytes of random entropy
- [ ] Docker socket mount removed or replaced with a read-only log aggregator (Loki) for production
- [ ] `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD_HASH` / `JWT_SECRET` never committed to git
- [ ] CSP headers added in `next.config.ts`
- [ ] Rate limit on `/api/auth/login` (e.g. 5 attempts per 15 min per IP) — use `next-rate-limit` or a Redis counter

---

## 15. Summary

| Item | Value |
|------|-------|
| Framework | Next.js 14 (App Router, TypeScript) |
| Port | 4000 (host) → 3000 (container) |
| Auth | bcrypt + JWT cookie |
| Real-time | SSE (server bridges WS to message-router) |
| Log streaming | Docker socket + child_process + SSE |
| Metrics | Prometheus HTTP API + Recharts |
| Networks | `internal` + `public` |
| Build time | ~14h across 5 phases |
| New containers | 1 (`mas/dashboard`) |
| New `.env` vars | 4 (`DASHBOARD_USERNAME`, `DASHBOARD_PASSWORD_HASH`, `JWT_SECRET`, `MAS_API_KEY`) |
| New compose services | 1 (+ Prometheus if not already in base compose) |
