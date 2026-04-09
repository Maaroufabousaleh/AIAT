import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const isActive = searchParams.get("is_active");
  const limit = searchParams.get("limit") || "100";
  const offset = searchParams.get("offset") || "0";

  try {
    const path = `/flows?${new URLSearchParams({
      ...(isActive !== null && { is_active: isActive }),
      limit,
      offset,
    })}`;
    const data = await orchestratorFetch(path);
    return NextResponse.json(data);
  } catch (e) {
    if (e instanceof OrchestratorError) {
      return NextResponse.json({ error: e.message }, { status: e.status });
    }
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}

export async function POST(req: Request) {
  try {
    const body = await req.json();
    const data = await orchestratorFetch("/flows", {
      method: "POST",
      body: JSON.stringify(body),
    });
    return NextResponse.json(data, { status: 201 });
  } catch (e) {
    if (e instanceof OrchestratorError) {
      return NextResponse.json({ error: e.message }, { status: e.status });
    }
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}
