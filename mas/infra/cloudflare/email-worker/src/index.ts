/* AIAT Cloudflare Email Routing Worker.

   The email handler authorizes the SMTP envelope recipient against an
   AIAT-owned D1 registry, streams bounded raw MIME into the selected local
   storage backend, and publishes a signed, replay-protected pull event only
   after the raw content has been verified. D1 chunk storage is the default;
   R2 is an explicitly optional compatibility profile.
*/

const DEFAULT_AUTH_VERSION = "aiat.mail-edge.v1";
export const D1_RAW_CHUNK_SIZE = 256 * 1024;
export const D1_MAX_MESSAGE_BYTES = 4 * 1024 * 1024;
const DEFAULT_MAX_MESSAGE_BYTES = D1_MAX_MESSAGE_BYTES;
const MAX_PROVIDER_MESSAGE_BYTES = 25 * 1024 * 1024;
const MAX_API_BODY_BYTES = 1024 * 1024;
const MAX_ADDRESS_LENGTH = 320;
const MAX_ID_LENGTH = 300;
const MAX_HEADER_LENGTH = 998;
const HEADER_SCAN_BYTES = 128 * 1024;
const CLEANUP_BATCH_LIMIT = 20;
const RECOVERY_BATCH_LIMIT = 1;
const DEFAULT_INCOMPLETE_RETENTION_SECONDS = 60 * 60;

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

type StorageBackend = "d1" | "r2";

export interface Env {
  DB: D1Database;
  // Optional by design: the default Wrangler environment has no R2 binding.
  MAIL_OBJECTS?: R2Bucket;
  MAIL_EDGE_AUTH_SECRET: string;
  MAIL_EDGE_AUTH_VERSION?: string;
  MAIL_EDGE_STORAGE_BACKEND?: string;
  MAIL_EDGE_MAX_MESSAGE_BYTES?: string;
  MAIL_EDGE_AUTH_TOLERANCE_SECONDS?: string;
  MAIL_EDGE_RETENTION_DAYS?: string;
  MAIL_EDGE_PROCESSED_RETENTION_DAYS?: string;
  MAIL_EDGE_INCOMPLETE_RETENTION_SECONDS?: string;
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
  event_id: string | null;
  provider_key: string;
  provider_message_id: string | null;
  rfc_message_id: string | null;
  identity_id: string;
  worker_id: string;
  envelope_recipient: string;
  sender: string | null;
  subject: string;
  received_at: string;
  raw_object_key: string;
  raw_size: number;
  content_hash: string;
  storage_backend: StorageBackend;
  storage_state: string;
  chunk_count: number;
  processed_at: string | null;
  protected_until: string | null;
  deleted_at: string | null;
  created_at: string;
  updated_at: string;
}

interface EventRow {
  sequence: number;
  event_id: string;
  message_id: string;
  provider_message_id: string | null;
  provider_key: string;
  identity_id: string;
  worker_id: string;
  envelope_recipient: string;
  event_type: string;
  received_at: string;
  raw_object_key: string;
  raw_size: number;
  content_hash: string;
  acked_at: string | null;
}

interface ChunkRow {
  chunk_index: number;
  chunk_data: unknown;
  chunk_size_bytes: number;
  chunk_sha256: string;
}

interface BeginMessage {
  messageId: string;
  identityId: string;
  workerId: string;
  recipient: string;
  provisionalKey: string;
  rawObjectKey: string;
  receivedAt: string;
}

interface FinalMessage {
  providerKey: string;
  providerMessageId: string | null;
  rfcMessageId: string | null;
  sender: string | null;
  subject: string;
  receivedAt: string;
  rawSize: number;
  rawSha256: string;
  chunkCount: number;
}

