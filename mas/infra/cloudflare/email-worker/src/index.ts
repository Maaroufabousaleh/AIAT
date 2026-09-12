/* AIAT Cloudflare Email Routing Worker.

   The email handler is deliberately small: the SMTP envelope recipient is
   authorized against an AIAT-owned D1 registry, raw MIME is written to R2,
   and a bounded metadata event is appended to D1. The HTTP surface is a
   signed, replay-protected pull API for the AIAT identity-service only.
*/

const DEFAULT_AUTH_VERSION = "aiat.mail-edge.v1";
const DEFAULT_MAX_MESSAGE_BYTES = 10 * 1024 * 1024;
const MAX_API_BODY_BYTES = 1024 * 1024;
const MAX_ADDRESS_LENGTH = 320;
const MAX_ID_LENGTH = 300;
const MAX_HEADER_LENGTH = 998;

interface D1Meta {
  changes?: number;
}

interface D1Result<T = Record<string, unknown>> {
  results: T[];
  success?: boolean;
  meta?: D1Meta;
}

interface D1Statement {
  bind(...values: unknown[]): D1Statement;
  all<T = Record<string, unknown>>(): Promise<D1Result<T>>;
  first<T = Record<string, unknown>>(): Promise<T | null>;
  run(): Promise<D1Result>;
}

interface D1Database {
  prepare(query: string): D1Statement;
  batch<T = unknown>(statements: D1Statement[]): Promise<T[]>;
}

interface R2ObjectBody {
  arrayBuffer(): Promise<ArrayBuffer>;
}

interface R2Bucket {
  put(key: string, value: ArrayBuffer | ArrayBufferView | string, options?: Record<string, unknown>): Promise<unknown>;
  get(key: string): Promise<R2ObjectBody | null>;
  delete(key: string): Promise<void>;
}

interface EmailMessage {
  readonly from: string;
  readonly to: string;
  readonly rawSize: number;
  readonly raw: ReadableStream<Uint8Array>;
  setReject(reason: string): void;
}

interface ExecutionContext {
  waitUntil(promise: Promise<unknown>): void;
}

export interface Env {
  DB: D1Database;
  MAIL_OBJECTS: R2Bucket;
  MAIL_EDGE_AUTH_SECRET: string;
  MAIL_EDGE_AUTH_VERSION?: string;
  MAIL_EDGE_MAX_MESSAGE_BYTES?: string;
  MAIL_EDGE_AUTH_TOLERANCE_SECONDS?: string;
  MAIL_EDGE_RETENTION_DAYS?: string;
  MAIL_EDGE_PROCESSED_RETENTION_DAYS?: string;
}

interface RegistryRow {
  identity_id: string;
  worker_id: string;
  address: string;
  provider_reference: string;
  idempotency_key: string;
  state: string;
}

interface MessageRow {
  message_id: string;
  event_id: string;
  provider_key: string;
  identity_id: string;
  worker_id: string;
  envelope_recipient: string;
  sender: string | null;
  subject: string;
  received_at: string;
  raw_object_key: string;
  raw_size: number;
  content_hash: string;
  processed_at: string | null;
  protected_until: string | null;
  deleted_at: string | null;
}

