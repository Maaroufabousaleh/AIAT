import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

const MAX_PAGE_SIZE = 100;
const MAX_CURSOR_LENGTH = 512;

type ProjectDecisionPage = {
  items: Array<{
    id: string;
    project_id: string;
    project_name: string;
    project_state: string;
    company_id: string;
    gate_type: string;
    status: string;
    created_at: string;
  }>;
  next_cursor: string | null;
};

export async function GET(request: Request) {
  const searchParams = new URL(request.url).searchParams;
  const allowedKeys = new Set(["limit", "cursor"]);
  if ([...searchParams.keys()].some((key) => !allowedKeys.has(key))) {
    return NextResponse.json({ error: "Unsupported approval inbox query parameter" }, { status: 400 });
  }
  if ([...searchParams.keys()].some((key) => searchParams.getAll(key).length !== 1)) {
    return NextResponse.json({ error: "Duplicate approval inbox query parameter" }, { status: 400 });
  }

  const rawLimit = searchParams.get("limit") ?? "50";
  if (!/^\d{1,3}$/.test(rawLimit)) {
    return NextResponse.json({ error: "limit must be an integer from 1 to 100" }, { status: 400 });
  }
  const limit = Number(rawLimit);
  if (!Number.isInteger(limit) || limit < 1 || limit > MAX_PAGE_SIZE) {
    return NextResponse.json({ error: "limit must be an integer from 1 to 100" }, { status: 400 });
  }

  const cursor = searchParams.get("cursor");
  if (cursor !== null && (!cursor || cursor.length > MAX_CURSOR_LENGTH)) {
    return NextResponse.json({ error: "cursor must contain at most 512 characters" }, { status: 400 });
  }

  const query = new URLSearchParams({ limit: String(limit) });
  if (cursor !== null) query.set("cursor", cursor);

  try {
    const page = await orchestratorFetch<ProjectDecisionPage>(
      `/approvals/project-decisions?${query.toString()}`,
    );
    if (!page || !Array.isArray(page.items) || page.items.length > limit
      || !(page.next_cursor === null || typeof page.next_cursor === "string")
      || (typeof page.next_cursor === "string" && page.next_cursor.length > MAX_CURSOR_LENGTH)) {
      return NextResponse.json({ error: "Invalid project decision page" }, { status: 502 });
    }
    return NextResponse.json(page);
  } catch (error: unknown) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}
