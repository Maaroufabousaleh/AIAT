import { NextRequest, NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const MAX_LIMIT = 1_000;
const MAX_OFFSET = 100_000;

type JsonRecord = Record<string, unknown>;

function record(value: unknown): JsonRecord | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as JsonRecord
    : null;
}

function safeWorkerRun(value: unknown): JsonRecord | null {
  const run = record(value);
  if (!run || typeof run.id !== "string" || !UUID_PATTERN.test(run.id)) return null;

  const output: JsonRecord = { id: run.id };
  for (const field of [
    "project_id",
    "worker_id",
    "task_type",
    "state",
    "model_resolution_snapshot_id",
    "created_at",
  ] as const) {
    const fieldValue = run[field];
    if (typeof fieldValue === "string" || fieldValue === null) {
      output[field] = fieldValue;
    }
  }
  if (typeof run.retry_of_run_id === "string" && UUID_PATTERN.test(run.retry_of_run_id)) {
    output.retry_of_run_id = run.retry_of_run_id;
  }

  // The issue ID and revision are copied only when the durable request
  // matches the exact project.issue idempotency contract used by Project
  // Detail. No task input, prompt, grants, or other request data crosses this
  // dashboard boundary.
  const request = record(run.request_json);
  const taskInput = record(request?.task_input);
  const issueId = taskInput?.issue_id;
  const projectId = run.project_id;
  const requestProjectId = request?.project_id;
  const idempotencyKey = request?.idempotency_key;
  if (
    run.task_type === "project.issue" &&
    typeof projectId === "string" && UUID_PATTERN.test(projectId) &&
    requestProjectId === projectId &&
    typeof issueId === "string" && UUID_PATTERN.test(issueId) &&
    typeof idempotencyKey === "string"
  ) {
    const prefix = `project-issue:${projectId}:${issueId}:r`;
    if (idempotencyKey.startsWith(prefix)) {
      const revisionText = idempotencyKey.slice(prefix.length);
      if (/^(0|[1-9][0-9]*)$/.test(revisionText)) {
        const issueRevision = Number(revisionText);
        if (Number.isSafeInteger(issueRevision)) {
          output.issue_id = issueId;
          output.issue_revision = issueRevision;
        }
      }
    }
  }
  if (
    output.issue_id === undefined &&
    run.task_type === "project.issue" &&
    typeof projectId === "string" && UUID_PATTERN.test(projectId) &&
    requestProjectId === projectId &&
    typeof issueId === "string" && UUID_PATTERN.test(issueId) &&
    typeof output.retry_of_run_id === "string"
  ) {
    // This bounded issue UUID is only a candidate. It becomes an association
    // after its durable retry parent is verified in this or a later page.
    output.retry_issue_id_candidate = issueId;
  }

  return output;
}

export async function GET(request: NextRequest) {
  const projectId = request.nextUrl.searchParams.get("project_id");
  const rawLimit = request.nextUrl.searchParams.get("limit") ?? "50";
  const rawOffset = request.nextUrl.searchParams.get("offset") ?? "0";
  const limit = Number(rawLimit);
  const offset = Number(rawOffset);

  if (!Number.isSafeInteger(limit) || limit < 1 || limit > MAX_LIMIT) {
    return NextResponse.json({ error: `limit must be an integer from 1 to ${MAX_LIMIT}` }, { status: 400 });
  }
  if (!Number.isSafeInteger(offset) || offset < 0 || offset > MAX_OFFSET) {
    return NextResponse.json({ error: `offset must be an integer from 0 to ${MAX_OFFSET}` }, { status: 400 });
  }
  if (
    projectId !== null &&
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(projectId)
  ) {
    return NextResponse.json({ error: "project_id must be a UUID" }, { status: 400 });
  }

  try {
    const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (projectId !== null) query.set("project_id", projectId);
    const payload: unknown = await orchestratorFetch(`/workers/runs?${query.toString()}`);
    if (!Array.isArray(payload)) {
      return NextResponse.json({ error: "Worker Run list returned an invalid response." }, { status: 502 });
    }
    const runs = payload.flatMap((row) => {
      const safe = safeWorkerRun(row);
      if (!safe) return [];
      if (
        projectId !== null &&
        (typeof safe.project_id !== "string" || safe.project_id.toLowerCase() !== projectId.toLowerCase())
      ) return [];
      return [safe];
    });
    const rawRunsById = new Map(
      payload.flatMap((row) => {
        const raw = record(row);
        if (
          projectId !== null &&
          (typeof raw?.project_id !== "string" || raw.project_id.toLowerCase() !== projectId.toLowerCase())
        ) return [];
        return typeof raw?.id === "string" ? [[raw.id, raw] as const] : [];
      }),
    );

    // Retry requests use a different idempotency key. Resolve through durable
    // retry lineage recursively so ancestry is independent of list ordering;
    // the root still must pass the exact project.issue idempotency check above.
    const runsById = new Map(
      runs.flatMap((run) => typeof run.id === "string" ? [[run.id, run] as const] : []),
    );
    const correlationCache = new Map<string, { issue_id: string; issue_revision: number } | null>();
    const resolving = new Set<string>();
    const resolveIssueCorrelation = (
      run: JsonRecord,
    ): { issue_id: string; issue_revision: number } | null => {
      if (
        run.task_type === "project.issue" &&
        typeof run.issue_id === "string" && UUID_PATTERN.test(run.issue_id) &&
        typeof run.issue_revision === "number" && Number.isSafeInteger(run.issue_revision)
      ) {
        return { issue_id: run.issue_id, issue_revision: run.issue_revision };
      }
      const runId = run.id;
      if (typeof runId !== "string" || typeof run.project_id !== "string" || !UUID_PATTERN.test(run.project_id)) {
        return null;
      }
      if (correlationCache.has(runId)) return correlationCache.get(runId) ?? null;
      if (resolving.has(runId)) return null;
      if (run.task_type !== "project.issue" || typeof run.retry_of_run_id !== "string") {
        correlationCache.set(runId, null);
        return null;
      }

      resolving.add(runId);
      let result: { issue_id: string; issue_revision: number } | null = null;
      const parent = runsById.get(run.retry_of_run_id);
      const rawRun = rawRunsById.get(runId);
      const retryRequest = record(rawRun?.request_json);
      const taskInput = record(retryRequest?.task_input);
      if (
        parent &&
        parent.task_type === "project.issue" &&
        parent.project_id === run.project_id &&
        retryRequest?.project_id === run.project_id &&
        typeof taskInput?.issue_id === "string" && UUID_PATTERN.test(taskInput.issue_id)
      ) {
        const parentCorrelation = resolveIssueCorrelation(parent);
        if (
          parentCorrelation &&
          taskInput.issue_id === parentCorrelation.issue_id &&
          run.retry_issue_id_candidate === parentCorrelation.issue_id
        ) {
          result = parentCorrelation;
        }
      }
      resolving.delete(runId);
      correlationCache.set(runId, result);
      return result;
    };

    for (const run of runs) {
      if (run.issue_id) continue;
      const correlation = resolveIssueCorrelation(run);
      if (correlation) {
        run.issue_id = correlation.issue_id;
        run.issue_revision = correlation.issue_revision;
        delete run.retry_issue_id_candidate;
      }
    }

    return NextResponse.json(runs, {
      headers: { "X-AIAT-Has-More": String(payload.length === limit) },
    });
  } catch (error: unknown) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    return NextResponse.json({ error: error instanceof Error ? error.message : String(error) }, { status: 500 });
  }
}
