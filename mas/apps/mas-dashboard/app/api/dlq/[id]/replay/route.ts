import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

type Params = { params: Promise<{ id: string }> };

export async function POST(_: Request, props: Params) {
  const params = await props.params;
  try {
    const data = await orchestratorFetch(`/dead-letters/${params.id}/replay`, {
      method: "POST",
    });
    return NextResponse.json(data);
  } catch (e) {
    if (e instanceof OrchestratorError)
      return NextResponse.json({ error: e.message }, { status: e.status });
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}