class EdgeError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function jsonResponse(status: number, body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

function envNumber(value: string | undefined, fallback: number, min: number, max: number): number {
  const parsed = Number(value ?? "");
  if (!Number.isSafeInteger(parsed) || parsed < min || parsed > max) return fallback;
  return parsed;
}

function authVersion(env: Env): string {
  return env.MAIL_EDGE_AUTH_VERSION?.trim() || DEFAULT_AUTH_VERSION;
}

function maxMessageBytes(env: Env): number {
  return envNumber(env.MAIL_EDGE_MAX_MESSAGE_BYTES, DEFAULT_MAX_MESSAGE_BYTES, 1024, 25 * 1024 * 1024);
}

export function normalizeEmailAddress(value: string): string {
  const candidate = String(value || "").trim();
  if (
    candidate.length === 0 ||
    candidate.length > MAX_ADDRESS_LENGTH ||
    candidate.split("@").length !== 2 ||
    /[\s<>\u0000-\u001f\u007f]/.test(candidate)
  ) {
    throw new EdgeError(422, "recipient is not a safe email address");
  }
  const at = candidate.lastIndexOf("@");
  const local = candidate.slice(0, at);
  const domain = candidate.slice(at + 1).replace(/\.+$/, "");
  if (!local || (!domain.includes(".") && !["localhost", "invalid"].includes(domain.toLowerCase()))) {
    throw new EdgeError(422, "recipient domain is not valid");
  }
  return `${local.toLocaleLowerCase()}@${domain.toLocaleLowerCase()}`;
}

function safeReference(value: unknown, label: string): string {
  const text = String(value ?? "").trim();
  if (!text || text.length > MAX_ID_LENGTH || !/^[A-Za-z0-9][A-Za-z0-9._:/+-]*$/.test(text)) {
    throw new EdgeError(422, `${label} is invalid`);
  }
  return text;
}

function safeId(value: string, label: string): string {
  const text = String(value || "");
  if (!text || text.length > MAX_ID_LENGTH || text.includes("/") || /[\u0000-\u001f\u007f]/.test(text)) {
    throw new EdgeError(422, `${label} is invalid`);
  }
  return text;
}

function base64Encode(bytes: Uint8Array): string {
  let output = "";
  const chunkSize = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    output += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return btoa(output);
}

function base64Decode(value: string): Uint8Array {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes;
}

function constantTimeEqual(left: Uint8Array, right: Uint8Array): boolean {
  if (left.length !== right.length) return false;
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) difference |= left[index] ^ right[index];
  return difference === 0;
}

async function sha256Hex(value: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", value);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function concatBytes(...parts: Uint8Array[]): Uint8Array {
  const size = parts.reduce((total, part) => total + part.length, 0);
  const output = new Uint8Array(size);
  let offset = 0;
  for (const part of parts) {
    output.set(part, offset);
    offset += part.length;
  }
  return output;
}

async function hmacSha256(secret: string, value: string): Promise<Uint8Array> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(value)));
}

async function readRequestBody(request: Request, maxBytes: number): Promise<Uint8Array> {
  const reader = request.body?.getReader();
  if (!reader) return new Uint8Array();
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const next = await reader.read();
    if (next.done) break;
    size += next.value.byteLength;
    if (size > maxBytes) {
      await reader.cancel();
      throw new EdgeError(413, "request body exceeds the bounded limit");
    }
    chunks.push(next.value);
  }
  return concatBytes(...chunks);
}

function headerMessageId(raw: Uint8Array): string | null {
  const headerText = new TextDecoder("utf-8", { fatal: false }).decode(raw.subarray(0, 128 * 1024));
  const match = headerText.match(/^Message-ID\s*:\s*([^\r\n]+)$/im);
  const value = match?.[1]?.trim() || "";
  if (!value || value.length > MAX_ID_LENGTH || /[\u0000-\u001f\u007f]/.test(value)) return null;
  return value;
}

function headerValue(raw: Uint8Array, name: string): string {
  const text = new TextDecoder("utf-8", { fatal: false }).decode(raw.subarray(0, 128 * 1024));
  const match = text.match(new RegExp(`^${name}\\s*:\\s*([^\\r\\n]*(?:\\r?\\n[ \\t]+[^\\r\\n]*)*)$`, "im"));
  return (match?.[1] || "").replace(/\r?\n[ \t]+/g, " ").trim().slice(0, MAX_HEADER_LENGTH);
}

function safeHeaderSender(raw: Uint8Array): string | null {
  const value = headerValue(raw, "From");
  const angle = value.match(/<([^<>]+)>/);
  const candidate = angle?.[1] || value.split(",")[0]?.trim() || "";
  try {
    return candidate ? normalizeEmailAddress(candidate) : null;
  } catch {
    return null;
  }
}

