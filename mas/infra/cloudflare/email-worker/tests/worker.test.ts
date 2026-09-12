import { describe, expect, it } from "vitest";
import worker, {
  D1_RAW_CHUNK_SIZE,
  cleanupExpiredMail,
  handleEmailMessage,
  normalizeEmailAddress,
  type Env,
} from "../src/index";

type Row = Record<string, any>;

class Statement {
  values: unknown[] = [];

  constructor(private readonly db: TinyD1, private readonly sql: string) {}

  bind(...values: unknown[]): Statement {
    this.values = values;
    return this;
  }

  async first<T>(): Promise<T | null> {
    return this.db.first<T>(this.sql, this.values);
  }

  async all<T>(): Promise<{ results: T[] }> {
    return { results: this.db.all<T>(this.sql, this.values) };
  }

  async run(): Promise<{ results: Row[]; meta: { changes: number } }> {
    return { results: [], meta: { changes: this.db.run(this.sql, this.values) } };
  }
}

class TinyD1 {
  recipients = new Map<string, Row>();
  messages = new Map<string, Row>();
  events = new Map<string, Row>();
  chunks = new Map<string, Map<number, Row>>();
  nonces = new Map<string, number>();
  sequence = 0;
  failChunkWriteAt: number | undefined;
  chunkWriteCount = 0;
  failEventInsertOnce = false;
  failStoredUpdateOnce = false;

  prepare(sql: string): Statement {
    return new Statement(this, sql);
  }

  async batch(statements: Statement[]): Promise<any[]> {
    const results: any[] = [];
    for (const statement of statements) results.push(await statement.run());
    return results;
  }

  private normalized(sql: string): string {
    return sql.replace(/\s+/g, " ").trim();
  }

  first<T>(source: string, values: unknown[]): T | null {
    const sql = this.normalized(source);
    if (sql.includes("FROM recipient_registry")) {
      if (sql.includes("idempotency_key = ?")) {
        return ([...this.recipients.values()].find((row) => row.idempotency_key === values[0]) as T | undefined) || null;
      }
      if (sql.includes("provider_reference = ? AND address = ?")) {
        return ([...this.recipients.values()].find((row) => row.provider_reference === values[0] && row.address === values[1]) as T | undefined) || null;
      }
      if (sql.includes("address = ? AND provider_reference = ?")) {
        return ([...this.recipients.values()].find((row) => row.address === values[0] && row.provider_reference === values[1]) as T | undefined) || null;
      }
      if (sql.includes("provider_reference = ?")) {
        return ([...this.recipients.values()].find((row) => row.provider_reference === values[0]) as T | undefined) || null;
      }
      if (sql.includes("address = ?")) {
        return (this.recipients.get(String(values[0])) as T | undefined) || null;
      }
    }
    if (sql.includes("FROM mail_messages")) {
      if (sql.includes("provider_key = ?")) {
        return ([...this.messages.values()].find((row) => row.provider_key === values[0]) as T | undefined) || null;
      }
      if (sql.includes("message_id = ?")) return (this.messages.get(String(values[0])) as T | undefined) || null;
    }
    if (sql.includes("FROM inbound_events")) {
      if (sql.includes("provider_key = ?")) {
        return ([...this.events.values()].find((row) => row.provider_key === values[0]) as T | undefined) || null;
      }
      if (sql.includes("event_id = ?")) return (this.events.get(String(values[0])) as T | undefined) || null;
    }
    if (sql.includes("FROM mail_message_chunks")) {
      const messageId = String(values[0]);
      const messageChunks = this.chunks.get(messageId) || new Map<number, Row>();
      if (sql.includes("COUNT(*)")) {
        let totalSize = 0;
        for (const row of messageChunks.values()) totalSize += Number(row.chunk_size_bytes);
        return { chunk_count: messageChunks.size, total_size: totalSize } as T;
      }
      const row = messageChunks.get(Number(values[1]));
      return (row as T | undefined) || null;
    }
    return null;
  }

