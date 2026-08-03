import { NextRequest, NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

export async function GET(request: NextRequest) {
  const connectionId = request.nextUrl.searchParams.get("connection_id");
  const suffix = connectionId ? `?connection_id=${encodeURIComponent(connectionId)}` : "";
  try {
    const [connections, conflicts, outbox, runs, lifecyclePlans] = await Promise.all([
      orchestratorFetch("/integrations/connections"),
      orchestratorFetch(`/integrations/conflicts${suffix}`),
      orchestratorFetch(`/integrations/outbox${suffix}`),
      orchestratorFetch(`/integrations/reconciliation-runs${suffix}`),
      orchestratorFetch(`/integrations/lifecycle-plans${suffix}`),
    ]);
    return NextResponse.json({ connections, conflicts, outbox, runs, lifecyclePlans });
  } catch (error) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ detail: error.message }, { status: error.status });
    }
    return NextResponse.json({ detail: "orchestrator unavailable" }, { status: 503 });
  }
}