async function readEmailRaw(message: EmailMessage, limit: number): Promise<Uint8Array> {
  if (Number.isFinite(message.rawSize) && message.rawSize > limit) throw new EdgeError(552, "message exceeds the bounded size limit");
  const reader = message.raw.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const next = await reader.read();
    if (next.done) break;
    size += next.value.byteLength;
    if (size > limit) {
      await reader.cancel();
      throw new EdgeError(552, "message exceeds the bounded size limit");
    }
    chunks.push(next.value);
  }
  return concatBytes(...chunks);
}

async function consumeNonce(env: Env, nonce: string, expiresAt: number): Promise<boolean> {
  await env.DB.prepare("DELETE FROM edge_nonces WHERE expires_at <= ?").bind(Math.floor(Date.now() / 1000)).run();
  const result = await env.DB.prepare("INSERT OR IGNORE INTO edge_nonces (nonce, expires_at) VALUES (?, ?)")
    .bind(nonce, expiresAt)
    .run();
  return Number(result.meta?.changes || 0) === 1;
}

async function verifySignedRequest(request: Request, env: Env, body: Uint8Array): Promise<boolean> {
  const version = request.headers.get("X-AIAT-Mail-Edge-Version") || "";
  const timestampText = request.headers.get("X-AIAT-Mail-Edge-Timestamp") || "";
  const nonce = request.headers.get("X-AIAT-Mail-Edge-Nonce") || "";
  const signatureText = request.headers.get("X-AIAT-Mail-Edge-Signature") || "";
  const timestamp = Number(timestampText);
  const tolerance = envNumber(env.MAIL_EDGE_AUTH_TOLERANCE_SECONDS, 300, 30, 900);
  if (
    !env.MAIL_EDGE_AUTH_SECRET ||
    env.MAIL_EDGE_AUTH_SECRET.length < 20 ||
    version !== authVersion(env) ||
    !/^\d+$/.test(timestampText) ||
    !Number.isSafeInteger(timestamp) ||
    Math.abs(Date.now() / 1000 - timestamp) > tolerance ||
    !/^[A-Za-z0-9-]{16,128}$/.test(nonce) ||
    !signatureText
  ) return false;
  const digest = await sha256Hex(body);
  const url = new URL(request.url);
  const canonical = `${authVersion(env)}\n${request.method.toUpperCase()}\n${url.pathname}${url.search}\n${timestampText}\n${nonce}\n${digest}`;
  let provided: Uint8Array;
  try {
    provided = base64Decode(signatureText);
  } catch {
    return false;
  }
  const expected = await hmacSha256(env.MAIL_EDGE_AUTH_SECRET || "", canonical);
  if (!constantTimeEqual(provided, expected)) return false;
  return consumeNonce(env, nonce, timestamp + tolerance);
}

function requestBodyObject(body: Uint8Array): Record<string, unknown> {
  if (body.length === 0) return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(new TextDecoder().decode(body));
  } catch {
    throw new EdgeError(400, "request body is invalid");
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new EdgeError(400, "request body is invalid");
  return parsed as Record<string, unknown>;
}

async function registerRecipient(env: Env, body: Record<string, unknown>): Promise<Record<string, unknown>> {
  const identityId = safeReference(body.identity_id, "identity_id");
  const workerId = safeReference(body.worker_id, "worker_id");
  const address = normalizeEmailAddress(String(body.address || ""));
  const idempotencyKey = safeReference(body.idempotency_key, "idempotency_key");
  const existingByKey = await env.DB.prepare("SELECT * FROM recipient_registry WHERE idempotency_key = ?").bind(idempotencyKey).first<RegistryRow>();
  if (existingByKey && (existingByKey.address !== address || existingByKey.identity_id !== identityId || existingByKey.worker_id !== workerId)) {
    throw new EdgeError(409, "recipient registration idempotency conflict");
  }
  const existing = await env.DB.prepare("SELECT * FROM recipient_registry WHERE address = ?").bind(address).first<RegistryRow>();
  if (existing) {
    if (existing.identity_id !== identityId || existing.worker_id !== workerId) throw new EdgeError(409, "recipient is already owned by another identity");
    if (["SUSPENDED", "RETIRED"].includes(existing.state)) throw new EdgeError(409, "recipient lifecycle must be reconciled before registration");
    return { provider_reference: existing.provider_reference, state: existing.state };
  }
  const addressHash = await sha256Hex(new TextEncoder().encode(address));
  const providerReference = `recipient:${addressHash.slice(0, 32)}`;
  await env.DB.prepare(
    `INSERT INTO recipient_registry
       (identity_id, worker_id, address, provider_reference, idempotency_key, state)
     VALUES (?, ?, ?, ?, ?, 'VERIFYING')`,
  ).bind(identityId, workerId, address, providerReference, idempotencyKey).run();
  return { provider_reference: providerReference, state: "VERIFYING" };
}

