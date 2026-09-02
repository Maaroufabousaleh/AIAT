import { NextRequest, NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

type JsonRecord = Record<string, unknown>;

const SAFE_SCALAR_KEYS = new Set([
  "id",
  "name",
  "title",
  "kind",
  "type",
  "state",
  "status",
  "outcome",
  "error_code",
  "gate_type",
  "decision_type",
  "decision",
  "project_id",
  "company_id",
  "run_id",
  "worker_id",
  "team_id",
  "model",
  "agent_id",
  "path",
  "sha256",
  "size_bytes",
  "event_type",
  "prompt_tokens",
  "completion_tokens",
  "cost_usd",
  "duration_ms",
  "occurred_at",
  "flow_id",
  "runtime_type",
  "runtime_version",
  "provider_id",
  "model_id",
  "api_style",
  "profile_state",
  "max_context_tokens",
  "cost_per_1m_input",
  "cost_per_1m_output",
  "provider_kind",
  "capability_profile",
  "display_name",
  "tool_name",
  "tool_group",
  "schema_status",
  "risk_tier",
  "approval_policy",
  "transport",
  "cache_ttl_seconds",
  "idempotent",
  "side_effect",
  "available",
  "configured",
  "backend",
  "degraded",
  "canonical_tool_name",
  "alias",
  "deprecated_alias_of",
  "profile_id",
  "task_type",
  "revision",
  "version",
  "created_at",
  "updated_at",
  "started_at",
  "finished_at",
  "trace_id",
  "span_id",
  "generated_at",
  "item_count",
  "first_observed_at",
  "last_observed_at",
]);

const SAFE_STRING_LIMIT = 256;

const DETAIL_PATHS: Record<string, (id: string) => string> = {
  integration: () => "/integrations/connections",
  model: () => "/model-profiles/catalogue",
  tool: () => "/tools",
  project: (id) => `/projects/${encodeURIComponent(id)}`,
  flow: (id) => `/flows/${encodeURIComponent(id)}`,
  flow_instance: (id) => `/flows/instances/${encodeURIComponent(id)}`,
  worker: (id) => `/capabilities/workers/${encodeURIComponent(id)}`,
  worker_run: (id) => `/workers/runs/${encodeURIComponent(id)}`,
  credential: (id) => `/credentials/${encodeURIComponent(id)}`,
  dead_letter: (id) => `/dead-letters/${encodeURIComponent(id)}`,
  trace: (id) => `/observability/traces/${encodeURIComponent(id)}`,
  runtime: () => "/runtimes",
  artifact: (id) => `/artifacts/${encodeURIComponent(id)}`,
  usage: (id) => `/usage/events/${encodeURIComponent(id)}`,
};

function isRecord(value: unknown): value is JsonRecord {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
function projectScalars(value: unknown): JsonRecord {
  if (!isRecord(value)) return {};
  const projected: JsonRecord = {};
  for (const [key, candidate] of Object.entries(value)) {
    if (!SAFE_SCALAR_KEYS.has(key)) continue;
    if (candidate === null) {
      projected[key] = null;
    } else if (typeof candidate === "string") {
      projected[key] = candidate.slice(0, SAFE_STRING_LIMIT);
    } else if (typeof candidate === "number" && Number.isFinite(candidate)) {
      projected[key] = candidate;
    } else if (typeof candidate === "boolean") {
      projected[key] = candidate;
    }
  }
  return projected;
}

function selectRuntime(raw: unknown, id: string): unknown {
  if (!isRecord(raw)) return null;
  const runtimes = Array.isArray(raw.runtimes) ? raw.runtimes : [];
  return runtimes.find((runtime) => {
    if (!isRecord(runtime)) return false;
    return runtime.id === id || runtime.runtime_type === id || runtime.name === id;
  }) ?? null;
}

function selectListRecord(raw: unknown, id: string): unknown {
  if (!Array.isArray(raw)) return null;
  return raw.find((item) => {
    if (!isRecord(item)) return false;
    return item.id === id || item.connection_id === id;
  }) ?? null;
}

function selectModel(raw: unknown, id: string): unknown {
  if (!isRecord(raw) || !Array.isArray(raw.entries)) return null;
  return raw.entries.find((entry) => isRecord(entry) && entry.model_id === id) ?? null;
}

function selectTool(raw: unknown, id: string): unknown {
  const tools = Array.isArray(raw)
    ? raw
    : isRecord(raw) && Array.isArray(raw.tools)
      ? raw.tools
      : [];
  return tools.find((tool) => {
    if (!isRecord(tool)) return false;
    return tool.tool_name === id || tool.name === id || tool.id === id;
  }) ?? null;
}

export async function GET(
  _request: NextRequest,
  props: { params: Promise<{ kind: string; id: string }> },
) {
  const { kind, id } = await props.params;
  const pathFactory = DETAIL_PATHS[kind];
  if (!pathFactory || !id) {
    return NextResponse.json(
      { error: "bounded evidence detail is not available for this reference kind", detail_supported: false },
      { status: 404 },
    );
  }

  try {
    const raw = kind === "tool"
      ? await fetch(`${process.env.TOOL_SERVICE_URL ?? "http://localhost:8002"}/tools`, {
          headers: { Authorization: `Bearer ${process.env.TOOL_SECRET ?? ""}` },
          cache: "no-store",
        }).then(async (response) => {
          if (!response.ok) throw new Error(`tool service returned ${response.status}`);
          return response.json() as Promise<unknown>;
        })
      : await orchestratorFetch(pathFactory(id));
    const record = kind === "runtime"
      ? selectRuntime(raw, id)
      : kind === "integration"
        ? selectListRecord(raw, id)
        : kind === "model"
          ? selectModel(raw, id)
          : kind === "tool"
            ? selectTool(raw, id)
            : raw;
    if (!record) {
      return NextResponse.json({ error: "evidence record was not found" }, { status: 404 });
    }
    return NextResponse.json({
      schema_version: "aiat.evidence-detail.v1",
      kind,
      id,
      source: "control-plane",
      record: projectScalars(record),
    });
  } catch (error: unknown) {
    const status = error instanceof OrchestratorError ? error.status : 502;
    return NextResponse.json(
      { error: "evidence detail is temporarily unavailable", detail_supported: true },
      { status },
    );
  }
}
