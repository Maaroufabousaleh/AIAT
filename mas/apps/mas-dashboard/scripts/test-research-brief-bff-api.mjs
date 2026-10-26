import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import path from "node:path";
import ts from "typescript";

const dashboardRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const instanceId = "50000000-0000-4000-8000-000000000501";
const projectId = "50000000-0000-4000-8000-000000000503";
const apiDestination = new URL(process.env.ORCHESTRATOR_URL ?? "");
if (
  apiDestination.protocol !== "http:" ||
  apiDestination.hostname !== "127.0.0.1" ||
  !apiDestination.port ||
  Number(apiDestination.port) < 32768 ||
  apiDestination.username ||
  apiDestination.password ||
  apiDestination.pathname !== "/" ||
  apiDestination.search ||
  apiDestination.hash
) {
  throw new Error("The Research Brief BFF harness accepts only an ephemeral loopback API destination");
}

function compileTypeScript(sourcePath, injectedRequire) {
  const source = readFileSync(sourcePath, "utf8");
  const javascript = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      esModuleInterop: true,
    },
    fileName: sourcePath,
  }).outputText;
  const compiled = { exports: {} };
  runInNewContext(javascript, {
    exports: compiled.exports,
    module: compiled,
    require: injectedRequire,
    process,
    fetch,
    Request,
    Response,
    URL,
    URLSearchParams,
    setTimeout,
    clearTimeout,
  }, { filename: sourcePath });
  return compiled.exports;
}

const orchestrator = compileTypeScript(
  path.join(dashboardRoot, "lib", "orchestrator.ts"),
  (specifier) => {
    throw new Error(`Unexpected orchestrator dependency: ${specifier}`);
  },
);

const nextServer = {
  NextResponse: {
    json(body, init = {}) {
      return new Response(JSON.stringify(body), {
        status: init.status ?? 200,
        headers: { "content-type": "application/json", ...(init.headers ?? {}) },
      });
    },
  },
};

const nodeAction = compileTypeScript(
  path.join(dashboardRoot, "app", "api", "flows", "instances", "[id]", "node-action", "route.ts"),
  (specifier) => {
    if (specifier === "next/server") return nextServer;
    if (specifier === "@/lib/orchestrator") return orchestrator;
    throw new Error(`Unexpected node-action BFF dependency: ${specifier}`);
  },
);

const instanceRoute = compileTypeScript(
  path.join(dashboardRoot, "app", "api", "flows", "instances", "[id]", "route.ts"),
  (specifier) => {
    if (specifier === "next/server") return nextServer;
    if (specifier === "@/lib/orchestrator") return orchestrator;
    throw new Error(`Unexpected flow-instance BFF dependency: ${specifier}`);
  },
);

const flowActionRoute = compileTypeScript(
  path.join(dashboardRoot, "app", "api", "flows", "instances", "[id]", "action", "route.ts"),
  (specifier) => {
    if (specifier === "next/server") return nextServer;
    if (specifier === "@/lib/orchestrator") return orchestrator;
    throw new Error(`Unexpected flow-action BFF dependency: ${specifier}`);
  },
);

const runRoute = compileTypeScript(
  path.join(dashboardRoot, "app", "api", "governance", "runs", "[run_id]", "route.ts"),
  (specifier) => {
    if (specifier === "next/server") return nextServer;
    if (specifier === "@/lib/orchestrator") return orchestrator;
    throw new Error(`Unexpected WorkerRun BFF dependency: ${specifier}`);
  },
);

const runResourceRoute = compileTypeScript(
  path.join(dashboardRoot, "app", "api", "governance", "runs", "[run_id]", "[resource]", "route.ts"),
  (specifier) => {
    if (specifier === "next/server") return nextServer;
    if (specifier === "@/lib/orchestrator") return orchestrator;
    throw new Error(`Unexpected WorkerRun-resource BFF dependency: ${specifier}`);
  },
);

const projectRoute = compileTypeScript(
  path.join(dashboardRoot, "app", "api", "projects", "[id]", "route.ts"),
  (specifier) => {
    if (specifier === "next/server") return nextServer;
    if (specifier === "@/lib/orchestrator") return orchestrator;
    throw new Error(`Unexpected project BFF dependency: ${specifier}`);
  },
);

const projectEvidenceRoute = compileTypeScript(
  path.join(dashboardRoot, "app", "api", "projects", "[id]", "evidence", "route.ts"),
  (specifier) => {
    if (specifier === "next/server") return nextServer;
    if (specifier === "@/lib/orchestrator") return orchestrator;
    throw new Error(`Unexpected project-evidence BFF dependency: ${specifier}`);
  },
);

