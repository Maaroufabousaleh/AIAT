import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

type Params = { params: Promise<{ id: string; item_id: string }> };

export async function GET(_: Request, props: Params) {
  const params = await props.params;
  try {
    const data = await orchestratorFetch(
      `/projects/${params.id}/context/${params.item_id}`,
    );
    return NextResponse.json(data);
  } catch (e) {
    if (e instanceof OrchestratorError)
      return NextResponse.json({ error: e.message }, { status: e.status });
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}

export async function DELETE(_: Request, props: Params) {
  const params = await props.params;
  try {
    const data = await orchestratorFetch(
      `/projects/${params.id}/context/${params.item_id}`,
      {
        method: "DELETE",
      },
    );
    return NextResponse.json(data);
  } catch (e) {
    if (e instanceof OrchestratorError)
      return NextResponse.json({ error: e.message }, { status: e.status });
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}
