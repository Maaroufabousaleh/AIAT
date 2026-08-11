import { orchestratorFetch } from "@/lib/orchestrator";
import { promQuery } from "@/lib/prometheus";
import { WORKFLOW_STATES } from "@/lib/constants";
import { formatInTz } from "@/lib/datetime";
import { clsx } from "clsx";
import { SeedDefaultCompanyButton } from "@/components/SeedDefaultCompanyButton";
import { KpiCard } from "@/components/ui/KpiCard";
import { EmptyState } from "@/components/ui/EmptyState";
import { QuickLinkCard } from "@/components/ui/QuickLinkCard";
import { Activity, AlertCircle, ArrowUpRight, Layers } from "lucide-react";
import Link from "next/link";

interface StateVisual {
  bg: string;
  text: string;
  dot: string;
}

interface ExecutiveReconciliation {
  schema_version: string;
  status: string;
  coverage: { project_count: number; worker_run_count: number; budget_count: number };
  projects: { active_count: number; usage: { total_cost_usd: number } };
  delivery: { successful_run_count: number; failed_run_count: number; success_rate: number | null };
  budgets: { available_usd: number; used_usd: number; overages: Array<unknown> };
  models?: { profile_pending_model_count?: number };
  findings: Array<{ code: string; severity?: string }>;
  views?: {
    cfo: {
      status: string;
      spend_usd: number;
      budget_available_usd: number;
      reservation_active_usd: number;
      reservation_anomaly_count: number;
    };
    cto: {
      status: string;
      active_worker_runs: number;
      success_rate: number | null;
      failed_worker_runs: number;
      profile_coverage: { pending_models: number };
    };
    ceo: {
      status: string;
      active_projects: number;
      total_projects: number;
      finding_count: number;
      budget_available_usd: number;
    };
  };
}

const STATE_VISUAL: Record<string, StateVisual> = {
  CREATED: { bg: "bg-slate-500/20", text: "text-slate-300", dot: "bg-slate-400" },
  PLANNING: { bg: "bg-blue-500/20", text: "text-blue-300", dot: "bg-blue-400" },
  AWAITING_APPROVAL: { bg: "bg-amber-500/20", text: "text-amber-300", dot: "bg-amber-400" },
  IN_PROGRESS: { bg: "bg-indigo-500/20", text: "text-indigo-300", dot: "bg-indigo-400" },
  BLOCKED: { bg: "bg-rose-500/20", text: "text-rose-300", dot: "bg-rose-400" },
  REVIEW: { bg: "bg-purple-500/20", text: "text-purple-300", dot: "bg-purple-400" },
  COMPLETED: { bg: "bg-emerald-500/20", text: "text-emerald-300", dot: "bg-emerald-400" },
  ARCHIVED: { bg: "bg-slate-500/20", text: "text-slate-400", dot: "bg-slate-500" },
  FAILED: { bg: "bg-rose-500/30", text: "text-rose-300", dot: "bg-rose-400" },
};

/**
 * Wraps a prometheus query and records its wall-clock duration. Used by
 * {@link getOverviewData} to surface "Prometheus query time" on the system
 * health card. Returns the original results plus the elapsed time in ms.
 */
async function timedPromQuery(
  query: string
): Promise<{ results: Awaited<ReturnType<typeof promQuery>>; durationMs: number; ok: boolean }> {
  const started = Date.now();
  try {
    const results = await promQuery(query);
    return { results, durationMs: Date.now() - started, ok: true };
  } catch {
    return { results: [], durationMs: Date.now() - started, ok: false };
  }
}

