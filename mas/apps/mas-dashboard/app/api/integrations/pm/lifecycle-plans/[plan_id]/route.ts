import { NextRequest, NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

async function forward(path: string, init?: RequestInit) {
  try {
    return NextResponse.json(await orchestratorFetch(path, init));
  } catch (error) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ detail: error.message }, { status: error.status });
    }
    return NextResponse.json({ detail: "orchestrator unavailable" }, { status: 503 });
  }
}

export async function GET(
  _request: NextRequest,
  context: { params: Promise<{ plan_id: string }> },
) {
  const { plan_id } = await context.params;
  return forward(`/integrations/lifecycle-plans/${encodeURIComponent(plan_id)}`);
}
