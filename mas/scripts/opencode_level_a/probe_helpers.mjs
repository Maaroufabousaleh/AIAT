export function transportFailureCode(error) {
  const name = error && typeof error.name === "string" ? error.name : "";
  if (name === "AbortError" || name === "TimeoutError") return "OPENCODE_REQUEST_TIMEOUT";
  return "OPENCODE_REQUEST_TRANSPORT_ERROR";
}

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function sessionIdFrom(value) {
  if (!isRecord(value)) return "";
  const candidate = value.sessionID ?? value.sessionId ?? value.session_id;
  return typeof candidate === "string" ? candidate : "";
}

export function hasSessionIdle(value, expectedSessionId) {
  if (!isRecord(value) && !Array.isArray(value)) return false;
  const queue = [{ value, depth: 0 }];
  let visited = 0;
  while (queue.length > 0 && visited < 128) {
    const current = queue.shift();
    if (!current || current.depth > 5) continue;
    const node = current.value;
    visited += 1;
    if (Array.isArray(node)) {
      for (const item of node) {
        if (isRecord(item) || Array.isArray(item)) queue.push({ value: item, depth: current.depth + 1 });
      }
      continue;
    }
    if (!isRecord(node)) continue;

    if (node.type === "session.idle" || node.type === "session.status") {
      const data = [node.properties, node.data, node.payload, node].find(isRecord) || node;
      const sessionID = sessionIdFrom(data) || sessionIdFrom(node);
      if (sessionID === expectedSessionId) {
        if (node.type === "session.idle") return true;
        const status = data.status ?? node.status;
        if (isRecord(status) && status.type === "idle") return true;
      }
    }

    for (const key of ["payload", "data", "event", "properties"]) {
      const nested = node[key];
      if (isRecord(nested) || Array.isArray(nested)) queue.push({ value: nested, depth: current.depth + 1 });
    }
  }
  return false;
}
