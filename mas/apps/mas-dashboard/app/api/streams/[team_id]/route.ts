import WebSocket from "ws";

const MESSAGE_ROUTER_URL = process.env.MESSAGE_ROUTER_URL ?? "http://localhost:8001";
const ROUTER_SECRET = process.env.ROUTER_SECRET ?? "";

type Params = { params: { team_id: string } };

export async function GET(req: Request, { params }: Params) {
  const { team_id } = params;
  const { searchParams } = new URL(req.url);
  const token = `dashboard:${ROUTER_SECRET}`;

  if (searchParams.get("history") === "1") {
    const limit = searchParams.get("limit") ?? "50";
    const res = await fetch(
      `${MESSAGE_ROUTER_URL}/streams/${encodeURIComponent(team_id)}/recent?limit=${encodeURIComponent(limit)}`,
      {
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store",
      }
    );
    const body = await res.text();
    return new Response(body, {
      status: res.status,
      headers: { "Content-Type": res.headers.get("content-type") ?? "application/json" },
    });
  }

  const wsUrl = MESSAGE_ROUTER_URL.replace(/^http/, "ws") + `/ws/subscribe/${team_id}`;
  const encoder = new TextEncoder();

  const stream = new ReadableStream({
    start(controller) {
      let ws: WebSocket;
      try {
        ws = new WebSocket(wsUrl, {
          headers: { Authorization: `Bearer ${token}` },
        });
      } catch (err) {
        controller.enqueue(
          encoder.encode(`event: error\ndata: ${JSON.stringify({ error: String(err) })}\n\n`)
        );
        controller.close();
        return;
      }

      ws.on("open", () => {
        controller.enqueue(encoder.encode(`event: connected\ndata: {"team":"${team_id}"}\n\n`));
      });

      ws.on("message", (data) => {
        controller.enqueue(encoder.encode(`data: ${data.toString()}\n\n`));
      });

      ws.on("error", (err) => {
        controller.enqueue(
          encoder.encode(`event: error\ndata: ${JSON.stringify({ error: err.message })}\n\n`)
        );
      });

      ws.on("close", () => {
        try { controller.close(); } catch { /* already closed */ }
      });

      req.signal.addEventListener("abort", () => {
        ws.close();
      });
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
