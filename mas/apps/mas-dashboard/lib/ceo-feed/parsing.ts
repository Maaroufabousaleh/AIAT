import { MessageEnvelope, FeedEntry } from "./types";

export function parseFirstTimestamp(...values: Array<string | undefined>): number {
  for (const value of values) {
    if (!value) continue;
    const timestamp = Date.parse(value);
    if (Number.isFinite(timestamp)) return timestamp;
  }
  return Date.now();
}

export function entryFromRaw(raw: string, outbound = false): FeedEntry {
  let parsed: MessageEnvelope | null = null;
  try { parsed = JSON.parse(raw); } catch { /* ignore */ }
  const timestamp = parseFirstTimestamp(
    parsed?.timestamp,
    parsed?.sent_at,
    parsed?.created_at,
    parsed?.envelope?.timestamp,
    parsed?.envelope?.sent_at,
    parsed?.envelope?.created_at,
  );
  return { raw, parsed, ts: timestamp, outbound };
}

export function parseMessage(
  raw: string,
): { sender_id?: string; msg_type?: string; payload?: Record<string, unknown> } | null {
  try { return JSON.parse(raw); } catch { return null; }
}

export function cleanChatText(text: string): string {
  const withoutThoughts = text
    .replace(/<thought>[\s\S]*?(?:<\/thought>|$)/gi, "")
    .trim();
  const fromHumanNotify = withoutThoughts.replace(
    /<human\.notify>([\s\S]*?)<\/human\.notify>/gi,
    (_match, raw) => {
      try {
        const payload = JSON.parse(String(raw).trim()) as { message?: unknown };
        return typeof payload.message === "string"
          ? payload.message
          : String(raw).trim();
      } catch {
        return String(raw).trim();
      }
    },
  );
  return fromHumanNotify.replace(/<[^>\n]+>/g, "").trim();
}

export function payloadText(payload: Record<string, unknown> | undefined): string | null {
  const raw =
    payload?.result
      ? typeof payload.result === "string"
        ? payload.result
        : JSON.stringify(payload.result)
      : payload?.response
        ? String(payload.response)
        : payload?.report
          ? String(payload.report)
          : null;
  return raw ? cleanChatText(raw) : null;
}

export function progressText(payload: Record<string, unknown> | undefined): string | null {
  const raw = payload?.detail ?? payload?.summary ?? payload?.message ?? payload?.stage;
  return raw == null ? null : cleanChatText(String(raw));
}
