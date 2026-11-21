"use client";

import { useState } from "react";
import { Check, Copy, ExternalLink } from "lucide-react";

interface ExternalDashboardEmbedProps {
  /** Absolute URL of the external analytics dashboard. */
  target: string;
  /** Short human label, e.g. "LiteLLM dashboard". */
  label: string;
}

/**
 * In-dashboard launcher for an external analytics UI. The upstream
 * dashboards prohibit framing (`frame-ancestors 'none'`), so instead of a
 * broken iframe this renders the target, an Open action, and a copy-URL
 * action. The operator stays oriented and is never thrown off-dashboard
 * without context.
 */
export function ExternalDashboardEmbed({ target, label }: ExternalDashboardEmbedProps) {
  const [copied, setCopied] = useState(false);

  async function copyTarget() {
    try {
      await navigator.clipboard.writeText(target);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="space-y-4">
      <section
        role="region"
        aria-label={`${label} status`}
        aria-live="polite"
        className="dashboard-surface flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3"
      >
        <p className="text-sm text-slate-300">
          The {label} runs as an external service and opens in a new tab.
        </p>
      </section>

      <div className="dashboard-surface space-y-3 p-4">
        <div className="text-xs text-slate-500">Dashboard URL</div>
        <code className="block break-all rounded-lg border border-slate-700/70 bg-slate-950/60 px-3 py-2 text-sm text-slate-200">
          {target}
        </code>
        <div className="flex flex-wrap gap-2">
          <a
            href={target}
            target="_blank"
            rel="noreferrer"
            aria-label={`Open ${label}`}
            className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-500"
          >
            <ExternalLink size={14} aria-hidden="true" />
            Open {label}
          </a>
          <button
            type="button"
            onClick={() => void copyTarget()}
            aria-label={`Copy ${label} URL`}
            className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-slate-700 px-4 py-2 text-sm font-medium text-slate-200 transition-colors hover:border-slate-600 hover:bg-slate-800"
          >
            {copied ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
            {copied ? "Copied" : "Copy URL"}
          </button>
        </div>
      </div>
    </div>
  );
}
