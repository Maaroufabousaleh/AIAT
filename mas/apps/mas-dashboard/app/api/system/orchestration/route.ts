import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

export const dynamic = "force-dynamic";

interface WorkflowGraphTransition {
  from: string;
  to: string;
  event: string;
}

interface WorkflowGraphResponse {
  states?: Array<{ id: string; label: string; description: string }>;
  transitions?: WorkflowGraphTransition[];
}

interface StoredFlowNode {
  id?: string;
  type?: string;
  label?: string;
  config?: {
    team_id?: string;
    approver_user?: string;
  } | null;
}

interface StoredFlowEdge {
  source?: string;
  target?: string;
  condition?: string;
}

interface StoredFlow {
  id?: string;
  name?: string;
  description?: string;
  definition_json?: {
    nodes?: StoredFlowNode[];
    edges?: StoredFlowEdge[];
  } | null;
}

function toText(value: unknown, fallback = ""): string {
  return typeof value === "string" && value.length > 0 ? value : fallback;
}

function toOrchestrationFlow(flow: StoredFlow, index: number) {
  const rawNodes = Array.isArray(flow.definition_json?.nodes)
    ? flow.definition_json.nodes
    : [];
  const rawEdges = Array.isArray(flow.definition_json?.edges)
    ? flow.definition_json.edges
    : [];
  const id = toText(flow.id, `flow-${index}`);
  return {
    id,
    name: toText(flow.name, id),
    description: toText(flow.description),
    nodes: rawNodes.map((node, nodeIndex) => {
      const nodeId = toText(node?.id, `node-${nodeIndex}`);
      const team = toText(node?.config?.team_id);
      const approver = toText(node?.config?.approver_user);
      return {
        id: nodeId,
        type: toText(node?.type, "task"),
        label: toText(node?.label, nodeId),
        ...(team ? { team } : {}),
        ...(approver ? { approver } : {}),
      };
    }),
    edges: rawEdges.map((edge) => {
      const condition = toText(edge?.condition);
      return {
        source: toText(edge?.source),
        target: toText(edge?.target),
        ...(condition ? { condition } : {}),
      };
    }),
  };
}

export async function GET() {
  try {
    // Both payloads are backend-owned contracts asserted once here; the
    // Array.isArray/typeof checks below only guard the nullable edges.
    const [graph, flowsRaw] = await Promise.all([
      orchestratorFetch<WorkflowGraphResponse>("/workflow/graph"),
      orchestratorFetch<StoredFlow[] | { flows?: StoredFlow[] }>(
        "/flows?limit=100",
      ),
    ]);

    const states = Array.isArray(graph.states) ? graph.states : [];

    // Group the live {from, to, event} edge list by source state for the
    // orchestration view. Dedupe targets while preserving backend order.
    const transitions: Record<string, string[]> = {};
    for (const t of Array.isArray(graph.transitions) ? graph.transitions : []) {
      if (typeof t?.from !== "string" || typeof t?.to !== "string") continue;
      const targets = transitions[t.from] ?? [];
      if (!targets.includes(t.to)) targets.push(t.to);
      transitions[t.from] = targets;
    }

    // The backend returns a bare list; tolerate a {flows: [...]} wrapper.
    const storedFlows: StoredFlow[] = Array.isArray(flowsRaw)
      ? flowsRaw
      : Array.isArray(flowsRaw.flows)
        ? flowsRaw.flows
        : [];

    return NextResponse.json({
      states,
      transitions,
      flows: storedFlows.map(toOrchestrationFlow),
    });
  } catch (e) {
    const detail =
      e instanceof OrchestratorError ? e.message : "orchestrator unreachable";
    console.error("Error loading orchestration data:", detail);
    return NextResponse.json(
      { error: `Orchestration data unavailable: ${detail}`.slice(0, 500) },
      { status: 502 },
    );
  }
}
