import { NextRequest, NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

export async function GET(request: NextRequest) {
  try {
    const limit = request.nextUrl.searchParams.get("limit") ?? "50";
    return NextResponse.json(await orchestratorFetch(`/workers/runs?limit=${encodeURIComponent(limit)}`));
  } catch (error: unknown) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    return NextResponse.json({ error: error instanceof Error ? error.message : String(error) }, { status: 500 });
  }
}
