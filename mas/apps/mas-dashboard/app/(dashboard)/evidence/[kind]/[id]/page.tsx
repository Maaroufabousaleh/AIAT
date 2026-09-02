"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, ExternalLink, ShieldCheck } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";

type EvidenceDetail = {
  schema_version: string;
  kind: string;
  id: string;
  source: string;
  record: Record<string, string | number | boolean | null>;
};

const SUPPORTED_KINDS = new Set([
  "company",
  "project",
  "worker",
  "flow",
  "flow_instance",
  "evaluation",
  "credential",
  "dead_letter",
  "artifact",
  "integration",
  "model",
  "runtime",
  "tool",
  "usage",
  "worker_run",
  "trace",
]);

const DETAIL_KINDS = new Set([
  "integration",
  "model",
  "tool",
  "project",
  "flow",
  "flow_instance",
  "worker",
  "worker_run",
  "credential",
  "dead_letter",
  "trace",
  "runtime",
  "artifact",
  "usage",
]);

function canonicalEvidenceHref(kind: string, id: string): string | null {
  const encodedId = encodeURIComponent(id);
  switch (kind) {
    case "project":
      return `/projects/${encodedId}`;
    case "flow":
      return `/flows/${encodedId}`;
    case "flow_instance":
      return `/flows?evidence_kind=${encodeURIComponent(kind)}&evidence_id=${encodedId}`;
    case "artifact":
    case "usage":
      return `/projects?evidence_kind=${encodeURIComponent(kind)}&evidence_id=${encodedId}`;
    case "company":
    case "evaluation":
    case "model":
    case "runtime":
      return `/governance?evidence_kind=${encodeURIComponent(kind)}&evidence_id=${encodedId}`;
    case "integration":
      return `/integrations?evidence_kind=${encodeURIComponent(kind)}&evidence_id=${encodedId}`;
    case "worker":
    case "worker_run":
      return `/workers?evidence_kind=${encodeURIComponent(kind)}&evidence_id=${encodedId}`;
    case "credential":
      return `/credentials?evidence_kind=${encodeURIComponent(kind)}&evidence_id=${encodedId}`;
    case "tool":
      return `/tools?evidence_kind=${encodeURIComponent(kind)}&evidence_id=${encodedId}`;
    case "trace":
      return `/logs?trace_id=${encodedId}`;
    case "dead_letter":
      return `/dlq?evidence_kind=${encodeURIComponent(kind)}&evidence_id=${encodedId}`;
    default:
      return null;
  }
}

