import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

const ROLES = new Set(["cfo", "cto", "ceo"]);

type Params = { params: Promise<{ role: string }> };

export async function GET(request: Request, props: Params) {
  const { role } = await props.params;
  if (!ROLES.has(role)) {
    return NextResponse.json({ error: "Unknown executive role" }, { status: 404 });
  }
  const companyId = new URL(request.url).searchParams.get("company_id");
  const query = companyId ? `?company_id=${encodeURIComponent(companyId)}` : "";
  try {
    return NextResponse.json(await orchestratorFetch(`/executive/views/${encodeURIComponent(role)}${query}`));
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