async function lifecycleRecipient(env: Env, action: string, body: Record<string, unknown>): Promise<Record<string, unknown>> {
  const identityId = safeReference(body.identity_id, "identity_id");
  const address = normalizeEmailAddress(String(body.address || ""));
  const providerReference = safeReference(body.provider_reference, "provider_reference");
  const existing = await env.DB.prepare("SELECT * FROM recipient_registry WHERE provider_reference = ? AND address = ?")
    .bind(providerReference, address).first<RegistryRow>();
  if (!existing || existing.identity_id !== identityId) throw new EdgeError(404, "recipient binding not found");
  const nextState = ({ activate: "ACTIVE", suspend: "SUSPENDED", retire: "RETIRED" } as Record<string, string>)[action];
  if (!nextState) throw new EdgeError(404, "recipient lifecycle action not found");
  if (existing.state === "RETIRED" && nextState !== "RETIRED") throw new EdgeError(409, "retired recipient cannot be reactivated");
  await env.DB.prepare("UPDATE recipient_registry SET state = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE provider_reference = ?")
    .bind(nextState, providerReference).run();
  return { state: nextState, provider_reference: providerReference };
}

async function addAlias(env: Env, body: Record<string, unknown>): Promise<Record<string, unknown>> {
  const identityId = body.identity_id ? safeReference(body.identity_id, "identity_id") : "";
  const providerReference = safeReference(body.provider_reference, "provider_reference");
  const address = normalizeEmailAddress(String(body.address || ""));
  const owner = await env.DB.prepare("SELECT * FROM recipient_registry WHERE provider_reference = ?")
    .bind(providerReference).first<RegistryRow>();
  if (!owner || owner.identity_id !== identityId || !["VERIFYING", "ACTIVE"].includes(owner.state)) throw new EdgeError(404, "recipient binding not found");
  const existing = await env.DB.prepare("SELECT * FROM recipient_registry WHERE address = ?").bind(address).first<RegistryRow>();
  if (existing && existing.identity_id !== identityId) throw new EdgeError(409, "recipient alias is already owned by another identity");
  if (!existing) {
    const aliasKey = `alias:${identityId}:${address}`;
    await env.DB.prepare(
      `INSERT INTO recipient_registry
         (identity_id, worker_id, address, provider_reference, idempotency_key, state)
       VALUES (?, ?, ?, ?, ?, ?)`,
    ).bind(identityId, owner.worker_id, address, providerReference, aliasKey, owner.state).run();
  }
  return { state: existing?.state || owner.state, provider_reference: providerReference };
}

async function listEvents(env: Env, url: URL): Promise<Record<string, unknown>> {
  const after = Number(url.searchParams.get("after") || "0");
  const limit = Number(url.searchParams.get("limit") || "100");
  if (!Number.isSafeInteger(after) || after < 0 || !Number.isSafeInteger(limit) || limit < 1 || limit > 1000) throw new EdgeError(422, "cursor or limit is invalid");
  const query = await env.DB.prepare(
    `SELECT sequence, event_id, message_id, provider_message_id, identity_id,
            worker_id, envelope_recipient, event_type, received_at,
            raw_object_key, raw_size
       FROM inbound_events
      WHERE sequence > ?
      ORDER BY sequence ASC
      LIMIT ?`,
  ).bind(after, limit).all<Record<string, unknown>>();
  const events = query.results;
  const nextCursor = events.length ? Number(events[events.length - 1].sequence) : after;
  return { cursor: after, next_cursor: nextCursor, events };
}