  all<T>(source: string, values: unknown[]): T[] {
    const sql = this.normalized(source);
    if (sql.includes("FROM mail_messages")) {
      let rows = [...this.messages.values()].filter((row) => row.deleted_at === null);
      if (sql.includes("storage_state IN ('STORED', 'EVENT_READY')")) {
        rows = rows.filter((row) => ["STORED", "EVENT_READY"].includes(row.storage_state));
        rows.sort((left, right) => String(left.updated_at).localeCompare(String(right.updated_at)));
        return rows.slice(0, Number(values[0])) as T[];
      }
      if (sql.includes("storage_state = 'RECEIVING'")) {
        const incompleteCutoff = String(values[0]);
        const now = String(values[1]);
        const rawCutoff = String(values[2]);
        const processedCutoff = String(values[3]);
        const limit = Number(values[4]);
        rows = rows.filter((row) => {
          if (row.storage_state === "RECEIVING") return String(row.created_at) <= incompleteCutoff;
          if (!["STORED", "EVENT_READY", "ACKNOWLEDGED"].includes(row.storage_state)) return false;
          if (row.protected_until !== null && String(row.protected_until) > now) return false;
          return row.processed_at === null
            ? String(row.received_at) <= rawCutoff
            : String(row.processed_at) <= processedCutoff;
        });
        rows.sort((left, right) => String(left.created_at).localeCompare(String(right.created_at)));
        return rows.slice(0, limit) as T[];
      }
    }
    if (sql.includes("FROM inbound_events")) {
      const after = Number(values[0]);
      const limit = Number(values[1]);
      return [...this.events.values()]
        .filter((row) => Number(row.sequence) > after)
        .sort((left, right) => Number(left.sequence) - Number(right.sequence))
        .slice(0, limit)
        .map((row) => ({
          sequence: row.sequence,
          event_id: row.event_id,
          message_id: row.message_id,
          provider_message_id: row.provider_message_id,
          identity_id: row.identity_id,
          worker_id: row.worker_id,
          envelope_recipient: row.envelope_recipient,
          event_type: row.event_type,
          received_at: row.received_at,
          raw_object_key: row.raw_object_key,
          raw_size: row.raw_size,
        })) as T[];
    }
    return [];
  }

  run(source: string, values: unknown[]): number {
    const sql = this.normalized(source);
    if (sql.startsWith("DELETE FROM edge_nonces")) {
      const cutoff = Number(values[0]);
      for (const [nonce, expiry] of this.nonces) if (expiry <= cutoff) this.nonces.delete(nonce);
      return 0;
    }
    if (sql.startsWith("INSERT OR IGNORE INTO edge_nonces")) {
      const nonce = String(values[0]);
      if (this.nonces.has(nonce)) return 0;
      this.nonces.set(nonce, Number(values[1]));
      return 1;
    }
    if (sql.startsWith("INSERT INTO recipient_registry")) {
      const row = {
        identity_id: values[0],
        worker_id: values[1],
        address: values[2],
        provider_reference: values[3],
        idempotency_key: values[4],
        state: "VERIFYING",
      };
      this.recipients.set(String(row.address), row);
      return 1;
    }
    if (sql.startsWith("UPDATE recipient_registry SET state")) {
      let changes = 0;
      for (const row of this.recipients.values()) {
        if (row.provider_reference === values[1]) {
          row.state = values[0];
          changes += 1;
        }
      }
      return changes;
    }
    if (sql.startsWith("INSERT INTO mail_messages")) {
      const now = values[8];
      this.messages.set(String(values[0]), {
        message_id: values[0],
        event_id: null,
        provider_key: values[1],
        provider_message_id: null,
        rfc_message_id: null,
        identity_id: values[2],
        worker_id: values[3],
        envelope_recipient: values[4],
        sender: null,
        subject: "",
        received_at: values[5],
        raw_object_key: values[6],
        raw_size: 0,
        content_hash: "",
        storage_backend: values[7],
        storage_state: "RECEIVING",
        chunk_count: 0,
        processed_at: null,
        protected_until: null,
        deleted_at: null,
        created_at: now,
        updated_at: values[9],
      });
      return 1;
    }
    if (sql.startsWith("INSERT OR IGNORE INTO mail_message_chunks")) {
      this.chunkWriteCount += 1;
      if (this.failChunkWriteAt === this.chunkWriteCount) {
        this.failChunkWriteAt = undefined;
        throw new Error("fixture chunk write failure");
      }
      const messageId = String(values[0]);
      const messageChunks = this.chunks.get(messageId) || new Map<number, Row>();
      this.chunks.set(messageId, messageChunks);
      const index = Number(values[1]);
      if (messageChunks.has(index)) return 0;
      messageChunks.set(index, {
        chunk_index: index,
        chunk_data: Array.from(values[2] as Uint8Array),
        chunk_size_bytes: values[3],
        chunk_sha256: values[4],
        created_at: values[5],
      });
      return 1;
    }
    if (sql.startsWith("DELETE FROM mail_message_chunks")) {
      this.chunks.delete(String(values[0]));
      return 1;
    }
    if (sql.startsWith("UPDATE mail_messages SET provider_key")) {
      if (this.failStoredUpdateOnce) {
        this.failStoredUpdateOnce = false;
        throw new Error("fixture final state write failure");
      }
      const row = this.messages.get(String(values[10]));
      if (!row || row.storage_state !== "RECEIVING" || row.deleted_at !== null) return 0;
      Object.assign(row, {
        provider_key: values[0],
        provider_message_id: values[1],
        rfc_message_id: values[2],
        sender: values[3],
        subject: values[4],
        received_at: values[5],
        raw_size: values[6],
        content_hash: values[7],
        chunk_count: values[8],
        updated_at: values[9],
        storage_state: "STORED",
      });
      return 1;
    }
    if (sql.startsWith("UPDATE mail_messages SET event_id")) {
      const row = this.messages.get(String(values[2]));
      if (!row || row.deleted_at !== null) return 0;
      row.event_id = values[0];
      row.storage_state = "EVENT_READY";
      row.updated_at = values[1];
      return 1;
    }
    if (sql.startsWith("UPDATE mail_messages SET processed_at")) {
      const row = this.messages.get(String(values[0]));
      if (!row || row.deleted_at !== null) return 0;
      row.processed_at ||= new Date().toISOString();
      row.updated_at = new Date().toISOString();
      return 1;
    }
    if (sql.startsWith("UPDATE mail_messages SET protected_until")) {
      const row = this.messages.get(String(values[1]));
      if (!row || row.deleted_at !== null) return 0;
      row.protected_until = values[0];
      row.updated_at = new Date().toISOString();
      return 1;
    }
    if (sql.startsWith("UPDATE mail_messages SET storage_state = 'ACKNOWLEDGED'")) {
      const row = this.messages.get(String(values[1]));
      if (!row || row.deleted_at !== null) return 0;
      row.storage_state = "ACKNOWLEDGED";
      row.updated_at = values[0];
      return 1;
    }
    if (sql.startsWith("UPDATE mail_messages SET deleted_at")) {
      const row = this.messages.get(String(values[2]));
      if (!row || row.deleted_at !== null) return 0;
      row.deleted_at = values[0];
      row.storage_state = "DELETED";
      row.chunk_count = 0;
      row.updated_at = values[1];
      return 1;
    }
    if (sql.startsWith("UPDATE mail_messages SET storage_state = ?, raw_size")) {
      const row = this.messages.get(String(values[5]));
      if (!row || row.deleted_at !== null) return 0;
      row.storage_state = values[0];
      row.raw_size = values[1];
      row.content_hash = values[2];
      row.chunk_count = 0;
      row.deleted_at = values[3];
      row.updated_at = values[4];
      return 1;
    }
    if (sql.startsWith("INSERT OR IGNORE INTO inbound_events")) {
      if (this.failEventInsertOnce) {
        this.failEventInsertOnce = false;
        throw new Error("fixture event write failure");
      }
      const providerKey = String(values[3]);
      if ([...this.events.values()].some((row) => row.provider_key === providerKey || row.event_id === values[0])) return 0;
      this.sequence += 1;
      this.events.set(String(values[0]), {
        sequence: this.sequence,
        event_id: values[0],
        message_id: values[1],
        provider_message_id: values[2],
        provider_key: providerKey,
        identity_id: values[4],
        worker_id: values[5],
        envelope_recipient: values[6],
        event_type: "inbound.message.received",
        received_at: values[7],
        raw_object_key: values[8],
        raw_size: values[9],
        content_hash: values[10],
        acked_at: null,
      });
      return 1;
    }
    if (sql.startsWith("UPDATE inbound_events SET acked_at")) {
      const row = this.events.get(String(values[1]));
      if (!row) return 0;
      row.acked_at ||= values[0];
      return 1;
    }
    if (sql.startsWith("INSERT INTO recipient_registry")) return 1;
    return 1;
  }
}