interface RawMessageStore {
  readonly backend: StorageBackend;
  begin(message: BeginMessage): Promise<void>;
  appendChunk(chunkIndex: number, chunk: Uint8Array): Promise<void>;
  finalize(message: FinalMessage): Promise<void>;
  verifyStored(row: MessageRow): Promise<void>;
  read(row: MessageRow): Promise<Uint8Array>;
  delete(row: MessageRow): Promise<void>;
  discard(state: "REJECTED" | "REJECTED_OVERSIZE", rawSize: number, rawSha256?: string): Promise<void>;
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

function storageBackend(env: Env): StorageBackend {
  const configured = env.MAIL_EDGE_STORAGE_BACKEND?.trim().toLowerCase() || "d1";
  if (configured !== "d1" && configured !== "r2") {
    throw new EdgeError(500, "MAIL_EDGE_STORAGE_BACKEND must be d1 or r2");
  }
  return configured;
}

function authVersion(env: Env): string {
  return env.MAIL_EDGE_AUTH_VERSION?.trim() || DEFAULT_AUTH_VERSION;
}

function maxMessageBytes(env: Env): number {
  const backend = storageBackend(env);
  const upperBound = backend === "d1" ? D1_MAX_MESSAGE_BYTES : MAX_PROVIDER_MESSAGE_BYTES;
  const fallback = backend === "d1" ? DEFAULT_MAX_MESSAGE_BYTES : MAX_PROVIDER_MESSAGE_BYTES;
  const configured = Number(env.MAIL_EDGE_MAX_MESSAGE_BYTES ?? "");
  if (!Number.isSafeInteger(configured) || configured < 1024) return fallback;
  if (configured > upperBound) {
    throw new EdgeError(500, `MAIL_EDGE_MAX_MESSAGE_BYTES exceeds the ${backend} bounded limit`);
  }
  return configured;
}

function incompleteRetentionSeconds(env: Env): number {
  return envNumber(
    env.MAIL_EDGE_INCOMPLETE_RETENTION_SECONDS,
    DEFAULT_INCOMPLETE_RETENTION_SECONDS,
    1,
    7 * 24 * 60 * 60,
  );
}

function nowIso(): string {
  return new Date().toISOString();
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

const SHA256_K = [
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
] as const;

function rotateRight(value: number, bits: number): number {
  return (value >>> bits) | (value << (32 - bits));
}

function compressSha256(state: Uint32Array, block: Uint8Array): void {
  const schedule = new Uint32Array(64);
  for (let index = 0; index < 16; index += 1) {
    const offset = index * 4;
    schedule[index] = (
      (block[offset] << 24) |
      (block[offset + 1] << 16) |
      (block[offset + 2] << 8) |
      block[offset + 3]
    ) >>> 0;
  }
  for (let index = 16; index < 64; index += 1) {
    const value = schedule[index - 15];
    const smallSigma0 = rotateRight(value, 7) ^ rotateRight(value, 18) ^ (value >>> 3);
    const prior = schedule[index - 2];
    const smallSigma1 = rotateRight(prior, 17) ^ rotateRight(prior, 19) ^ (prior >>> 10);
    schedule[index] = (schedule[index - 16] + smallSigma0 + schedule[index - 7] + smallSigma1) >>> 0;
  }

  let a = state[0];
  let b = state[1];
  let c = state[2];
  let d = state[3];
  let e = state[4];
  let f = state[5];
  let g = state[6];
  let h = state[7];
  for (let index = 0; index < 64; index += 1) {
    const bigSigma1 = rotateRight(e, 6) ^ rotateRight(e, 11) ^ rotateRight(e, 25);
    const choose = (e & f) ^ (~e & g);
    const temporary1 = (h + bigSigma1 + choose + SHA256_K[index] + schedule[index]) >>> 0;
    const bigSigma0 = rotateRight(a, 2) ^ rotateRight(a, 13) ^ rotateRight(a, 22);
    const majority = (a & b) ^ (a & c) ^ (b & c);
    const temporary2 = (bigSigma0 + majority) >>> 0;
    h = g;
    g = f;
    f = e;
    e = (d + temporary1) >>> 0;
    d = c;
    c = b;
    b = a;
    a = (temporary1 + temporary2) >>> 0;
  }
  state[0] = (state[0] + a) >>> 0;
  state[1] = (state[1] + b) >>> 0;
  state[2] = (state[2] + c) >>> 0;
  state[3] = (state[3] + d) >>> 0;
  state[4] = (state[4] + e) >>> 0;
  state[5] = (state[5] + f) >>> 0;
  state[6] = (state[6] + g) >>> 0;
  state[7] = (state[7] + h) >>> 0;
}

class StreamingSha256 {
  private readonly state = new Uint32Array([
    0x6a09e667,
    0xbb67ae85,
    0x3c6ef372,
    0xa54ff53a,
    0x510e527f,
    0x9b05688c,
    0x1f83d9ab,
    0x5be0cd19,
  ]);
  private readonly pending = new Uint8Array(64);
  private pendingLength = 0;
  private byteLength = 0;

  update(bytes: Uint8Array): void {
    this.byteLength += bytes.byteLength;
    let offset = 0;
    if (this.pendingLength > 0) {
      const take = Math.min(64 - this.pendingLength, bytes.byteLength);
      this.pending.set(bytes.subarray(0, take), this.pendingLength);
      this.pendingLength += take;
      offset += take;
      if (this.pendingLength === 64) {
        compressSha256(this.state, this.pending);
        this.pendingLength = 0;
      }
    }
    while (offset + 64 <= bytes.byteLength) {
      compressSha256(this.state, bytes.subarray(offset, offset + 64));
      offset += 64;
    }
    if (offset < bytes.byteLength) {
      this.pending.set(bytes.subarray(offset), 0);
      this.pendingLength = bytes.byteLength - offset;
    }
  }

  digest(): Uint8Array {
    const state = this.state.slice();
    const final = new Uint8Array(this.pendingLength <= 55 ? 64 : 128);
    final.set(this.pending.subarray(0, this.pendingLength));
    final[this.pendingLength] = 0x80;
    const bitLength = this.byteLength * 8;
    const high = Math.floor(bitLength / 0x100000000);
    const low = bitLength >>> 0;
    const lengthOffset = final.length - 8;
    final[lengthOffset] = (high >>> 24) & 0xff;
    final[lengthOffset + 1] = (high >>> 16) & 0xff;
    final[lengthOffset + 2] = (high >>> 8) & 0xff;
    final[lengthOffset + 3] = high & 0xff;
    final[lengthOffset + 4] = (low >>> 24) & 0xff;
    final[lengthOffset + 5] = (low >>> 16) & 0xff;
    final[lengthOffset + 6] = (low >>> 8) & 0xff;
    final[lengthOffset + 7] = low & 0xff;
    for (let offset = 0; offset < final.length; offset += 64) {
      compressSha256(state, final.subarray(offset, offset + 64));
    }
    const output = new Uint8Array(32);
    for (let index = 0; index < state.length; index += 1) {
      output[index * 4] = (state[index] >>> 24) & 0xff;
      output[index * 4 + 1] = (state[index] >>> 16) & 0xff;
      output[index * 4 + 2] = (state[index] >>> 8) & 0xff;
      output[index * 4 + 3] = state[index] & 0xff;
    }
    return output;
  }
}

function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function sha256Hex(value: Uint8Array): string {
  const hash = new StreamingSha256();
  hash.update(value);
  return bytesToHex(hash.digest());
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

function d1BlobBytes(value: unknown): Uint8Array {
  if (value instanceof Uint8Array) return new Uint8Array(value);
  if (value instanceof ArrayBuffer) return new Uint8Array(value.slice(0));
  if (ArrayBuffer.isView(value)) {
    return new Uint8Array(value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength));
  }
  if (Array.isArray(value) && value.every((byte) => Number.isInteger(byte) && byte >= 0 && byte <= 255)) {
    return Uint8Array.from(value as number[]);
  }
  throw new EdgeError(503, "message chunk is not valid binary data");
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
  const headerText = new TextDecoder("utf-8", { fatal: false }).decode(raw.subarray(0, HEADER_SCAN_BYTES));
  const match = headerText.match(/^Message-ID\s*:\s*([^\r\n]+)$/im);
  const value = match?.[1]?.trim() || "";
  if (!value || value.length > MAX_ID_LENGTH || /[\u0000-\u001f\u007f]/.test(value)) return null;
  return value;
}

function headerValue(raw: Uint8Array, name: string): string {
  const text = new TextDecoder("utf-8", { fatal: false }).decode(raw.subarray(0, HEADER_SCAN_BYTES));
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
  const digest = sha256Hex(body);
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
  const addressHash = sha256Hex(new TextEncoder().encode(address));
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

async function insertReceivingMessage(env: Env, message: BeginMessage, backend: StorageBackend): Promise<void> {
  await env.DB.prepare(
    `INSERT INTO mail_messages
      (message_id, event_id, provider_key, provider_message_id, rfc_message_id,
       identity_id, worker_id, envelope_recipient, sender, subject, received_at,
       raw_object_key, raw_size, content_hash, storage_backend, storage_state,
       chunk_count, processed_at, protected_until, deleted_at, created_at, updated_at)
     VALUES (?, NULL, ?, NULL, NULL, ?, ?, ?, NULL, '', ?, ?, 0, '', ?, 'RECEIVING', 0, NULL, NULL, NULL, ?, ?)`
  ).bind(
    message.messageId,
    message.provisionalKey,
    message.identityId,
    message.workerId,
    message.recipient,
    message.receivedAt,
    message.rawObjectKey,
    backend,
    message.receivedAt,
    message.receivedAt,
  ).run();
}

async function markStoredMessage(env: Env, messageId: string, final: FinalMessage): Promise<void> {
  const result = await env.DB.prepare(
    `UPDATE mail_messages
        SET provider_key = ?, provider_message_id = ?, rfc_message_id = ?, sender = ?,
            subject = ?, received_at = ?, raw_size = ?, content_hash = ?,
            chunk_count = ?, storage_state = 'STORED', updated_at = ?
      WHERE message_id = ? AND storage_state = 'RECEIVING' AND deleted_at IS NULL`,
  ).bind(
    final.providerKey,
    final.providerMessageId,
    final.rfcMessageId,
    final.sender,
    final.subject,
    final.receivedAt,
    final.rawSize,
    final.rawSha256,
    final.chunkCount,
    nowIso(),
    messageId,
  ).run();
  if (Number(result.meta?.changes || 0) !== 1) throw new EdgeError(503, "message storage state could not be finalized");
}

class D1RawMessageStore implements RawMessageStore {
  readonly backend = "d1" as const;

  constructor(private readonly env: Env, private readonly messageId: string, private readonly rawObjectKey: string) {}

  async begin(message: BeginMessage): Promise<void> {
    await insertReceivingMessage(this.env, message, this.backend);
  }

  async appendChunk(chunkIndex: number, chunk: Uint8Array): Promise<void> {
    if (chunk.byteLength < 1 || chunk.byteLength > D1_RAW_CHUNK_SIZE) throw new EdgeError(503, "message chunk is outside the bounded size");
    const digest = sha256Hex(chunk);
    const result = await this.env.DB.prepare(
      `INSERT OR IGNORE INTO mail_message_chunks
        (message_id, chunk_index, chunk_data, chunk_size_bytes, chunk_sha256, created_at)
       VALUES (?, ?, ?, ?, ?, ?)`,
    ).bind(this.messageId, chunkIndex, chunk, chunk.byteLength, digest, nowIso()).run();
    if (Number(result.meta?.changes || 0) === 1) return;
    const existing = await this.env.DB.prepare(
      "SELECT chunk_index, chunk_size_bytes, chunk_sha256 FROM mail_message_chunks WHERE message_id = ? AND chunk_index = ?",
    ).bind(this.messageId, chunkIndex).first<Pick<ChunkRow, "chunk_index" | "chunk_size_bytes" | "chunk_sha256">>();
    if (!existing || Number(existing.chunk_size_bytes) !== chunk.byteLength || existing.chunk_sha256 !== digest) {
      throw new EdgeError(409, "message chunk content conflict");
    }
  }

  private async verifyParts(expected: { rawSize: number; rawSha256: string; chunkCount: number }): Promise<void> {
    const summary = await this.env.DB.prepare(
      `SELECT COUNT(*) AS chunk_count, COALESCE(SUM(chunk_size_bytes), 0) AS total_size
         FROM mail_message_chunks WHERE message_id = ?`,
    ).bind(this.messageId).first<{ chunk_count: number; total_size: number }>();
    if (!summary || Number(summary.chunk_count) !== expected.chunkCount || Number(summary.total_size) !== expected.rawSize) {
      throw new EdgeError(503, "message chunks are incomplete");
    }
    const whole = new StreamingSha256();
    for (let index = 0; index < expected.chunkCount; index += 1) {
      const row = await this.env.DB.prepare(
        `SELECT chunk_index, chunk_data, chunk_size_bytes, chunk_sha256
           FROM mail_message_chunks WHERE message_id = ? AND chunk_index = ?`,
      ).bind(this.messageId, index).first<ChunkRow>();
      if (!row || Number(row.chunk_index) !== index) throw new EdgeError(503, "message chunk ordering is invalid");
      const bytes = d1BlobBytes(row.chunk_data);
      if (bytes.byteLength !== Number(row.chunk_size_bytes) || bytes.byteLength > D1_RAW_CHUNK_SIZE || sha256Hex(bytes) !== row.chunk_sha256) {
        throw new EdgeError(503, "message chunk checksum validation failed");
      }
      whole.update(bytes);
    }
    if (bytesToHex(whole.digest()) !== expected.rawSha256) throw new EdgeError(503, "message checksum validation failed");
  }

  async finalize(message: FinalMessage): Promise<void> {
    await this.verifyParts(message);
    await markStoredMessage(this.env, this.messageId, message);
  }

  async verifyStored(row: MessageRow): Promise<void> {
    await this.verifyParts({ rawSize: Number(row.raw_size), rawSha256: row.content_hash, chunkCount: Number(row.chunk_count) });
  }

  async read(row: MessageRow): Promise<Uint8Array> {
    const parts: Uint8Array[] = [];
    const whole = new StreamingSha256();
    let total = 0;
    for (let index = 0; index < Number(row.chunk_count); index += 1) {
      const chunk = await this.env.DB.prepare(
        `SELECT chunk_index, chunk_data, chunk_size_bytes, chunk_sha256
           FROM mail_message_chunks WHERE message_id = ? AND chunk_index = ?`,
      ).bind(row.message_id, index).first<ChunkRow>();
      if (!chunk || Number(chunk.chunk_index) !== index) throw new EdgeError(503, "message chunk ordering is invalid");
      const bytes = d1BlobBytes(chunk.chunk_data);
      if (bytes.byteLength !== Number(chunk.chunk_size_bytes) || bytes.byteLength > D1_RAW_CHUNK_SIZE || sha256Hex(bytes) !== chunk.chunk_sha256) {
        throw new EdgeError(503, "message chunk checksum validation failed");
      }
      parts.push(bytes);
      total += bytes.byteLength;
      whole.update(bytes);
    }
    const raw = concatBytes(...parts);
    if (total !== Number(row.raw_size) || raw.byteLength !== Number(row.raw_size) || bytesToHex(whole.digest()) !== row.content_hash) {
      throw new EdgeError(503, "message checksum or length validation failed");
    }
    return raw;
  }

  async delete(row: MessageRow): Promise<void> {
    const deletedAt = nowIso();
    await this.env.DB.batch([
      this.env.DB.prepare("DELETE FROM mail_message_chunks WHERE message_id = ?").bind(row.message_id),
      this.env.DB.prepare(
        `UPDATE mail_messages
            SET deleted_at = COALESCE(deleted_at, ?), storage_state = 'DELETED',
                chunk_count = 0, updated_at = ?
          WHERE message_id = ? AND deleted_at IS NULL`,
      ).bind(deletedAt, deletedAt, row.message_id),
    ]);
  }

  async discard(state: "REJECTED" | "REJECTED_OVERSIZE", rawSize: number, rawSha256 = ""): Promise<void> {
    const discardedAt = nowIso();
    await this.env.DB.batch([
      this.env.DB.prepare("DELETE FROM mail_message_chunks WHERE message_id = ?").bind(this.messageId),
      this.env.DB.prepare(
        `UPDATE mail_messages
            SET storage_state = ?, raw_size = ?, content_hash = ?, chunk_count = 0,
                deleted_at = ?, updated_at = ?
          WHERE message_id = ? AND deleted_at IS NULL`,
      ).bind(state, rawSize, rawSha256, discardedAt, discardedAt, this.messageId),
    ]);
  }
}

function requireR2(env: Env): R2Bucket {
  if (!env.MAIL_OBJECTS) throw new EdgeError(500, "R2 storage backend requires the optional MAIL_OBJECTS binding");
  return env.MAIL_OBJECTS;
}

class R2RawMessageStore implements RawMessageStore {
  readonly backend = "r2" as const;
  private readonly chunks: Uint8Array[] = [];

  constructor(private readonly env: Env, private readonly messageId: string, private readonly rawObjectKey: string) {}

  async begin(message: BeginMessage): Promise<void> {
    await insertReceivingMessage(this.env, message, this.backend);
  }

  async appendChunk(_chunkIndex: number, chunk: Uint8Array): Promise<void> {
    this.chunks.push(new Uint8Array(chunk));
  }

  private assembled(): Uint8Array {
    return concatBytes(...this.chunks);
  }

  private verifyBytes(raw: Uint8Array, expected: { rawSize: number; rawSha256: string }): void {
    if (raw.byteLength !== expected.rawSize || sha256Hex(raw) !== expected.rawSha256) {
      throw new EdgeError(503, "message checksum or length validation failed");
    }
  }

  async finalize(message: FinalMessage): Promise<void> {
    const raw = this.assembled();
    this.verifyBytes(raw, message);
    await requireR2(this.env).put(this.rawObjectKey, raw, { httpMetadata: { contentType: "message/rfc822" } });
    await markStoredMessage(this.env, this.messageId, message);
  }

  async verifyStored(row: MessageRow): Promise<void> {
    const object = await requireR2(this.env).get(row.raw_object_key);
    if (!object) throw new EdgeError(503, "message object is temporarily unavailable");
    this.verifyBytes(new Uint8Array(await object.arrayBuffer()), { rawSize: Number(row.raw_size), rawSha256: row.content_hash });
  }

  async read(row: MessageRow): Promise<Uint8Array> {
    const object = await requireR2(this.env).get(row.raw_object_key);
    if (!object) throw new EdgeError(503, "message object is temporarily unavailable");
    const raw = new Uint8Array(await object.arrayBuffer());
    this.verifyBytes(raw, { rawSize: Number(row.raw_size), rawSha256: row.content_hash });
    return raw;
  }

  async delete(row: MessageRow): Promise<void> {
    const deletedAt = nowIso();
    await requireR2(this.env).delete(row.raw_object_key);
    await this.env.DB.prepare(
      `UPDATE mail_messages
          SET deleted_at = COALESCE(deleted_at, ?), storage_state = 'DELETED',
              chunk_count = 0, updated_at = ?
        WHERE message_id = ? AND deleted_at IS NULL`,
    ).bind(deletedAt, deletedAt, row.message_id).run();
  }

  async discard(state: "REJECTED" | "REJECTED_OVERSIZE", rawSize: number, rawSha256 = ""): Promise<void> {
    const discardedAt = nowIso();
    await requireR2(this.env).delete(this.rawObjectKey);
    await this.env.DB.prepare(
      `UPDATE mail_messages
          SET storage_state = ?, raw_size = ?, content_hash = ?, chunk_count = 0,
              deleted_at = ?, updated_at = ?
        WHERE message_id = ? AND deleted_at IS NULL`,
    ).bind(state, rawSize, rawSha256, discardedAt, discardedAt, this.messageId).run();
  }
}

function rawStoreForNew(env: Env, messageId: string, rawObjectKey: string): RawMessageStore {
  return storageBackend(env) === "d1"
    ? new D1RawMessageStore(env, messageId, rawObjectKey)
    : new R2RawMessageStore(env, messageId, rawObjectKey);
}

function rawStoreForRow(env: Env, row: MessageRow): RawMessageStore {
  const backend = row.storage_backend || "d1";
  if (backend === "d1") return new D1RawMessageStore(env, row.message_id, row.raw_object_key);
  if (backend === "r2") return new R2RawMessageStore(env, row.message_id, row.raw_object_key);
  throw new EdgeError(503, "message storage backend is not recognized");
}

async function publishEvent(env: Env, row: MessageRow, alreadyVerified = false): Promise<{ eventId: string; duplicate: boolean; messageId: string }> {
  const store = rawStoreForRow(env, row);
  if (!alreadyVerified) await store.verifyStored(row);
  const eventId = row.event_id || `evt-${sha256Hex(new TextEncoder().encode(row.provider_key)).slice(0, 48)}`;
  const existing = await env.DB.prepare("SELECT * FROM inbound_events WHERE provider_key = ?")
    .bind(row.provider_key).first<EventRow>();
  if (existing && existing.message_id !== row.message_id) {
    if (existing.content_hash !== row.content_hash) {
      await store.discard("REJECTED", Number(row.raw_size), row.content_hash);
      throw new EdgeError(409, "provider event content conflict");
    }
    await store.discard("REJECTED", Number(row.raw_size), row.content_hash);
    return { eventId: existing.event_id, duplicate: true, messageId: existing.message_id };
  }

  await env.DB.prepare(
    `UPDATE mail_messages SET event_id = ?, storage_state = 'EVENT_READY', updated_at = ?
      WHERE message_id = ? AND storage_state IN ('STORED', 'EVENT_READY') AND deleted_at IS NULL`,
  ).bind(eventId, nowIso(), row.message_id).run();

  const result = await env.DB.prepare(
    `INSERT OR IGNORE INTO inbound_events
      (event_id, message_id, provider_message_id, provider_key, identity_id,
       worker_id, envelope_recipient, event_type, received_at, raw_object_key,
       raw_size, content_hash)
     VALUES (?, ?, ?, ?, ?, ?, ?, 'inbound.message.received', ?, ?, ?, ?)`,
  ).bind(
    eventId,
    row.message_id,
    row.provider_message_id,
    row.provider_key,
    row.identity_id,
    row.worker_id,
    row.envelope_recipient,
    row.received_at,
    row.raw_object_key,
    row.raw_size,
    row.content_hash,
  ).run();
  if (Number(result.meta?.changes || 0) === 1) return { eventId, duplicate: false, messageId: row.message_id };

  const concurrent = await env.DB.prepare("SELECT * FROM inbound_events WHERE provider_key = ?")
    .bind(row.provider_key).first<EventRow>();
  if (concurrent?.content_hash === row.content_hash) {
    await store.discard("REJECTED", Number(row.raw_size), row.content_hash);
    return { eventId: concurrent.event_id, duplicate: true, messageId: concurrent.message_id };
  }
  throw new EdgeError(409, "provider event content conflict");
}

async function recoverPendingMessages(env: Env): Promise<void> {
  const pending = await env.DB.prepare(
    `SELECT * FROM mail_messages
      WHERE deleted_at IS NULL AND storage_state IN ('STORED', 'EVENT_READY')
      ORDER BY updated_at ASC LIMIT ?`,
  ).bind(RECOVERY_BATCH_LIMIT).all<MessageRow>();
  for (const row of pending.results) {
    const existing = row.event_id
      ? await env.DB.prepare("SELECT * FROM inbound_events WHERE event_id = ?").bind(row.event_id).first<EventRow>()
      : null;
    if (!existing) await publishEvent(env, row);
  }
}

async function listEvents(env: Env, url: URL): Promise<Record<string, unknown>> {
  const after = Number(url.searchParams.get("after") || "0");
  const limit = Number(url.searchParams.get("limit") || "100");
  if (!Number.isSafeInteger(after) || after < 0 || !Number.isSafeInteger(limit) || limit < 1 || limit > 1000) throw new EdgeError(422, "cursor or limit is invalid");
  await recoverPendingMessages(env);
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

async function fetchMessage(env: Env, messageId: string, workerScope?: string): Promise<Record<string, unknown>> {
  const safeMessageId = safeId(messageId, "message_id");
  const row = await env.DB.prepare("SELECT * FROM mail_messages WHERE message_id = ? AND deleted_at IS NULL")
    .bind(safeMessageId).first<MessageRow>();
  if (!row || !["STORED", "EVENT_READY", "ACKNOWLEDGED"].includes(row.storage_state)) throw new EdgeError(404, "message not found");
  if (workerScope && row.worker_id !== safeReference(workerScope, "worker_id")) throw new EdgeError(404, "message not found");
  const raw = await rawStoreForRow(env, row).read(row);
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
  const row = await env.DB.prepare("SELECT * FROM mail_messages WHERE message_id = ?")
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
    await env.DB.prepare("UPDATE mail_messages SET processed_at = COALESCE(processed_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')), updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE message_id = ? AND deleted_at IS NULL")
      .bind(safeMessageId).run();
    return { message_id: safeMessageId, processed: true };
  }
  if (operation === "protect") {
    const until = String(body.until || "");
    const parsed = Date.parse(until);
    if (!until || !Number.isFinite(parsed)) throw new EdgeError(422, "retention deadline is invalid");
    await env.DB.prepare("UPDATE mail_messages SET protected_until = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE message_id = ? AND deleted_at IS NULL")
      .bind(new Date(parsed).toISOString(), safeMessageId).run();
    return { message_id: safeMessageId, protected_until: new Date(parsed).toISOString() };
  }
  if (row.deleted_at) return { message_id: safeMessageId, deleted: true };
  if (row.protected_until && Date.parse(row.protected_until) > Date.now()) throw new EdgeError(409, "message is protected by an active verification retention hold");
  await rawStoreForRow(env, row).delete(row);
  return { message_id: safeMessageId, deleted: true };
}

export async function cleanupExpiredMail(env: Env, now = Date.now()): Promise<number> {
  const rawDays = envNumber(env.MAIL_EDGE_RETENTION_DAYS, 7, 1, 90);
  const processedDays = envNumber(env.MAIL_EDGE_PROCESSED_RETENTION_DAYS, 1, 0, 30);
  const incompleteCutoff = new Date(now - incompleteRetentionSeconds(env) * 1000).toISOString();
  const rawCutoff = new Date(now - rawDays * 24 * 60 * 60 * 1000).toISOString();
  const processedCutoff = new Date(now - processedDays * 24 * 60 * 60 * 1000).toISOString();
  const result = await env.DB.prepare(
    `SELECT * FROM mail_messages
      WHERE deleted_at IS NULL
        AND (
          (storage_state = 'RECEIVING' AND created_at <= ?)
          OR (storage_state IN ('STORED', 'EVENT_READY', 'ACKNOWLEDGED')
              AND (protected_until IS NULL OR protected_until <= ?)
              AND ((processed_at IS NULL AND received_at <= ?)
                   OR (processed_at IS NOT NULL AND processed_at <= ?)))
        )
      ORDER BY created_at ASC
      LIMIT ?`,
  ).bind(incompleteCutoff, new Date(now).toISOString(), rawCutoff, processedCutoff, CLEANUP_BATCH_LIMIT).all<MessageRow>();
  let deleted = 0;
  for (const row of result.results) {
    await rawStoreForRow(env, row).delete(row);
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

  const limit = maxMessageBytes(env);
  if (Number.isFinite(message.rawSize) && message.rawSize > limit) {
    message.setReject("message exceeds the bounded size limit");
    return { accepted: false, reason: "message_too_large" };
  }

  const backend = storageBackend(env);
  const messageId = `m-${crypto.randomUUID().replaceAll("-", "")}`;
  const receivedAt = nowIso();
  const rawObjectKey = backend === "d1"
    ? `d1://mail-message/${messageId}`
    : `inbound/${identity.identity_id}/${messageId}.eml`;
  const store = rawStoreForNew(env, messageId, rawObjectKey);
  await store.begin({
    messageId,
    identityId: identity.identity_id,
    workerId: identity.worker_id,
    recipient,
    provisionalKey: `pending:${messageId}`,
    rawObjectKey,
    receivedAt,
  });

  const reader = message.raw.getReader();
  const whole = new StreamingSha256();
  let header = new Uint8Array();
  let total = 0;
  let chunkIndex = 0;
  let chunkLength = 0;
  let chunkBuffer = new Uint8Array(D1_RAW_CHUNK_SIZE);
  try {
    while (true) {
      const next = await reader.read();
      if (next.done) break;
      const value = next.value;
      total += value.byteLength;
      if (total > limit) {
        await reader.cancel();
        await store.discard("REJECTED_OVERSIZE", total);
        message.setReject("message exceeds the bounded size limit");
        return { accepted: false, reason: "message_too_large" };
      }
      if (header.byteLength < HEADER_SCAN_BYTES) {
        header = concatBytes(header, value.subarray(0, HEADER_SCAN_BYTES - header.byteLength));
      }
      whole.update(value);
      let offset = 0;
      while (offset < value.byteLength) {
        const take = Math.min(D1_RAW_CHUNK_SIZE - chunkLength, value.byteLength - offset);
        chunkBuffer.set(value.subarray(offset, offset + take), chunkLength);
        chunkLength += take;
        offset += take;
        if (chunkLength === D1_RAW_CHUNK_SIZE) {
          await store.appendChunk(chunkIndex, chunkBuffer);
          chunkIndex += 1;
          chunkLength = 0;
          chunkBuffer = new Uint8Array(D1_RAW_CHUNK_SIZE);
        }
      }
    }
    if (chunkLength > 0) {
      await store.appendChunk(chunkIndex, chunkBuffer.subarray(0, chunkLength));
      chunkIndex += 1;
    }
  } catch (error) {
    // Leave RECEIVING metadata and any durable chunks in place. A retry can
    // arrive safely, while the bounded cleanup job reclaims abandoned writes.
    throw error;
  }

  const rawSha256 = bytesToHex(whole.digest());
  const rfcMessageId = headerMessageId(header);
  const sourceId = rfcMessageId || `raw:${rawSha256}`;
  const providerKey = `${recipient}\u0000${sourceId}`;
  const existing = await env.DB.prepare("SELECT * FROM mail_messages WHERE provider_key = ?")
    .bind(providerKey).first<MessageRow>();
  if (existing && existing.message_id !== messageId) {
    await store.discard("REJECTED", total, rawSha256);
    if (existing.content_hash === rawSha256) {
      return { accepted: true, duplicate: true, event_id: existing.event_id, message_id: existing.message_id };
    }
    message.setReject("provider event content conflict");
    return { accepted: false, reason: "provider_event_conflict" };
  }

  const final: FinalMessage = {
    providerKey,
    providerMessageId: rfcMessageId,
    rfcMessageId,
    sender: safeHeaderSender(header),
    subject: headerValue(header, "Subject"),
    receivedAt,
    rawSize: total,
    rawSha256,
    chunkCount: chunkIndex,
  };
  try {
    await store.finalize(final);
  } catch (error) {
    // A concurrent delivery can win the provider-key uniqueness race after
    // the preflight lookup. Resolve that race without emitting a second event.
    const concurrent = await env.DB.prepare("SELECT * FROM mail_messages WHERE provider_key = ?")
      .bind(providerKey).first<MessageRow>();
    if (concurrent && concurrent.message_id !== messageId) {
      await store.discard("REJECTED", total, rawSha256);
      if (concurrent.content_hash === rawSha256) {
        return { accepted: true, duplicate: true, event_id: concurrent.event_id, message_id: concurrent.message_id };
      }
      message.setReject("provider event content conflict");
      return { accepted: false, reason: "provider_event_conflict" };
    }
    throw error;
  }

  const stored = await env.DB.prepare("SELECT * FROM mail_messages WHERE message_id = ?").bind(messageId).first<MessageRow>();
  if (!stored) throw new EdgeError(503, "finalized message metadata is unavailable");
  // D1RawMessageStore.finalize already verified every persisted chunk and the
  // whole stream. Recovery calls publishEvent without this fast-path flag.
  const published = await publishEvent(env, stored, true);
  return {
    accepted: true,
    duplicate: published.duplicate,
    event_id: published.eventId,
    message_id: published.messageId,
  };
}

async function handleApiRequest(request: Request, env: Env): Promise<Response> {
  let body: Uint8Array;
  try {
    body = await readRequestBody(request, MAX_API_BODY_BYTES);
    if (!(await verifySignedRequest(request, env, body))) return jsonResponse(401, { error: "invalid mail-edge authentication" });
    const url = new URL(request.url);
    const input = requestBodyObject(body);
    if (request.method === "GET" && url.pathname === "/v1/health") {
      const backend = storageBackend(env);
      return jsonResponse(200, { status: "ok", provider: "cloudflare", storage: { metadata: "d1", raw: backend }, storage_backend: backend });
    }
    if (request.method === "POST" && url.pathname === "/v1/recipients/register") return jsonResponse(200, await registerRecipient(env, input));
    if (request.method === "POST" && ["activate", "suspend", "retire"].some((action) => url.pathname === `/v1/recipients/${action}`)) {
      return jsonResponse(200, await lifecycleRecipient(env, url.pathname.split("/").pop() || "", input));
    }
    if (request.method === "POST" && url.pathname === "/v1/recipients/alias") return jsonResponse(200, await addAlias(env, input));
    if (request.method === "GET" && url.pathname === "/v1/events") return jsonResponse(200, await listEvents(env, url));
    if (request.method === "POST" && /^\/v1\/events\/[^/]+\/ack$/.test(url.pathname)) {
      const eventId = safeId(url.pathname.split("/")[3] || "", "event_id");
      const event = await env.DB.prepare("SELECT * FROM inbound_events WHERE event_id = ?").bind(eventId).first<EventRow>();
      if (!event) throw new EdgeError(404, "event not found");
      const acknowledgedAt = nowIso();
      await env.DB.batch([
        env.DB.prepare("UPDATE inbound_events SET acked_at = COALESCE(acked_at, ?) WHERE event_id = ?").bind(acknowledgedAt, eventId),
        env.DB.prepare("UPDATE mail_messages SET storage_state = 'ACKNOWLEDGED', updated_at = ? WHERE message_id = ? AND deleted_at IS NULL").bind(acknowledgedAt, event.message_id),
      ]);
      return jsonResponse(200, { event_id: eventId, acknowledged: true });
    }
    const messageMatch = url.pathname.match(/^\/v1\/messages\/([^/]+)$/);
    if (request.method === "GET" && messageMatch) {
      const workerScope = url.searchParams.get("worker_id") || undefined;
      return jsonResponse(200, await fetchMessage(env, decodeURIComponent(messageMatch[1]), workerScope));
    }
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
