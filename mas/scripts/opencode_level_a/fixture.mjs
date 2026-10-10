import http from "node:http";

const host = process.env.AIAT_FIXTURE_HOST || "0.0.0.0";
const port = Number.parseInt(process.env.AIAT_FIXTURE_PORT || "8000", 10);
const expectedKey = process.env.AIAT_FIXTURE_PROVIDER_KEY || "aiat-level-a-provider-dummy";
const expectedModel = "fixture-model";
const maxBodyBytes = 1024 * 1024;
const requests = [];

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
  const model = typeof payload?.model === "string" ? payload.model : "";
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

  if (payload.stream === true) {
    response.writeHead(200, {
      "content-type": "text/event-stream",
      "cache-control": "no-cache, no-store",
      connection: "keep-alive",
    });
    response.write(`data: ${JSON.stringify({
      id: "chatcmpl-aiat-level-a-fixture",
      object: "chat.completion.chunk",
      created: 0,
      model: expectedModel,
      choices: [{ index: 0, delta: { role: "assistant", content: "AIAT_LEVEL_A_FIXTURE_REPLY" }, finish_reason: null }],
    })}\n\n`);
    response.write(`data: ${JSON.stringify({
      id: "chatcmpl-aiat-level-a-fixture",
      object: "chat.completion.chunk",
      created: 0,
      model: expectedModel,
      choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
      usage: { prompt_tokens: 8, completion_tokens: 5, total_tokens: 13 },
    })}\n\n`);
    response.end("data: [DONE]\n\n");
    return;
  }

  return sendJson(response, 200, {
    id: "chatcmpl-aiat-level-a-fixture",
    object: "chat.completion",
    created: 0,
    model: expectedModel,
    choices: [{ index: 0, finish_reason: "stop", message: { role: "assistant", content: "AIAT_LEVEL_A_FIXTURE_REPLY" } }],
    usage: { prompt_tokens: 8, completion_tokens: 5, total_tokens: 13 },
  });
});

server.requestTimeout = 10_000;
server.headersTimeout = 5_000;
server.listen(port, host);

function stop() {
  server.close(() => process.exit(0));
}
process.on("SIGTERM", stop);
process.on("SIGINT", stop);
