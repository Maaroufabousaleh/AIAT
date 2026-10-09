import { NextRequest, NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

export async function GET(request: NextRequest) {
  const connectionId = request.nextUrl.searchParams.get("connection_id");
  const suffix = connectionId ? `?connection_id=${encodeURIComponent(connectionId)}` : "";
  const canaryQuery = new URLSearchParams({ limit: "100" });
  if (connectionId) canaryQuery.set("connection_id", connectionId);
  try {
    const [connections, conflicts, outbox, runs, lifecyclePlans, canaryResult] = await Promise.all([
      orchestratorFetch("/integrations/connections"),
      orchestratorFetch(`/integrations/conflicts${suffix}`),
      orchestratorFetch(`/integrations/outbox${suffix}`),
      orchestratorFetch(`/integrations/reconciliation-runs${suffix}`),
      orchestratorFetch(`/integrations/lifecycle-plans${suffix}`),
      orchestratorFetch(`/integrations/inbound-canaries?${canaryQuery.toString()}`),
    ]);
    const canaryPlans = canaryResult && typeof canaryResult === "object" && "plans" in canaryResult
      && Array.isArray(canaryResult.plans)
      ? canaryResult.plans
      : [];
    const canaryPlansHasMore = Boolean(
      canaryResult && typeof canaryResult === "object" && "has_more" in canaryResult
      && canaryResult.has_more === true,
    );
    return NextResponse.json({ connections, conflicts, outbox, runs, lifecyclePlans, canaryPlans, canaryPlansHasMore });
  } catch (error) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ detail: error.message }, { status: error.status });
    }
    return NextResponse.json({ detail: "orchestrator unavailable" }, { status: 503 });
  }
}
