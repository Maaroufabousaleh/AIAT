import { describe, expect, it } from "vitest";
import worker, { handleEmailMessage, normalizeEmailAddress, type Env } from "../src/index";

type Row = Record<string, any>;

class Statement {
  values: unknown[] = [];
  constructor(private readonly db: TinyD1, private readonly sql: string) {}
  bind(...values: unknown[]): Statement {
    this.values = values;
    return this;
  }
  async first<T>(): Promise<T | null> {
    const sql = this.sql;
    if (sql.includes("FROM recipient_registry") && sql.includes("address = ?")) {
      return (this.db.recipients.get(String(this.values[0])) as T | undefined) || null;
    }
    if (sql.includes("FROM recipient_registry") && sql.includes("idempotency_key = ?")) {
      return [...this.db.recipients.values()].find((row) => row.idempotency_key === this.values[0]) as T || null;
    }
    if (sql.includes("FROM recipient_registry") && sql.includes("provider_reference = ?")) {
      return [...this.db.recipients.values()].find((row) => row.provider_reference === this.values[0]) as T || null;
    }
    if (sql.includes("FROM inbound_messages") && sql.includes("provider_key = ?")) {
      return [...this.db.messages.values()].find((row) => row.provider_key === this.values[0]) as T || null;
    }
    if (sql.includes("FROM inbound_messages") && sql.includes("message_id = ?")) {
      return this.db.messages.get(String(this.values[0])) as T || null;
    }
    return null;
  }
  async all<T>(): Promise<{ results: T[] }> {
    return { results: [...this.db.messages.values()] as T[] };
  }
  async run(): Promise<{ results: Row[]; meta: { changes: number } }> {
    if (this.sql.startsWith("DELETE FROM edge_nonces")) {
      const cutoff = Number(this.values[0]);
      for (const [nonce, expiry] of this.db.nonces) if (expiry <= cutoff) this.db.nonces.delete(nonce);
      return { results: [], meta: { changes: 0 } };
    }
    if (this.sql.startsWith("INSERT OR IGNORE INTO edge_nonces")) {
      const nonce = String(this.values[0]);
      if (this.db.nonces.has(nonce)) return { results: [], meta: { changes: 0 } };
      this.db.nonces.set(nonce, Number(this.values[1]));
      return { results: [], meta: { changes: 1 } };
    }
    return { results: [], meta: { changes: 1 } };
  }
}

class TinyD1 {
  recipients = new Map<string, Row>();
  messages = new Map<string, Row>();
  events = new Map<string, Row>();
  nonces = new Map<string, number>();
  sequence = 0;
  prepare(sql: string): Statement {
    return new Statement(this, sql);
  }
  async batch(statements: Statement[]): Promise<any[]> {
    const eventStatement = statements[0];
    const messageStatement = statements[1];
    const eventValues = eventStatement.values;
    const providerKey = String(eventValues[3]);
    if ([...this.events.values()].some((row) => row.provider_key === providerKey)) return [{ meta: { changes: 0 } }, { meta: { changes: 0 } }];
    this.sequence += 1;
    const event = {
      sequence: this.sequence,
      event_id: eventValues[0],
      message_id: eventValues[1],
      provider_message_id: eventValues[2],
      provider_key: providerKey,
      identity_id: eventValues[4],
      worker_id: eventValues[5],
      envelope_recipient: eventValues[6],
      event_type: "inbound.message.received",
      received_at: eventValues[7],
      raw_object_key: eventValues[8],
      raw_size: eventValues[9],
      content_hash: eventValues[10],
      acked_at: null,
    };
    this.events.set(String(event.event_id), event);
    const messageValues = messageStatement.values;
    this.messages.set(String(messageValues[0]), {
      message_id: messageValues[0],
      event_id: messageValues[1],
      provider_key: messageValues[2],
      identity_id: messageValues[3],
      worker_id: messageValues[4],
      envelope_recipient: messageValues[5],
      sender: messageValues[6],
      subject: messageValues[7],
      received_at: messageValues[8],
      raw_object_key: messageValues[9],
      raw_size: messageValues[10],
      content_hash: messageValues[11],
      processed_at: null,
      protected_until: null,
      deleted_at: null,
    });
    return [{ meta: { changes: 1 } }, { meta: { changes: 1 } }];
  }
}

class TinyR2 {
  objects = new Map<string, Uint8Array>();
  async put(key: string, value: ArrayBuffer | ArrayBufferView | string): Promise<void> {
    this.objects.set(key, typeof value === "string" ? new TextEncoder().encode(value) : new Uint8Array(value as ArrayBuffer));
  }
  async get(key: string): Promise<{ arrayBuffer(): Promise<ArrayBuffer> } | null> {
    const value = this.objects.get(key);
    return value ? { arrayBuffer: async () => value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength) } : null;
  }
  async delete(key: string): Promise<void> {
    this.objects.delete(key);
  }
}

function environment(db = new TinyD1(), r2 = new TinyR2()): Env & { db: TinyD1; r2: TinyR2 } {
  return {
    DB: db,
    MAIL_OBJECTS: r2,
    MAIL_EDGE_AUTH_SECRET: "fixture-mail-edge-secret-012345",
    MAIL_EDGE_MAX_MESSAGE_BYTES: "10485760",
    db,
    r2,
  };
}

