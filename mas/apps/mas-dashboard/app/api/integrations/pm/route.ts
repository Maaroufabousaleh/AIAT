import { NextRequest, NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

type SourceOk = { ok: true; name: string; value: unknown };
type SourceErr = { ok: false; name: string; status: number };
type SourceResult = SourceOk | SourceErr;

async function readSource(name: string, path: string): Promise<SourceResult> {
  try {
    return { ok: true, name, value: await orchestratorFetch(path) };
  } catch (error) {
    return {
      ok: false,
      name,
      status: error instanceof OrchestratorError ? error.status : 503,
    };
  }
}

function canaryPlansFrom(value: unknown): { plans: unknown[]; hasMore: boolean } {
  if (value && typeof value === "object" && "plans" in value && Array.isArray(value.plans)) {
    const hasMore = "has_more" in value && value.has_more === true;
    return { plans: value.plans, hasMore };
  }
  return { plans: [], hasMore: false };
}

export async function GET(request: NextRequest) {
  const connectionId = request.nextUrl.searchParams.get("connection_id");
  const suffix = connectionId ? `?connection_id=${encodeURIComponent(connectionId)}` : "";
  const canaryQuery = new URLSearchParams({ limit: "100" });
  if (connectionId) canaryQuery.set("connection_id", connectionId);
  const sources = await Promise.all([
    readSource("connections", "/integrations/connections"),
    readSource("conflicts", `/integrations/conflicts${suffix}`),
    readSource("outbox", `/integrations/outbox${suffix}`),
    readSource("reconciliation runs", `/integrations/reconciliation-runs${suffix}`),
    readSource("lifecycle plans", `/integrations/lifecycle-plans${suffix}`),
    readSource("canary plans", `/integrations/inbound-canaries?${canaryQuery.toString()}`),
  ]);
  const failed = sources.filter((source): source is SourceErr => !source.ok);
  if (failed.length > 0) {
    const names = failed.map((source) => `${source.name} (HTTP ${source.status})`).join(", ");
    return NextResponse.json(
      { detail: `aggregate integration read failed: ${names}` },
      { status: failed[0].status },
    );
  }
  const succeeded = sources.filter((source): source is SourceOk => source.ok);
  const byName = new Map(succeeded.map((source) => [source.name, source.value]));
  const canary = canaryPlansFrom(byName.get("canary plans"));
  return NextResponse.json({
    connections: byName.get("connections"),
    conflicts: byName.get("conflicts"),
    outbox: byName.get("outbox"),
    runs: byName.get("reconciliation runs"),
    lifecyclePlans: byName.get("lifecycle plans"),
    canaryPlans: canary.plans,
    canaryPlansHasMore: canary.hasMore,
  });
}
