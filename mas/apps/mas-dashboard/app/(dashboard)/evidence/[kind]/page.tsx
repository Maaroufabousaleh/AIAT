"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, ExternalLink, ShieldCheck } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
const SUPPORTED_KIND: Record<string, true> = {
  company: true,
  project: true,
  worker: true,
  flow: true,
  flow_instance: true,
  evaluation: true,
  credential: true,
  dead_letter: true,
  artifact: true,
  integration: true,
  model: true,
  runtime: true,
  tool: true,
  usage: true,
  worker_run: true,
  trace: true,
};

/** Kind-level owning section (no record id) — mirrors the detail page mapping. */
function kindHomeHref(kind: string): string | null {
  switch (kind) {
    case "project":
      return "/projects";
    case "flow":
    case "flow_instance":
      return "/flows";
    case "artifact":
    case "usage":
      return "/projects";
    case "company":
    case "evaluation":
    case "model":
    case "runtime":
      return "/governance";
    case "integration":
      return "/integrations";
    case "worker":
    case "worker_run":
      return "/workers";
    case "credential":
      return "/credentials";
    case "tool":
      return "/tools";
    case "trace":
      return "/logs";
    case "dead_letter":
      return "/dlq";
    default:
      return null;
  }
}

export default function EvidenceKindPage() {
  const params = useParams<{ kind: string }>();
  const kind = typeof params.kind === "string" ? params.kind : "";
  const homeHref = kind ? kindHomeHref(kind) : null;
  const supported = Boolean(kind) && SUPPORTED_KIND[kind] === true && Boolean(homeHref);

  return (
    <main aria-label="Evidence kind" className="dashboard-page">
      <PageHeader
        title="Evidence"
        description={
          kind
            ? `Kind-level citation index for "${kind}". Full citations include a record ID.`
            : "Kind-level citation index. Full citations include a kind and a record ID."
        }
        actions={(
          <Link
            href="/ceo/chat"
            className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800"
          >
            <ArrowLeft size={14} aria-hidden="true" /> CEO chat
          </Link>
        )}
      />
      <section
        className="mt-6 max-w-2xl rounded-xl border border-slate-800 bg-slate-900/70 p-6"
        data-testid="evidence-kind-record"
        aria-labelledby="evidence-kind-heading"
      >
        <div className="flex items-center gap-2 text-sm font-semibold text-cyan-200">
          <ShieldCheck size={16} aria-hidden="true" />
          <h2 id="evidence-kind-heading">Citation kind</h2>
        </div>
        {supported ? (
          <>
            <dl className="mt-5 grid gap-4 sm:grid-cols-[8rem_1fr] text-sm">
              <dt className="text-slate-500">Kind</dt>
              <dd className="font-mono text-slate-200">{kind}</dd>
            </dl>
            <p className="mt-5 text-sm leading-6 text-slate-400">
              This page preserves the evidence kind while you move between the CEO transcript
              and the owning dashboard section. Open a full citation at{" "}
              <span className="font-mono text-cyan-200">/evidence/{kind}/&lt;id&gt;</span>{" "}
              for the bounded, secret-safe record detail.
            </p>
            <div className="mt-5 flex flex-wrap gap-2">
              <Link
                href={homeHref!}
                data-testid="evidence-kind-home-link"
                className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-cyan-400/30 bg-cyan-400/10 px-3 py-2 text-sm font-medium text-cyan-200 hover:bg-cyan-400/20"
              >
                Open owning section <ExternalLink size={14} aria-hidden="true" />
              </Link>
            </div>
          </>
        ) : (
          <div className="mt-2">
            <EmptyState
              icon="alert"
              title="Unsupported evidence kind"
              description={
                kind
                  ? `"${kind}" is not a canonical citation kind. Return to the CEO transcript and use a canonical citation supplied by the control plane.`
                  : "No evidence kind was supplied. Return to the CEO transcript and use a canonical citation supplied by the control plane."
              }
            />
          </div>
        )}
      </section>
    </main>
  );
}
