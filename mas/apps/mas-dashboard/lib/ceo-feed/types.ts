export interface MessageEnvelope {
  message_id?: string;
  correlation_id?: string;
  parent_id?: string;
  type?: string;
  msg_type?: string;
  message_type?: string;
  sender_id?: string;
  sender_team?: string;
  project_id?: string;
  payload?: Record<string, unknown>;
  timestamp?: string;
  sent_at?: string;
  created_at?: string;
  envelope?: MessageEnvelope;
}

export interface FeedEntry {
  parsed: MessageEnvelope | null;
  raw: string;
  ts: number;
  cycleKey?: string;
  outbound?: boolean;
}

export interface RecentStreamEntry {
  entry_id: string;
  envelope: string;
}

export type KnownType =
  | "ALL"
  | "TOOL_CALL"
  | "TOOL_RESULT"
  | "DIRECTIVE"
  | "REPORT"
  | "RESPONSE"
  | "VETO"
  | "HEARTBEAT"
  | "PROGRESS"
  | "OUTBOUND"
  | "UNKNOWN";

export const KNOWN_TYPES: KnownType[] = [
  "ALL",
  "TOOL_CALL",
  "TOOL_RESULT",
  "DIRECTIVE",
  "REPORT",
  "RESPONSE",
  "VETO",
  "HEARTBEAT",
  "PROGRESS",
  "OUTBOUND",
  "UNKNOWN",
];
