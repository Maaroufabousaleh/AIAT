import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

const HISTORY_FIXTURE = {
  entries: [
    {
      entry_id: "ceo-chat-states-001",
      envelope: JSON.stringify({
        message_type: "RESPONSE",
        sender_id: "ceo",
        timestamp: "2026-08-01T00:00:00Z",
        payload: { response: "retained CEO response" },
      }),
    },
  ],
};

test("CEO chat retains history through a stream failure and recovers on retry", async ({
  page,
}) => {
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
          this.emit("connected", JSON.stringify({ team: "exec_ceo" }));
          if (currentAttempt === 1) {
            window.setTimeout(() => {
              this.emit("error", JSON.stringify({ error: "CEO chat fixture unavailable" }));
            }, 20);
            return;
          }
          if (currentAttempt >= 2) {
            this.emit(
              "message",
              JSON.stringify({
                message_type: "RESPONSE",
                sender_id: "ceo",
                timestamp: "2026-08-01T00:00:01Z",
                payload: { response: "recovered CEO response" },
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

  await page.route("**/api/streams/exec_ceo**", async (route) => {
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

  await authenticate(page, "/ceo/chat");
  await expect(page.getByRole("heading", { name: "CEO Command Center" })).toBeVisible();
  await expect(page.getByText("retained CEO response", { exact: true })).toBeVisible();

  await expect(page.getByText("Showing last known CEO conversation")).toBeVisible();
  await expect(page.getByText(/CEO chat fixture unavailable/i)).toBeVisible();
  await expect(page.getByText("retained CEO response", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry CEO conversation" })).toBeVisible();

  await page.getByRole("button", { name: "Retry CEO conversation" }).click();
  await expect(page.getByText("Showing last known CEO conversation")).toHaveCount(0);
  await expect(page.getByText("recovered CEO response", { exact: true })).toBeVisible();
});
