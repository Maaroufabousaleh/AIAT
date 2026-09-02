import { NextResponse } from "next/server";

import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

type Params = { params: Promise<{ id: string }> };

export async function GET(_: Request, props: Params) {
  const params = await props.params;
  try {
    return NextResponse.json(
      await orchestratorFetch(`/projects/${params.id}/evidence/package`),
    );
  } catch (error) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}
