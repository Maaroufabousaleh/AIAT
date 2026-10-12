import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

type Params = { params: Promise<{ id: string }> };

const PROJECT_EVIDENCE_INCOMPLETE = "PROJECT_EVIDENCE_INCOMPLETE";
const PROJECT_EVIDENCE_INCOMPLETE_MESSAGE =
  "Project cannot be completed until its required evidence is complete.";
const MAX_FAILED_CHECK_NAMES = 12;
const DOCUMENT_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const DOCUMENT_TYPE_BY_EVENT: Record<string, string> = {
  pdr_submitted: "PDR",
  cdr_submitted: "CDR",
  rr_submitted: "RR",
};

type RecordValue = Record<string, unknown>;

function isRecord(value: unknown): value is RecordValue {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function readProjectEvidenceIncompleteDetail(message: string): RecordValue | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(message);
  } catch {
    return null;
  }
  if (!isRecord(parsed) || !isRecord(parsed.detail)) return null;

  const detail = parsed.detail;
  if (
    detail.code !== PROJECT_EVIDENCE_INCOMPLETE ||
    typeof detail.policy_id !== "string" ||
    !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$/.test(detail.policy_id) ||
    typeof detail.policy_version !== "string" ||
    !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$/.test(detail.policy_version) ||
    typeof detail.evidence_status !== "string" ||
    !["incomplete", "legacy/incomplete evidence"].includes(detail.evidence_status) ||
    !Array.isArray(detail.failed_check_names) ||
    detail.failed_check_names.length > MAX_FAILED_CHECK_NAMES ||
    !detail.failed_check_names.every(
      (name) => typeof name === "string" && /^[a-z][a-z0-9_]{0,79}$/.test(name),
    ) ||
    !Number.isSafeInteger(detail.failed_check_count) ||
    (detail.failed_check_count as number) < detail.failed_check_names.length ||
    (detail.failed_check_count as number) > 10_000 ||
    typeof detail.failed_check_names_truncated !== "boolean"
  ) {
    return null;
  }

  return {
    code: PROJECT_EVIDENCE_INCOMPLETE,
    message: PROJECT_EVIDENCE_INCOMPLETE_MESSAGE,
    policy_id: detail.policy_id,
    policy_version: detail.policy_version,
    evidence_status: detail.evidence_status,
    failed_check_names: detail.failed_check_names,
    failed_check_count: detail.failed_check_count,
    failed_check_names_truncated: detail.failed_check_names_truncated,
  };
}

function safeTransitionError(status: number) {
  return NextResponse.json(
    { error: "Project transition failed." },
    { status },
  );
}

function normalizeTransitionRequest(value: unknown): RecordValue | null {
  if (
    !isRecord(value) ||
    Object.keys(value).some((key) => key !== "event" && key !== "context") ||
    typeof value.event !== "string" ||
    !value.event.trim() ||
    value.event.length > 80
  ) {
    return null;
  }

  const request: RecordValue = {
    event: value.event,
    // The authenticated operator principal is authoritative. Never trust a
    // browser-supplied actor_id, even though the API request model requires it.
    actor_id: "dashboard-operator",
  };
  if (value.context === undefined || value.context === null) return request;
  if (!isRecord(value.context)) return null;

  const expectedDocumentType = DOCUMENT_TYPE_BY_EVENT[value.event];
  const context = value.context;
  if (
    Object.keys(context).some((key) => key !== "document_id" && key !== "doc_type") ||
    (context.document_id !== undefined &&
      (typeof context.document_id !== "string" || !DOCUMENT_ID_PATTERN.test(context.document_id))) ||
    (context.doc_type !== undefined &&
      (typeof context.doc_type !== "string" || context.doc_type !== expectedDocumentType)) ||
    (context.doc_type !== undefined && context.document_id === undefined) ||
    (Object.keys(context).length > 0 && !expectedDocumentType)
  ) {
    return null;
  }
  request.context = context;
  return request;
}

export async function GET(_: Request, props: Params) {
  const params = await props.params;
  try {
    const data = await orchestratorFetch<{ allowed_events?: string[] }>(
      `/projects/${params.id}/allowed-transitions`,
    );
    return NextResponse.json(data.allowed_events ?? []);
  } catch (e) {
    if (e instanceof OrchestratorError)
      return NextResponse.json({ error: "Project transitions unavailable." }, { status: e.status });
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}

export async function POST(req: Request, props: Params) {
  const params = await props.params;
  try {
    const body = normalizeTransitionRequest(await req.json().catch(() => null));
    if (!body) {
      return NextResponse.json(
        { error: "Transition request contains unsupported fields or context." },
        { status: 400 },
      );
    }
    const data = await orchestratorFetch(`/projects/${params.id}/transition`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    return NextResponse.json(data);
  } catch (e) {
    if (e instanceof OrchestratorError) {
      const detail = readProjectEvidenceIncompleteDetail(e.message);
      if (e.status === 409 && detail) {
        return NextResponse.json({ detail }, { status: e.status });
      }
      return safeTransitionError(e.status);
    }
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}
