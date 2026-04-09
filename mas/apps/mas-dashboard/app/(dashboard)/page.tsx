import { orchestratorFetch } from "@/lib/orchestrator";
import { promQuery } from "@/lib/prometheus";
import { WORKFLOW_STATES, STATE_COLORS } from "@/lib/constants";
import { clsx } from "clsx";

async function getOverviewData() {
  const [projects, systemStatus, dlq, llmRate, dlqDepth] = await Promise.allSettled([
    orchestratorFetch<{ projects: Array<{ state: string }> }>("/projects"),
    orchestratorFetch("/system/status"),
    orchestratorFetch<{ dead_letters: unknown[] }>("/dead-letters"),
    promQuery("sum(rate(mas_llm_calls_total[5m]))"),
    promQuery("sum(mas_dlq_depth)"),
  ]);

  const projectList = projects.status === "fulfilled"
    ? ((projects.value as unknown as { projects: Array<{ state: string }> }).projects ?? [])
    : [];

  const stateCounts = WORKFLOW_STATES.reduce<Record<string, number>>((acc, s) => {
    acc[s] = projectList.filter((p) => p.state === s).length;
    return acc;
  }, {});

  const status = systemStatus.status === "fulfilled"
    ? (systemStatus.value as { status?: string })
    : null;

  const dlqCount = dlq.status === "fulfilled"
    ? ((dlq.value as { dead_letters?: unknown[] })?.dead_letters?.length ?? 0)
    : 0;

  const llmPerMin = llmRate.status === "fulfilled" && llmRate.value[0]
    ? (parseFloat(llmRate.value[0].value?.[1] ?? "0") * 60).toFixed(1)
    : "—";

  const dlqDepthVal = dlqDepth.status === "fulfilled" && dlqDepth.value[0]
    ? dlqDepth.value[0].value?.[1] ?? "0"
    : "0";

  return { projectList, stateCounts, status, dlqCount, llmPerMin, dlqDepthVal };
}

export default async function HomePage() {
  const { projectList, stateCounts, status, dlqCount, llmPerMin, dlqDepthVal } =
    await getOverviewData().catch(() => ({
      projectList: [] as Array<{ state: string }>,
      stateCounts: {} as Record<string, number>,
      status: null,
      dlqCount: 0,
      llmPerMin: "—",
      dlqDepthVal: "0",
    }));

  const systemStatusStr = (status as { status?: string })?.status ?? "unknown";
  const systemColor =
    systemStatusStr === "running" ? "text-green-400" :
    systemStatusStr === "shutdown" ? "text-amber-400" : "text-red-400";

  const activeCount = projectList.filter(
    (p) => !["COMPLETED", "ARCHIVED", "FAILED"].includes(p.state)
  ).length;

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-white">System Overview</h1>
        <p className="text-sm text-gray-500 mt-1">AIAT Multi-Agent System</p>
      </div>

      {/* KPI cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
          <div className="text-xs text-gray-500 uppercase tracking-wider mb-1">System Status</div>
          <div className={clsx("text-xl font-bold capitalize", systemColor)}>
            {systemStatusStr}
          </div>
        </div>
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
          <div className="text-xs text-gray-500 uppercase tracking-wider mb-1">Active Projects</div>
          <div className="text-xl font-bold text-white">{activeCount}</div>
          <div className="text-xs text-gray-600 mt-0.5">{projectList.length} total</div>
        </div>
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
          <div className="text-xs text-gray-500 uppercase tracking-wider mb-1">LLM calls/min</div>
          <div className="text-xl font-bold text-white">{llmPerMin}</div>
          <div className="text-xs text-gray-600 mt-0.5">5-min rate</div>
        </div>
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
          <div className={clsx("text-xs uppercase tracking-wider mb-1",
            dlqCount > 0 ? "text-red-400" : "text-gray-500")}>Dead Letters</div>
          <div className={clsx("text-xl font-bold", dlqCount > 0 ? "text-red-400" : "text-white")}>
            {dlqCount}
          </div>
          <div className="text-xs text-gray-600 mt-0.5">depth: {dlqDepthVal}</div>
        </div>
      </div>

      {/* Project state distribution */}
      {projectList.length > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
          <h2 className="text-sm font-medium text-gray-300 mb-3">Projects by State</h2>
          <div className="flex flex-wrap gap-2">
            {WORKFLOW_STATES.filter((s) => (stateCounts[s] ?? 0) > 0).map((state) => (
              <div
                key={state}
                className={clsx(
                  "flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium text-white",
                  STATE_COLORS[state]
                )}
              >
                <span>{state.replace(/_/g, " ")}</span>
                <span className="bg-black/20 rounded-full px-1.5 py-0.5 text-xxs">
                  {stateCounts[state]}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Quick links */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {[
          { href: "/projects", label: "Manage Projects", desc: "View all projects, create new ones, approve decisions" },
          { href: "/ceo", label: "CEO Live Feed", desc: "Watch the CEO agent's think loop in real-time" },
          { href: "/streams", label: "Agent Streams", desc: "Monitor any of the 11 team message streams" },
          { href: "/metrics", label: "Prometheus Metrics", desc: "LLM calls, tool usage, DLQ depth, budget" },
          { href: "/logs", label: "Log Viewer", desc: "Streaming structured logs from all containers" },
          { href: "/dlq", label: "Dead Letter Queue", desc: "Inspect and replay failed messages" },
        ].map(({ href, label, desc }) => (
          <a
            key={href}
            href={href}
            className="bg-gray-900 border border-gray-800 hover:border-blue-700 rounded-xl p-4
                       transition-colors group"
          >
            <div className="text-sm font-medium text-gray-200 group-hover:text-blue-400 mb-1">
              {label}
            </div>
            <div className="text-xs text-gray-500">{desc}</div>
          </a>
        ))}
      </div>
    </div>
  );
}