async function fetchMessage(env: Env, messageId: string): Promise<Record<string, unknown>> {
  const safeMessageId = safeId(messageId, "message_id");
  const row = await env.DB.prepare("SELECT * FROM inbound_messages WHERE message_id = ? AND deleted_at IS NULL")
    .bind(safeMessageId).first<MessageRow>();
  if (!row) throw new EdgeError(404, "message not found");
  const object = await env.MAIL_OBJECTS.get(row.raw_object_key);
  if (!object) throw new EdgeError(503, "message object is temporarily unavailable");
  const raw = new Uint8Array(await object.arrayBuffer());
  if (raw.byteLength > maxMessageBytes(env)) throw new EdgeError(503, "message object exceeds the bounded limit");
  return {
    message_id: row.message_id,
    provider_event_id: row.event_id,
    identity_id: row.identity_id,
    worker_id: row.worker_id,
    envelope_recipient: row.envelope_recipient,
    sender: row.sender,
    subject: row.subject,
    received_at: row.received_at,
    raw_size: row.raw_size,
    raw_mime_base64: base64Encode(raw),
  };
}

async function authorizeMessageMutation(env: Env, messageId: string, body: Record<string, unknown>): Promise<MessageRow> {
  const safeMessageId = safeId(messageId, "message_id");
  const providerReference = safeReference(body.provider_reference, "provider_reference");
  const row = await env.DB.prepare("SELECT * FROM inbound_messages WHERE message_id = ?")
    .bind(safeMessageId).first<MessageRow>();
  if (!row) throw new EdgeError(404, "message not found");
  const owner = await env.DB.prepare("SELECT * FROM recipient_registry WHERE address = ? AND provider_reference = ?")
    .bind(row.envelope_recipient, providerReference).first<RegistryRow>();
  if (!owner || owner.identity_id !== row.identity_id) throw new EdgeError(403, "message provider binding denied");
  return row;
}

async function mutateMessage(env: Env, messageId: string, operation: "processed" | "delete" | "protect", body: Record<string, unknown>): Promise<Record<string, unknown>> {
  const row = await authorizeMessageMutation(env, messageId, body);
  const safeMessageId = safeId(messageId, "message_id");
  if (operation === "processed") {
    await env.DB.prepare("UPDATE inbound_messages SET processed_at = COALESCE(processed_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) WHERE message_id = ?")
      .bind(safeMessageId).run();
    return { message_id: safeMessageId, processed: true };
  }
  if (operation === "protect") {
    const until = String(body.until || "");
    const parsed = Date.parse(until);
    if (!until || !Number.isFinite(parsed)) throw new EdgeError(422, "retention deadline is invalid");
    await env.DB.prepare("UPDATE inbound_messages SET protected_until = ? WHERE message_id = ? AND deleted_at IS NULL")
      .bind(new Date(parsed).toISOString(), safeMessageId).run();
    return { message_id: safeMessageId, protected_until: new Date(parsed).toISOString() };
  }
  if (row.protected_until && Date.parse(row.protected_until) > Date.now()) throw new EdgeError(409, "message is protected by an active verification retention hold");
  await env.DB.prepare("UPDATE inbound_messages SET deleted_at = COALESCE(deleted_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) WHERE message_id = ?")
    .bind(safeMessageId).run();
  await env.MAIL_OBJECTS.delete(row.raw_object_key);
  return { message_id: safeMessageId, deleted: true };
}

export async function cleanupExpiredMail(env: Env, now = Date.now()): Promise<number> {
  const rawDays = envNumber(env.MAIL_EDGE_RETENTION_DAYS, 7, 1, 90);
  const processedDays = envNumber(env.MAIL_EDGE_PROCESSED_RETENTION_DAYS, 1, 0, 30);
  const rawCutoff = new Date(now - rawDays * 24 * 60 * 60 * 1000).toISOString();
  const processedCutoff = new Date(now - processedDays * 24 * 60 * 60 * 1000).toISOString();
  const result = await env.DB.prepare(
    `SELECT * FROM inbound_messages
      WHERE deleted_at IS NULL
        AND (protected_until IS NULL OR protected_until <= ?)
        AND ((processed_at IS NULL AND received_at <= ?)
             OR (processed_at IS NOT NULL AND processed_at <= ?))`,
  ).bind(new Date(now).toISOString(), rawCutoff, processedCutoff).all<MessageRow>();
  let deleted = 0;
  for (const row of result.results) {
    await env.MAIL_OBJECTS.delete(row.raw_object_key);
    await env.DB.prepare("UPDATE inbound_messages SET deleted_at = ? WHERE message_id = ? AND deleted_at IS NULL")
      .bind(new Date(now).toISOString(), row.message_id).run();
    deleted += 1;
  }
  return deleted;
}