class TinyR2 {
  objects = new Map<string, Uint8Array>();

  async put(key: string, value: ArrayBuffer | ArrayBufferView | string): Promise<void> {
    if (typeof value === "string") {
      this.objects.set(key, new TextEncoder().encode(value));
      return;
    }
    const bytes = value instanceof ArrayBuffer
      ? new Uint8Array(value)
      : new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
    this.objects.set(key, new Uint8Array(bytes));
  }

  async get(key: string): Promise<{ arrayBuffer(): Promise<ArrayBuffer> } | null> {
    const value = this.objects.get(key);
    return value
      ? { arrayBuffer: async () => value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength) }
      : null;
  }

  async delete(key: string): Promise<void> {
    this.objects.delete(key);
  }
}

function environment(db = new TinyD1(), r2?: TinyR2, backend: "d1" | "r2" = "d1"): Env & { db: TinyD1; r2?: TinyR2 } {
  return {
    DB: db,
    ...(r2 ? { MAIL_OBJECTS: r2 } : {}),
    MAIL_EDGE_AUTH_SECRET: "fixture-mail-edge-secret-012345",
    MAIL_EDGE_STORAGE_BACKEND: backend,
    MAIL_EDGE_MAX_MESSAGE_BYTES: "4194304",
    MAIL_EDGE_AUTH_TOLERANCE_SECONDS: "300",
    MAIL_EDGE_RETENTION_DAYS: "7",
    MAIL_EDGE_PROCESSED_RETENTION_DAYS: "1",
    db,
    r2,
  };
}

