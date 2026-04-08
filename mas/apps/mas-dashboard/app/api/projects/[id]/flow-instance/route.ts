import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

export async function GET(
  req: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  try {
    const data = await orchestratorFetch(`/projects/${id}/flow-instance`);
    return NextResponse.json(data);
  } catch (e) {
    if (e instanceof OrchestratorError) {
      return NextResponse.json({ error: e.message }, { status: e.status });
    }
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}

export async function POST(
  req: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  try {
    const body = await req.json();

    if (!body.flow_id || typeof body.flow_id !== "string") {
      return NextResponse.json({ error: "flow_id is required and must be a string" }, { status: 400 });
    }

    const data = await orchestratorFetch("/flows/instances", {
      method: "POST",
      body: JSON.stringify({
        flow_id: body.flow_id,
        project_id: id,
        task_id: body.task_id,
        department_id: body.department_id,
      }),
    });
    return NextResponse.json(data);
  } catch (e) {
    if (e instanceof OrchestratorError) {
      return NextResponse.json({ error: e.message }, { status: e.status });
    }
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}