export default function EvidenceRecordPage() {
  const params = useParams<{ kind: string; id: string }>();
  const kind = typeof params.kind === "string" ? params.kind : "";
  const id = typeof params.id === "string" ? params.id : "";
  const canonicalHref = canonicalEvidenceHref(kind, id);
  const supported = Boolean(id) && SUPPORTED_KINDS.has(kind) && Boolean(canonicalHref);
  const detailSupported = supported && DETAIL_KINDS.has(kind);
  const [detail, setDetail] = useState<EvidenceDetail | null>(null);
  const [detailState, setDetailState] = useState<"idle" | "loading" | "loaded" | "stale" | "unavailable" | "denied">("idle");
  const [detailAttempt, setDetailAttempt] = useState(0);
  const [accessDenied, setAccessDenied] = useState(false);
  const accessDeniedRef = useRef(false);
  const latestDetail = useRef<EvidenceDetail | null>(null);
  const detailKey = useRef("");

  const handleAccessDenied = useCallback(() => {
    accessDeniedRef.current = true;
    setAccessDenied(true);
    setDetailState("denied");
  }, []);

  useEffect(() => {
    if (!detailSupported) {
      setDetail(null);
      latestDetail.current = null;
      detailKey.current = "";
      accessDeniedRef.current = false;
      setAccessDenied(false);
      setDetailState("idle");
      return;
    }
    const currentKey = `${kind}:${id}`;
    if (detailKey.current !== currentKey) {
      detailKey.current = currentKey;
      latestDetail.current = null;
      setDetail(null);
      accessDeniedRef.current = false;
      setAccessDenied(false);
    }
    if (accessDeniedRef.current) return;
    let active = true;
    const controller = new AbortController();
    setDetailState("loading");
    void fetch(`/api/evidence/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`, {
      cache: "no-store",
      signal: controller.signal,
    })
      .then(async (response) => {
        if (response.status === 401 || response.status === 403) {
          handleAccessDenied();
          return null;
        }
        if (!response.ok) throw new Error("evidence detail unavailable");
        return response.json() as Promise<EvidenceDetail>;
      })
      .then((value) => {
        if (!active || !value) return;
        latestDetail.current = value;
        setDetail(value);
        setDetailState("loaded");
      })
      .catch(() => {
        if (!active) return;
        setDetailState(latestDetail.current ? "stale" : "unavailable");
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [detailAttempt, detailSupported, handleAccessDenied, id, kind]);

  return (
    <main aria-label="Evidence detail" className="min-h-full p-6 lg:p-8">
      <PageHeader
        title="Evidence record"
        description="A stable, secret-safe deep link for a canonical CEO citation."
        actions={(
          <Link href="/ceo/chat" className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800">
            <ArrowLeft size={14} aria-hidden="true" /> CEO chat
          </Link>
        )}
      />
      <section className="mt-6 max-w-2xl rounded-xl border border-slate-800 bg-slate-900/70 p-6" data-testid="ceo-evidence-record" aria-labelledby="evidence-record-heading">
        <div className="flex items-center gap-2 text-sm font-semibold text-cyan-200">
          <ShieldCheck size={16} aria-hidden="true" />
          <h2 id="evidence-record-heading">Canonical citation</h2>
        </div>
        {supported ? (
          <>
            <dl className="mt-5 grid gap-4 sm:grid-cols-[8rem_1fr] text-sm">
              <dt className="text-slate-500">Kind</dt>
              <dd className="font-mono text-slate-200">{kind}</dd>
              <dt className="text-slate-500">Record ID</dt>
              <dd className="break-all font-mono text-cyan-200">{id}</dd>
            </dl>
            <p className="mt-5 text-sm leading-6 text-slate-400">
              This page preserves the evidence identity while you move between the CEO transcript and the owning dashboard section. It never displays secret values or arbitrary model payloads.
            </p>
            {detailSupported && (
              <section className="mt-5 border-t border-slate-800 pt-5" data-testid="ceo-evidence-detail" aria-labelledby="evidence-detail-heading" aria-busy={detailState === "loading"}>
                <div className="flex items-center justify-between gap-3">
                  <h2 id="evidence-detail-heading" className="text-xs font-semibold uppercase tracking-wide text-slate-400">Bounded record detail</h2>
                  <div className="flex items-center gap-3">
                    {!accessDenied && <button type="button" onClick={() => setDetailAttempt((attempt) => attempt + 1)} className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-md border border-slate-700 px-2 py-1 text-[11px] font-medium text-slate-300 hover:bg-slate-800" aria-label="Refresh evidence detail" data-testid="ceo-evidence-retry">Refresh</button>}
                    <span className="font-mono text-[11px] text-slate-500">aiat.evidence-detail.v1</span>
                  </div>
                </div>
                {accessDenied && (
                  <section role="region" aria-label="Evidence detail access status" className="mt-3 rounded-lg border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-xs text-amber-100">
                    <h3 className="font-semibold text-amber-50">Evidence detail access denied</h3>
                    <p className="mt-1 text-amber-100/80">
                      {detail
                        ? "The last successful scalar projection is retained as read-only context. Refresh is hidden until authorization is restored."
                        : "Safe record fields cannot be read while authorization is unavailable. The citation identity remains valid."}
                    </p>
                  </section>
                )}
                {detailState === "loading" && <p className="mt-3 text-xs text-slate-500" role="status" aria-live="polite">Loading safe record fields…</p>}
                {detailState === "unavailable" && (
                  <p className="mt-3 text-xs text-amber-300" role="status" aria-live="polite">Safe detail is temporarily unavailable; the citation identity remains valid.</p>
                )}
                {detailState === "stale" && (
                  <p className="mt-3 text-xs text-amber-300" role="status" aria-live="polite">Safe detail is temporarily unavailable; showing the last successful scalar projection.</p>
                )}
                {detailState === "denied" && !accessDenied && (
                  <p className="mt-3 text-xs text-amber-300" role="status" aria-live="polite">Safe detail access is unavailable; the citation identity remains valid.</p>
                )}
                {(detailState === "loaded" || detailState === "stale" || detailState === "denied") && detail && (
                  <dl className="mt-3 grid gap-x-4 gap-y-2 text-xs sm:grid-cols-[9rem_1fr]">
                    {Object.entries(detail.record).map(([key, value]) => (
                      <Fragment key={key}>
                        <dt className="text-slate-500">{key.replaceAll("_", " ")}</dt>
                        <dd className="break-all font-mono text-slate-300">{String(value ?? "—")}</dd>
                      </Fragment>
                    ))}
                  </dl>
                )}
              </section>
            )}
            <Link href={canonicalHref!} className="mt-5 inline-flex min-h-11 items-center gap-2 rounded-lg border border-cyan-400/30 bg-cyan-400/10 px-3 py-2 text-sm font-medium text-cyan-200 hover:bg-cyan-400/20" data-testid="ceo-evidence-canonical-link">
              Open owning section <ExternalLink size={14} aria-hidden="true" />
            </Link>
          </>
        ) : (
          <p className="mt-5 text-sm leading-6 text-rose-300">
            This evidence reference is unsupported or incomplete. Return to the CEO transcript and use a canonical citation supplied by the control plane.
          </p>
        )}
      </section>
    </main>
  );
}