export async function handleEmailMessage(message: EmailMessage, env: Env): Promise<Record<string, unknown>> {
  let recipient: string;
  try {
    // EmailMessage.to is the SMTP envelope recipient. The To header is never
    // consulted for authorization or identity ownership.
    recipient = normalizeEmailAddress(message.to);
  } catch (error) {
    message.setReject(error instanceof EdgeError ? error.message : "invalid recipient");
    return { accepted: false, reason: "invalid_recipient" };
  }
  const identity = await env.DB.prepare("SELECT * FROM recipient_registry WHERE address = ?")
    .bind(recipient).first<RegistryRow>();
  if (!identity) {
    message.setReject("unknown AIAT recipient");
    return { accepted: false, reason: "unknown_recipient" };
  }
  if (!["ACTIVE", "VERIFYING"].includes(identity.state)) {
    message.setReject("AIAT recipient is not active");
    return { accepted: false, reason: "recipient_inactive" };
  }
  let raw: Uint8Array;
  try {
    raw = await readEmailRaw(message, maxMessageBytes(env));
  } catch (error) {
    message.setReject(error instanceof EdgeError ? error.message : "message could not be read");
    return { accepted: false, reason: "message_too_large" };
  }
  const headerId = headerMessageId(raw);
  const rawHash = await sha256Hex(raw);
  const sourceId = headerId || `raw:${rawHash}`;
  const providerKey = `${recipient}\u0000${sourceId}`;
  const existing = await env.DB.prepare("SELECT * FROM inbound_messages WHERE provider_key = ?")
    .bind(providerKey).first<MessageRow>();
  if (existing) {
    if (existing.content_hash !== rawHash) {
      message.setReject("provider event content conflict");
      return { accepted: false, reason: "provider_event_conflict" };
    }
    return { accepted: true, duplicate: true, event_id: existing.event_id, message_id: existing.message_id };
  }
  const messageId = `m-${(await sha256Hex(concatBytes(new TextEncoder().encode(`${recipient}\u0000${sourceId}\u0000`), raw))).slice(0, 48)}`;
  const eventId = `evt-${(await sha256Hex(new TextEncoder().encode(providerKey))).slice(0, 48)}`;
  const objectKey = `inbound/${identity.identity_id}/${messageId}.eml`;
  const receivedAt = new Date().toISOString();
  const sender = safeHeaderSender(raw);
  const subject = headerValue(raw, "Subject");
  await env.MAIL_OBJECTS.put(objectKey, raw, { httpMetadata: { contentType: "message/rfc822" } });
  try {
    const results = await env.DB.batch([
      env.DB.prepare(
        `INSERT OR IGNORE INTO inbound_events
          (event_id, message_id, provider_message_id, provider_key, identity_id,
           worker_id, envelope_recipient, event_type, received_at, raw_object_key,
           raw_size, content_hash)
         VALUES (?, ?, ?, ?, ?, ?, ?, 'inbound.message.received', ?, ?, ?, ?)`,
      ).bind(eventId, messageId, headerId, providerKey, identity.identity_id, identity.worker_id, recipient, receivedAt, objectKey, raw.byteLength, rawHash),
      env.DB.prepare(
        `INSERT OR IGNORE INTO inbound_messages
          (message_id, event_id, provider_key, identity_id, worker_id,
           envelope_recipient, sender, subject, received_at, raw_object_key,
           raw_size, content_hash)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      ).bind(messageId, eventId, providerKey, identity.identity_id, identity.worker_id, recipient, sender, subject, receivedAt, objectKey, raw.byteLength, rawHash),
    ]);
    const eventChanges = Number((results[0] as D1Result).meta?.changes || 0);
    if (eventChanges === 0) {
      const concurrent = await env.DB.prepare("SELECT * FROM inbound_messages WHERE provider_key = ?").bind(providerKey).first<MessageRow>();
      await env.MAIL_OBJECTS.delete(objectKey);
      if (concurrent?.content_hash === rawHash) return { accepted: true, duplicate: true, event_id: concurrent.event_id, message_id: concurrent.message_id };
      throw new EdgeError(409, "provider event content conflict");
    }
  } catch (error) {
    await env.MAIL_OBJECTS.delete(objectKey);
    if (error instanceof EdgeError) {
      message.setReject(error.message);
      return { accepted: false, reason: "provider_event_conflict" };
    }
    throw error;
  }
  return { accepted: true, duplicate: false, event_id: eventId, message_id: messageId };
}

async function handleApiRequest(request: Request, env: Env): Promise<Response> {
  let body: Uint8Array;
  try {
    body = await readRequestBody(request, MAX_API_BODY_BYTES);
    if (!(await verifySignedRequest(request, env, body))) return jsonResponse(401, { error: "invalid mail-edge authentication" });
    const url = new URL(request.url);
    const input = requestBodyObject(body);
    if (request.method === "GET" && url.pathname === "/v1/health") {
      return jsonResponse(200, { status: "ok", provider: "cloudflare", storage: { metadata: "d1", raw: "r2" } });
    }
    if (request.method === "POST" && url.pathname === "/v1/recipients/register") return jsonResponse(200, await registerRecipient(env, input));
    if (request.method === "POST" && ["activate", "suspend", "retire"].some((action) => url.pathname === `/v1/recipients/${action}`)) {
      return jsonResponse(200, await lifecycleRecipient(env, url.pathname.split("/").pop() || "", input));
    }
    if (request.method === "POST" && url.pathname === "/v1/recipients/alias") return jsonResponse(200, await addAlias(env, input));
    if (request.method === "GET" && url.pathname === "/v1/events") return jsonResponse(200, await listEvents(env, url));
    if (request.method === "POST" && /^\/v1\/events\/[^/]+\/ack$/.test(url.pathname)) {
      const eventId = safeId(url.pathname.split("/")[3] || "", "event_id");
      const result = await env.DB.prepare("UPDATE inbound_events SET acked_at = COALESCE(acked_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) WHERE event_id = ?")
        .bind(eventId).run();
      if (Number(result.meta?.changes || 0) === 0) throw new EdgeError(404, "event not found");
      return jsonResponse(200, { event_id: eventId, acknowledged: true });
    }
    const messageMatch = url.pathname.match(/^\/v1\/messages\/([^/]+)$/);
    if (request.method === "GET" && messageMatch) return jsonResponse(200, await fetchMessage(env, decodeURIComponent(messageMatch[1])));
    const mutationMatch = url.pathname.match(/^\/v1\/messages\/([^/]+)\/(processed|delete|protect)$/);
    if (request.method === "POST" && mutationMatch) {
      return jsonResponse(200, await mutateMessage(env, decodeURIComponent(mutationMatch[1]), mutationMatch[2] as "processed" | "delete" | "protect", input));
    }
    throw new EdgeError(404, "mail-edge route not found");
  } catch (error) {
    if (error instanceof EdgeError) return jsonResponse(error.status, { error: error.message });
    return jsonResponse(503, { error: "mail-edge operation unavailable" });
  }
}

const worker = {
  async email(message: EmailMessage, env: Env, _ctx: ExecutionContext): Promise<void> {
    await handleEmailMessage(message, env);
  },
  async fetch(request: Request, env: Env, _ctx: ExecutionContext): Promise<Response> {
    return handleApiRequest(request, env);
  },
  async scheduled(_event: ScheduledEvent, env: Env, _ctx: ExecutionContext): Promise<void> {
    await cleanupExpiredMail(env);
  },
};

export default worker;
