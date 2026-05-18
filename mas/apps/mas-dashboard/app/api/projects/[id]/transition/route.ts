import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

type Params = { params: { id: string } };

export async function GET(_: Request, { params }: Params) {
  try {
    const data = await orchestratorFetch<{ allowed_events?: string[] }>(
      `/projects/${params.id}/allowed-transitions`
    );
    return NextResponse.json(data.allowed_events ?? []);
  } catch (e) {
    if (e instanceof OrchestratorError) return NextResponse.json({ error: e.message }, { status: e.status });
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}

export async function POST(req: Request, { params }: Params) {
  try {
    const body = await req.json();
    const data = await orchestratorFetch(`/projects/${params.id}/transition`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    return NextResponse.json(data);
  } catch (e) {
    if (e instanceof OrchestratorError) return NextResponse.json({ error: e.message }, { status: e.status });
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}
