import { NextRequest, NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ plan_id: string }> },
) {
  const { plan_id } = await context.params;
  try {
    const body = await request.text();
    return NextResponse.json(
      await orchestratorFetch(`/integrations/lifecycle-plans/${encodeURIComponent(plan_id)}/reject`, {
        method: "POST",
        body,
      }),
    );
  } catch (error) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ detail: error.message }, { status: error.status });
    }
    return NextResponse.json({ detail: "orchestrator unavailable" }, { status: 503 });
  }
}
