import { spawn } from "child_process";

export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const container = searchParams.get("container");
  const tail = searchParams.get("tail") ?? "100";

  if (!container) {
    return new Response(JSON.stringify({ error: "container parameter required" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  const encoder = new TextEncoder();

  const stream = new ReadableStream({
    start(controller) {
      const proc = spawn("docker", ["logs", "--follow", "--tail", tail, container], {
        stdio: ["ignore", "pipe", "pipe"],
      });

      function sendLine(line: string) {
        controller.enqueue(
          encoder.encode(`data: ${JSON.stringify({ line, ts: Date.now() })}\n\n`)
        );
      }

      proc.stdout.on("data", (chunk: Buffer) => {
        chunk.toString().split("\n").filter(Boolean).forEach(sendLine);
      });
      proc.stderr.on("data", (chunk: Buffer) => {
        chunk.toString().split("\n").filter(Boolean).forEach(sendLine);
      });
      proc.on("error", (err) => {
        controller.enqueue(
          encoder.encode(`event: error\ndata: ${JSON.stringify({ error: err.message })}\n\n`)
        );
        controller.close();
      });
      proc.on("close", () => {
        try { controller.close(); } catch { /* already closed */ }
      });

      req.signal.addEventListener("abort", () => {
        proc.kill();
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
