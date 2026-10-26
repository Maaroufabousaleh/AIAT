import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import path from "node:path";
import ts from "typescript";

const dashboardRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const instanceId = "50000000-0000-4000-8000-000000000501";
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

async function invoke(route, method, payload) {
  const request = new Request(`http://dashboard.test/api/flows/instances/${instanceId}/node-action`, {
    method,
    headers: payload === undefined ? {} : { "content-type": "application/json" },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
  const response = await route(
    request,
    { params: Promise.resolve({ id: instanceId }) },
  );
  return { status: response.status, body: await response.json() };
}

const mode = process.argv[2];
let result;
if (mode === "advance-research") {
  result = await invoke(nodeAction.POST, "POST", { node_id: "research", action: "advance" });
} else if (mode === "complete-research") {
  const runId = process.argv[3];
  if (!runId) throw new Error("complete-research requires the synthetic persisted WorkerRun ID");
  result = await invoke(nodeAction.POST, "POST", {
    node_id: "research",
    action: "complete",
    worker_run_id: runId,
  });
} else if (mode === "read-instance") {
  const request = new Request(`http://dashboard.test/api/flows/instances/${instanceId}`);
  const response = await instanceRoute.GET(
    request,
    { params: Promise.resolve({ id: instanceId }) },
  );
  result = { status: response.status, body: await response.json() };
} else {
  throw new Error("mode must be advance-research, complete-research, or read-instance");
}

process.stdout.write(`${JSON.stringify(result)}\n`);
