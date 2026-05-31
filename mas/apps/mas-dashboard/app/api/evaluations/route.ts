import { NextResponse } from "next/server";
import { orchestratorFetch } from "@/lib/orchestrator";

export async function GET(req: Request) {
  const url = new URL(req.url);
  const tech = url.searchParams.get("tech");

  const paths: Record<string, string> = {
    vault: "/evaluations/vault",
    zitadel: "/evaluations/zitadel",
    temporal: "/evaluations/temporal",
    garage: "/evaluations/garage",
    firecracker: "/evaluations/firecracker",
  };

  const path = tech && paths[tech];
  if (!path) {
    return NextResponse.json({ error: "Unknown technology" }, { status: 400 });
  }

  try {
    const result = await orchestratorFetch(path);
    return NextResponse.json(result);
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}
