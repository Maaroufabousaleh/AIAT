import http from "node:http";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const host = process.env.AIAT_FIXTURE_HOST || "0.0.0.0";
const port = Number.parseInt(process.env.AIAT_FIXTURE_PORT || "8000", 10);
const expectedKey = process.env.AIAT_FIXTURE_PROVIDER_KEY || "aiat-level-a-provider-dummy";
const expectedModel = "fixture-model";
const maxBodyBytes = 1024 * 1024;
const maxMessages = 100;
const maxTools = 64;
const maxToolReplyBytes = 64 * 1024;
const requests = [];

export const TOOL_LOOP_SCENARIO = "AIAT_LEVEL_A_WORKSPACE_TOOL_LOOP";
export const TOOL_LOOP_FINAL =
  "AIAT_LEVEL_A_WORKSPACE_TOOL_LOOP_FINISHED. This deterministic provider fixture is not a WorkerRun certificate.";

const toolOperations = [
  {
    id: "aiat-fixture-write-source-v1",
    toolName: "opencode.workspace_write",
    arguments: {
      path: "expenses.py",
      content: "import csv\nfrom collections import defaultdict\n\n\ndef totals_by_category(csv_text):\n    totals = defaultdict(float)\n    for row in csv.DictReader(csv_text.splitlines()):\n        totals[row[\"category\"]] += float(row[\"amount\"])\n    return dict(totals)\n",
    },
  },
  {
    id: "aiat-fixture-write-sample-v1",
    toolName: "opencode.workspace_write",
    arguments: {
      path: "sample_expenses.csv",
      content: "category,amount\nfood,2.50\ntravel,7.00\nfood,1.25\n",
    },
  },
  {
    id: "aiat-fixture-write-test-v1",
    toolName: "opencode.workspace_write",
    arguments: {
      path: "test_expenses.py",
      content: "from expenses import totals_by_category\n\n\ndef test_totals_by_category_from_csv():\n    source = \"category,amount\\nfood,2.50\\ntravel,7\\nfood,1.25\\n\"\n    assert totals_by_category(source) == {\"food\": 3.75, \"travel\": 7.0}\n",
    },
  },
  {
    id: "aiat-fixture-write-readme-v1",
    toolName: "opencode.workspace_write",
    arguments: {
      path: "README.md",
      content: "# Expense totals\n\n`totals_by_category(csv_text)` adds CSV expense amounts by category. The CSV header must include `category` and `amount`.\n\nRun the included test with `pytest -q test_expenses.py`.\n",
    },
  },
  {
    id: "aiat-fixture-pytest-v1",
    toolName: "opencode.workspace_pytest",
    arguments: { path: "test_expenses.py" },
  },
];

function contentText(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter((part) => part && typeof part === "object" && part.type === "text" && typeof part.text === "string")
    .map((part) => part.text)
    .join("");
}

function facadeFunctionName(tools) {
  if (!Array.isArray(tools) || tools.length > maxTools) return null;
  const matches = tools
    .filter((tool) => tool?.type === "function" && typeof tool?.function?.name === "string")
    .map((tool) => tool.function.name)
    .filter((name) => /^aiat-[0-9a-f]{32}_aiat_tool$/.test(name));
  return matches.length === 1 ? matches[0] : null;
}

function parseFacadeCall(call, facadeName) {
  if (
    call?.type !== "function" ||
    call?.function?.name !== facadeName ||
    typeof call?.id !== "string" ||
    call.id.length > 128 ||
    typeof call?.function?.arguments !== "string" ||
    Buffer.byteLength(call.function.arguments) > 4096
  ) {
    return null;
  }
  try {
    const args = JSON.parse(call.function.arguments);
    if (!args || typeof args !== "object" || Array.isArray(args)) return null;
    if (typeof args.tool_name !== "string" || !args.arguments || typeof args.arguments !== "object") return null;
    return {
      id: call.id,
      toolName: args.tool_name,
      arguments: args.arguments,
    };
  } catch {
    return null;
  }
}

function parseToolReply(message) {
  const raw = contentText(message.content);
  if (!raw || Buffer.byteLength(raw) > maxToolReplyBytes) return { valid: false, success: false };
  try {
    const response = JSON.parse(raw);
    if (!response || typeof response !== "object" || Array.isArray(response)) {
      return { valid: false, success: false };
    }
    return { valid: true, success: response.success === true, response };
  } catch {
    return { valid: false, success: false };
  }
}

