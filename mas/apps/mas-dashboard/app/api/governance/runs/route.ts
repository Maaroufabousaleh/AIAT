import { NextRequest, NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

export async function GET(request: NextRequest) {
  const projectId = request.nextUrl.searchParams.get("project_id");
  const rawLimit = request.nextUrl.searchParams.get("limit") ?? "50";
  const limit = Number(rawLimit);

  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100) {
    return NextResponse.json({ error: "limit must be an integer from 1 to 100" }, { status: 400 });
  }
  if (
    projectId !== null &&
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(projectId)
  ) {
    return NextResponse.json({ error: "project_id must be a UUID" }, { status: 400 });
  }

  try {
    const query = new URLSearchParams({ limit: String(limit) });
    if (projectId !== null) query.set("project_id", projectId);
    return NextResponse.json(await orchestratorFetch(`/workers/runs?${query.toString()}`));
  } catch (error: unknown) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    return NextResponse.json({ error: error instanceof Error ? error.message : String(error) }, { status: 500 });
  }
}
