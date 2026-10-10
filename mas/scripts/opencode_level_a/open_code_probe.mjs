import { hasSessionIdle, transportFailureCode } from "./probe_helpers.mjs";

const base = "http://127.0.0.1:4096";
const username = process.env.OPENCODE_SERVER_USERNAME || "aiat-level-a";
const password = process.env.OPENCODE_SERVER_PASSWORD;
const timeoutMs = 60_000;

class ProbeFailure extends Error {
  constructor(stage, code) {
    super(code);
    this.stage = stage;
    this.code = code;
  }
}

const report = {
  schema_version: "aiat.opencode-level-a-runtime-probe.v1",
  opencode_process: "real",
  opencode_version: "1.17.13 (image preflight required)",
  sandbox: "runsc (Compose runtime declaration; live Docker check required)",
  session_created: false,
  prompt_request_started: false,
  prompt_submitted: false,
  prompt_completed: false,
  session_idle_event_seen: false,
  fixture_marker_seen: false,
  workspace_persisted: false,
  aiat_workerrun_controller_used: false,
  external_provider_called: false,
};

const authorization = password
  ? `Basic ${Buffer.from(`${username}:${password}`).toString("base64")}`
  : "";
const headers = { authorization, "content-type": "application/json" };

function transportFailure(stage, error) {
  return new ProbeFailure(stage, transportFailureCode(error));
}