const projectArtifactsRoute = compileTypeScript(
  path.join(dashboardRoot, "app", "api", "projects", "[id]", "artifacts", "route.ts"),
  (specifier) => {
    if (specifier === "next/server") return nextServer;
    if (specifier === "@/lib/orchestrator") return orchestrator;
    throw new Error(`Unexpected project-artifacts BFF dependency: ${specifier}`);
  },
);

const projectTransitionRoute = compileTypeScript(
  path.join(dashboardRoot, "app", "api", "projects", "[id]", "transition", "route.ts"),
  (specifier) => {
    if (specifier === "next/server") return nextServer;
    if (specifier === "@/lib/orchestrator") return orchestrator;
    throw new Error(`Unexpected project-transition BFF dependency: ${specifier}`);
  },
);

async function invoke(route, method, routePath, payload, params = {}) {
  const request = new Request(`http://dashboard.test${routePath}`, {
    method,
    headers: payload === undefined ? {} : { "content-type": "application/json" },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
  const response = await route(request, { params: Promise.resolve(params) });
  return { status: response.status, body: await response.json() };
}

function checkedRunId(value) {
  if (typeof value !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)) {
    throw new Error("A synthetic WorkerRun UUID is required");
  }
  return value;
}

const mode = process.argv[2];
let result;
if (mode === "complete-research") {
  const runId = checkedRunId(process.argv[3]);
  result = await invoke(nodeAction.POST, "POST", `/api/flows/instances/${instanceId}/node-action`, {
    node_id: "research",
    action: "complete",
    worker_run_id: runId,
  }, { id: instanceId });
} else if (mode === "start-flow") {
  result = await invoke(flowActionRoute.POST, "POST", `/api/flows/instances/${instanceId}/action`, { action: "start" }, { id: instanceId });
} else if (mode === "complete-start") {
  result = await invoke(nodeAction.POST, "POST", `/api/flows/instances/${instanceId}/node-action`, { node_id: "start", action: "complete" }, { id: instanceId });
} else if (mode === "approve-review") {
  result = await invoke(nodeAction.POST, "POST", `/api/flows/instances/${instanceId}/node-action`, { node_id: "review", action: "complete", decision: "approved" }, { id: instanceId });
} else if (mode === "complete-report" || mode === "fail-report") {
  const runId = checkedRunId(process.argv[3]);
  result = await invoke(nodeAction.POST, "POST", `/api/flows/instances/${instanceId}/node-action`, {
    node_id: "report",
    action: mode === "complete-report" ? "complete" : "fail",
    worker_run_id: runId,
    ...(mode === "fail-report" ? { error: "The mediated report effect was not confirmed." } : {}),
  }, { id: instanceId });
} else if (mode === "wait-run") {
  const runId = checkedRunId(process.argv[3]);
  const deadline = Date.now() + 10_000;
  let observed;
  do {
    observed = await invoke(
      runRoute.GET,
      "GET",
      `/api/governance/runs/${encodeURIComponent(runId)}`,
      undefined,
      { run_id: runId },
    );
    const state = String(observed.body?.state ?? "").toUpperCase();
    if (observed.status !== 200 || ["SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"].includes(state)) {
      result = observed;
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 25));
  } while (Date.now() < deadline);
  if (!result) throw new Error("Synthetic WorkerRun did not reach a terminal state in the bounded wait");
} else if (mode === "read-run-resource") {
  const runId = checkedRunId(process.argv[3]);
  const resource = process.argv[4];
  if (!["artifacts", "usage", "tool-effects"].includes(resource)) throw new Error("Unsupported WorkerRun read resource");
  result = await invoke(
    runResourceRoute.GET,
    "GET",
    `/api/governance/runs/${encodeURIComponent(runId)}/${resource}`,
    undefined,
    { run_id: runId, resource },
  );
} else if (mode === "read-project-evidence") {
  result = await invoke(projectEvidenceRoute.GET, "GET", `/api/projects/${projectId}/evidence`, undefined, { id: projectId });
} else if (mode === "read-project-artifacts") {
  result = await invoke(projectArtifactsRoute.GET, "GET", `/api/projects/${projectId}/artifacts`, undefined, { id: projectId });
} else if (mode === "read-project") {
  result = await invoke(projectRoute.GET, "GET", `/api/projects/${projectId}`, undefined, { id: projectId });
} else if (mode === "transition-project") {
  const event = process.argv[3];
  if (!["all_sprints_done", "retrospective_done", "kpi_saved"].includes(event)) throw new Error("Unsupported synthetic project transition");
  result = await invoke(projectTransitionRoute.POST, "POST", `/api/projects/${projectId}/transition`, { event }, { id: projectId });
} else if (mode === "read-instance") {
  result = await invoke(instanceRoute.GET, "GET", `/api/flows/instances/${instanceId}`, undefined, { id: instanceId });
} else {
  throw new Error("Unsupported hermetic Research Brief BFF harness mode");
}

process.stdout.write(`${JSON.stringify(result)}\n`);
