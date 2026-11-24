import { NextRequest, NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

export async function GET(
  _request: NextRequest,
  context: { params: Promise<{ connection_id: string }> },
) {
  const { connection_id } = await context.params;
  try {
    return NextResponse.json(
      await orchestratorFetch(`/integrations/connections/${encodeURIComponent(connection_id)}/health`),
    );
  } catch (error) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ detail: error.message }, { status: error.status });
    }
    return NextResponse.json({ detail: "orchestrator unavailable" }, { status: 503 });
  }
}
