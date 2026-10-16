import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { beforeEach, describe, it } from "node:test";
import ts from "typescript";

const TOOL_SECRET = "test-tool-secret";
const state = { calls: [], responses: new Map(), fetchError: null };

async function mockedFetch(url, init) {
  const parsedUrl = new URL(String(url));
  state.calls.push({ url: parsedUrl, init });
  if (state.fetchError) throw state.fetchError;
  const response = state.responses.get(parsedUrl.pathname);
  if (!response) throw new Error(`Unexpected request path: ${parsedUrl.pathname}`);
  return response;
}

function loadRoute() {
  const sourcePath = fileURLToPath(
    new URL("../app/api/tools/route.ts", import.meta.url),
  );
  const source = readFileSync(sourcePath, "utf8");
  const javascript = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      esModuleInterop: true,
    },
    fileName: sourcePath,
  }).outputText;
  const routeModule = { exports: {} };
  runInNewContext(javascript, {
    exports: routeModule.exports,
    module: routeModule,
    require(specifier) {
      if (specifier === "next/server") {
        return { NextResponse: { json: (body, init = {}) => Response.json(body, init) } };
      }
      throw new Error(`Unexpected route dependency: ${specifier}`);
    },
    process: { env: { TOOL_SERVICE_URL: "http://tool-service.test", TOOL_SECRET } },
    fetch: mockedFetch,
    Response,
    URL,
  }, { filename: sourcePath });
  return routeModule.exports;
}

const route = loadRoute();

function jsonResponse(body, status = 200) {
  return Response.json(body, { status });
}

async function result(response) {
  return { status: response.status, body: await response.json() };
}

beforeEach(() => {
  state.calls = [];
  state.responses = new Map([
    ["/tools", jsonResponse({ tools: [], count: 0 })],
    ["/health", jsonResponse({ status: "ok", tools_registered: 0 })],
  ]);
  state.fetchError = null;
});

describe("tools catalogue BFF", () => {
  it("surfaces an upstream catalogue failure even when health is healthy", async () => {
    state.responses.set(
      "/tools",
      jsonResponse({ detail: "private upstream diagnostic", credential: "hidden-value" }, 503),
    );
    state.responses.set(
      "/health",
      jsonResponse({ status: "ok", tools_registered: 4 }),
    );

    const response = await result(await route.GET());

    assert.equal(response.status, 503);
    assert.deepEqual(response.body, {
      error: "Tool Service catalogue is temporarily unavailable.",
    });
    assert.equal(JSON.stringify(response.body).includes("private upstream"), false);
    assert.equal(JSON.stringify(response.body).includes("hidden-value"), false);
    assert.equal(JSON.stringify(response.body).includes(TOOL_SECRET), false);
    assert.equal(state.calls.length, 2);
    const toolsCall = state.calls.find((call) => call.url.pathname === "/tools");
    assert.equal(toolsCall.init.headers.Authorization, `Bearer ${TOOL_SECRET}`);
  });

  it("preserves 401 and 403 access denials", async () => {
    for (const status of [401, 403]) {
      state.responses.set("/tools", jsonResponse({ detail: "private auth detail" }, status));
      const response = await result(await route.GET());

      assert.equal(response.status, status);
      assert.deepEqual(response.body, { error: "Tools access denied", status });
      assert.equal(JSON.stringify(response.body).includes("private auth detail"), false);
      state.responses.set("/tools", jsonResponse({ tools: [], count: 0 }));
    }

    state.responses.set("/health", jsonResponse({ detail: "private auth detail" }, 403));
    const healthDenied = await result(await route.GET());
    assert.equal(healthDenied.status, 403);
    assert.deepEqual(healthDenied.body, { error: "Tools access denied", status: 403 });
  });

  it("normalizes the valid Tool Service manifest without exposing credentials", async () => {
    state.responses.set("/tools", jsonResponse({
      tools: [{ tool_name: "repo.read", tool_group: "Development", description: "Read a repository" }],
      groups: ["Development"],
      count: 1,
    }));
    state.responses.set("/health", jsonResponse({ status: "ok", tools_registered: 1 }));

    const response = await result(await route.GET());

    assert.equal(response.status, 200);
    assert.deepEqual(response.body, {
      tools: [{
        tool_name: "repo.read",
        tool_group: "Development",
        description: "Read a repository",
        name: "repo.read",
        group: "Development",
      }],
      groups: ["Development"],
      health: { status: "ok", tools_registered: 1 },
    });
    assert.equal(JSON.stringify(response.body).includes(TOOL_SECRET), false);
    const toolsCall = state.calls.find((call) => call.url.pathname === "/tools");
    const healthCall = state.calls.find((call) => call.url.pathname === "/health");
    assert.equal(toolsCall.init.headers.Authorization, `Bearer ${TOOL_SECRET}`);
    assert.equal(healthCall.init.headers?.Authorization, undefined);
  });

  it("maps a rejected Tool Service request to a bounded gateway error", async () => {
    state.fetchError = new Error(`request failed: ${TOOL_SECRET}`);

    const response = await result(await route.GET());

    assert.equal(response.status, 503);
    assert.deepEqual(response.body, {
      error: "Tool Service catalogue is temporarily unavailable.",
    });
    assert.equal(JSON.stringify(response.body).includes(TOOL_SECRET), false);
  });
});
