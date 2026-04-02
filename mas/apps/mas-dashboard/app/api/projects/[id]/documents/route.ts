import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

type Params = { params: { id: string } };

export async function GET(_: Request, { params }: Params) {
  try {
    const [documents, feasibility, sprints] = await Promise.allSettled([
      orchestratorFetch(`/projects/${params.id}/documents`),
      orchestratorFetch(`/projects/${params.id}/feasibility`),
      orchestratorFetch(`/projects/${params.id}/sprints`),
    ]);
    return NextResponse.json({
      documents: documents.status === "fulfilled" ? documents.value : null,
      feasibility: feasibility.status === "fulfilled" ? feasibility.value : null,
      sprints: sprints.status === "fulfilled" ? sprints.value : null,
    });
  } catch (e) {
    if (e instanceof OrchestratorError) return NextResponse.json({ error: e.message }, { status: e.status });
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}