function addRecipient(db: TinyD1, address: string, identity = "identity-a", worker = "worker-a", state = "ACTIVE"): void {
  db.recipients.set(address, {
    identity_id: identity,
    worker_id: worker,
    address,
    provider_reference: `recipient:${worker}`,
    idempotency_key: `mailbox:${worker}`,
    state,
  });
}

function bytes(text: string): Uint8Array {
  return new TextEncoder().encode(text);
}

function plaintextMessage(id = "<plain@example.test>"): Uint8Array {
  return bytes(
    `From: sender@example.test\r\nTo: spoofed-other@agents.aiat.ca\r\nMessage-ID: ${id}\r\nSubject: Code\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nYour code is 481516\r\n`,
  );
}

function htmlMessage(id = "<html@example.test>"): Uint8Array {
  return bytes(
    `From: sender@example.test\r\nTo: spoofed-other@agents.aiat.ca\r\nMessage-ID: ${id}\r\nSubject: HTML Code\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<p>Your code is <b>481516</b>.</p>\r\n`,
  );
}

function multipartMessage(id = "<multipart@example.test>"): Uint8Array {
  return bytes(
    `From: sender@example.test\r\nTo: spoofed-other@agents.aiat.ca\r\nMessage-ID: ${id}\r\nSubject: Multipart Code\r\nContent-Type: multipart/alternative; boundary="aiat-boundary"\r\n\r\n--aiat-boundary\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nYour code is 481516.\r\n--aiat-boundary\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<p>Your code is <b>481516</b>.</p>\r\n--aiat-boundary--\r\n`,
  );
}

function binaryMessage(size = 1024, id = "<binary@example.test>"): Uint8Array {
  const header = bytes(
    `From: sender@example.test\r\nTo: spoofed-other@agents.aiat.ca\r\nMessage-ID: ${id}\r\nSubject: Binary\r\nContent-Type: application/octet-stream\r\n\r\n`,
  );
  const body = new Uint8Array(size);
  for (let index = 0; index < body.length; index += 1) body[index] = (index * 37 + 11) & 0xff;
  return new Uint8Array([...header, ...body]);
}

function stream(value: Uint8Array, segment = value.byteLength): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (let offset = 0; offset < value.byteLength; offset += segment) controller.enqueue(value.slice(offset, offset + segment));
      controller.close();
    },
  });
}

async function deliver(
  env: Env,
  address: string,
  raw: Uint8Array,
  options: { segment?: number; rawSize?: number } = {},
): Promise<{ result: Record<string, any>; rejected: string[] }> {
  const rejected: string[] = [];
  const result = await handleEmailMessage(
    {
      from: "sender@example.test",
      to: address,
      rawSize: options.rawSize ?? raw.byteLength,
      raw: stream(raw, options.segment ?? raw.byteLength),
      setReject: (reason: string) => rejected.push(reason),
    },
    env,
  );
  return { result, rejected };
}

let nonceCounter = 0;

