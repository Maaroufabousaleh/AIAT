import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

type Params = { params: { id: string } };

export async function GET(_: Request, { params }: Params) {
  try {
    const data = await orchestratorFetch(`/projects/${params.id}/state-history`);
    return NextResponse.json(data);
  } catch (e) {
    if (e instanceof OrchestratorError) return NextResponse.json({ error: e.message }, { status: e.status });
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}
