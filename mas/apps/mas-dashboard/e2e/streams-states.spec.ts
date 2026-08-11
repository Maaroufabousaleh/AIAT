import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

const HISTORY_FIXTURE = {
  entries: [
    {
      entry_id: "stream-e2e-001",
      envelope: JSON.stringify({
        message_type: "agent.started",
        sender_id: "streams-e2e",
        timestamp: "2026-08-01T00:00:00Z",
      }),
    },
  ],
};

test("stream monitor retains messages when reconnect fails", async ({ page }) => {
  await page.addInitScript(() => {
    let attempt = 0;

    class FakeEventSource extends EventTarget {
      static readonly CONNECTING = 0;
      static readonly OPEN = 1;
      static readonly CLOSED = 2;
      readonly url: string;
      readyState = FakeEventSource.OPEN;
      onerror: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent<string>) => void) | null = null;

      constructor(url: string) {
        super();
        this.url = url;
        const currentAttempt = ++attempt;
        window.setTimeout(() => {
          if (currentAttempt === 2) {
            this.emit("error", JSON.stringify({ error: "stream fixture unavailable" }));
            return;
          }
          this.emit("connected", JSON.stringify({ team: "exec_ceo" }));
          if (currentAttempt >= 3) {
            this.emit(
              "message",
              JSON.stringify({
                message_type: "agent.completed",
                sender_id: "streams-e2e",
                timestamp: "2026-08-01T00:00:01Z",
              }),
            );
          }
        }, 0);
      }

      close() {
        this.readyState = FakeEventSource.CLOSED;
      }

      private emit(type: string, data: string) {
        const event = new MessageEvent(type, { data });
        this.dispatchEvent(event);
        const handler = type === "message" ? this.onmessage : this.onerror;
        if (handler) handler(event);
      }
    }

    Object.defineProperty(window, "EventSource", {
      configurable: true,
      writable: true,
      value: FakeEventSource,
    });
  });

  await page.route("**/api/streams/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get("history") === "1") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(HISTORY_FIXTURE),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: "",
    });
  });

  await authenticate(page, "/streams");
  await expect(page.getByRole("heading", { name: "Agent Stream Monitor" })).toBeVisible();
  await expect(page.getByText("agent.started", { exact: true }).first()).toBeVisible();

  await page.getByRole("button", { name: "Reconnect stream" }).click();
  await expect(page.getByText("Showing last known stream data")).toBeVisible();
  await expect(page.getByText(/stream fixture unavailable/i)).toBeVisible();
  await expect(page.getByText("agent.started", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();

  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText("Showing last known stream data")).toHaveCount(0);
  await expect(page.getByText("agent.completed", { exact: true }).first()).toBeVisible();
});