async function signedRequest(env: Env, method: string, path: string, payload?: Record<string, unknown>): Promise<Request> {
  const body = payload === undefined ? new Uint8Array() : bytes(JSON.stringify(payload));
  const timestamp = Math.floor(Date.now() / 1000).toString();
  const nonce = `nonce-${++nonceCounter}-0123456789`;
  const digest = await crypto.subtle.digest("SHA-256", body);
  const digestHex = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
  const canonical = `aiat.mail-edge.v1\n${method}\n${path}\n${timestamp}\n${nonce}\n${digestHex}`;
  const key = await crypto.subtle.importKey("raw", bytes(env.MAIL_EDGE_AUTH_SECRET), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const signature = btoa(String.fromCharCode(...new Uint8Array(await crypto.subtle.sign("HMAC", key, bytes(canonical)))));
  return new Request(`https://edge.test${path}`, {
    method,
    headers: {
      "content-type": "application/json",
      "X-AIAT-Mail-Edge-Version": "aiat.mail-edge.v1",
      "X-AIAT-Mail-Edge-Timestamp": timestamp,
      "X-AIAT-Mail-Edge-Nonce": nonce,
      "X-AIAT-Mail-Edge-Signature": signature,
    },
    body: body.byteLength ? body : undefined,
  });
}

async function api(env: Env, method: string, path: string, payload?: Record<string, unknown>): Promise<Response> {
  return worker.fetch(await signedRequest(env, method, path, payload), env, {} as any);
}

function decodeBase64(value: string): Uint8Array {
  const binary = atob(value);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

describe("AIAT Cloudflare Email Worker D1 backend", () => {
  it("normalizes addresses and rejects ambiguous recipients", () => {
    expect(normalizeEmailAddress("W-1@Agents.AIAT.CA")).toBe("w-1@agents.aiat.ca");
    expect(() => normalizeEmailAddress("w-1@agents.aiat.ca extra")).toThrow();
  });

  it("round-trips a plaintext verification email exactly through D1 chunks", async () => {
    const env = environment();
    addRecipient(env.db, "worker-a@agents.aiat.ca");
    const raw = plaintextMessage();
    const delivered = await deliver(env, "worker-a@agents.aiat.ca", raw);
    expect(delivered.result.accepted).toBe(true);
    const row = env.db.messages.get(String(delivered.result.message_id)) as Row;
    expect(row.storage_backend).toBe("d1");
    expect(row.storage_state).toBe("EVENT_READY");
    expect(JSON.stringify(row)).not.toContain("Your code");
    const digest = await crypto.subtle.digest("SHA-256", raw);
    expect(row.content_hash).toBe(Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join(""));
    const response = await api(env, "GET", `/v1/messages/${delivered.result.message_id}`);
    expect(response.status).toBe(200);
    expect(decodeBase64((await response.json() as Row).raw_mime_base64)).toEqual(raw);
  });

  it("round-trips an HTML verification email without changing its bytes", async () => {
    const env = environment();
    addRecipient(env.db, "worker-a@agents.aiat.ca");
    const raw = htmlMessage();
    const delivered = await deliver(env, "worker-a@agents.aiat.ca", raw);
    const response = await api(env, "GET", `/v1/messages/${delivered.result.message_id}`);
    expect(response.status).toBe(200);
    expect(decodeBase64((await response.json() as Row).raw_mime_base64)).toEqual(raw);
  });

  it("round-trips multipart MIME with no raw body in event metadata", async () => {
    const env = environment();
    addRecipient(env.db, "worker-a@agents.aiat.ca");
    const raw = multipartMessage();
    const delivered = await deliver(env, "worker-a@agents.aiat.ca", raw, { segment: 17 });
    expect(delivered.result.accepted).toBe(true);
    expect(JSON.stringify([...env.db.events.values()])).not.toContain("Your code");
    const response = await api(env, "GET", `/v1/messages/${delivered.result.message_id}`);
    expect(decodeBase64((await response.json() as Row).raw_mime_base64)).toEqual(raw);
  });

  it("preserves binary MIME bytes exactly", async () => {
    const env = environment();
    addRecipient(env.db, "worker-a@agents.aiat.ca");
    const raw = binaryMessage(4096);
    const delivered = await deliver(env, "worker-a@agents.aiat.ca", raw, { segment: 31 });
    const response = await api(env, "GET", `/v1/messages/${delivered.result.message_id}`);
    expect(decodeBase64((await response.json() as Row).raw_mime_base64)).toEqual(raw);
  });

  it("stores a multi-chunk message under the conservative D1 chunk bound", async () => {
    const env = environment();
    addRecipient(env.db, "worker-a@agents.aiat.ca");
    const raw = binaryMessage(D1_RAW_CHUNK_SIZE * 2 + 17, "<multi@example.test>");
    const delivered = await deliver(env, "worker-a@agents.aiat.ca", raw, { segment: 4093 });
    const chunks = env.db.chunks.get(String(delivered.result.message_id));
    expect(chunks?.size).toBe(3);
    expect([...chunks!.keys()]).toEqual([0, 1, 2]);
    expect([...chunks!.values()].map((chunk) => chunk.chunk_size_bytes)).toEqual([D1_RAW_CHUNK_SIZE, D1_RAW_CHUNK_SIZE, 17 + binaryMessage(0, "<multi@example.test>").byteLength]);
  });

  it("reconstructs by chunk index even when the local rows are visited in reverse order", async () => {
    const env = environment();
    addRecipient(env.db, "worker-a@agents.aiat.ca");
    const raw = binaryMessage(D1_RAW_CHUNK_SIZE + 33, "<ordered@example.test>");
    const delivered = await deliver(env, "worker-a@agents.aiat.ca", raw, { segment: 97 });
    const messageChunks = env.db.chunks.get(String(delivered.result.message_id))!;
    const reversed = new Map([...messageChunks.entries()].reverse());
    env.db.chunks.set(String(delivered.result.message_id), reversed);
    const response = await api(env, "GET", `/v1/messages/${delivered.result.message_id}`);
    expect(decodeBase64((await response.json() as Row).raw_mime_base64)).toEqual(raw);
  });

  it("rejects a tampered chunk through checksum validation", async () => {
    const env = environment();
    addRecipient(env.db, "worker-a@agents.aiat.ca");
    const delivered = await deliver(env, "worker-a@agents.aiat.ca", binaryMessage(2048));
    const chunk = env.db.chunks.get(String(delivered.result.message_id))!.get(0)!;
    (chunk.chunk_data as number[])[0] ^= 0xff;
    const response = await api(env, "GET", `/v1/messages/${delivered.result.message_id}`);
    expect(response.status).toBe(503);
  });

  it("leaves partial writes reclaimable and accepts a later retry", async () => {
    const env = environment();
    env.MAIL_EDGE_INCOMPLETE_RETENTION_SECONDS = "1";
    addRecipient(env.db, "worker-a@agents.aiat.ca");
    env.db.failChunkWriteAt = 2;
    const raw = binaryMessage(D1_RAW_CHUNK_SIZE + 20, "<partial@example.test>");
    await expect(deliver(env, "worker-a@agents.aiat.ca", raw)).rejects.toThrow("fixture chunk write failure");
    const partial = [...env.db.messages.values()][0];
    expect(partial.storage_state).toBe("RECEIVING");
    expect(env.db.events.size).toBe(0);
    expect(env.db.chunks.get(partial.message_id)?.size).toBe(1);
    partial.created_at = new Date(Date.now() - 2_000).toISOString();
    expect(await cleanupExpiredMail(env, Date.now())).toBe(1);
    expect(env.db.chunks.get(partial.message_id)).toBeUndefined();
    const retry = await deliver(env, "worker-a@agents.aiat.ca", raw);
    expect(retry.result.accepted).toBe(true);
    expect(env.db.events.size).toBe(1);
  });

  it("reclaims a complete chunk set when the final storage-state commit fails", async () => {
    const env = environment();
    env.MAIL_EDGE_INCOMPLETE_RETENTION_SECONDS = "1";
    addRecipient(env.db, "worker-a@agents.aiat.ca");
    env.db.failStoredUpdateOnce = true;
    await expect(deliver(env, "worker-a@agents.aiat.ca", plaintextMessage("<state-commit@example.test>"))).rejects.toThrow("fixture final state write failure");
    const row = [...env.db.messages.values()][0];
    expect(row.storage_state).toBe("RECEIVING");
    expect(env.db.events.size).toBe(0);
    expect(env.db.chunks.get(row.message_id)?.size).toBe(1);
    row.created_at = new Date(Date.now() - 2_000).toISOString();
    expect(await cleanupExpiredMail(env, Date.now())).toBe(1);
    expect(env.db.chunks.get(row.message_id)).toBeUndefined();
  });

  it("makes duplicate delivery retries idempotent and recipient-scoped", async () => {
    const env = environment();
    addRecipient(env.db, "worker-a@agents.aiat.ca");
    const raw = plaintextMessage("<duplicate@example.test>");
    const first = await deliver(env, "worker-a@agents.aiat.ca", raw);
    const retry = await deliver(env, "worker-a@agents.aiat.ca", raw);
    expect(first.result.accepted).toBe(true);
    expect(retry.result).toMatchObject({ accepted: true, duplicate: true, message_id: first.result.message_id });
    expect(env.db.events.size).toBe(1);
    expect([...env.db.messages.values()].filter((row) => row.storage_state === "REJECTED")).toHaveLength(1);
  });

  it("marks a streaming oversize delivery rejected without publishing an event", async () => {
    const env = environment();
    env.MAIL_EDGE_MAX_MESSAGE_BYTES = "1024";
    addRecipient(env.db, "worker-a@agents.aiat.ca");
    const raw = binaryMessage(1025, "<oversize@example.test>");
    const delivered = await deliver(env, "worker-a@agents.aiat.ca", raw, { rawSize: 0 });
    expect(delivered.result).toEqual({ accepted: false, reason: "message_too_large" });
    expect(delivered.rejected).toHaveLength(1);
    expect(env.db.events.size).toBe(0);
    expect([...env.db.messages.values()][0].storage_state).toBe("REJECTED_OVERSIZE");
    expect(env.db.chunks.size).toBe(0);
  });

  it("rejects unknown recipients before creating D1 message state", async () => {
    const env = environment();
    const delivered = await deliver(env, "unknown@agents.aiat.ca", plaintextMessage());
    expect(delivered.result).toEqual({ accepted: false, reason: "unknown_recipient" });
    expect(env.db.messages.size).toBe(0);
    expect(env.db.chunks.size).toBe(0);
  });

  it("rejects suspended recipients", async () => {
    const env = environment();
    addRecipient(env.db, "suspended@agents.aiat.ca", "identity-s", "worker-s", "SUSPENDED");
    const delivered = await deliver(env, "suspended@agents.aiat.ca", plaintextMessage());
    expect(delivered.result).toEqual({ accepted: false, reason: "recipient_inactive" });
    expect(env.db.messages.size).toBe(0);
  });

  it("rejects retired recipients", async () => {
    const env = environment();
    addRecipient(env.db, "retired@agents.aiat.ca", "identity-r", "worker-r", "RETIRED");
    const delivered = await deliver(env, "retired@agents.aiat.ca", plaintextMessage());
    expect(delivered.result).toEqual({ accepted: false, reason: "recipient_inactive" });
    expect(env.db.messages.size).toBe(0);
  });

  it("uses the SMTP envelope and ignores a forged To header for ownership", async () => {
    const env = environment();
    addRecipient(env.db, "worker-a@agents.aiat.ca", "identity-a", "worker-a");
    addRecipient(env.db, "worker-b@agents.aiat.ca", "identity-b", "worker-b");
    const delivered = await deliver(env, "worker-a@agents.aiat.ca", plaintextMessage("<forged-to@example.test>"));
    const row = env.db.messages.get(String(delivered.result.message_id)) as Row;
    expect(row.identity_id).toBe("identity-a");
    expect(row.worker_id).toBe("worker-a");
    expect(row.envelope_recipient).toBe("worker-a@agents.aiat.ca");
  });

  it("delivers the same RFC Message-ID independently to two recipients", async () => {
    const env = environment();
    addRecipient(env.db, "worker-a@agents.aiat.ca", "identity-a", "worker-a");
    addRecipient(env.db, "worker-b@agents.aiat.ca", "identity-b", "worker-b");
    const raw = plaintextMessage("<same-message-id@example.test>");
    const first = await deliver(env, "worker-a@agents.aiat.ca", raw);
    const second = await deliver(env, "worker-b@agents.aiat.ca", raw);
    expect(first.result.message_id).not.toBe(second.result.message_id);
    expect(env.db.events.size).toBe(2);
    expect([...env.db.messages.values()].map((row) => row.envelope_recipient).sort()).toEqual([
      "worker-a@agents.aiat.ca",
      "worker-b@agents.aiat.ca",
    ]);
  });

  it("enforces worker scope when a scoped message fetch is requested", async () => {
    const env = environment();
    addRecipient(env.db, "worker-a@agents.aiat.ca", "identity-a", "worker-a");
    addRecipient(env.db, "worker-b@agents.aiat.ca", "identity-b", "worker-b");
    const delivery = await deliver(env, "worker-b@agents.aiat.ca", plaintextMessage("<worker-scope@example.test>"));
    const denied = await api(env, "GET", `/v1/messages/${delivery.result.message_id}?worker_id=worker-a`);
    const allowed = await api(env, "GET", `/v1/messages/${delivery.result.message_id}?worker_id=worker-b`);
    expect(denied.status).toBe(404);
    expect(allowed.status).toBe(200);
  });

  it("requires a signed API request", async () => {
    const env = environment();
    const response = await worker.fetch(new Request("https://edge.test/v1/health"), env, {} as any);
    expect(response.status).toBe(401);
  });

  it("rejects replay of a signed request nonce", async () => {
    const env = environment();
    const request = await signedRequest(env, "GET", "/v1/health");
    const first = await worker.fetch(request, env, {} as any);
    const replay = await worker.fetch(request, env, {} as any);
    expect(first.status).toBe(200);
    expect(replay.status).toBe(401);
  });

  it("deletes D1 chunks during retention cleanup", async () => {
    const env = environment();
    addRecipient(env.db, "worker-a@agents.aiat.ca");
    const delivery = await deliver(env, "worker-a@agents.aiat.ca", binaryMessage(2048));
    const row = env.db.messages.get(String(delivery.result.message_id)) as Row;
    const now = Date.now();
    row.received_at = new Date(now - 8 * 24 * 60 * 60 * 1000).toISOString();
    row.created_at = row.received_at;
    expect(await cleanupExpiredMail(env, now)).toBe(1);
    expect(env.db.chunks.get(row.message_id)).toBeUndefined();
    expect(row.storage_state).toBe("DELETED");
  });

  it("honors an active verification retention hold", async () => {
    const env = environment();
    addRecipient(env.db, "worker-a@agents.aiat.ca");
    const delivery = await deliver(env, "worker-a@agents.aiat.ca", plaintextMessage());
    const row = env.db.messages.get(String(delivery.result.message_id)) as Row;
    const now = Date.now();
    row.received_at = new Date(now - 8 * 24 * 60 * 60 * 1000).toISOString();
    row.created_at = row.received_at;
    row.protected_until = new Date(now + 60 * 60 * 1000).toISOString();
    expect(await cleanupExpiredMail(env, now)).toBe(0);
    expect(env.db.chunks.get(row.message_id)?.size).toBe(1);
    row.protected_until = new Date(now - 1_000).toISOString();
    expect(await cleanupExpiredMail(env, now)).toBe(1);
    expect(env.db.chunks.get(row.message_id)).toBeUndefined();
  });

  it("reclaims an incomplete abandoned upload", async () => {
    const env = environment();
    env.MAIL_EDGE_INCOMPLETE_RETENTION_SECONDS = "1";
    addRecipient(env.db, "worker-a@agents.aiat.ca");
    env.db.failChunkWriteAt = 1;
    await expect(deliver(env, "worker-a@agents.aiat.ca", binaryMessage(D1_RAW_CHUNK_SIZE + 1))).rejects.toThrow();
    const row = [...env.db.messages.values()][0];
    row.created_at = new Date(Date.now() - 2_000).toISOString();
    expect(await cleanupExpiredMail(env, Date.now())).toBe(1);
    expect(env.db.chunks.get(row.message_id)).toBeUndefined();
    expect(row.storage_state).toBe("DELETED");
  });

  it("recovers an event after storage was finalized but event insertion failed", async () => {
    const env = environment();
    addRecipient(env.db, "worker-a@agents.aiat.ca");
    env.db.failEventInsertOnce = true;
    await expect(deliver(env, "worker-a@agents.aiat.ca", plaintextMessage("<offline@example.test>"))).rejects.toThrow("fixture event write failure");
    expect(env.db.events.size).toBe(0);
    const list = await api(env, "GET", "/v1/events?after=0&limit=10");
    expect(list.status).toBe(200);
    expect((await list.json() as Row).events).toHaveLength(1);
  });

  it("supports cursor catch-up after an offline interval", async () => {
    const env = environment();
    addRecipient(env.db, "worker-a@agents.aiat.ca");
    const first = await deliver(env, "worker-a@agents.aiat.ca", plaintextMessage("<cursor-1@example.test>"));
    await deliver(env, "worker-a@agents.aiat.ca", plaintextMessage("<cursor-2@example.test>"));
    const list = await api(env, "GET", "/v1/events?after=0&limit=1");
    const firstPage = await list.json() as Row;
    expect(firstPage.events).toHaveLength(1);
    expect(firstPage.next_cursor).toBe(1);
    const catchup = await api(env, "GET", `/v1/events?after=${firstPage.next_cursor}&limit=10`);
    const secondPage = await catchup.json() as Row;
    expect(secondPage.events).toHaveLength(1);
    expect(secondPage.events[0].message_id).not.toBe(first.result.message_id);
  });

  it("allows ACK retry and transitions the stored message to ACKNOWLEDGED", async () => {
    const env = environment();
    addRecipient(env.db, "worker-a@agents.aiat.ca");
    const delivery = await deliver(env, "worker-a@agents.aiat.ca", plaintextMessage("<ack@example.test>"));
    const eventId = String(delivery.result.event_id);
    const first = await api(env, "POST", `/v1/events/${eventId}/ack`, {});
    const retry = await api(env, "POST", `/v1/events/${eventId}/ack`, {});
    expect(first.status).toBe(200);
    expect(retry.status).toBe(200);
    expect((await retry.json() as Row).acknowledged).toBe(true);
    expect((env.db.messages.get(String(delivery.result.message_id)) as Row).storage_state).toBe("ACKNOWLEDGED");
  });

  it("uses no R2 binding in the default D1 environment", async () => {
    const env = environment();
    expect(env.MAIL_OBJECTS).toBeUndefined();
    addRecipient(env.db, "worker-a@agents.aiat.ca");
    const delivery = await deliver(env, "worker-a@agents.aiat.ca", plaintextMessage());
    expect(delivery.result.accepted).toBe(true);
    const health = await api(env, "GET", "/v1/health");
    expect(await health.json()).toMatchObject({ storage_backend: "d1", storage: { raw: "d1" } });
  });

  it("keeps the optional R2 backend working without changing the API", async () => {
    const r2 = new TinyR2();
    const env = environment(new TinyD1(), r2, "r2");
    addRecipient(env.db, "worker-a@agents.aiat.ca");
    const raw = plaintextMessage("<optional-r2@example.test>");
    const delivery = await deliver(env, "worker-a@agents.aiat.ca", raw);
    const row = env.db.messages.get(String(delivery.result.message_id)) as Row;
    expect(row.storage_backend).toBe("r2");
    expect(env.db.chunks.size).toBe(0);
    expect(r2.objects.size).toBe(1);
    const response = await api(env, "GET", `/v1/messages/${delivery.result.message_id}`);
    expect(decodeBase64((await response.json() as Row).raw_mime_base64)).toEqual(raw);
    const deleted = await api(env, "POST", `/v1/messages/${delivery.result.message_id}/delete`, { provider_reference: "recipient:worker-a" });
    expect(deleted.status).toBe(200);
    expect(r2.objects.size).toBe(0);
  });

  it("limits one cleanup invocation to a bounded batch", async () => {
    const env = environment();
    addRecipient(env.db, "worker-a@agents.aiat.ca");
    const now = Date.now();
    for (let index = 0; index < 21; index += 1) {
      const delivery = await deliver(env, "worker-a@agents.aiat.ca", plaintextMessage(`<batch-${index}@example.test>`));
      const row = env.db.messages.get(String(delivery.result.message_id)) as Row;
      row.received_at = new Date(now - 8 * 24 * 60 * 60 * 1000).toISOString();
      row.created_at = row.received_at;
    }
    expect(await cleanupExpiredMail(env, now)).toBe(20);
    expect([...env.db.messages.values()].filter((row) => row.storage_state !== "DELETED")).toHaveLength(1);
  });
});
