import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

type Params = { params: Promise<{ id: string }> };

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function GET(request: Request, props: Params) {
  const { id } = await props.params;
  if (!UUID_PATTERN.test(id)) {
    return NextResponse.json({ error: "project id must be a UUID" }, { status: 400 });
  }

  const rawLimit = new URL(request.url).searchParams.get("limit") ?? "100";
  const limit = Number(rawLimit);
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100) {
    return NextResponse.json({ error: "limit must be an integer from 1 to 100" }, { status: 400 });
  }

  try {
    const data = await orchestratorFetch(`/projects/${id}/issues?limit=${limit}`);
    return NextResponse.json(data);
  } catch (error: unknown) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}
