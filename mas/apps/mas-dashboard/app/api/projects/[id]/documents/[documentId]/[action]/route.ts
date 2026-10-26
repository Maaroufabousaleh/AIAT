import { NextResponse } from "next/server";

type Params = {
  params: Promise<{ id: string; documentId: string; action: string }>;
};

const ORCHESTRATOR_URL = process.env.ORCHESTRATOR_URL ?? "http://localhost:8000";
const OPERATOR_KEY = process.env.AIAT_OPERATOR_API_KEY ?? "";
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function GET(_: Request, props: Params) {
  const { id, documentId, action } = await props.params;
  if (!UUID_PATTERN.test(id) || !UUID_PATTERN.test(documentId)) {
    return NextResponse.json({ error: "Invalid project or document ID" }, { status: 400 });
  }
  if (action !== "preview" && action !== "download") {
    return NextResponse.json({ error: "Unsupported document action" }, { status: 404 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(
      `${ORCHESTRATOR_URL}/projects/${encodeURIComponent(id)}/documents/${encodeURIComponent(documentId)}/${action}`,
      {
        headers: {
          "X-API-Key": OPERATOR_KEY,
          "X-AIAT-Actor-ID": "dashboard-operator",
          "X-AIAT-Dashboard-Section": "projects",
          Accept: "*/*",
        },
        cache: "no-store",
        signal: AbortSignal.timeout(30_000),
      },
    );
  } catch {
    return NextResponse.json({ error: "Document service is temporarily unavailable" }, { status: 502 });
  }

  if (!upstream.ok) {
    // Keep backend exception details and storage diagnostics inside the
    // control plane. The UI needs the status, not arbitrary error payloads.
    return NextResponse.json(
      { error: `Document ${action} failed with status ${upstream.status}` },
      { status: upstream.status },
    );
  }

  const contentType = upstream.headers.get("content-type") ?? "application/octet-stream";
  const headers = new Headers({
    "Content-Type": contentType,
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
  });

  if (action === "preview") {
    // The dashboard renders preview bytes as text. Keep any direct navigation
    // to this endpoint inert as well.
    headers.set("Content-Security-Policy", "sandbox; default-src 'none'");
    headers.set("Content-Disposition", "inline");
  } else {
    const upstreamDisposition = upstream.headers.get("content-disposition") ?? "";
    const candidate = upstreamDisposition.match(
      /^attachment;\s*filename="([a-zA-Z0-9][a-zA-Z0-9._-]{0,99})"$/i,
    )?.[1];
    const filename = candidate ?? "project-document";
    headers.set("Content-Disposition", `attachment; filename="${filename}"`);
  }

  const sha256 = upstream.headers.get("x-aiat-document-sha256");
  if (sha256 && /^[a-f0-9]{64}$/i.test(sha256)) {
    headers.set("X-AIAT-Document-SHA256", sha256);
  }

  return new Response(upstream.body, { status: upstream.status, headers });
}
