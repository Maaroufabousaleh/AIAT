import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

export async function GET(
  _request: Request,
  props: { params: Promise<{ trace_id: string }> },
) {
  const { trace_id: traceId } = await props.params;
  if (!traceId) {
    return NextResponse.json({ error: "trace id is required" }, { status: 400 });
  }

  try {
    const incident = await orchestratorFetch(
      `/observability/incidents/${encodeURIComponent(traceId)}`,
    );
    return NextResponse.json(incident);
  } catch (error: unknown) {
    const status = error instanceof OrchestratorError ? error.status : 502;
    return NextResponse.json(
      { error: "trace incident is temporarily unavailable" },
      { status },
    );
  }
}
