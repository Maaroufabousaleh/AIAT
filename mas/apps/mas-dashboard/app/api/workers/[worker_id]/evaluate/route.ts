import { NextRequest, NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

export async function POST(
  req: NextRequest,
  { params }: { params: { worker_id: string } }
) {
  try {
    const body = await req.json().catch(() => ({}));
    const result = await orchestratorFetch(
      `/capabilities/workers/${params.worker_id}/evaluate`,
      {
        method: "POST",
        body: JSON.stringify(body),
      }
    );
    return NextResponse.json(result);
  } catch (e: unknown) {
    if (e instanceof OrchestratorError) {
      return NextResponse.json({ error: e.message }, { status: e.status });
    }
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}
