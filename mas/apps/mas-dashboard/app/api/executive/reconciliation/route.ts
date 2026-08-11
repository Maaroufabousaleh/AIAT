import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

export async function GET(request: Request) {
  try {
    const companyId = new URL(request.url).searchParams.get("company_id");
    const query = companyId ? `?company_id=${encodeURIComponent(companyId)}` : "";
    return NextResponse.json(await orchestratorFetch(`/executive/reconciliation${query}`));
  } catch (error: unknown) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    return NextResponse.json(
      { error: error instanceof Error ? error.message : String(error) },
      { status: 500 },
    );
  }
}
