import { NextRequest, NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

export async function GET(
  _request: NextRequest,
  context: { params: Promise<{ project_id: string }> },
) {
  const { project_id } = await context.params;
  try {
    const bindings = await orchestratorFetch(
      `/projects/${encodeURIComponent(project_id)}/pm-bindings`,
    );
    return NextResponse.json(bindings);
  } catch (error) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ detail: error.message }, { status: error.status });
    }
    return NextResponse.json({ detail: "orchestrator unavailable" }, { status: 503 });
  }
}