function toolReplyMatches(operation, reply) {
  if (!reply?.valid || !reply.success) return false;
  const response = reply.response;
  if (response.tool_name !== operation.toolName || !response.result || typeof response.result !== "object") {
    return false;
  }
  const result = response.result;
  if (operation.toolName === "opencode.workspace_write") {
    return result.path === operation.arguments.path && result.bytes_written === Buffer.byteLength(operation.arguments.content);
  }
  return (
    result.path === operation.arguments.path &&
    result.certification_status === "PASSED" &&
    result.exit_code === 0 &&
    Number.isInteger(result.passed) &&
    result.passed > 0 &&
    result.failed === 0
  );
}

/** Normalize only the fields needed to select the fixed fixture scenario. */
export function normalizeRequest(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new TypeError("fixture request must be an object");
  }
  const inputMessages = Array.isArray(payload.messages) ? payload.messages : [];
  if (inputMessages.length > maxMessages) throw new TypeError("fixture message limit exceeded");

  const facadeName = facadeFunctionName(payload.tools);
  const scenarioSelected = inputMessages.some(
    (message) => message?.role === "user" && contentText(message.content) === TOOL_LOOP_SCENARIO,
  );
  const calls = [];
  const callIds = new Set();
  let duplicateCallId = false;
  const toolReplies = new Map();
  let duplicateToolReplyId = false;
  for (const message of inputMessages) {
    if (!message || typeof message !== "object") continue;
    if (message.role === "assistant" && Array.isArray(message.tool_calls)) {
      if (message.tool_calls.length > 32) throw new TypeError("fixture tool-call limit exceeded");
      for (const call of message.tool_calls) {
        const normalized = parseFacadeCall(call, facadeName);
        if (normalized) {
          if (callIds.has(normalized.id)) duplicateCallId = true;
          callIds.add(normalized.id);
          calls.push(normalized);
        }
      }
    } else if (message.role === "tool" && typeof message.tool_call_id === "string") {
      if (message.tool_call_id.length <= 128) {
        if (toolReplies.has(message.tool_call_id)) duplicateToolReplyId = true;
        toolReplies.set(message.tool_call_id, parseToolReply(message));
      }
    }
  }
  return {
    model: typeof payload.model === "string" ? payload.model : "",
    stream: payload.stream === true,
    scenarioSelected,
    facadeName,
    calls,
    duplicateCallId,
    toolReplies,
    duplicateToolReplyId,
  };
}

function operationForRequest(normalized) {
  if (normalized.duplicateCallId || normalized.duplicateToolReplyId) {
    return { kind: "invalid_history" };
  }
  const callsById = new Map(normalized.calls.map((call) => [call.id, call]));
  for (const operation of toolOperations) {
    const recorded = callsById.get(operation.id);
    if (!recorded) return { kind: "tool_call", operation };
    if (
      recorded.toolName !== operation.toolName ||
      JSON.stringify(recorded.arguments) !== JSON.stringify(operation.arguments)
    ) return { kind: "invalid_history" };
    const reply = normalized.toolReplies.get(operation.id);
    if (!reply) return { kind: "ambiguous_history" };
    if (!reply.valid || !reply.success || !toolReplyMatches(operation, reply)) {
      return { kind: "tool_failed" };
    }
  }
  return { kind: "final" };
}

/** Selects a fixed response; user prompt text is never used as tool input. */
export function selectFixtureResponse(payload) {
  const normalized = normalizeRequest(payload);
  if (!normalized.scenarioSelected) return { kind: "text_reply" };
  if (!normalized.facadeName) return { kind: "facade_unavailable" };
  return operationForRequest(normalized);
}

function completionBody(message, finishReason, id = "chatcmpl-aiat-level-a-fixture") {
  return {
    id,
    object: "chat.completion",
    created: 0,
    model: expectedModel,
    choices: [{ index: 0, finish_reason: finishReason, message }],
    usage: { prompt_tokens: 8, completion_tokens: 5, total_tokens: 13 },
  };
}

export function responseForSelection(selection, facadeName) {
  if (selection.kind === "text_reply") {
    return completionBody({ role: "assistant", content: "AIAT_LEVEL_A_FIXTURE_REPLY" }, "stop");
  }
  if (selection.kind === "tool_call") {
    const operation = selection.operation;
    return completionBody({
      role: "assistant",
      content: null,
      tool_calls: [{
        id: operation.id,
        type: "function",
        function: {
          name: facadeName,
          arguments: JSON.stringify({ tool_name: operation.toolName, arguments: operation.arguments }),
        },
      }],
    }, "tool_calls");
  }
  if (selection.kind === "final") {
    return completionBody({ role: "assistant", content: TOOL_LOOP_FINAL }, "stop");
  }
  throw new TypeError("unsupported fixture selection kind");
}

