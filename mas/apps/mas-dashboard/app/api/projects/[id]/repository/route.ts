import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

type Params = { params: Promise<{ id: string }> };

async function handleError(error: unknown) {
  if (error instanceof OrchestratorError) {
    return NextResponse.json({ error: error.message }, { status: error.status });
  }
  return NextResponse.json({ error: "Internal error" }, { status: 500 });
}

export async function GET(_: Request, props: Params) {
  const params = await props.params;
  try {
    const data = await orchestratorFetch(`/projects/${params.id}/repository`);
    return NextResponse.json(data);
  } catch (error) {
    return handleError(error);
  }
}

export async function POST(req: Request, props: Params) {
  const params = await props.params;
  try {
    const body = await req.json().catch(() => ({}));
    const data = await orchestratorFetch(`/projects/${params.id}/repository`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    return NextResponse.json(data);
  } catch (error) {
    return handleError(error);
  }
}
