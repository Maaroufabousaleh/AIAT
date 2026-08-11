"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, ExternalLink, ShieldCheck } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";

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

  return (
    <div className="min-h-full p-6 lg:p-8">
      <PageHeader
        title="Evidence record"
        description="A stable, secret-safe deep link for a canonical CEO citation."
        actions={(
          <Link href="/ceo/chat" className="inline-flex items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800">
            <ArrowLeft size={14} /> CEO chat
          </Link>
        )}
      />
      <section className="mt-6 max-w-2xl rounded-xl border border-slate-800 bg-slate-900/70 p-6" data-testid="ceo-evidence-record">
        <div className="flex items-center gap-2 text-sm font-semibold text-cyan-200">
          <ShieldCheck size={16} /> Canonical citation
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
            <Link href={canonicalHref!} className="mt-5 inline-flex items-center gap-2 rounded-lg border border-cyan-400/30 bg-cyan-400/10 px-3 py-2 text-sm font-medium text-cyan-200 hover:bg-cyan-400/20" data-testid="ceo-evidence-canonical-link">
              Open owning section <ExternalLink size={14} />
            </Link>
          </>
        ) : (
          <p className="mt-5 text-sm leading-6 text-rose-300">
            This evidence reference is unsupported or incomplete. Return to the CEO transcript and use a canonical citation supplied by the control plane.
          </p>
        )}
      </section>
    </div>
  );
}