function rawMessage(): Uint8Array {
  return new TextEncoder().encode(
    "From: sender@example.test\r\nTo: spoofed-other@agents.aiat.ca\r\nMessage-ID: <same@example.test>\r\nSubject: Code\r\n\r\nYour code is 481516",
  );
}

function stream(bytes: Uint8Array): ReadableStream<Uint8Array> {
  return new ReadableStream({ start(controller) { controller.enqueue(bytes); controller.close(); } });
}

describe("AIAT Cloudflare Email Worker", () => {
  it("normalizes addresses and rejects ambiguous recipients", () => {
    expect(normalizeEmailAddress("W-1@Agents.AIAT.CA")).toBe("w-1@agents.aiat.ca");
    expect(() => normalizeEmailAddress("w-1@agents.aiat.ca extra")).toThrow();
  });

  it("uses the envelope recipient, persists raw MIME in R2, and suppresses retries", async () => {
    const env = environment();
    env.db.recipients.set("w-a@agents.aiat.ca", {
      identity_id: "identity-a",
      worker_id: "worker-a",
      address: "w-a@agents.aiat.ca",
      provider_reference: "recipient:a",
      idempotency_key: "mailbox:a",
      state: "ACTIVE",
    });
    const rejected: string[] = [];
    const message = { from: "sender@example.test", to: "w-a@agents.aiat.ca", rawSize: rawMessage().byteLength, raw: stream(rawMessage()), setReject: (reason: string) => rejected.push(reason) };
    const result = await handleEmailMessage(message, env);
    expect(result.accepted).toBe(true);
    expect(env.db.messages.size).toBe(1);
    expect(env.r2.objects.size).toBe(1);
    expect((env.db.messages.values().next().value as Row).envelope_recipient).toBe("w-a@agents.aiat.ca");
    expect(rejected).toEqual([]);

    const retry = { ...message, raw: stream(rawMessage()), setReject: (reason: string) => rejected.push(reason) };
    const duplicate = await handleEmailMessage(retry, env);
    expect(duplicate.duplicate).toBe(true);
    expect(env.db.messages.size).toBe(1);
  });

  it("rejects unknown recipients before writing an object", async () => {
    const env = environment();
    const rejected: string[] = [];
    const bytes = rawMessage();
    const result = await handleEmailMessage({ from: "sender@example.test", to: "unknown@agents.aiat.ca", rawSize: bytes.byteLength, raw: stream(bytes), setReject: (reason: string) => rejected.push(reason) }, env);
    expect(result).toEqual({ accepted: false, reason: "unknown_recipient" });
    expect(rejected).toHaveLength(1);
    expect(env.r2.objects.size).toBe(0);
  });

  it("rejects suspended recipients and oversized messages before R2", async () => {
    const env = environment();
    env.db.recipients.set("suspended@agents.aiat.ca", {
      identity_id: "identity-suspended",
      worker_id: "worker-suspended",
      address: "suspended@agents.aiat.ca",
      provider_reference: "recipient:suspended",
      idempotency_key: "mailbox:suspended",
      state: "SUSPENDED",
    });
    const rejected: string[] = [];
    const suspended = await handleEmailMessage(
      { from: "sender@example.test", to: "suspended@agents.aiat.ca", rawSize: 1, raw: stream(new Uint8Array([1])), setReject: (reason: string) => rejected.push(reason) },
      env,
    );
    expect(suspended).toEqual({ accepted: false, reason: "recipient_inactive" });

    env.db.recipients.set("large@agents.aiat.ca", {
      identity_id: "identity-large",
      worker_id: "worker-large",
      address: "large@agents.aiat.ca",
      provider_reference: "recipient:large",
      idempotency_key: "mailbox:large",
      state: "ACTIVE",
    });
    const oversized = await handleEmailMessage(
      { from: "sender@example.test", to: "large@agents.aiat.ca", rawSize: 1025, raw: stream(new Uint8Array(1025)), setReject: (reason: string) => rejected.push(reason) },
      { ...env, MAIL_EDGE_MAX_MESSAGE_BYTES: "1024" },
    );
    expect(oversized).toEqual({ accepted: false, reason: "message_too_large" });
    expect(rejected).toHaveLength(2);
    expect(env.r2.objects.size).toBe(0);
  });

  it("keeps the signed HTTP API replay-protected", async () => {
    const env = environment();
    const path = "/v1/health";
    const body = new Uint8Array();
    const timestamp = Math.floor(Date.now() / 1000).toString();
    const nonce = "nonce-0123456789";
    const digest = await crypto.subtle.digest("SHA-256", body);
    const digestHex = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
    const canonical = `aiat.mail-edge.v1\nGET\n${path}\n${timestamp}\n${nonce}\n${digestHex}`;
    const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(env.MAIL_EDGE_AUTH_SECRET), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    const signature = btoa(String.fromCharCode(...new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(canonical)))));
    const headers = { "X-AIAT-Mail-Edge-Version": "aiat.mail-edge.v1", "X-AIAT-Mail-Edge-Timestamp": timestamp, "X-AIAT-Mail-Edge-Nonce": nonce, "X-AIAT-Mail-Edge-Signature": signature };
    const first = await worker.fetch(new Request(`https://edge.test${path}`, { headers }), env, {} as any);
    const replay = await worker.fetch(new Request(`https://edge.test${path}`, { headers }), env, {} as any);
    expect(first.status).toBe(200);
    expect(replay.status).toBe(401);
  });
});
