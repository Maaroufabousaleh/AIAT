import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

const HISTORY_FIXTURE = {
  entries: [
    {
      entry_id: "ceo-states-001",
      envelope: JSON.stringify({
        message_type: "DIRECTIVE",
        sender_id: "ceo-states",
        project_id: "project-states",
        timestamp: "2026-08-01T00:00:00Z",
        payload: { summary: "retained directive" },
      }),
    },
  ],
};

test("CEO live feed retains messages when reconnect fails", async ({ page }) => {
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
            this.emit("error", JSON.stringify({ error: "CEO stream fixture unavailable" }));
            return;
          }
          this.emit("connected", JSON.stringify({ team: "exec_ceo" }));
          if (currentAttempt >= 3) {
            this.emit(
              "message",
              JSON.stringify({
                message_type: "REPORT",
                sender_id: "ceo-states",
                timestamp: "2026-08-01T00:00:01Z",
                payload: { summary: "recovered report" },
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

  await authenticate(page, "/ceo");
  await expect(page.getByRole("heading", { name: "CEO Live Feed" })).toBeVisible();
  await expect(page.getByText("DIRECTIVE", { exact: true }).first()).toBeVisible();

  await page.getByRole("button", { name: "Reconnect CEO feed" }).click();
  await expect(page.getByText("Showing last known CEO feed")).toBeVisible();
  await expect(page.getByText(/CEO stream fixture unavailable/i)).toBeVisible();
  await expect(page.getByText("DIRECTIVE", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();

  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText("Showing last known CEO feed")).toHaveCount(0);
  await expect(page.getByText("REPORT", { exact: true }).first()).toBeVisible();
});
