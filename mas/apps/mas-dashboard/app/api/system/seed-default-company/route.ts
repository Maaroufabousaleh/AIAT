import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

export async function POST() {
  try {
    const result = await orchestratorFetch("/system/seed-default-company", {
      method: "POST",
    });
    return NextResponse.json(result);
  } catch (e: unknown) {
    if (e instanceof OrchestratorError) {
      return NextResponse.json({ error: e.message }, { status: e.status });
    }
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}