async function getOverviewData() {
  // Time the prometheus calls so we can show a "query time" on the health card.
  const [projects, systemStatus, companyOverview, dlq, llmRate, dlqDepth, executiveReport] = await Promise.allSettled([
    orchestratorFetch<Array<{ state: string }>>("/projects?limit=1000"),
    orchestratorFetch("/system/status"),
    orchestratorFetch<{
      departments: Array<{ id: string; name: string; worker_count: number; active_projects: number; pending_approvals: number; evaluation_warnings: number }>;
      totals: {
        workers: number;
        active_workers: number;
        projects: number;
        active_projects: number;
        pending_approvals: number;
        evaluation_warnings: number;
      };
    }>("/system/company"),
    orchestratorFetch<unknown[]>("/dead-letters"),
    timedPromQuery("sum(rate(mas_llm_calls_total[5m]))"),
    timedPromQuery("sum(mas_dlq_depth)"),
    orchestratorFetch<ExecutiveReconciliation>("/executive/reconciliation"),
  ]);

  const projectList = projects.status === "fulfilled"
    ? (Array.isArray(projects.value)
      ? projects.value
      : ((projects.value as unknown as { projects?: Array<{ state: string }> }).projects ?? []))
    : [];

  const stateCounts = WORKFLOW_STATES.reduce<Record<string, number>>((acc, s) => {
    acc[s] = projectList.filter((p) => p.state === s).length;
    return acc;
  }, {});

  const status = systemStatus.status === "fulfilled"
    ? (systemStatus.value as { status?: string; state?: string; first_run?: string })
    : null;

  const dlqCount = dlq.status === "fulfilled"
    ? (Array.isArray(dlq.value) ? dlq.value.length : 0)
    : 0;

  // Roll prometheus timings into a single summary used by the system health card.
  const llmTimed = llmRate.status === "fulfilled" ? llmRate.value : null;
  const dlqTimed = dlqDepth.status === "fulfilled" ? dlqDepth.value : null;
  const promQueryMs =
    llmTimed && dlqTimed
      ? Math.max(llmTimed.durationMs, dlqTimed.durationMs)
      : (llmTimed?.durationMs ?? dlqTimed?.durationMs ?? null);
  const promOk = (llmTimed?.ok ?? false) && (dlqTimed?.ok ?? false);
  const lastRefreshedAt = new Date();

  const llmPerMin = llmTimed && llmTimed.ok && llmTimed.results[0]
    ? (parseFloat(llmTimed.results[0].value?.[1] ?? "0") * 60).toFixed(1)
    : llmTimed?.ok ? "0.0" : null;

  const dlqDepthVal = dlqTimed && dlqTimed.ok && dlqTimed.results[0]
    ? dlqTimed.results[0].value?.[1] ?? "0"
    : "0";

  const company = companyOverview.status === "fulfilled" ? companyOverview.value : null;
  const executive = executiveReport.status === "fulfilled" ? executiveReport.value : null;

  return {
    projectList,
    stateCounts,
    status,
    company,
    dlqCount,
    llmPerMin,
    dlqDepthVal,
    promQueryMs,
    promOk,
    executive,
    lastRefreshedAt,
  };
}

