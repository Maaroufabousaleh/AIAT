import { NextResponse } from "next/server";
import { promQuery, promQueryRange } from "@/lib/prometheus";

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const query = searchParams.get("query");
  const type = searchParams.get("type") ?? "instant"; // "instant" | "range"
  const start = searchParams.get("start");
  const end = searchParams.get("end");
  const step = searchParams.get("step") ?? "60";

  if (!query) {
    return NextResponse.json({ error: "query parameter required" }, { status: 400 });
  }

  try {
    if (type === "range" && start && end) {
      const results = await promQueryRange(query, Number(start), Number(end), Number(step));
      return NextResponse.json({ results });
    } else {
      const results = await promQuery(query);
      return NextResponse.json({ results });
    }
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
