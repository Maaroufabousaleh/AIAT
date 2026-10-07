import { NextResponse } from "next/server";
import type { CanonicalIssueCommentRequest } from "@/lib/generated/orchestrator-api";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

type Params = { params: Promise<{ id: string; issue_id: string }> };

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function isCommentBody(value: unknown): value is Pick<CanonicalIssueCommentRequest, "body"> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const body = value as Record<string, unknown>;
  return Object.keys(body).length === 1 && typeof body.body === "string" && body.body.trim().length > 0 && body.body.length <= 100_000;
}

export async function POST(request: Request, props: Params) {
  const { id, issue_id: issueId } = await props.params;
  if (!UUID_PATTERN.test(id) || !UUID_PATTERN.test(issueId)) {
    return NextResponse.json({ error: "project and issue ids must be UUIDs" }, { status: 400 });
  }
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "request body must be valid JSON" }, { status: 400 });
  }
  if (!isCommentBody(body)) {
    return NextResponse.json({ error: "comment body is required and must be at most 100000 characters" }, { status: 400 });
  }
  try {
    // Do not accept actor_id from the browser. The dashboard BFF supplies its
    // fixed operator attribution to the canonical issue audit record.
    const data = await orchestratorFetch(`/projects/${id}/issues/${issueId}/comments`, {
      method: "POST",
      body: JSON.stringify({ body: body.body.trim(), actor_id: "dashboard-operator" }),
    });
    return NextResponse.json(data, { status: 201 });
  } catch (error: unknown) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}