export function completionChunks(body) {
  const base = { id: body.id, object: "chat.completion.chunk", created: 0, model: expectedModel };
  const message = body.choices[0].message;
  if (Array.isArray(message.tool_calls)) {
    const call = message.tool_calls[0];
    return [
      {
        ...base,
        choices: [{
          index: 0,
          delta: {
            role: "assistant",
            tool_calls: [{
              index: 0,
              id: call.id,
              type: "function",
              function: { name: call.function.name, arguments: "" },
            }],
          },
          finish_reason: null,
        }],
      },
      {
        ...base,
        choices: [{
          index: 0,
          delta: { tool_calls: [{ index: 0, function: { arguments: call.function.arguments } }] },
          finish_reason: null,
        }],
      },
      { ...base, choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }], usage: body.usage },
    ];
  }
  return [
    {
      ...base,
      choices: [{ index: 0, delta: { role: "assistant", content: message.content }, finish_reason: null }],
    },
    { ...base, choices: [{ index: 0, delta: {}, finish_reason: "stop" }], usage: body.usage },
  ];
}

function sendJson(response, statusCode, value) {
  response.writeHead(statusCode, { "content-type": "application/json", "cache-control": "no-store" });
  response.end(JSON.stringify(value));
}

async function readJson(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > maxBodyBytes) {
      throw new Error("request body limit exceeded");
    }
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function sendStream(response, body) {
  response.writeHead(200, {
    "content-type": "text/event-stream",
    "cache-control": "no-cache, no-store",
    connection: "keep-alive",
  });
  for (const chunk of completionChunks(body)) response.write(`data: ${JSON.stringify(chunk)}\n\n`);
  response.end("data: [DONE]\n\n");
}

const server = http.createServer(async (request, response) => {
  const url = new URL(request.url || "/", `http://${request.headers.host || "localhost"}`);
  if (request.method === "GET" && url.pathname === "/health") {
    return sendJson(response, 200, { status: "healthy", fixture: true });
  }
  if (request.method === "GET" && url.pathname === "/__fixture/report") {
    return sendJson(response, 200, {
      schema_version: "aiat.opencode-level-a-provider-fixture.v1",
      request_count: requests.length,
      requests: requests.map((row) => ({ ...row })),
    });
  }
  if (request.method === "GET" && url.pathname === "/v1/models") {
    return sendJson(response, 200, {
      object: "list",
      data: [{ id: expectedModel, object: "model", created: 0, owned_by: "aiat-level-a-fixture" }],
    });
  }
  if (request.method !== "POST" || url.pathname !== "/v1/chat/completions") {
    return sendJson(response, 404, { error: { message: "fixture endpoint not found" } });
  }

  let payload;
  try {
    payload = await readJson(request);
  } catch {
    return sendJson(response, 400, { error: { message: "invalid or oversized fixture request" } });
  }
  const auth = request.headers.authorization || "";
  let normalized;
  let selection;
  try {
    normalized = normalizeRequest(payload);
    selection = selectFixtureResponse(payload);
  } catch {
    return sendJson(response, 400, { error: { message: "invalid fixture request shape" } });
  }
  const model = normalized.model;
  requests.push({
    method: request.method,
    path: url.pathname,
    model,
    authorized: auth === `Bearer ${expectedKey}`,
    source: request.socket.remoteAddress || "unknown",
    body_bytes: Buffer.byteLength(JSON.stringify(payload)),
  });
  if (requests.length > 100) requests.shift();
  if (auth !== `Bearer ${expectedKey}`) {
    return sendJson(response, 401, { error: { message: "fixture authorization rejected" } });
  }
  if (model !== expectedModel) {
    return sendJson(response, 400, { error: { message: "fixture model is not configured" } });
  }
  if (selection.kind === "facade_unavailable") {
    return sendJson(response, 400, { error: { message: "fixture scenario requires one advertised AIAT tool facade" } });
  }
  if (selection.kind === "ambiguous_history" || selection.kind === "invalid_history" || selection.kind === "tool_failed") {
    return sendJson(response, 409, { error: { message: "fixture tool history is incomplete, inconsistent, or unsuccessful" } });
  }

  if (selection.kind === "text_reply") {
    const textBody = responseForSelection(selection, normalized.facadeName);
    if (payload.stream === true) return sendStream(response, textBody);
    return sendJson(response, 200, textBody);
  }
  const body = responseForSelection(selection, normalized.facadeName);
  if (payload.stream === true) return sendStream(response, body);
  return sendJson(response, 200, body);
});

server.requestTimeout = 10_000;
server.headersTimeout = 5_000;

function stop() {
  server.close(() => process.exit(0));
}

export function startFixture() {
  server.listen(port, host);
  process.on("SIGTERM", stop);
  process.on("SIGINT", stop);
  return server;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  startFixture();
}
