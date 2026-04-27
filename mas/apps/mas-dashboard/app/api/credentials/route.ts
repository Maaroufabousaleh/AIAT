import { NextRequest, NextResponse } from "next/server";

const ORCHESTRATOR = process.env.ORCHESTRATOR_URL ?? "http://orchestrator-api:8000";

export async function GET() {
  try {
    const res = await fetch(`${ORCHESTRATOR}/credentials`, { cache: "no-store" });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ error: "Failed to reach orchestrator" }, { status: 502 });
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const res = await fetch(`${ORCHESTRATOR}/credentials`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ error: "Failed to reach orchestrator" }, { status: 502 });
  }
}
