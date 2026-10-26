import { NextRequest, NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

export async function GET(
  _request: NextRequest,
  context: { params: Promise<{ plan_id: string }> },
) {
  const { plan_id } = await context.params;
  try {
    return NextResponse.json(
      await orchestratorFetch(`/integrations/inbound-canaries/${encodeURIComponent(plan_id)}`),
    );
  } catch (error) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ detail: error.message }, { status: error.status });
    }
    return NextResponse.json({ detail: "orchestrator unavailable" }, { status: 503 });
  }
}