function SystemStatusPill({ status }: { status: string }) {
  const visual =
    status === "running"
      ? { label: "Running", cls: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30", dot: "bg-emerald-400" }
      : status === "degraded"
      ? { label: "Degraded", cls: "bg-amber-500/15 text-amber-300 border-amber-500/30", dot: "bg-amber-400" }
      : status === "shutdown"
      ? { label: "Shutdown", cls: "bg-slate-500/20 text-slate-300 border-slate-500/30", dot: "bg-slate-400" }
      : { label: status || "Unknown", cls: "bg-slate-500/15 text-slate-400 border-slate-500/30", dot: "bg-slate-500" };
  return (
    <span className={clsx("inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium border", visual.cls)}>
      <span className={clsx("w-1.5 h-1.5 rounded-full", visual.dot, status === "running" && "animate-pulse")} />
      {visual.label}
    </span>
  );
}

/**
 * Compact system health card. Surfaces Prometheus query latency, the most
 * recent refresh timestamp, and an at-a-glance status indicator so operators
 * can see whether the metrics pipeline is healthy without leaving the
 * overview page.
 */
function SystemHealthCard({
  promQueryMs,
  promOk,
  lastRefreshedAt,
  systemStatusStr,
  hasBackend,
}: {
  promQueryMs: number | null;
  promOk: boolean;
  lastRefreshedAt: Date;
  systemStatusStr: string;
  hasBackend: boolean;
}) {
  // Choose a tone for the latency read-out: <150ms healthy, 150-500ms slow,
  // >500ms or unknown degraded. Matches common SRE conventions.
  const latencyTone =
    !hasBackend
      ? "slate"
      : promQueryMs === null
        ? "rose"
        : promQueryMs < 150
          ? "emerald"
          : promQueryMs < 500
            ? "amber"
            : "rose";
  const latencyText =
    !hasBackend
      ? "n/a"
      : promQueryMs === null
        ? "err"
        : `${promQueryMs} ms`;
  const latencyHint = !hasBackend
    ? "orchestrator offline"
    : promQueryMs === null
      ? "query failed"
      : promQueryMs < 150
        ? "healthy"
        : promQueryMs < 500
          ? "slow"
          : "degraded";

  // Status indicator dot: prefers backend status, falls back to prometheus
  // health, then a neutral state.
  const statusDotCls = !hasBackend
    ? "bg-slate-500"
    : systemStatusStr === "running" && promOk
      ? "bg-emerald-400 animate-pulse"
      : systemStatusStr === "running"
        ? "bg-amber-400"
        : systemStatusStr === "degraded"
          ? "bg-amber-400"
          : "bg-rose-400";
  const statusLabel = !hasBackend
    ? "Waiting for orchestrator"
    : systemStatusStr === "running" && promOk
      ? "All systems nominal"
      : systemStatusStr === "running"
        ? "Orchestrator up · metrics degraded"
        : systemStatusStr === "degraded"
          ? "Degraded performance"
          : systemStatusStr === "shutdown"
            ? "System shutdown"
            : "Status unknown";

  return (
    <div
      role="region"
      aria-label="System health summary"
      className="dashboard-surface p-4 transition-colors hover:border-slate-700"
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-3 min-w-0">
          <div
            aria-hidden="true"
            className={clsx(
              "flex-shrink-0 w-2.5 h-2.5 rounded-full mt-1.5 shadow-sm",
              statusDotCls
            )}
          />
          <div className="min-w-0">
            <div className="text-xs text-slate-500 uppercase tracking-wider font-semibold">
              System health
            </div>
            <div className="text-sm font-medium text-slate-100 mt-0.5">
              {statusLabel}
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3 sm:gap-6">
          <div
            className="flex flex-col"
            aria-label={`Prometheus query time: ${latencyText}`}
          >
            <span className="text-xxs text-slate-500 uppercase tracking-wider font-semibold">
              Prom latency
            </span>
            <span
              className={clsx(
                "text-sm font-mono font-semibold mt-0.5",
                latencyTone === "emerald" && "text-emerald-300",
                latencyTone === "amber" && "text-amber-300",
                latencyTone === "rose" && "text-rose-300",
                latencyTone === "slate" && "text-slate-400"
              )}
            >
              {latencyText}
            </span>
            <span className="text-xxs text-slate-500 mt-0.5">{latencyHint}</span>
          </div>
          <div
            className="flex flex-col"
            aria-label={`Last refreshed at ${lastRefreshedAt.toISOString()}`}
          >
            <span className="text-xxs text-slate-500 uppercase tracking-wider font-semibold">
              Last refresh
            </span>
            <span className="text-sm font-mono text-slate-200 mt-0.5">
              {formatInTz(lastRefreshedAt, "HH:mm:ss")}
            </span>
            <span className="text-xxs text-slate-500 mt-0.5">
              {formatInTz(lastRefreshedAt, "MMM d, yyyy")}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

interface QuickLink {
  href: string;
  label: string;
  desc: string;
  icon: "folder-kanban" | "brain" | "activity" | "bar-chart" | "scroll" | "inbox"
    | "network" | "users" | "lock" | "git-branch" | "settings" | "wrench" | "rocket";
  tone: "blue" | "indigo" | "emerald" | "amber" | "rose" | "zinc";
}

const QUICK_LINKS: QuickLink[] = [
  { href: "/projects",    label: "Projects",     desc: "View all projects, create new ones, approve decisions",   icon: "folder-kanban", tone: "blue" },
  { href: "/ceo",         label: "CEO Live Feed", desc: "Watch the CEO agent's think loop in real-time",         icon: "brain",        tone: "indigo" },
  { href: "/streams",     label: "Agent Streams", desc: "Monitor any of the 11 team message streams",            icon: "activity",     tone: "emerald" },
  { href: "/analytics/litellm", label: "LiteLLM Analytics", desc: "LLM usage, spend, models, keys, and gateway activity", icon: "bar-chart", tone: "amber" },
  { href: "/analytics/omniroute", label: "OmniRoute Analytics", desc: "Provider routing, savings, evaluations, and target health", icon: "network", tone: "emerald" },
  { href: "/logs",        label: "Log Viewer",   desc: "Streaming structured logs from all containers",          icon: "scroll",       tone: "zinc" },
  { href: "/dlq",         label: "Dead Letter Queue", desc: "Inspect and replay failed messages",              icon: "inbox",        tone: "rose" },
  { href: "/system-viz",  label: "System Viz",   desc: "Hierarchy, permissions, and orchestration graph",        icon: "network",      tone: "indigo" },
  { href: "/workers",     label: "Workers",      desc: "Hiring board — register, evaluate, and activate",        icon: "users",        tone: "emerald" },
  { href: "/credentials", label: "Credentials",  desc: "Centralised secret store with policy gates",             icon: "lock",         tone: "amber" },
  { href: "/flows",       label: "Flows",        desc: "Orchestration flows and their state machines",           icon: "git-branch",   tone: "blue" },
  { href: "/system",      label: "System Control", desc: "Runtime state, scheduled shutdown/resume",            icon: "settings",     tone: "zinc" },
  { href: "/tools",       label: "Tools",        desc: "Available tools and circuit breaker health",             icon: "wrench",       tone: "blue" },
];

export default async function HomePage() {
  const {
    projectList,
    stateCounts,
    status,
    company,
    dlqCount,
    llmPerMin,
    dlqDepthVal,
    promQueryMs,
    promOk,
    executive,
    lastRefreshedAt,
  } = await getOverviewData().catch(() => ({
    projectList: [] as Array<{ state: string }>,
    stateCounts: {} as Record<string, number>,
    status: null,
    company: null,
    dlqCount: 0,
    llmPerMin: null as string | null,
    dlqDepthVal: "0",
    promQueryMs: null as number | null,
    promOk: false,
    executive: null as ExecutiveReconciliation | null,
    lastRefreshedAt: new Date(),
  }));

  const systemStatusStr = (status as { status?: string; state?: string })?.status ?? (status as { state?: string })?.state ?? "unknown";
  const firstRun = (status as { first_run?: string })?.first_run ?? "needs_migration_config";
  const hasBackend = !!status;

  const activeCount = company?.totals.active_projects ?? projectList.filter(
    (p) => !["COMPLETED", "ARCHIVED", "FAILED"].includes(p.state)
  ).length;
  const totalProjects = company?.totals.projects ?? projectList.length;

  // Empty / unavailable KPI handling: when the upstream query failed we show a
  // muted "x" placeholder (more visually distinct than an em-dash) and an
  // aria-label so screen readers announce the unavailable state. The dashboard
  // hint underneath is the human-readable explanation.
  const llmEmpty = llmPerMin === null;
  const llmDisplay = llmEmpty ? "x" : llmPerMin;

  return (
    <div className="dashboard-page">
      {/* Hero */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className="w-11 h-11 rounded-xl bg-blue-500/10 border border-blue-400/25 flex items-center justify-center text-blue-300 shadow-sm shadow-blue-950/40">
            <Activity size={20} />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-white tracking-tight">System Overview</h1>
            <p className="text-sm text-slate-400 mt-0.5">Operator-ready summary of projects, workers, approvals, queues, and runtime health.</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <SystemStatusPill status={systemStatusStr} />
        </div>
      </div>

      {/* System health card: prometheus query time + last refresh timestamp */}
      <SystemHealthCard
        promQueryMs={promQueryMs}
        promOk={promOk}
        lastRefreshedAt={lastRefreshedAt}
        systemStatusStr={systemStatusStr}
        hasBackend={hasBackend}
      />

      {/* KPI row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KpiCard
          label="Active Projects"
          value={activeCount}
          hint={`${totalProjects} total`}
          icon="folder-kanban"
          tone="info"
        />
        <KpiCard
          label="LLM calls / min"
          value={
            <span
              aria-label={llmEmpty ? "Data unavailable" : `${llmPerMin} calls per minute`}
              className={clsx(llmEmpty && "text-slate-600 font-normal")}
            >
              {llmDisplay}
            </span>
          }
          hint={llmEmpty ? "Prometheus unavailable — click Metrics to investigate" : "5-min rate"}
          icon="brain"
          tone={llmEmpty ? "neutral" : "info"}
        />
        <KpiCard
          label="Pending approvals"
          value={company?.totals.pending_approvals ?? 0}
          hint={`${company?.totals.workers ?? 0} workers total`}
          icon="check-circle"
          tone="warning"
        />
        <KpiCard
          label="Dead letters"
          value={dlqCount}
          hint={`depth ${dlqDepthVal}`}
          icon="inbox"
          tone={dlqCount > 0 ? "negative" : "positive"}
        />
      </div>

      {executive && (
        <section className="dashboard-surface p-4" aria-label="Executive reconciliation">
          <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
            <div>
              <h2 className="text-sm font-medium text-white">Executive reconciliation</h2>
              <p className="text-xs text-slate-500 mt-0.5">CFO cost, CTO delivery, and CEO portfolio reads from durable control-plane evidence.</p>
            </div>
            <span className={clsx("text-xs uppercase tracking-wide", executive.status === "reconciled" ? "text-emerald-300" : "text-amber-300")}>
              {executive.status.replace(/_/g, " ")}
            </span>
          </div>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <div className="rounded-lg border border-slate-800 bg-slate-950/55 p-3"><div className="text-lg font-semibold text-white">${executive.projects.usage.total_cost_usd.toFixed(2)}</div><div className="text-xxs text-slate-500 uppercase tracking-wide">recorded usage cost</div></div>
            <div className="rounded-lg border border-slate-800 bg-slate-950/55 p-3"><div className="text-lg font-semibold text-white">{executive.projects.active_count}/{executive.coverage.project_count}</div><div className="text-xxs text-slate-500 uppercase tracking-wide">active projects</div></div>
            <div className="rounded-lg border border-slate-800 bg-slate-950/55 p-3"><div className="text-lg font-semibold text-white">{executive.delivery.success_rate === null ? "—" : `${Math.round(executive.delivery.success_rate * 100)}%`}</div><div className="text-xxs text-slate-500 uppercase tracking-wide">terminal run success</div></div>
            <div className="rounded-lg border border-slate-800 bg-slate-950/55 p-3"><div className="text-lg font-semibold text-white">${executive.budgets.available_usd.toFixed(2)}</div><div className="text-xxs text-slate-500 uppercase tracking-wide">budget available</div></div>
          </div>
          {executive.findings.length > 0 && <div className="mt-3 text-xs text-amber-300">{executive.findings.length} reconciliation finding(s); model profiles pending: {executive.models?.profile_pending_model_count ?? 0}.</div>}
          {executive.views && (
            <div className="mt-4 grid grid-cols-1 md:grid-cols-3 gap-3" aria-label="Executive role views">
              <div className="rounded-lg border border-slate-800 bg-slate-950/55 p-3">
                <div className="flex items-center justify-between gap-2">
                  <div className="text-xs font-semibold uppercase tracking-wide text-slate-300">CFO</div>
                  <span className={clsx("text-xxs uppercase", executive.views.cfo.status === "clear" ? "text-emerald-300" : "text-amber-300")}>{executive.views.cfo.status}</span>
                </div>
                <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
                  <div><div className="text-sm font-semibold text-white">${executive.views.cfo.spend_usd.toFixed(2)}</div><div className="text-xxs text-slate-500">spend</div></div>
                  <div><div className="text-sm font-semibold text-white">${executive.views.cfo.budget_available_usd.toFixed(2)}</div><div className="text-xxs text-slate-500">available</div></div>
                  <div><div className="text-sm font-semibold text-white">${executive.views.cfo.reservation_active_usd.toFixed(2)}</div><div className="text-xxs text-slate-500">reserved/committed</div></div>
                  <div><div className="text-sm font-semibold text-white">{executive.views.cfo.reservation_anomaly_count}</div><div className="text-xxs text-slate-500">ledger anomalies</div></div>
                </div>
              </div>
              <div className="rounded-lg border border-slate-800 bg-slate-950/55 p-3">
                <div className="flex items-center justify-between gap-2">
                  <div className="text-xs font-semibold uppercase tracking-wide text-slate-300">CTO</div>
                  <span className={clsx("text-xxs uppercase", executive.views.cto.status === "clear" ? "text-emerald-300" : "text-amber-300")}>{executive.views.cto.status}</span>
                </div>
                <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
                  <div><div className="text-sm font-semibold text-white">{executive.views.cto.success_rate === null ? "—" : `${Math.round(executive.views.cto.success_rate * 100)}%`}</div><div className="text-xxs text-slate-500">run success</div></div>
                  <div><div className="text-sm font-semibold text-white">{executive.views.cto.active_worker_runs}</div><div className="text-xxs text-slate-500">active runs</div></div>
                  <div><div className="text-sm font-semibold text-white">{executive.views.cto.failed_worker_runs}</div><div className="text-xxs text-slate-500">failed runs</div></div>
                  <div><div className="text-sm font-semibold text-white">{executive.views.cto.profile_coverage.pending_models}</div><div className="text-xxs text-slate-500">models pending</div></div>
                </div>
              </div>
              <div className="rounded-lg border border-slate-800 bg-slate-950/55 p-3">
                <div className="flex items-center justify-between gap-2">
                  <div className="text-xs font-semibold uppercase tracking-wide text-slate-300">CEO</div>
                  <span className={clsx("text-xxs uppercase", executive.views.ceo.status === "clear" ? "text-emerald-300" : "text-amber-300")}>{executive.views.ceo.status}</span>
                </div>
                <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
                  <div><div className="text-sm font-semibold text-white">{executive.views.ceo.active_projects}/{executive.views.ceo.total_projects}</div><div className="text-xxs text-slate-500">active projects</div></div>
                  <div><div className="text-sm font-semibold text-white">{executive.views.ceo.finding_count}</div><div className="text-xxs text-slate-500">findings</div></div>
                  <div><div className="text-sm font-semibold text-white">${executive.views.ceo.budget_available_usd.toFixed(2)}</div><div className="text-xxs text-slate-500">budget available</div></div>
                </div>
              </div>
            </div>
          )}
        </section>
      )}

      {/* First run callout — quiet, not alarming */}
      {firstRun !== "seeded" && (
        <div className="flex items-center justify-between gap-4 rounded-xl border border-amber-500/30 bg-amber-500/5 px-4 py-3 shadow-sm shadow-amber-950/10">
          <div className="flex items-center gap-3 min-w-0">
            <div className="flex-shrink-0 w-8 h-8 rounded-md bg-amber-500/10 border border-amber-500/30 flex items-center justify-center text-amber-300">
              <AlertCircle size={16} />
            </div>
            <div className="min-w-0">
              <div className="text-sm font-medium text-amber-100">
                {firstRun === "not_seeded" ? "Default company not yet seeded" : "Configuration required"}
              </div>
              <div className="text-xs text-amber-200/70 mt-0.5">
                {firstRun === "not_seeded"
                  ? "Seed the default company to bring the system online."
                  : "The orchestrator has not reported its first run state yet."}
              </div>
            </div>
          </div>
          {firstRun === "not_seeded" && <SeedDefaultCompanyButton />}
        </div>
      )}

      {/* Company + state distribution */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {company ? (
          <div className="lg:col-span-2 dashboard-surface p-4">
            <div className="flex items-center justify-between gap-4 mb-4">
              <div>
                <h2 className="text-sm font-medium text-white">Company Overview</h2>
                <p className="text-xs text-slate-500 mt-0.5">
                  {company.totals.active_workers}/{company.totals.workers} workers active · {company.totals.pending_approvals} approvals pending
                </p>
              </div>
              <Link href="/system-viz" prefetch={false} className="inline-flex items-center gap-1 text-xs text-blue-400 hover:text-blue-300">
                Open graph <ArrowUpRight size={12} />
              </Link>
            </div>
            {company.departments.length === 0 ? (
              <EmptyState
                icon="layers"
                title="No departments yet"
                description="Departments will appear here once the company is fully seeded."
              />
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
                {company.departments.slice(0, 6).map((department) => (
                  <div
                    key={department.id}
                    className="rounded-lg bg-slate-950/55 border border-slate-800 p-3 hover:border-slate-700 transition-colors"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="text-sm text-gray-100 truncate">{department.name}</div>
                      {department.evaluation_warnings > 0 && (
                        <div className="text-xxs px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-300 border border-amber-500/20">
                          {department.evaluation_warnings} warn
                        </div>
                      )}
                    </div>
                    <div className="mt-2.5 grid grid-cols-3 gap-2 text-xs">
                      <div>
                        <div className="text-slate-200 font-medium">{department.worker_count}</div>
                        <div className="text-slate-500 text-xxs">workers</div>
                      </div>
                      <div>
                        <div className="text-slate-200 font-medium">{department.active_projects}</div>
                        <div className="text-slate-500 text-xxs">projects</div>
                      </div>
                      <div>
                        <div className="text-slate-200 font-medium">{department.pending_approvals}</div>
                        <div className="text-slate-500 text-xxs">approvals</div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="lg:col-span-2">
            <EmptyState
              icon="layers"
              title="Company overview unavailable"
              description={hasBackend ? "The orchestrator didn't return company data." : "Waiting for the orchestrator to come online."}
            />
          </div>
        )}

        {/* Project state distribution */}
        <div className="dashboard-surface p-4">
          <h2 className="text-sm font-medium text-white mb-3">Projects by State</h2>
          {projectList.length === 0 ? (
            <EmptyState
              icon="circle"
              title="No projects yet"
              description="Create your first project to see state distribution."
              className="!py-6 !border-0 !bg-transparent"
            />
          ) : (
            <div className="space-y-2">
              {WORKFLOW_STATES.filter((s) => (stateCounts[s] ?? 0) > 0).map((state) => {
                const v = STATE_VISUAL[state] ?? STATE_VISUAL.CREATED;
                const count = stateCounts[state] ?? 0;
                const max = Math.max(...Object.values(stateCounts));
                const pct = max > 0 ? Math.max(8, Math.round((count / max) * 100)) : 0;
                return (
                  <div key={state} className="text-xs">
                    <div className="flex items-center justify-between mb-1">
                      <span className={clsx("inline-flex items-center gap-1.5", v.text)}>
                        <span className={clsx("w-1.5 h-1.5 rounded-full", v.dot)} />
                        {state.replace(/_/g, " ")}
                      </span>
                      <span className="text-slate-400 font-mono">{count}</span>
                    </div>
                    <div className="h-1.5 rounded-full bg-slate-800 overflow-hidden">
                      <div
                        className={clsx("h-full rounded-full", v.dot)}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Quick links */}
      <div className="dashboard-surface p-4">
        <h2 className="text-sm font-medium text-white mb-3">Quick Links</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-2.5">
          {QUICK_LINKS.map((q) => (
            <QuickLinkCard key={q.href} {...q} />
          ))}
        </div>
      </div>
    </div>
  );
}
