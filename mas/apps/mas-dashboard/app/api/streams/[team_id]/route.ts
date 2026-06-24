const MESSAGE_ROUTER_URL = process.env.MESSAGE_ROUTER_URL ?? "http://localhost:8001";
const ROUTER_SECRET = process.env.ROUTER_SECRET ?? "";

type Params = { params: { team_id: string } };
type RecentEntry = { entry_id: string; envelope: string };
type RecentResponse = { entries?: RecentEntry[] };

const STREAM_POLL_INTERVAL_MS = 1000;

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const onAbort = () => {
      clearTimeout(timeout);
      resolve();
    };
    const timeout = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

async function fetchRecentEntries(teamId: string, limit: string, token: string, after?: string) {
  const query = new URLSearchParams({ limit });
  if (after) query.set("after", after);
  const res = await fetch(
    `${MESSAGE_ROUTER_URL}/streams/${encodeURIComponent(teamId)}/recent?${query.toString()}`,
    {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    }
  );
  return res;
}

export async function GET(req: Request, { params }: Params) {
  const { team_id } = params;
  const { searchParams } = new URL(req.url);
  const token = `dashboard:${ROUTER_SECRET}`;

  if (searchParams.get("history") === "1") {
    const limit = searchParams.get("limit") ?? "50";
    const res = await fetchRecentEntries(team_id, limit, token);
    const body = await res.text();
    return new Response(body, {
      status: res.status,
      headers: { "Content-Type": res.headers.get("content-type") ?? "application/json" },
    });
  }

  const encoder = new TextEncoder();

  const stream = new ReadableStream({
    async start(controller) {
      let closed = false;
      let lastEntryId = req.headers.get("last-event-id") ?? "0-0";

      const close = () => {
        closed = true;
        try { controller.close(); } catch { /* already closed */ }
      };

      req.signal.addEventListener("abort", close, { once: true });

      try {
        const initial = await fetchRecentEntries(team_id, "1", token);
        if (!initial.ok) {
          const body = await initial.text();
          controller.enqueue(
            encoder.encode(
              `event: error\ndata: ${JSON.stringify({ error: body || `HTTP ${initial.status}` })}\n\n`
            )
          );
          close();
          return;
        }
        const data = (await initial.json()) as RecentResponse;
        if (!req.headers.get("last-event-id")) {
          lastEntryId = data.entries?.at(-1)?.entry_id ?? "0-0";
        }
        controller.enqueue(encoder.encode(`event: connected\ndata: {"team":"${team_id}"}\n\n`));
      } catch (err) {
        controller.enqueue(
          encoder.encode(`event: error\ndata: ${JSON.stringify({ error: String(err) })}\n\n`)
        );
        close();
        return;
      }

      while (!closed && !req.signal.aborted) {
        await sleep(STREAM_POLL_INTERVAL_MS, req.signal);
        if (closed || req.signal.aborted) break;

        try {
          const pageSize = 500;
          while (!closed && !req.signal.aborted) {
            const cursor = lastEntryId;
            const res = await fetchRecentEntries(team_id, String(pageSize), token, cursor);
            if (!res.ok) {
              const body = await res.text();
              controller.enqueue(
                encoder.encode(
                  `event: error\ndata: ${JSON.stringify({ error: body || `HTTP ${res.status}` })}\n\n`
                )
              );
              break;
            }
            const data = (await res.json()) as RecentResponse;
            const entries = data.entries ?? [];
            for (const entry of entries) {
              if (!entry.envelope) continue;
              controller.enqueue(
                encoder.encode(`id: ${entry.entry_id}\ndata: ${entry.envelope}\n\n`)
              );
            }
            if (entries.length === 0) break;
            lastEntryId = entries[entries.length - 1].entry_id;
            if (lastEntryId === cursor || entries.length < pageSize) break;
          }
        } catch (err) {
          controller.enqueue(
            encoder.encode(`event: error\ndata: ${JSON.stringify({ error: String(err) })}\n\n`)
          );
        }
      }
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      "Connection": "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
