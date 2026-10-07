import { NextResponse } from "next/server";
import type { CanonicalIssueUpdateRequest } from "@/lib/generated/orchestrator-api";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

type Params = { params: Promise<{ id: string; issue_id: string }> };

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const UPDATE_FIELDS = new Set([
  "title", "description", "status", "priority", "assigned_team", "assigned_agent",
  "estimated_hours", "actual_hours", "story_points", "expected_revision",
]);

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function validOptionalText(value: unknown, max: number, min = 0): boolean {
  return value === null || (typeof value === "string" && value.length <= max && value.trim().length >= min);
}

function validateUpdateBody(value: unknown): value is CanonicalIssueUpdateRequest {
  if (!isObject(value) || Object.keys(value).length === 0 || !("expected_revision" in value)
    || Object.keys(value).some((key) => !UPDATE_FIELDS.has(key))) return false;
  if ("title" in value && !validOptionalText(value.title, 500, 1)) return false;
  if ("description" in value && !validOptionalText(value.description, 100_000)) return false;
  if ("status" in value && !validOptionalText(value.status, 64, 1)) return false;
  if ("priority" in value && !validOptionalText(value.priority, 64, 1)) return false;
  if ("assigned_team" in value && !validOptionalText(value.assigned_team, 200)) return false;
  if ("assigned_agent" in value && !validOptionalText(value.assigned_agent, 200)) return false;
  for (const field of ["estimated_hours", "actual_hours"] as const) {
    if (field in value && value[field] !== null && (typeof value[field] !== "number" || !Number.isFinite(value[field]) || (value[field] as number) < 0)) return false;
  }
  if ("story_points" in value && value.story_points !== null && (!Number.isSafeInteger(value.story_points) || (value.story_points as number) < 0)) return false;
  if (!Number.isSafeInteger(value.expected_revision) || (value.expected_revision as number) < 1) return false;
  return true;
}

export async function GET(_: Request, props: Params) {
  const { id, issue_id: issueId } = await props.params;
  if (!UUID_PATTERN.test(id) || !UUID_PATTERN.test(issueId)) {
    return NextResponse.json({ error: "project and issue ids must be UUIDs" }, { status: 400 });
  }
  try {
    const data = await orchestratorFetch(`/projects/${id}/issues/${issueId}`);
    return NextResponse.json(data);
  } catch (error: unknown) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}

export async function PATCH(request: Request, props: Params) {
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
  if (!validateUpdateBody(body)) {
    return NextResponse.json({ error: "issue fields do not match the supported update request" }, { status: 400 });
  }
  try {
    const data = await orchestratorFetch(`/projects/${id}/issues/${issueId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
    return NextResponse.json(data);
  } catch (error: unknown) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}