async function request(path, options = {}, stage) {
  let response;
  try {
    response = await fetch(`${base}${path}`, {
      ...options,
      headers: { ...headers, ...(options.headers || {}) },
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (error) {
    throw transportFailure(stage, error);
  }
  if (!response.ok) {
    throw new ProbeFailure(stage, `OPENCODE_HTTP_${response.status}`);
  }
  if (response.status === 204) return null;
  try {
    return await response.json();
  } catch {
    throw new ProbeFailure(stage, "OPENCODE_RESPONSE_INVALID");
  }
}

async function submitAsyncPrompt(sessionId) {
  let response;
  try {
    response = await fetch(
      `${base}/session/${encodeURIComponent(sessionId)}/prompt_async?directory=%2Fworkspace`,
      {
        method: "POST",
        headers,
        body: JSON.stringify({
          parts: [{ type: "text", text: "Reply with exactly AIAT_LEVEL_A_FIXTURE_REPLY and no other text." }],
          model: { providerID: "aiat", modelID: "omniroute-coding" },
          noReply: false,
        }),
        signal: AbortSignal.timeout(timeoutMs),
      },
    );
  } catch (error) {
    throw transportFailure("opencode.prompt_submit", error);
  }
  // OpenCode's async endpoint acknowledges with an empty 200/204 response.
  // Match the production adapter contract and never retain response content.
  if (![200, 204].includes(response.status)) {
    throw new ProbeFailure("opencode.prompt_submit", `OPENCODE_HTTP_${response.status}`);
  }
  await response.body?.cancel().catch(() => {});
}

async function observeIdle(sessionId, controller) {
  let response;
  try {
    response = await fetch(`${base}/global/event`, {
      headers: { authorization, accept: "text/event-stream" },
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(timeoutMs)]),
    });
  } catch (error) {
    return {
      seen: false,
      code: controller.signal.aborted ? "OBSERVER_STOPPED" : transportFailureCode(error),
    };
  }
  if (!response.ok || !response.body) return { seen: false, code: "EVENT_STREAM_UNAVAILABLE" };

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = "";
  try {
    while (true) {
      let result;
      try {
        result = await reader.read();
      } catch (error) {
        return {
          seen: false,
          code: controller.signal.aborted ? "OBSERVER_STOPPED" : transportFailureCode(error),
        };
      }
      if (result.done) return { seen: false, code: "EVENT_STREAM_CLOSED" };
      pending += decoder.decode(result.value, { stream: true });
      const records = pending.split(/\r?\n\r?\n/);
      pending = records.pop() || "";
      for (const record of records) {
        for (const line of record.split(/\r?\n/)) {
          if (!line.startsWith("data:")) continue;
          try {
            if (hasSessionIdle(JSON.parse(line.slice(5).trim()), sessionId)) {
              return { seen: true, code: "SESSION_IDLE_EVENT_SEEN" };
            }
          } catch {
            // Ignore malformed/unrelated events; never retain event payloads.
          }
        }
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
  }
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

let sessionId = "";
let observerController = null;
let observerTask = null;
let failure = null;
let cleanupFailed = false;

try {
  if (!password) throw new ProbeFailure("opencode.health", "RUNTIME_TEST_AUTH_NOT_CONFIGURED");
  const health = await request("/global/health", {}, "opencode.health");
  if (health?.healthy !== true && health?.status !== "healthy") {
    throw new ProbeFailure("opencode.health", "OPENCODE_HEALTH_NOT_READY");
  }
  const created = await request("/session?directory=%2Fworkspace", {
    method: "POST",
    body: JSON.stringify({
      title: "Disposable deterministic Level A provider smoke",
      permission: [{ permission: "*", pattern: "*", action: "deny" }],
    }),
  }, "opencode.session_create");
  sessionId = typeof created?.id === "string" ? created.id : "";
  if (!sessionId) throw new ProbeFailure("opencode.session_create", "OPENCODE_SESSION_ID_MISSING");
  report.session_created = true;

  observerController = new AbortController();
  observerTask = observeIdle(sessionId, observerController);
  void observerTask.catch(() => {});
  await delay(150);

  report.prompt_request_started = true;
  await submitAsyncPrompt(sessionId);
  report.prompt_submitted = true;

  if (observerTask) {
    let observation = await Promise.race([observerTask, delay(timeoutMs).then(() => null)]);
    if (!observation) {
      observerController?.abort();
      observation = await observerTask;
    }
    report.session_idle_event_seen = observation.seen;
    report.session_idle_event_code = observation.code;
  }
  if (!report.session_idle_event_seen) {
    const observerCode = report.session_idle_event_code;
    const failureCode = observerCode === "OPENCODE_REQUEST_TIMEOUT"
      ? "OPENCODE_SESSION_IDLE_TIMEOUT"
      : observerCode === "OBSERVER_STOPPED"
        ? "OPENCODE_SESSION_IDLE_TIMEOUT"
        : "OPENCODE_EVENT_STREAM_FAILED";
    throw new ProbeFailure("opencode.await_idle", failureCode);
  }
  report.prompt_completed = true;
  const messages = await request(`/session/${encodeURIComponent(sessionId)}/message?directory=%2Fworkspace`, {}, "opencode.read_response");
  report.fixture_marker_seen = JSON.stringify(messages).includes("AIAT_LEVEL_A_FIXTURE_REPLY");
  if (!report.fixture_marker_seen) throw new ProbeFailure("opencode.read_response", "FIXTURE_RESPONSE_MARKER_MISSING");
} catch (error) {
  failure = error instanceof ProbeFailure
    ? error
    : new ProbeFailure("opencode.process", "OPENCODE_PROBE_ERROR");
} finally {
  observerController?.abort();
  if (observerTask) await observerTask.catch(() => {});
  if (sessionId) {
    try {
      const response = await fetch(`${base}/session/${encodeURIComponent(sessionId)}?directory=%2Fworkspace`, {
        method: "DELETE",
        headers: { authorization },
        signal: AbortSignal.timeout(10_000),
      });
      if (!response.ok) cleanupFailed = true;
    } catch {
      cleanupFailed = true;
    }
  }
}

if (cleanupFailed && !failure) failure = new ProbeFailure("opencode.cleanup", "OPENCODE_SESSION_CLEANUP_FAILED");
report.status = failure ? "FAIL" : "PASS";
if (failure) {
  report.failure_stage = failure.stage;
  report.failure_code = failure.code;
}
console.log(JSON.stringify(report));
if (failure) process.exitCode = 1;
