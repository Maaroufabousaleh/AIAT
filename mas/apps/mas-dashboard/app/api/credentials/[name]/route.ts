import { NextRequest, NextResponse } from "next/server";

const ORCHESTRATOR = process.env.ORCHESTRATOR_URL ?? "http://orchestrator-api:8000";

type Params = { params: { name: string } };

export async function GET(_req: NextRequest, { params }: Params) {
  try {
    const res = await fetch(`${ORCHESTRATOR}/credentials/${params.name}`, { cache: "no-store" });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ error: "Failed to reach orchestrator" }, { status: 502 });
  }
}

export async function PATCH(req: NextRequest, { params }: Params) {
  try {
    const body = await req.json();
    const res = await fetch(`${ORCHESTRATOR}/credentials/${params.name}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ error: "Failed to reach orchestrator" }, { status: 502 });
  }
}

export async function DELETE(_req: NextRequest, { params }: Params) {
  try {
    const res = await fetch(`${ORCHESTRATOR}/credentials/${params.name}`, { method: "DELETE" });
    if (res.status === 204) return new NextResponse(null, { status: 204 });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ error: "Failed to reach orchestrator" }, { status: 502 });
  }
}
