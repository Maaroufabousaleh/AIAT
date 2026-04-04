import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const flowId = searchParams.get("flow_id");
  const projectId = searchParams.get("project_id");
  const status = searchParams.get("status");
  const limit = searchParams.get("limit") || "100";
  const offset = searchParams.get("offset") || "0";

  try {
    const params = new URLSearchParams({ limit, offset });
    if (flowId) params.append("flow_id", flowId);
    if (projectId) params.append("project_id", projectId);
    if (status) params.append("status", status);
    
    const data = await orchestratorFetch(`/flows/instances?${params}`);
    return NextResponse.json(data);
  } catch (e) {
    if (e instanceof OrchestratorError) {
      return NextResponse.json({ error: e.message }, { status: e.status });
    }
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}

export async function POST(req: Request) {
  try {
    const body = await req.json();
    const data = await orchestratorFetch("/flows/instances", {
      method: "POST",
      body: JSON.stringify(body),
    });
    return NextResponse.json(data, { status: 201 });
  } catch (e) {
    if (e instanceof OrchestratorError) {
      return NextResponse.json({ error: e.message }, { status: e.status });
    }
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}
