import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

type Params = { params: Promise<{ id: string }> };

export async function GET(req: Request, props: Params) {
  const params = await props.params;
  try {
    const url = new URL(req.url);
    const limit = url.searchParams.get("limit") ?? "100";
    const data = await orchestratorFetch(
      `/projects/${params.id}/audit-timeline?limit=${encodeURIComponent(limit)}`,
    );
    return NextResponse.json(data);
  } catch (e) {
    if (e instanceof OrchestratorError)
      return NextResponse.json({ error: e.message }, { status: e.status });
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}
