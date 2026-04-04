import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

export async function GET(req: Request) {
  try {
    const data = await orchestratorFetch(`/flows/instances/active`);
    return NextResponse.json(data);
  } catch (e) {
    if (e instanceof OrchestratorError) {
      return NextResponse.json({ error: e.message }, { status: e.status });
    }
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}
