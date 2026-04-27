import { NextRequest, NextResponse } from "next/server";
import { orchestratorFetch } from "@/lib/orchestrator";

export async function GET(req: NextRequest) {
  try {
    const { searchParams } = new URL(req.url);
    const team_id = searchParams.get("team_id") ?? undefined;
    const status = searchParams.get("status") ?? undefined;

    let path = "/capabilities/workers";
    const params: string[] = [];
    if (team_id) params.push(`team_id=${encodeURIComponent(team_id)}`);
    if (status) params.push(`status=${encodeURIComponent(status)}`);
    if (params.length > 0) path += `?${params.join("&")}`;

    const workers = await orchestratorFetch(path);
    return NextResponse.json(workers);
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const worker = await orchestratorFetch("/capabilities/workers", {
      method: "POST",
      body: JSON.stringify(body),
    });
    return NextResponse.json(worker, { status: 201 });
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}
